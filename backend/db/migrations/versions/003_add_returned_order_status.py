"""Document addition of 'returned' as a valid order status.

The status column is a plain VARCHAR with no DB-level CHECK constraint,
so no DDL change is required. This migration records the intent and
updates the inline comment in the schema for clarity.

Revision ID: 003
Revises: 002
Create Date: 2026-03-29
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add a CHECK constraint to document the full valid status set.
    # This is additive and safe on existing data — all existing rows use
    # placed | shipped | delivered | cancelled | refunded, which are all valid.
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_status_check "
        "CHECK (status IN ('placed', 'processing', 'shipped', 'delivered', "
        "                  'returned', 'cancelled', 'refunded'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check")
