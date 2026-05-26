"""024 — Scheduled Reports + per-project email/Slack senders.

Introduces the scheduled-reports feature set:

  - `report_schedules`       — one row per schedule (dashboard + cadence + channels)
  - `report_runs`            — metadata-only audit of each send (NO payload stored)
  - `project_email_senders`  — BYO SMTP / SES credentials, per project, Fernet-encrypted
  - `project_slack_webhooks` — per-project Slack incoming webhooks, Fernet-encrypted

Design notes:

  * `report_runs` intentionally stores no card data / PDF bytes / rendered
    payload. Only metadata (started/finished, status, counts, error). This
    is the compliance story: we can prove the job ran without retaining
    the data it processed.

  * `report_schedules.cron_expression` is populated for *every* schedule,
    even preset cadences (daily/weekly/monthly), so the APScheduler job
    only needs to read one field. The preset picker in the UI simply writes
    the equivalent cron expression on save.

  * `filter_params` is a JSONB blob that may contain rolling-window macros
    like ``{"date_range": "{{last_7_days}}"}``. The scheduler interpolates
    these at run time so "last 7 days" stays meaningful across runs.

  * Credentials are Fernet-encrypted with the same ``TOKEN_ENCRYPTION_KEY``
    we use for Google tokens, via ``app.util.encryption``. Never store
    plaintext in these tables.

Revision ID: 024_scheduled_reports
Revises: 023_search_console
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "024_scheduled_reports"
down_revision = "023_search_console"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # project_email_senders — BYO SMTP / SES, per project
    # ------------------------------------------------------------------
    op.create_table(
        "project_email_senders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        # 'smtp' | 'ses'
        sa.Column("type", sa.String(16), nullable=False),
        # Fernet-encrypted JSON blob containing the per-type config:
        #   smtp: {host, port, username, password, tls_mode}
        #   ses:  {region, access_key_id, secret_access_key}
        sa.Column("config_encrypted", sa.Text, nullable=False),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(255), nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        # Last test-send result (from the "Send test" button in settings UI)
        sa.Column("last_tested_at", sa.DateTime, nullable=True),
        sa.Column("last_test_status", sa.String(16), nullable=True),   # 'success' | 'failed'
        sa.Column("last_test_error", sa.Text, nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("type IN ('smtp', 'ses')", name="ck_email_sender_type_valid"),
    )
    op.create_index("ix_project_email_senders_project_id", "project_email_senders", ["project_id"])
    # One default sender per project (partial unique index — only enforced where is_default = true)
    op.create_index(
        "ux_project_email_senders_one_default",
        "project_email_senders",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )

    # ------------------------------------------------------------------
    # project_slack_webhooks — per-project Slack incoming webhooks
    # ------------------------------------------------------------------
    op.create_table(
        "project_slack_webhooks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        # e.g. "#marketing-daily" or "Engineering alerts" — free text for humans
        sa.Column("label", sa.String(255), nullable=False),
        # Fernet-encrypted full webhook URL
        sa.Column("webhook_url_encrypted", sa.Text, nullable=False),
        sa.Column("last_tested_at", sa.DateTime, nullable=True),
        sa.Column("last_test_status", sa.String(16), nullable=True),
        sa.Column("last_test_error", sa.Text, nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_project_slack_webhooks_project_id", "project_slack_webhooks", ["project_id"])

    # ------------------------------------------------------------------
    # report_schedules — one row per scheduled report
    # ------------------------------------------------------------------
    op.create_table(
        "report_schedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dashboard_id", UUID(as_uuid=True), sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),

        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),

        # 'daily' | 'weekly' | 'monthly' | 'custom_cron'
        sa.Column("cadence", sa.String(16), nullable=False),
        # Always populated — even presets derive a cron expression.
        # APScheduler reads this directly.
        sa.Column("cron_expression", sa.String(128), nullable=False),
        # IANA timezone — per-schedule
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),

        # Filter state applied at render time. May contain rolling-window macros
        # like {"date_range": "{{last_7_days}}"} which the scheduler interpolates
        # against run_started_at.
        sa.Column("filter_params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),

        # List of channel specs. Each entry is one of:
        #   {"type": "email", "sender_id": "<uuid>", "to": ["a@b.com", ...]}
        #   {"type": "slack_webhook", "webhook_id": "<uuid>"}
        #   {"type": "slack_oauth", "channel_id": "..."}     (phase 2)
        sa.Column("channels", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),

        # 'pdf' for now. 'csv' / 'png' reserved for later.
        sa.Column("format", sa.String(16), nullable=False, server_default="pdf"),
        # Scheduled sends exclude the AI insights narrative by default.
        sa.Column("include_insights", sa.Boolean, nullable=False, server_default=sa.text("false")),

        # Scheduler state
        sa.Column("next_run_at", sa.DateTime, nullable=True),
        sa.Column("last_run_at", sa.DateTime, nullable=True),
        sa.Column("last_status", sa.String(16), nullable=True),   # 'success' | 'failed' | 'partial'
        sa.Column("last_error", sa.Text, nullable=True),
        # Auto-disables after 5 consecutive failures (see gotcha #3)
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),

        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),

        sa.CheckConstraint(
            "cadence IN ('daily', 'weekly', 'monthly', 'custom_cron')",
            name="ck_report_schedule_cadence_valid",
        ),
        sa.CheckConstraint(
            "format IN ('pdf', 'csv', 'png')",
            name="ck_report_schedule_format_valid",
        ),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN ('success', 'failed', 'partial')",
            name="ck_report_schedule_last_status_valid",
        ),
    )
    op.create_index("ix_report_schedules_project_id", "report_schedules", ["project_id"])
    op.create_index("ix_report_schedules_dashboard_id", "report_schedules", ["dashboard_id"])
    # APScheduler scans enabled schedules whose next_run_at has passed
    op.create_index(
        "ix_report_schedules_enabled_next_run",
        "report_schedules",
        ["enabled", "next_run_at"],
    )

    # ------------------------------------------------------------------
    # report_runs — metadata-only audit (NO payload)
    # ------------------------------------------------------------------
    op.create_table(
        "report_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("schedule_id", UUID(as_uuid=True), sa.ForeignKey("report_schedules.id", ondelete="CASCADE"), nullable=False),

        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime, nullable=True),

        # 'running' | 'success' | 'failed' | 'partial'
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),

        # Channel send counts — lets us answer "did everyone get it?" without
        # storing who.
        sa.Column("recipient_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("channels_succeeded", sa.Integer, nullable=False, server_default="0"),
        sa.Column("channels_failed", sa.Integer, nullable=False, server_default="0"),

        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),

        # 'schedule' (APScheduler fired it) | 'manual' ("Run now" button)
        sa.Column("triggered_by", sa.String(16), nullable=False, server_default="schedule"),

        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial')",
            name="ck_report_run_status_valid",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('schedule', 'manual')",
            name="ck_report_run_triggered_by_valid",
        ),
    )
    op.create_index("ix_report_runs_schedule_id", "report_runs", ["schedule_id"])
    op.create_index("ix_report_runs_started_at", "report_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_report_runs_started_at", table_name="report_runs")
    op.drop_index("ix_report_runs_schedule_id", table_name="report_runs")
    op.drop_table("report_runs")

    op.drop_index("ix_report_schedules_enabled_next_run", table_name="report_schedules")
    op.drop_index("ix_report_schedules_dashboard_id", table_name="report_schedules")
    op.drop_index("ix_report_schedules_project_id", table_name="report_schedules")
    op.drop_table("report_schedules")

    op.drop_index("ix_project_slack_webhooks_project_id", table_name="project_slack_webhooks")
    op.drop_table("project_slack_webhooks")

    op.drop_index("ux_project_email_senders_one_default", table_name="project_email_senders")
    op.drop_index("ix_project_email_senders_project_id", table_name="project_email_senders")
    op.drop_table("project_email_senders")
