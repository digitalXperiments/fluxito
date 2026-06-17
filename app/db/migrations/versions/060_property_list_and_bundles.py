"""058 — Property list flag + reusable property bundles.

Adds:
- ``tp_properties.is_list`` — flags a property whose value is an array of the
  declared ``data_type`` (list properties).
- ``tp_property_bundles`` — a named, reusable group of properties on a branch.
- ``tp_bundle_properties`` — the M2M link between a bundle and its properties,
  carrying a per-link ``required`` flag and ``sort_order``.

Bundles are template-copy: attaching a bundle to an event copies its property
links into ``tp_event_properties``. Editing a bundle later does NOT retroactively
update events it was already attached to (live-link is future work).

Revision ID: 058_property_list_and_bundles
Revises: 057_tp_comments
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "060_property_list_and_bundles"
down_revision = "059_tp_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tp_properties",
        sa.Column(
            "is_list",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "tp_property_bundles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_property_bundle_name"),
    )

    op.create_table(
        "tp_bundle_properties",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bundle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_property_bundles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint("bundle_id", "property_id", name="uq_tp_bundle_property"),
    )


def downgrade() -> None:
    op.drop_table("tp_bundle_properties")
    op.drop_table("tp_property_bundles")
    op.drop_column("tp_properties", "is_list")
