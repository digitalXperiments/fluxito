"""078 — Hosted Streamlit dashboard columns.

Adds kind/manifest/host status/connection bindings so Fluxito can store and
run a model-authored Streamlit artifact. Existing rows stay kind=legacy_cards.
Does not revive artifact_js / artifact_html / render_mode.

Revision ID: 078_hosted_dashboards
Revises: 077_project_ai_keys
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "078_hosted_dashboards"
down_revision = "077_project_ai_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column(
            "kind",
            sa.String(32),
            nullable=False,
            server_default="legacy_cards",
        ),
    )
    op.add_column(
        "dashboards",
        sa.Column(
            "manifest",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "dashboards",
        sa.Column("artifact_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "dashboards",
        sa.Column(
            "host_status",
            sa.String(32),
            nullable=False,
            server_default="stopped",
        ),
    )
    op.add_column(
        "dashboards",
        sa.Column("host_port", sa.Integer(), nullable=True),
    )
    op.add_column(
        "dashboards",
        sa.Column("host_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "dashboards",
        sa.Column(
            "connection_bindings",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "dashboards",
        sa.Column("runtime_token", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dashboards", "runtime_token")
    op.drop_column("dashboards", "connection_bindings")
    op.drop_column("dashboards", "host_error")
    op.drop_column("dashboards", "host_port")
    op.drop_column("dashboards", "host_status")
    op.drop_column("dashboards", "artifact_hash")
    op.drop_column("dashboards", "manifest")
    op.drop_column("dashboards", "kind")
