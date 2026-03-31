"""
Knowledge service — non-customer-facing.
Embeds the query, searches pgvector for relevant KB chunks,
and returns raw chunks + metadata to the conversation agent.
Does NOT generate a customer-facing response.
"""
import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.agents.state import AgentState

settings = get_settings()


async def knowledge_service_node(state: AgentState, config: dict) -> dict:
    """LangGraph node: embed query → pgvector search → return raw chunks."""
    db: AsyncSession = config["configurable"]["db"]

    # Extract the last customer message as the search query
    query = ""
    for msg in reversed(state["messages"]):
        if msg["role"] == "customer":
            query = msg["content"]
            break

    # Embed the query and run vector search
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
            LIMIT 5
        """)
        result = await db.execute(sql, {"embedding": str(query_embedding)})
        chunks = result.fetchall()
    except Exception as e:
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
        "pending_service": "",  # clear so conversation_agent routes to END after responding
        "actions_taken": state.get("actions_taken", []) + [
            {
                "service": "knowledge_service",
                "action": "search_kb",
                "query": query,
                "chunks_retrieved": len(retrieved),
                "top_similarity": top_similarity,
            }
        ],
    }
