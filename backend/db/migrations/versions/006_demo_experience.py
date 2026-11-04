"""Demo experience: CSAT tags and per-turn telemetry.

- conversations.csat_tags: JSONB list of structured feedback tags.
- turns: one row per agent turn with the pipeline events, latency and cost
  the demo's behind-the-scenes panel renders (replayable timeline).

Revision ID: 006
Revises: 005
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("csat_tags", JSONB, nullable=True))
    op.create_table(
        "turns",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=False), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("customer_message_id", UUID(as_uuid=False), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("agent_message_id", UUID(as_uuid=False), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("llm_calls", sa.Integer, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("cost_basis", sa.String, nullable=False, server_default="unavailable"),
        sa.Column("events", JSONB, nullable=False, server_default="[]"),
        sa.Column("escalation_reason", sa.String, nullable=True),
        sa.Column("context_summary", sa.Text, nullable=True),
        sa.Column("error", sa.String, nullable=True),
    )
    op.create_index("idx_turns_conversation", "turns", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("idx_turns_conversation", table_name="turns")
    op.drop_table("turns")
    op.drop_column("conversations", "csat_tags")
