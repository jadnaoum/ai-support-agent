"""Add tsvector column and GIN index to kb_chunks for hybrid search.

Adds a tsvector column populated via to_tsvector('english', chunk_text),
with a GIN index for fast full-text keyword search.

Revision ID: 005
Revises: 004
Create Date: 2026-04-08
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE kb_chunks ADD COLUMN chunk_tsv tsvector")
    op.execute(
        "UPDATE kb_chunks SET chunk_tsv = to_tsvector('english', chunk_text)"
    )
    op.execute(
        "CREATE INDEX idx_kb_chunks_tsv ON kb_chunks USING GIN (chunk_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_tsv")
    op.execute("ALTER TABLE kb_chunks DROP COLUMN IF EXISTS chunk_tsv")
