import asyncio
import time
import uuid
from pathlib import Path

import litellm
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.models import KBChunk, KBDocument
from backend.db.session import AsyncSessionLocal
from backend.ingestion.chunker import chunk_text

settings = get_settings()

KB_DIR = Path(__file__).parent.parent.parent / "docs" / "kb"

FILENAME_TO_CATEGORY: dict[str, str] = {
    "returns_and_refunds.md": "returns",
    "shipping.md": "shipping",
    "payments.md": "payments",
    "account_management.md": "account",
    "warranties.md": "warranty",
    "faq.md": "faq",
    "business_limitations.md": "limitations",
}


def _extract_title(content: str, fallback: str) -> str:
    """Return the first H1 heading from markdown, or fallback."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


async def ingest_file(db: AsyncSession, path: Path) -> int:
    """Ingest a single KB file. Returns number of chunks created."""
    filename = path.name
    category = FILENAME_TO_CATEGORY.get(filename, "general")
    content = path.read_text(encoding="utf-8")
    title = _extract_title(content, fallback=filename)

    # Delete existing document (chunks cascade via FK)
    result = await db.execute(select(KBDocument).where(KBDocument.filename == filename))
    existing = result.scalar_one_or_none()
    if existing:
        await db.execute(delete(KBChunk).where(KBChunk.document_id == existing.id))
        await db.delete(existing)
        await db.flush()

    # Create new document record
    doc = KBDocument(
        id=str(uuid.uuid4()),
        filename=filename,
        title=title,
        category=category,
    )
    db.add(doc)
    await db.flush()  # get doc.id

    # Chunk the content.
    # Lookup-table articles (one independent topic per H2 section) use heading-aware
    # splitting so each limitation gets its own embedding rather than being diluted
    # across a multi-topic token-bounded chunk.
    heading_split_files = {"business_limitations.md"}
    chunks = chunk_text(content, split_on_headings=(filename in heading_split_files))
    if not chunks:
        await db.commit()
        return 0

    # Batch embed all chunks in a single API call
    embed_response = await litellm.aembedding(
        model=settings.litellm_embedding_model,
        input=chunks,
    )
    embeddings = [item["embedding"] for item in embed_response["data"]]

    # Create chunk rows
    chunk_rows = [
        KBChunk(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            chunk_text=chunk,
            chunk_index=i,
            embedding=embedding,
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]
    db.add_all(chunk_rows)
    await db.commit()

    return len(chunk_rows)


async def ingest_all() -> None:
    """Ingest all markdown files from docs/kb/ into pgvector."""
    if not KB_DIR.exists():
        print(f"KB directory not found: {KB_DIR}")
        return

    md_files = sorted(KB_DIR.glob("*.md"))
    if not md_files:
        print(f"No markdown files found in {KB_DIR}")
        return

    print(f"Ingesting {len(md_files)} documents from {KB_DIR}\n")
    start = time.time()
    total_chunks = 0

    async with AsyncSessionLocal() as db:
        for path in md_files:
            file_start = time.time()
            n = await ingest_file(db, path)
            elapsed = time.time() - file_start
            print(f"  {path.name}: {n} chunks  ({elapsed:.1f}s)")
            total_chunks += n

        # Create HNSW index for cosine similarity search after bulk load
        print("\nCreating HNSW index...")
        await db.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_kb_embedding "
                "ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
        await db.commit()

    elapsed_total = time.time() - start
    print(f"\nDone. {total_chunks} total chunks across {len(md_files)} documents ({elapsed_total:.1f}s)")


if __name__ == "__main__":
    asyncio.run(ingest_all())
