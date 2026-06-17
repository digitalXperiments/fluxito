"""064 — tp_property_members link table + data-type cleanup + metric column drop.

Changes:
- Create ``tp_property_members`` (shared-reference link table replacing the old
  ``tp_properties.parent_property_id`` self-FK; Option B of the spec).
- Backfill ``tp_property_members`` from existing ``parent_property_id`` rows.
- Rewrite ``tp_properties.data_type`` values:
    - ``'int'`` → ``'integer'``
    - ``'array'`` rows that were parents of members → ``'object'`` + ``is_list=true``
    - remaining ``'array'`` rows → ``'string'`` + ``is_list=true``
- Swap the ``data_type`` CHECK from the old 6-value set to the new 5-value set
  (string / integer / float / boolean / object; no array, no int).
- Drop ``tp_properties.parent_property_id``.
- Drop measurement columns from ``tp_metrics``:
  ``type``, ``property_id``, ``dashboard_card_id``, ``filters``;
  drop ``ck_tp_metric_type``.

Downgrade reverses all of the above (best-effort; data-type values are mapped back).

Revision ID: 064_tp_members_type_cleanup
Revises: 063_tp_metric_dashboard_link
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "064_tp_members_type_cleanup"
down_revision = "063_tp_metric_dashboard_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create tp_property_members BEFORE touching tp_properties so we
    #    can backfill from the still-present parent_property_id column.
    # ------------------------------------------------------------------
    op.create_table(
        "tp_property_members",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "parent_property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_property_id",
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
        sa.UniqueConstraint(
            "parent_property_id",
            "member_property_id",
            name="uq_tp_property_member",
        ),
    )

    # ------------------------------------------------------------------
    # 2. Backfill: every tp_properties row with parent_property_id IS NOT
    #    NULL becomes a tp_property_members row.
    # ------------------------------------------------------------------
    op.execute(
        sa.text(
            """
            INSERT INTO tp_property_members
                        (id, parent_property_id, member_property_id, required, sort_order)
            SELECT  gen_random_uuid(),
                    parent_property_id,
                    id,
                    false,
                    0
            FROM    tp_properties
            WHERE   parent_property_id IS NOT NULL
            ON CONFLICT (parent_property_id, member_property_id) DO NOTHING
            """
        )
    )

    # ------------------------------------------------------------------
    # 3. Data-rewrite tp_properties.data_type BEFORE swapping the CHECK.
    # ------------------------------------------------------------------

    # 3a. int → integer
    op.execute(sa.text("UPDATE tp_properties SET data_type = 'integer' WHERE data_type = 'int'"))

    # 3b. array rows that ARE parents of at least one member → object + is_list=true
    op.execute(
        sa.text(
            """
            UPDATE tp_properties
            SET    data_type = 'object',
                   is_list   = true
            WHERE  data_type = 'array'
              AND  id IN (SELECT parent_property_id FROM tp_property_members)
            """
        )
    )

    # 3c. remaining array rows → string + is_list=true
    op.execute(
        sa.text(
            "UPDATE tp_properties " "SET data_type = 'string', is_list = true " "WHERE data_type = 'array'"
        )
    )

    # ------------------------------------------------------------------
    # 4. Swap the data_type CHECK constraint.
    # ------------------------------------------------------------------
    op.drop_constraint("ck_tp_property_data_type", "tp_properties", type_="check")
    op.create_check_constraint(
        "ck_tp_property_data_type",
        "tp_properties",
        "data_type IN ('string', 'integer', 'float', 'boolean', 'object')",
    )

    # ------------------------------------------------------------------
    # 5. Drop parent_property_id (FK cascade drops automatically with the
    #    column in PostgreSQL — no explicit constraint drop needed).
    # ------------------------------------------------------------------
    op.drop_column("tp_properties", "parent_property_id")

    # ------------------------------------------------------------------
    # 6. Drop measurement columns from tp_metrics.
    #    Drop CHECK first, then the columns (including those with FKs).
    # ------------------------------------------------------------------
    op.drop_constraint("ck_tp_metric_type", "tp_metrics", type_="check")

    # Drop the index added by migration 063 before dropping the column.
    op.drop_index("ix_tp_metrics_dashboard_card", table_name="tp_metrics")

    op.drop_column("tp_metrics", "type")
    op.drop_column("tp_metrics", "property_id")
    op.drop_column("tp_metrics", "dashboard_card_id")
    op.drop_column("tp_metrics", "filters")


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Reverse order of upgrade.
    # ------------------------------------------------------------------

    # 6r. Re-add tp_metrics measurement columns + CHECK.
    op.add_column(
        "tp_metrics",
        sa.Column(
            "type",
            sa.Text(),
            nullable=False,
            server_default="count",
        ),
    )
    op.add_column(
        "tp_metrics",
        sa.Column(
            "property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "tp_metrics",
        sa.Column(
            "dashboard_card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dashboard_cards.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "tp_metrics",
        sa.Column(
            "filters",
            JSONB(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_tp_metric_type",
        "tp_metrics",
        "type IN ('count', 'sum', 'unique', 'average', 'ratio')",
    )
    op.create_index("ix_tp_metrics_dashboard_card", "tp_metrics", ["dashboard_card_id"])

    # 5r. Re-add parent_property_id to tp_properties.
    op.add_column(
        "tp_properties",
        sa.Column(
            "parent_property_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tp_properties.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # Rebuild parent_property_id from tp_property_members.
    # Each member row's member_property_id gets parent_property_id set to
    # the row's parent_property_id (last-writer-wins for multi-parent edge case).
    op.execute(
        sa.text(
            """
            UPDATE tp_properties p
            SET    parent_property_id = m.parent_property_id
            FROM   tp_property_members m
            WHERE  m.member_property_id = p.id
            """
        )
    )

    # 4r. Restore old data_type CHECK (includes 'array', 'int').
    op.drop_constraint("ck_tp_property_data_type", "tp_properties", type_="check")
    op.create_check_constraint(
        "ck_tp_property_data_type",
        "tp_properties",
        "data_type IN ('string', 'int', 'float', 'boolean', 'object', 'array')",
    )

    # 3r. Reverse data_type rewrites (best-effort; is_list stays true for
    #     rows that were rewritten — that flag predates this migration).
    #     Reverse integer → int.
    op.execute(sa.text("UPDATE tp_properties SET data_type = 'int' WHERE data_type = 'integer'"))
    # Reverse object+is_list=true (that originated from array parent) → array.
    # We can only approximate: rows that have members in tp_property_members
    # and currently have data_type='object' and is_list=true are candidates.
    op.execute(
        sa.text(
            """
            UPDATE tp_properties
            SET    data_type = 'array',
                   is_list   = false
            WHERE  data_type = 'object'
              AND  is_list   = true
              AND  id IN (SELECT parent_property_id FROM tp_property_members)
            """
        )
    )
    # Reverse string+is_list=true (bare array) → array.
    op.execute(
        sa.text(
            "UPDATE tp_properties "
            "SET data_type = 'array', is_list = false "
            "WHERE data_type = 'string' AND is_list = true"
        )
    )

    # 3r-note: The above reverse for 'string + is_list=true → array' is
    # over-broad (legitimate string lists created AFTER migration 060 would
    # also be flipped). This is a known best-effort limitation of downgrade.

    # 2r + 1r. Drop tp_property_members (backfill data is gone).
    op.drop_table("tp_property_members")
