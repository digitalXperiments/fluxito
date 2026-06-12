"""055 — Drop the retired markdown-era sdr_* tables.

The structured tracking plan (tp_*, migration 054) is now the source of truth,
and all consumers read the published structured snapshot. The markdown SDR
parser/tools/routes/models were deleted in the cutover, so the sdr_* tables are
no longer referenced and can be dropped.

This is a one-way cutover migration (downgrade is not supported).

Revision ID: 055_drop_sdr_tables
Revises: 054_tracking_plan_schema
"""

from alembic import op

revision = "055_drop_sdr_tables"
down_revision = "054_tracking_plan_schema"
branch_labels = None
depends_on = None

# Drop child tables before parents. IF EXISTS keeps it safe even if a name is
# already absent; CASCADE clears the cross-table FKs (e.g. sdrs.current_version_id
# <-> sdr_versions).
_SDR_TABLES = (
    "sdr_refinement_state",
    "sdr_destinations",
    "sdr_parameters",
    "sdr_events",
    "sdr_intakes",
    "sdr_versions",
    "sdrs",
)


def upgrade() -> None:
    for table in _SDR_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    # Irreversible: the markdown-era schema is retired. To roll back, restore
    # from the 028/041/043 history on a fresh database.
    raise NotImplementedError("055_drop_sdr_tables is a one-way cutover migration")
