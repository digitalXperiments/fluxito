"""062 — Validation rules engine: tp_validation_rule table.

Additive: one new plan-scoped table storing configurable validation rules.
Includes a data migration that seeds default rules for every existing plan.
Reversible.

Revision ID: 062_tp_validation_rule
Revises: 061_tp_activity
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "062_tp_validation_rule"
down_revision = "061_tp_activity"
branch_labels = None
depends_on = None

# Default rules seeded for every plan (mirrored from rules.py DEFAULT_RULES).
# scope_category_id is NULL for all defaults (plan-wide scope).
_DEFAULT_RULES: list[dict] = [
    {
        "rule_type": "event_name_casing",
        "config": {"casing": "snake_case"},
        "severity": "warning",
        "enabled": True,
    },
    {
        "rule_type": "event_name_regex",
        "config": {"pattern": ""},
        "severity": "warning",
        "enabled": False,
    },
    {
        "rule_type": "event_requires_description",
        "config": None,
        "severity": "info",
        "enabled": True,
    },
    {
        "rule_type": "event_requires_owner",
        "config": {"business": True, "technical": False},
        "severity": "info",
        "enabled": True,
    },
    {
        "rule_type": "required_property",
        "config": {"property_name": "", "applies_to": "all"},
        "severity": "warning",
        "enabled": False,
    },
    {
        "rule_type": "property_type_consistency",
        "config": None,
        "severity": "error",
        "enabled": True,
    },
    {
        "rule_type": "pii_must_be_flagged",
        "config": {
            "patterns": [
                "email",
                "phone",
                "ssn",
                "first_name",
                "last_name",
                "address",
                "ip",
                "user_id",
            ]
        },
        "severity": "warning",
        "enabled": True,
    },
]


def upgrade() -> None:
    op.create_table(
        "tp_validation_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.Column("severity", sa.Text(), nullable=False, server_default="warning"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "scope_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name="ck_tp_validation_rule_severity",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "rule_type",
            "scope_category_id",
            name="uq_tp_validation_rule",
        ),
    )
    op.create_index(
        "ix_tp_validation_rule_plan",
        "tp_validation_rule",
        ["plan_id", "enabled"],
    )

    # Seed default rules for all existing plans.
    conn = op.get_bind()
    plan_rows = conn.execute(sa.text("SELECT id FROM tp_plans")).fetchall()
    if plan_rows:
        insert_rows = []
        for (plan_id,) in plan_rows:
            for rule in _DEFAULT_RULES:
                import json

                config_val = json.dumps(rule["config"]) if rule["config"] is not None else None
                insert_rows.append(
                    {
                        "id": str(uuid.uuid4()),
                        "plan_id": str(plan_id),
                        "rule_type": rule["rule_type"],
                        "config": config_val,
                        "severity": rule["severity"],
                        "enabled": rule["enabled"],
                        "scope_category_id": None,
                    }
                )
        if insert_rows:
            conn.execute(
                sa.text(
                    "INSERT INTO tp_validation_rule "
                    "(id, plan_id, rule_type, config, severity, enabled, scope_category_id) "
                    "VALUES (:id, :plan_id, :rule_type, CAST(:config AS jsonb), :severity, :enabled, :scope_category_id)"
                ),
                insert_rows,
            )


def downgrade() -> None:
    op.drop_index("ix_tp_validation_rule_plan", table_name="tp_validation_rule")
    op.drop_table("tp_validation_rule")
