"""Add turn_state JSONB column to conversations.

Persists agent state fields that must survive across turns:
actions_taken (confirmation gate), consecutive_blocks (3-block escalation),
and last_clarification_source (clarification cap).

Revision ID: 004
Revises: 003
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("turn_state", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "turn_state")
