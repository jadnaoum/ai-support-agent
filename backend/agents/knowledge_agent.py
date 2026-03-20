import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.agents.state import AgentState

settings = get_settings()

# PROMPT — edit here to change knowledge agent behavior
SYSTEM_PROMPT = """You are a helpful customer support agent for an e-commerce store.
Answer the customer's question using only the information provided in the knowledge base context below.
If the answer is not in the context, say you're not sure and offer to connect them with a specialist.
Be concise, friendly, and specific. Do not make up order numbers, dates, or prices.

Knowledge Base Context:
{context}"""


async def knowledge_agent_node(state: AgentState, config: dict) -> dict:
    """LangGraph node: embed query → pgvector search → LiteLLM response."""
    db: AsyncSession = config["configurable"]["db"]

    # Extract the last customer message as the search query
    query = ""
    for msg in reversed(state["messages"]):
        if msg["role"] == "customer":
            query = msg["content"]
            break

    # Embed the query
    embed_response = await litellm.aembedding(
        model=settings.litellm_embedding_model,
        input=[query],
    )
    query_embedding = embed_response["data"][0]["embedding"]

    # Vector search via raw SQL — pass embedding as string, pgvector accepts '[0.1, 0.2, ...]'
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

    # Build RAG context string
    if chunks:
        context = "\n\n---\n\n".join(
            f"Source: {row.title} ({row.category})\n{row.chunk_text}"
            for row in chunks
        )
        confidence = round(1.0 - chunks[0].cosine_distance, 4)
    else:
        context = "No relevant knowledge base articles found."
        confidence = 0.0

    # Build message list for LiteLLM
    messages_for_llm = [
        {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
    ]
    role_map = {"customer": "user", "agent": "assistant"}
    for msg in state["messages"][-settings.max_context_messages:]:
        if msg["role"] in role_map:
            messages_for_llm.append({"role": role_map[msg["role"]], "content": msg["content"]})

    # Generate response — do NOT commit; the endpoint owns the DB commit
    llm_response = await litellm.acompletion(
        model=settings.litellm_model,
        messages=messages_for_llm,
        stream=False,
    )
    agent_response = llm_response.choices[0].message.content

    return {
        "response": agent_response,
        "confidence": confidence,
        "actions_taken": state.get("actions_taken", []) + [
            {
                "agent": "knowledge",
                "action": "search_kb",
                "chunks_retrieved": len(chunks),
                "top_similarity": confidence,
            }
        ],
    }
