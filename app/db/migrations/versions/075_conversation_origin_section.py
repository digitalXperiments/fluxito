"""075 — conversations: add origin_section column.

Records which app section a chat was started from (page_context.section),
e.g. 'implement' or 'report', so the client can label conversations and the
harness can tailor the section-aware tool surface. Nullable — chats opened
outside a known section (or before this migration) have no origin.

Revision ID: 075_conversation_origin_section
Revises: 074_tp_drift
"""

import sqlalchemy as sa
from alembic import op

revision = "075_conversation_origin_section"
down_revision = "074_tp_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("origin_section", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "origin_section")
