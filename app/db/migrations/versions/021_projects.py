"""
Introduce project-based data model.

Replaces the user-level billing model with a project-level model. A Project
is the billing unit, data boundary, and collaboration space. Every connector,
dashboard, KPI definition, business context, template, and audit record
now belongs to a project — not to a user.

Steps:
  1. Create ``projects`` and ``project_members`` tables.
  2. Add ``project_id`` column (nullable) to all data tables.
  3. Backfill: create a personal project for each existing user, set project_id
     on all their data, create project_member (role=owner).
  4. For org-scoped data (KPIs, business context with org_id), migrate to the
     org owner's project.
  5. Drop old unique constraints and add new project-scoped ones.
  6. Drop legacy columns and tables (organizations, org_members, user_plans).

Revision ID: 021_projects
Revises: 020_usage_ledger_rollup
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "021_projects"
down_revision = "020_usage_ledger_rollup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Create new tables
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan", sa.String(16), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("billing_cycle", sa.String(16), nullable=True),
        sa.Column("max_seats", sa.Integer, nullable=False, server_default="1"),
        sa.Column("queries_included", sa.Integer, nullable=False, server_default="500"),
        sa.Column("current_period_reset", sa.Date, nullable=True),
        sa.Column("trial_ends_at", sa.DateTime, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.CheckConstraint("plan IN ('free', 'pro', 'team')", name="ck_project_plan_valid"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_projects_slug", "projects", ["slug"])

    op.create_table(
        "project_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("invited_by", UUID(as_uuid=True), nullable=True),
        sa.Column("invited_at", sa.DateTime, nullable=True),
        sa.Column("joined_at", sa.DateTime, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_project_member_role"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    # ------------------------------------------------------------------
    # Step 2: Add project_id column to all data tables (nullable first)
    # ------------------------------------------------------------------
    tables_needing_project_id = [
        "oauth_connections",
        "amplitude_connections",
        "adobe_connections",
        "redshift_connections",
        "snowflake_connections",
        "dashboards",
        "kpi_definitions",
        "business_contexts",
        "tool_call_audit",
        "usage_ledger",
        "templates",
        "activity_events",
    ]

    for table in tables_needing_project_id:
        op.add_column(
            table,
            sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        )

    # bq_connections uses "fluxito_project_id" to avoid collision with existing GCP project_id column
    op.add_column(
        "bq_connections",
        sa.Column("fluxito_project_id", UUID(as_uuid=True), nullable=True),
    )

    for table in tables_needing_project_id:
        # FK — tool_call_audit and activity_events use SET NULL, others CASCADE
        ondelete = "SET NULL" if table in ("tool_call_audit", "activity_events") else "CASCADE"
        op.create_foreign_key(
            f"fk_{table}_project_id",
            table,
            "projects",
            ["project_id"],
            ["id"],
            ondelete=ondelete,
        )
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])

    # FK + index for bq_connections.fluxito_project_id
    op.create_foreign_key(
        "fk_bq_connections_fluxito_project_id",
        "bq_connections",
        "projects",
        ["fluxito_project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_bq_connections_fluxito_project_id", "bq_connections", ["fluxito_project_id"])

    # ------------------------------------------------------------------
    # Step 3: Backfill — create personal projects for all existing users
    # ------------------------------------------------------------------
    # We use raw SQL for the backfill since this runs in Alembic.
    conn = op.get_bind()

    # 3a. Create a project for each user based on their plan
    conn.execute(sa.text("""
        INSERT INTO projects (id, name, slug, owner_id, plan, max_seats, queries_included,
                              stripe_customer_id, stripe_subscription_id, billing_cycle,
                              current_period_reset, trial_ends_at, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            COALESCE(u.display_name, split_part(u.email, '@', 1)) || '''s Project',
            LOWER(REPLACE(REPLACE(u.email, '@', '-'), '.', '-')) || '-' || LEFT(gen_random_uuid()::text, 8),
            u.id,
            COALESCE(up.tier, 'free'),
            CASE
                WHEN COALESCE(up.tier, 'free') = 'free' THEN 1
                WHEN COALESCE(up.tier, 'free') = 'pro' THEN 5
                WHEN COALESCE(up.tier, 'free') = 'team' THEN 10
                ELSE 1
            END,
            CASE
                WHEN COALESCE(up.tier, 'free') = 'free' THEN 500
                ELSE 0  -- 0 means unlimited for paid plans
            END,
            up.stripe_customer_id,
            up.stripe_subscription_id,
            up.billing_cycle,
            up.current_period_reset,
            up.trial_ends_at,
            NOW(),
            NOW()
        FROM users u
        LEFT JOIN user_plans up ON up.user_id = u.id
    """))

    # 3b. Create owner membership for each project
    conn.execute(sa.text("""
        INSERT INTO project_members (id, project_id, user_id, role, joined_at, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            p.id,
            p.owner_id,
            'owner',
            NOW(),
            true,
            NOW(),
            NOW()
        FROM projects p
    """))

    # 3c. Migrate org members to project members (for team plan orgs)
    # Find the project created for the org owner, then add org members
    conn.execute(sa.text("""
        INSERT INTO project_members (id, project_id, user_id, role, invited_by, invited_at, joined_at, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            p.id,
            om.user_id,
            om.role,
            om.invited_by,
            om.invited_at,
            om.joined_at,
            om.is_active,
            NOW(),
            NOW()
        FROM org_members om
        JOIN organizations o ON o.id = om.org_id
        JOIN projects p ON p.owner_id = o.owner_id
        WHERE om.user_id != o.owner_id  -- skip owner, already added above
        ON CONFLICT (project_id, user_id) DO NOTHING
    """))

    # ------------------------------------------------------------------
    # Step 4: Backfill project_id on all data tables
    # ------------------------------------------------------------------

    # 4a. Connection tables — match on user_id → project owner_id
    for table in [
        "oauth_connections", "amplitude_connections",
        "adobe_connections", "redshift_connections", "snowflake_connections",
    ]:
        conn.execute(sa.text(f"""
            UPDATE {table} t
            SET project_id = p.id
            FROM projects p
            WHERE p.owner_id = t.user_id
              AND t.project_id IS NULL
        """))

    # bq_connections uses fluxito_project_id (to avoid collision with GCP project_id column)
    conn.execute(sa.text("""
        UPDATE bq_connections t
        SET fluxito_project_id = p.id
        FROM projects p
        WHERE p.owner_id = t.user_id
          AND t.fluxito_project_id IS NULL
    """))

    # 4b. Dashboards — match on user_id → project owner_id
    conn.execute(sa.text("""
        UPDATE dashboards d
        SET project_id = p.id
        FROM projects p
        WHERE p.owner_id = d.user_id
          AND d.project_id IS NULL
    """))

    # 4c. KPI definitions — user_id-scoped → match on user_id
    conn.execute(sa.text("""
        UPDATE kpi_definitions k
        SET project_id = p.id
        FROM projects p
        WHERE k.user_id IS NOT NULL
          AND p.owner_id = k.user_id
          AND k.project_id IS NULL
    """))

    # 4d. KPI definitions — org_id-scoped → match on org owner's project
    conn.execute(sa.text("""
        UPDATE kpi_definitions k
        SET project_id = p.id
        FROM organizations o
        JOIN projects p ON p.owner_id = o.owner_id
        WHERE k.org_id IS NOT NULL
          AND k.org_id = o.id
          AND k.project_id IS NULL
    """))

    # 4e. Business contexts — same pattern as KPIs
    conn.execute(sa.text("""
        UPDATE business_contexts b
        SET project_id = p.id
        FROM projects p
        WHERE b.user_id IS NOT NULL
          AND p.owner_id = b.user_id
          AND b.project_id IS NULL
    """))
    conn.execute(sa.text("""
        UPDATE business_contexts b
        SET project_id = p.id
        FROM organizations o
        JOIN projects p ON p.owner_id = o.owner_id
        WHERE b.org_id IS NOT NULL
          AND b.org_id = o.id
          AND b.project_id IS NULL
    """))

    # 4f. Tool call audit — match on user_id
    conn.execute(sa.text("""
        UPDATE tool_call_audit t
        SET project_id = p.id
        FROM projects p
        WHERE p.owner_id = t.user_id
          AND t.project_id IS NULL
    """))

    # 4g. Usage ledger — match on user_id
    conn.execute(sa.text("""
        UPDATE usage_ledger ul
        SET project_id = p.id
        FROM projects p
        WHERE p.owner_id = ul.user_id
          AND ul.project_id IS NULL
    """))

    # 4h. Templates — user-created templates match on user_id (system templates stay NULL)
    conn.execute(sa.text("""
        UPDATE templates t
        SET project_id = p.id
        FROM projects p
        WHERE t.user_id IS NOT NULL
          AND p.owner_id = t.user_id
          AND t.project_id IS NULL
    """))

    # 4i. Activity events — match on user_id (best effort, some stay NULL)
    conn.execute(sa.text("""
        UPDATE activity_events ae
        SET project_id = p.id
        FROM projects p
        WHERE p.owner_id = ae.user_id
          AND ae.project_id IS NULL
    """))

    # ------------------------------------------------------------------
    # Step 5: Make project_id NOT NULL where appropriate
    # ------------------------------------------------------------------
    # Tables where project_id must be NOT NULL (all data belongs to a project)
    not_null_tables = [
        "oauth_connections",
        "amplitude_connections",
        "adobe_connections",
        "redshift_connections",
        "snowflake_connections",
        "dashboards",
        "usage_ledger",
    ]
    for table in not_null_tables:
        op.alter_column(table, "project_id", nullable=False)

    # bq_connections: fluxito_project_id NOT NULL
    op.alter_column("bq_connections", "fluxito_project_id", nullable=False)

    # Tables where project_id stays nullable:
    # - tool_call_audit: historical records before projects
    # - activity_events: some events are user-level (sign-ins)
    # - templates: system templates have no project
    # - kpi_definitions: allow project_id to be nullable during transition
    # - business_contexts: same

    # ------------------------------------------------------------------
    # Step 6: Drop old unique constraints and add project-scoped ones
    # ------------------------------------------------------------------
    # oauth_connections: drop old user-scoped unique, add project-scoped
    op.drop_constraint("uq_user_provider_email", "oauth_connections", type_="unique")
    op.create_unique_constraint(
        "uq_project_provider_email",
        "oauth_connections",
        ["project_id", "provider", "google_email"],
    )

    # ------------------------------------------------------------------
    # Step 7: Drop KPI/BusinessContext XOR constraints (replaced by project_id)
    # ------------------------------------------------------------------
    op.drop_constraint("ck_kpi_tenant_xor", "kpi_definitions", type_="check")
    op.drop_constraint("ck_bizctx_tenant_xor", "business_contexts", type_="check")

    # ------------------------------------------------------------------
    # Step 8: Drop all FK constraints that reference the legacy tables
    # (must happen BEFORE dropping the tables themselves)
    # ------------------------------------------------------------------

    # FKs on kpi_definitions → organizations, users
    op.drop_constraint("kpi_definitions_org_id_fkey", "kpi_definitions", type_="foreignkey")
    op.drop_constraint("kpi_definitions_user_id_fkey", "kpi_definitions", type_="foreignkey")

    # FKs on business_contexts → organizations, users
    op.drop_constraint("business_contexts_org_id_fkey", "business_contexts", type_="foreignkey")
    op.drop_constraint("business_contexts_user_id_fkey", "business_contexts", type_="foreignkey")

    # FK on user_plans → organizations
    op.drop_constraint("user_plans_org_id_fkey", "user_plans", type_="foreignkey")

    # ------------------------------------------------------------------
    # Step 9: Drop legacy tables (now safe — no remaining FKs)
    # ------------------------------------------------------------------
    # Drop org_members first (FK to organizations is implicit via table structure)
    op.drop_table("org_members")
    # Drop organizations
    op.drop_table("organizations")
    # Drop user_plans
    op.drop_table("user_plans")

    # ------------------------------------------------------------------
    # Step 10: Drop legacy columns from kpi_definitions and business_contexts
    # ------------------------------------------------------------------
    op.drop_column("kpi_definitions", "org_id")
    op.drop_column("kpi_definitions", "user_id")
    op.drop_column("business_contexts", "org_id")
    op.drop_column("business_contexts", "user_id")


def downgrade() -> None:
    """
    Downgrade is complex and lossy — recreating the old tables without
    the original data is not fully reversible. This provides structural
    rollback only.
    """
    # Re-add user_id and org_id to kpi_definitions and business_contexts
    op.add_column("business_contexts", sa.Column("user_id", UUID(as_uuid=True), nullable=True))
    op.add_column("business_contexts", sa.Column("org_id", UUID(as_uuid=True), nullable=True))
    op.add_column("kpi_definitions", sa.Column("user_id", UUID(as_uuid=True), nullable=True))
    op.add_column("kpi_definitions", sa.Column("org_id", UUID(as_uuid=True), nullable=True))

    # Re-create legacy tables (structure only)
    op.create_table(
        "user_plans",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tier", sa.String(16), nullable=False, server_default="free"),
        sa.Column("queries_included", sa.Integer, nullable=False, server_default="500"),
        sa.Column("credit_balance", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("billing_cycle", sa.String(16), nullable=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=True),
        sa.Column("plan_started_at", sa.DateTime, nullable=True),
        sa.Column("trial_ends_at", sa.DateTime, nullable=True),
        sa.Column("current_period_reset", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "organizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("billing_cycle", sa.String(16), nullable=True),
        sa.Column("seat_count", sa.Integer, nullable=False, server_default="5"),
        sa.Column("max_seats", sa.Integer, nullable=False, server_default="5"),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.CheckConstraint("max_seats >= 5", name="ck_org_min_seats"),
    )

    op.create_table(
        "org_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("invited_by", UUID(as_uuid=True), nullable=True),
        sa.Column("invited_at", sa.DateTime, nullable=True),
        sa.Column("joined_at", sa.DateTime, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_member"),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_org_member_role"),
    )

    # Re-add XOR constraints
    op.create_check_constraint("ck_kpi_tenant_xor", "kpi_definitions",
                               "(user_id IS NOT NULL) <> (org_id IS NOT NULL)")
    op.create_check_constraint("ck_bizctx_tenant_xor", "business_contexts",
                               "(user_id IS NOT NULL) <> (org_id IS NOT NULL)")

    # Restore old unique constraint on oauth_connections
    op.drop_constraint("uq_project_provider_email", "oauth_connections", type_="unique")
    op.create_unique_constraint("uq_user_provider_email", "oauth_connections",
                                ["user_id", "provider", "google_email"])

    # Drop project_id from all tables
    tables_with_project_id = [
        "oauth_connections", "amplitude_connections",
        "adobe_connections", "redshift_connections", "snowflake_connections",
        "dashboards", "kpi_definitions", "business_contexts",
        "tool_call_audit", "usage_ledger", "templates", "activity_events",
    ]
    for table in tables_with_project_id:
        op.drop_constraint(f"fk_{table}_project_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_project_id", table)
        op.drop_column(table, "project_id")

    # bq_connections: drop fluxito_project_id
    op.drop_constraint("fk_bq_connections_fluxito_project_id", "bq_connections", type_="foreignkey")
    op.drop_index("ix_bq_connections_fluxito_project_id", "bq_connections")
    op.drop_column("bq_connections", "fluxito_project_id")

    # Drop new tables
    op.drop_table("project_members")
    op.drop_table("projects")
