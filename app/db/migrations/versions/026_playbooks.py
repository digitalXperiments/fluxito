"""026 — Playbooks (Cowork-native scheduled monitors)

A *playbook* is a curated or user-authored Cowork scheduled-task recipe.

Why a new feature, not an extension of templates or report_schedules:

  * `templates`        deploy as dashboards (one-shot card recipes).
  * `report_schedules` render a deployed dashboard as PDF on a cron and
                       email/Slack it out — they run on OUR APScheduler.
  * `playbooks`        are PROMPTS Claude in Cowork executes on a cron —
                       monitoring/anomaly detection/digest delivery —
                       which means *zero* server-side compute on our side.

The two tables:

  ``playbooks``               — the recipe (curated system playbooks
                                seeded on boot, plus user-authored ones
                                scoped to a project on Pro/Team).
  ``playbook_installations``  — a record that a user installed a given
                                playbook into Cowork. We don't run the
                                task ourselves — Cowork does — but we
                                track installs so the UI can show
                                'Installed' status, and so a project
                                admin can see what monitors exist.

State / dedup is intentionally *not* a server table. Each playbook
prompt instructs Claude to read & write a per-playbook JSON file in
the Cowork workspace folder. That keeps cooldown logic transparent
to the user and zero-cost on our side.

Revision ID: 026_playbooks
Revises: 025_drop_dashboard_snapshots
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "026_playbooks"
down_revision = "025_drop_dashboard_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # playbooks — curated + user-authored Cowork prompt recipes
    # ------------------------------------------------------------------
    op.create_table(
        "playbooks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Project — NULL for system playbooks, set for user-authored.
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),

        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),

        # URL-friendly handle. Unique across system *and* user playbooks
        # so that copy/paste install commands are stable.
        sa.Column("slug", sa.String(160), nullable=False, unique=True),

        # 'system' | 'user'
        sa.Column(
            "playbook_type",
            sa.String(16),
            nullable=False,
            server_default="user",
        ),

        # Theme group used for filtering and badges in the UI.
        # See app.models.playbook for the canonical list.
        sa.Column(
            "theme",
            sa.String(32),
            nullable=False,
            server_default="daily_digest",
        ),
        sa.Column("icon", sa.String(32), nullable=True),

        # Which platforms must be connected for this playbook to be
        # useful. Same shape as templates.required_platforms.
        sa.Column(
            "required_platforms",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),

        # The actual prompt Claude in Cowork will execute on each fire.
        # May contain {{variable}} placeholders that are substituted at
        # install time from `variables` (see below).
        sa.Column("prompt_template", sa.Text, nullable=False),

        # Variable definitions: [{key, label, type, default, required, help}]
        sa.Column(
            "variables",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),

        # Suggested cron expression and human-readable label
        # ("Every weekday at 8am"). Both used by the install flow.
        sa.Column("default_cron", sa.String(64), nullable=True),
        sa.Column("default_schedule_label", sa.String(64), nullable=True),
        sa.Column("default_task_name", sa.String(160), nullable=True),

        # Advisory cooldown — the playbook prompt itself uses this when
        # writing its state file to avoid re-firing too quickly.
        sa.Column(
            "cooldown_hours",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),

        # Which channel types (slack / email / both) make sense for this
        # playbook. Hint only — the install flow lets users pick from
        # configured project channels.
        sa.Column(
            "channel_hints",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),

        sa.Column(
            "min_tier",
            sa.String(16),
            nullable=False,
            server_default="free",
        ),
        sa.Column(
            "use_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_featured",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),

        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.CheckConstraint(
            "playbook_type IN ('system', 'user')",
            name="ck_playbook_type_valid",
        ),
    )
    op.create_index("ix_playbooks_project_id", "playbooks", ["project_id"])
    op.create_index("ix_playbooks_theme", "playbooks", ["theme"])
    op.create_index("ix_playbooks_slug", "playbooks", ["slug"])
    op.create_index(
        "ix_playbooks_active_featured",
        "playbooks",
        ["is_active", "is_featured"],
    )

    # ------------------------------------------------------------------
    # playbook_installations — record of a Cowork install
    # ------------------------------------------------------------------
    op.create_table(
        "playbook_installations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "playbook_id",
            UUID(as_uuid=True),
            sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),

        # The exact cowork task name + cron used at install time. We
        # don't enforce uniqueness — a user might install the same
        # playbook twice with different parameters.
        sa.Column("task_name", sa.String(255), nullable=False),
        sa.Column("cron_expression", sa.String(128), nullable=False),

        # Resolved variable values used at install time, for display.
        sa.Column(
            "variable_values",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),

        # Free-text label of where the playbook reports to, e.g.
        # "Slack #growth" or "alerts@acme.com". Display only.
        sa.Column("channel_summary", sa.String(255), nullable=True),

        # Snapshot of the rendered prompt at install time. Stored so
        # the UI can show users exactly what Cowork will run, even if
        # the template changes upstream later.
        sa.Column("rendered_prompt", sa.Text, nullable=False),

        # 'active' | 'paused' | 'removed' — set by the user; we cannot
        # actually pause a Cowork task from this side, this is just an
        # acknowledgement flag.
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),

        sa.Column("notes", sa.Text, nullable=True),

        sa.Column(
            "installed_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime, nullable=True),

        sa.CheckConstraint(
            "status IN ('active', 'paused', 'removed')",
            name="ck_playbook_install_status_valid",
        ),
    )
    op.create_index(
        "ix_playbook_installs_playbook_id",
        "playbook_installations",
        ["playbook_id"],
    )
    op.create_index(
        "ix_playbook_installs_project_id",
        "playbook_installations",
        ["project_id"],
    )
    op.create_index(
        "ix_playbook_installs_user_id",
        "playbook_installations",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playbook_installs_user_id",
        table_name="playbook_installations",
    )
    op.drop_index(
        "ix_playbook_installs_project_id",
        table_name="playbook_installations",
    )
    op.drop_index(
        "ix_playbook_installs_playbook_id",
        table_name="playbook_installations",
    )
    op.drop_table("playbook_installations")

    op.drop_index("ix_playbooks_active_featured", table_name="playbooks")
    op.drop_index("ix_playbooks_slug", table_name="playbooks")
    op.drop_index("ix_playbooks_theme", table_name="playbooks")
    op.drop_index("ix_playbooks_project_id", table_name="playbooks")
    op.drop_table("playbooks")
