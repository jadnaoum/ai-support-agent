"""
Integration tests for the KB ingestion script.
LiteLLM embedding calls are mocked — no real API calls are made.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from sqlalchemy import select

from backend.db.models import KBChunk, KBDocument
from backend.ingestion.ingest import ingest_file

FAKE_EMBEDDING = [0.1] * 1536


def make_embedding_response(n_chunks: int) -> dict:
    return {"data": [{"embedding": FAKE_EMBEDDING} for _ in range(n_chunks)]}


def write_temp_kb_file(tmp_path: Path, filename: str, content: str) -> Path:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return path


SAMPLE_CONTENT = """# Shipping and Delivery

We offer several shipping options to meet your needs.

## Standard Shipping

Standard shipping takes 5-7 business days and is free on orders over $50.

## Express Shipping

Express shipping takes 2-3 business days and costs $9.99.

## Overnight Shipping

Overnight shipping delivers the next business day for $24.99.
"""


@pytest.fixture
def kb_file(tmp_path: Path) -> Path:
    return write_temp_kb_file(tmp_path, "shipping.md", SAMPLE_CONTENT)


@pytest.fixture
def kb_file_single_para(tmp_path: Path) -> Path:
    content = "# Simple Doc\n\nThis is a single short paragraph."
    return write_temp_kb_file(tmp_path, "faq.md", content)


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_ingest_creates_kb_document(mock_embed, db, kb_file):
    chunks_count = len(SAMPLE_CONTENT.split("\n\n"))
    mock_embed.return_value = make_embedding_response(chunks_count)

    await ingest_file(db, kb_file)

    result = await db.execute(select(KBDocument).where(KBDocument.filename == "shipping.md"))
    doc = result.scalar_one_or_none()
    assert doc is not None
    assert doc.title == "Shipping and Delivery"
    assert doc.category == "shipping"


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_ingest_creates_chunks(mock_embed, db, kb_file):
    # We need to match mock response size to actual chunk count — use a large number
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    n = await ingest_file(db, kb_file)

    assert n > 0
    result = await db.execute(select(KBChunk))
    chunks = result.scalars().all()
    assert len(chunks) == n


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_ingest_chunks_have_embeddings(mock_embed, db, kb_file):
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    await ingest_file(db, kb_file)

    result = await db.execute(select(KBChunk))
    chunks = result.scalars().all()
    assert all(c.embedding is not None for c in chunks)


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_reingest_replaces_old_chunks(mock_embed, db, kb_file):
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    n1 = await ingest_file(db, kb_file)
    n2 = await ingest_file(db, kb_file)

    # Chunk count should equal the second ingest, not double
    result = await db.execute(select(KBChunk))
    chunks = result.scalars().all()
    assert len(chunks) == n2
    # Also assert exactly one KBDocument with this filename
    result = await db.execute(select(KBDocument).where(KBDocument.filename == "shipping.md"))
    docs = result.scalars().all()
    assert len(docs) == 1


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_embedding_api_called_once_per_file(mock_embed, db, kb_file):
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    await ingest_file(db, kb_file)

    assert mock_embed.call_count == 1


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_embedding_batches_all_chunks_in_one_call(mock_embed, db, kb_file):
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    n = await ingest_file(db, kb_file)

    # The single call should have received all chunk texts as the input list
    call_args = mock_embed.call_args
    assert len(call_args.kwargs["input"]) == n


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_ingest_title_extracted_from_h1(mock_embed, db, kb_file_single_para):
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    await ingest_file(db, kb_file_single_para)

    result = await db.execute(select(KBDocument).where(KBDocument.filename == "faq.md"))
    doc = result.scalar_one_or_none()
    assert doc.title == "Simple Doc"


@patch("backend.ingestion.ingest.litellm.aembedding", new_callable=AsyncMock)
async def test_chunk_indices_are_sequential(mock_embed, db, kb_file):
    mock_embed.side_effect = lambda **kwargs: make_embedding_response(len(kwargs["input"]))

    n = await ingest_file(db, kb_file)

    result = await db.execute(
        select(KBChunk).order_by(KBChunk.chunk_index)
    )
    chunks = result.scalars().all()
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(n))
