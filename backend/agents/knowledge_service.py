"""
Knowledge service — non-customer-facing.
Embeds the query, searches pgvector for relevant KB chunks,
and returns raw chunks + metadata to the conversation agent.
Does NOT generate a customer-facing response.

When HYBRID_SEARCH_ENABLED=true (default), combines:
  - Semantic search: pgvector cosine similarity, top 20
  - Keyword search: PostgreSQL tsvector ts_rank, top 20
  Fused with Reciprocal Rank Fusion (RRF, k=60), returning top 6.

Falls back to semantic-only when HYBRID_SEARCH_ENABLED=false.
"""
import re

import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.agents.state import AgentState

settings = get_settings()

RRF_K = 60
TOP_CANDIDATES = 20
TOP_RESULTS = 6

_CONTRACTION_RE = re.compile(r"\w+'\w+")
_NON_ALPHA_RE = re.compile(r"[^a-zA-Z0-9 ]")


def _build_tsquery(q: str) -> str:
    """Build an OR-based tsquery string from a natural-language query.

    Uses OR (|) so a chunk only needs to match any term, not all terms.
    ts_rank naturally scores chunks that match more terms higher.
    Removes contractions first to avoid non-stopword fragments like 'didn'.
    Returns empty string if no terms remain after stopword removal.
    """
    # Remove contraction words, then non-alphanumeric chars
    cleaned = _CONTRACTION_RE.sub("", q)
    cleaned = _NON_ALPHA_RE.sub(" ", cleaned)
    terms = [t for t in cleaned.split() if len(t) > 1]
    if not terms:
        return ""
    return " | ".join(terms)


async def knowledge_service_node(state: AgentState, config: dict) -> dict:
    """LangGraph node: embed query → pgvector/tsvector search → RRF fusion → return raw chunks."""
    db: AsyncSession = config["configurable"]["db"]

    # For defective item claims the action tool sets a granular reason value.
    # Use a focused query rather than the raw customer message — defective claim
    # queries otherwise land on unrelated chunks due to low semantic overlap.
    _DEFECTIVE_QUERIES = {
        "defective_within_return_window": "defective damaged item return policy within return window refund replacement",
        "defective_within_warranty": "warranty claim defective product outside return window warranty coverage repair",
        "defective_no_coverage": "defective item outside warranty and return window options available",
    }
    last_action = (state.get("action_results") or [{}])[-1]
    reason = last_action.get("reason", "")
    if reason in _DEFECTIVE_QUERIES:
        query = _DEFECTIVE_QUERIES[reason]
    else:
        # Extract the last customer message as the search query
        query = ""
        for msg in reversed(state["messages"]):
            if msg["role"] == "customer":
                query = msg["content"]
                break

    if settings.hybrid_search_enabled:
        return await _hybrid_search(db, state, query)
    else:
        return await _semantic_search(db, state, query)


async def _semantic_search(db: AsyncSession, state: AgentState, query: str) -> dict:
    """Semantic-only search via pgvector cosine similarity."""
    try:
        embed_response = await litellm.aembedding(
            model=settings.litellm_embedding_model,
            input=[query],
        )
        query_embedding = embed_response["data"][0]["embedding"]

        sql = text("""
            SELECT c.id, c.chunk_text, d.title, d.category,
                   (c.embedding <=> :embedding) AS cosine_distance
            FROM kb_chunks c
            JOIN kb_documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> :embedding
            LIMIT :limit
        """)
        result = await db.execute(sql, {"embedding": str(query_embedding), "limit": TOP_RESULTS})
        chunks = result.fetchall()
    except Exception as e:
        return _error_result(state, query, e)

    retrieved = [
        {
            "chunk_text": row.chunk_text,
            "title": row.title,
            "category": row.category,
            "similarity": round(1.0 - row.cosine_distance, 4),
        }
        for row in chunks
    ]
    top_similarity = retrieved[0]["similarity"] if retrieved else 0.0

    return {
        "retrieved_context": retrieved,
        "pending_service": "",
        "actions_taken": state.get("actions_taken", []) + [
            {
                "service": "knowledge_service",
                "action": "search_kb",
                "mode": "semantic",
                "query": query,
                "chunks_retrieved": len(retrieved),
                "top_similarity": top_similarity,
            }
        ],
    }


async def _hybrid_search(db: AsyncSession, state: AgentState, query: str) -> dict:
    """Hybrid search: semantic + keyword, fused with RRF."""
    try:
        embed_response = await litellm.aembedding(
            model=settings.litellm_embedding_model,
            input=[query],
        )
        query_embedding = embed_response["data"][0]["embedding"]

        # Run semantic and keyword searches in parallel
        semantic_sql = text("""
            SELECT c.id, c.chunk_text, d.title, d.category,
                   (c.embedding <=> :embedding) AS cosine_distance
            FROM kb_chunks c
            JOIN kb_documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> :embedding
            LIMIT :limit
        """)
        keyword_sql = text("""
            SELECT c.id, c.chunk_text, d.title, d.category,
                   ts_rank(c.chunk_tsv, to_tsquery('english', :query)) AS rank
            FROM kb_chunks c
            JOIN kb_documents d ON c.document_id = d.id
            WHERE c.chunk_tsv IS NOT NULL
              AND c.chunk_tsv @@ to_tsquery('english', :query)
            ORDER BY rank DESC
            LIMIT :limit
        """)

        semantic_result = await db.execute(semantic_sql, {"embedding": str(query_embedding), "limit": TOP_CANDIDATES})
        semantic_rows = semantic_result.fetchall()

        kw_query = _build_tsquery(query)
        keyword_result = await db.execute(keyword_sql, {"query": kw_query, "limit": TOP_CANDIDATES}) if kw_query else None
        keyword_rows = keyword_result.fetchall() if keyword_result else []
    except Exception as e:
        return _error_result(state, query, e)

    # Build rank maps: chunk_id → 1-based rank
    semantic_ranks = {row.id: i + 1 for i, row in enumerate(semantic_rows)}
    keyword_ranks = {row.id: i + 1 for i, row in enumerate(keyword_rows)}

    # Collect all chunk metadata keyed by id
    chunk_meta: dict = {}
    for row in semantic_rows:
        chunk_meta[row.id] = {
            "chunk_text": row.chunk_text,
            "title": row.title,
            "category": row.category,
            "cosine_distance": row.cosine_distance,
        }
    for row in keyword_rows:
        if row.id not in chunk_meta:
            chunk_meta[row.id] = {
                "chunk_text": row.chunk_text,
                "title": row.title,
                "category": row.category,
                "cosine_distance": None,
            }

    # RRF fusion: score = sum(1 / (k + rank)) across both lists.
    # Chunks not in a list receive a default rank of TOP_CANDIDATES + 1 so they
    # still get a contribution from both lists rather than being penalised for
    # not appearing in keyword results at all.
    DEFAULT_RANK = TOP_CANDIDATES + 1
    all_ids = set(semantic_ranks) | set(keyword_ranks)
    rrf_scores: dict = {}
    for chunk_id in all_ids:
        sem_r = semantic_ranks.get(chunk_id, DEFAULT_RANK)
        kw_r = keyword_ranks.get(chunk_id, DEFAULT_RANK)
        rrf_scores[chunk_id] = 1.0 / (RRF_K + sem_r) + 1.0 / (RRF_K + kw_r)

    # Sort by RRF score descending, take top results
    ranked_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)[:TOP_RESULTS]

    retrieved = []
    for rank, chunk_id in enumerate(ranked_ids, start=1):
        meta = chunk_meta[chunk_id]
        sem_rank = semantic_ranks.get(chunk_id)
        kw_rank = keyword_ranks.get(chunk_id)
        similarity = round(1.0 - meta["cosine_distance"], 4) if meta["cosine_distance"] is not None else None
        retrieved.append({
            "chunk_text": meta["chunk_text"],
            "title": meta["title"],
            "category": meta["category"],
            "similarity": similarity,
            "rrf_score": round(rrf_scores[chunk_id], 6),
            "semantic_rank": sem_rank,
            "keyword_rank": kw_rank,
        })

    top_similarity = retrieved[0]["similarity"] if retrieved and retrieved[0]["similarity"] is not None else 0.0

    return {
        "retrieved_context": retrieved,
        "pending_service": "",
        "actions_taken": state.get("actions_taken", []) + [
            {
                "service": "knowledge_service",
                "action": "search_kb",
                "mode": "hybrid",
                "query": query,
                "chunks_retrieved": len(retrieved),
                "top_similarity": top_similarity,
                "semantic_candidates": len(semantic_rows),
                "keyword_candidates": len(keyword_rows),
            }
        ],
    }


def _error_result(state: AgentState, query: str, e: Exception) -> dict:
    return {
        "retrieved_context": [],
        "action_results": (state.get("action_results") or []) + [
            {"success": False, "error": f"Knowledge search failed: {str(e)}"}
        ],
        "pending_service": "",
        "actions_taken": (state.get("actions_taken") or []) + [
            {
                "service": "knowledge_service",
                "action": "search_kb",
                "query": query,
                "chunks_retrieved": 0,
                "top_similarity": 0.0,
                "success": False,
            }
        ],
    }
