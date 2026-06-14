# Tracking Plan Revamp — Plan 1A: Data Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the relational source-of-truth for the Avo-style tracking plan — 13 `tp_*` tables, their SQLAlchemy models, the alembic migration, and a shared service layer (CRUD + validation + `plan_to_dict` serializer) — all unit-tested. No MCP/UI/exports yet (those are Plans 1B–1D).

**Architecture:** A single plan per project. Every content row is scoped to a `branch_id`; Phase 1 only uses the auto-created `main` branch. A `app/services/tracking_plan/` package is the **only** module that mutates `tp_*` tables; every later layer (MCP, HTTP/UI) will call it. One `plan_to_dict` serializer is the canonical read shape feeding everything downstream.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async (`Mapped`/`mapped_column`), Postgres (asyncpg), Alembic, pytest + `pytest.mark.anyio`. Tests build the schema from `Base.metadata` via the `db_engine`/`db_session_factory` fixtures in `tests/conftest.py` (so new models MUST be registered in `app/models/__init__.py`).

---

## Conventions (read once)

- Run all commands from the repo root: `/Users/rambokkisa/Work/InsaneAnalytics/Development/fluxito/.worktrees/task/sdr-tracking-plan-revamp-f88283`.
- Tests need Postgres + Redis locally: `docker compose up -d postgres redis` (the `db_engine` fixture drops/creates all tables from `Base.metadata` each test).
- After editing any Python file, run `ruff format <files>` and `ruff check <files>` — CI uses pinned `ruff==0.8.4` and the format-check fails on un-formatted new lines.
- Every task ends green and committed. Do **not** push (the project's `tox`-green-before-push gate is out of scope for this plan; pushing happens later).
- Async DB idioms used throughout: `await session.execute(select(...))` then `.scalar_one_or_none()` / `.scalars().all()`; `session.add(obj)`; `await session.flush()` to populate PKs; `await session.delete(obj)`. **Never** lazy-load ORM relationships in async code — the serializer queries tables explicitly.

---

## File Structure

**Create:**
- `app/models/tracking_plan.py` — all 13 `tp_*` SQLAlchemy models + allowed-value tuples.
- `app/db/migrations/versions/054_tracking_plan_schema.py` — create/drop all 13 tables.
- `app/services/tracking_plan/__init__.py` — package re-exports (public service API).
- `app/services/tracking_plan/exceptions.py` — `TrackingPlanError`, `NotFoundError`, `ValidationError`, `ConflictError`.
- `app/services/tracking_plan/common.py` — `coerce_uuid`, `get_or_raise`, `apply_fields`, `_UNSET`.
- `app/services/tracking_plan/bootstrap.py` — `get_or_create_plan`, `get_main_branch`.
- `app/services/tracking_plan/taxonomy.py` — category CRUD.
- `app/services/tracking_plan/events.py` — event CRUD + `set_event_sources` + `set_event_destination`.
- `app/services/tracking_plan/properties.py` — property CRUD + `attach_property`/`detach_property`.
- `app/services/tracking_plan/routing.py` — source CRUD, destination CRUD, connect/disconnect routing.
- `app/services/tracking_plan/metrics.py` — metric CRUD.
- `app/services/tracking_plan/serializer.py` — `plan_to_dict`.
- `app/services/tracking_plan/validation.py` — `validate_plan`.
- `tests/services/tracking_plan/__init__.py` — empty.
- `tests/services/tracking_plan/test_*.py` — one module per task.

**Modify:**
- `app/models/__init__.py` — import + `__all__` the 13 new models.

---

### Task 1: Models — the 13 `tp_*` tables

**Files:**
- Create: `app/models/tracking_plan.py`
- Modify: `app/models/__init__.py`
- Test: `tests/services/tracking_plan/test_models.py`

- [ ] **Step 1: Create the models file**

```python
# app/models/tracking_plan.py
"""Tracking Plan (revamped SDR) relational models — the structured source of truth.

A tracking plan is the Avo-style structured definition of a project's analytics:
events, a reusable property library, user properties, sources, destinations,
source -> destination routing, per-event mapping rules, categories, and metrics.

One plan per project. Every content row is scoped to a branch (Phase 1 uses only
the auto-created ``main`` branch). Published snapshots live in ``tp_versions`` as
a JSONB ``snapshot`` produced by the service-layer serializer.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Allowed enum-ish values (enforced by CHECK constraints + service validation)
BRANCH_STATUSES = ("active", "merged", "abandoned")
PROPERTY_KINDS = ("event", "user", "group", "system")
PROPERTY_DATA_TYPES = ("string", "int", "float", "boolean", "object", "array")
IMPL_STATUSES = ("planned", "implemented", "verified", "deprecated")
METRIC_TYPES = ("count", "sum", "unique", "average", "ratio")


class TPPlan(Base):
    """One tracking plan per project."""

    __tablename__ = "tp_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tp_branches.id", use_alter=True, name="fk_tp_plan_default_branch"),
        nullable=True,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tp_versions.id", use_alter=True, name="fk_tp_plan_current_version"),
        nullable=True,
    )
    intake_answers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    __table_args__ = (UniqueConstraint("project_id", name="uq_tp_plan_per_project"),)


class TPBranch(Base):
    """A branch of a plan. Phase 1: exactly one `main` branch per plan."""

    __tablename__ = "tp_branches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    base_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id"), nullable=True
    )
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("plan_id", "name", name="uq_tp_branch_name"),
        CheckConstraint("status IN ('active', 'merged', 'abandoned')", name="ck_tp_branch_status"),
    )


class TPVersion(Base):
    """Immutable published snapshot of a branch (full plan serialized to JSONB)."""

    __tablename__ = "tp_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tp_branches.id"), nullable=False)
    version_number: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("plan_id", "version_number", name="uq_tp_version"),)


class TPCategory(Base):
    """Event category (grouping) — branch-scoped."""

    __tablename__ = "tp_categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_tp_category_name"),)


class TPEvent(Base):
    """A tracked event — branch-scoped."""

    __tablename__ = "tp_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_categories.id", ondelete="SET NULL"), nullable=True
    )
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_business: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_technical: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_required: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_tp_event_name"),)


class TPProperty(Base):
    """A reusable property in the library — event/user/group/system, branch-scoped."""

    __tablename__ = "tp_properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="event")
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parent_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=True
    )
    is_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("branch_id", "kind", "name", name="uq_tp_property_name"),
        CheckConstraint(
            "kind IN ('event', 'user', 'group', 'system')", name="ck_tp_property_kind"
        ),
        CheckConstraint(
            "data_type IN ('string', 'int', 'float', 'boolean', 'object', 'array')",
            name="ck_tp_property_data_type",
        ),
    )


class TPEventProperty(Base):
    """M2M link: an event uses a library property, with per-attachment overrides."""

    __tablename__ = "tp_event_properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=False
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (UniqueConstraint("event_id", "property_id", name="uq_tp_event_property"),)


class TPSource(Base):
    """A source platform that emits events — branch-scoped."""

    __tablename__ = "tp_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    platform_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_tp_source_name"),)


class TPDestination(Base):
    """A destination platform that receives events — branch-scoped."""

    __tablename__ = "tp_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    platform_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_tp_destination_name"),)


class TPSourceDestination(Base):
    """Routing M2M: a source forwards to a destination."""

    __tablename__ = "tp_source_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_sources.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_destinations.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (UniqueConstraint("source_id", "destination_id", name="uq_tp_source_destination"),)


class TPEventSource(Base):
    """Event scoping M2M + per-source implementation status."""

    __tablename__ = "tp_event_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_sources.id", ondelete="CASCADE"), nullable=False
    )
    implementation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planned")

    __table_args__ = (
        UniqueConstraint("event_id", "source_id", name="uq_tp_event_source"),
        CheckConstraint(
            "implementation_status IN ('planned', 'implemented', 'verified', 'deprecated')",
            name="ck_tp_event_source_status",
        ),
    )


class TPEventDestination(Base):
    """Per (event x destination) mapping rule."""

    __tablename__ = "tp_event_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_destinations.id", ondelete="CASCADE"), nullable=False
    )
    dest_event_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_mappings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("event_id", "destination_id", name="uq_tp_event_destination"),)


class TPMetric(Base):
    """An event-based metric — branch-scoped."""

    __tablename__ = "tp_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=False, server_default="count")
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=True
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_properties.id", ondelete="SET NULL"), nullable=True
    )
    filters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("branch_id", "name", name="uq_tp_metric_name"),
        CheckConstraint(
            "type IN ('count', 'sum', 'unique', 'average', 'ratio')", name="ck_tp_metric_type"
        ),
    )
```

- [ ] **Step 2: Register the models in `app/models/__init__.py`**

Add this import block (alphabetical-ish, after the `template` import) and the names to `__all__`:

```python
from app.models.tracking_plan import (
    TPBranch,
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventProperty,
    TPEventSource,
    TPMetric,
    TPPlan,
    TPProperty,
    TPSource,
    TPSourceDestination,
    TPVersion,
)
```

Add to `__all__` (anywhere in the list): `"TPBranch", "TPCategory", "TPDestination", "TPEvent", "TPEventDestination", "TPEventProperty", "TPEventSource", "TPMetric", "TPPlan", "TPProperty", "TPSource", "TPSourceDestination", "TPVersion",`.

- [ ] **Step 3: Write the failing schema test**

```python
# tests/services/tracking_plan/test_models.py
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.tracking_plan import TPBranch, TPEvent, TPPlan


async def _make_project_and_user(session):
    """Insert a minimal project + user to satisfy FKs; return (project_id, user_id)."""
    from app.models.project import Project
    from app.models.user import User

    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", name="T")
    session.add(user)
    await session.flush()
    project = Project(name="P", owner_id=user.id)
    session.add(project)
    await session.flush()
    return project.id, user.id


@pytest.mark.anyio
async def test_plan_event_unique_name_per_branch(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)

        plan = TPPlan(project_id=project_id, name="Plan", created_by=user_id)
        session.add(plan)
        await session.flush()
        branch = TPBranch(plan_id=plan.id, name="main", is_main=True, created_by=user_id)
        session.add(branch)
        await session.flush()

        session.add(TPEvent(plan_id=plan.id, branch_id=branch.id, name="purchase"))
        await session.flush()

        # Duplicate event name on the same branch must violate the unique constraint
        session.add(TPEvent(plan_id=plan.id, branch_id=branch.id, name="purchase"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.anyio
async def test_one_plan_per_project(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        session.add(TPPlan(project_id=project_id, name="A", created_by=user_id))
        await session.flush()
        session.add(TPPlan(project_id=project_id, name="B", created_by=user_id))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
```

> **Note for the implementer:** Open `app/models/project.py` and `app/models/user.py` first and adjust the `Project(...)` / `User(...)` kwargs in `_make_project_and_user` to match the real required columns (e.g. the owner column may be `owner_id` or `created_by`; `User` may require `hashed_password`). Use whatever those models actually require — this helper is reused by later test modules, so get it right here.

Also create the empty package marker:

```python
# tests/services/tracking_plan/__init__.py
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_models.py -v`
Expected: FAIL — `ImportError`/`no such table` until the migration-free `Base.metadata.create_all` picks up the new models. (The `db_engine` fixture calls `create_all`, so once models import cleanly and are registered, tables exist. If it fails on the FK helper, fix the `Project`/`User` kwargs per the note.)

- [ ] **Step 5: Make it pass**

Fix the `_make_project_and_user` kwargs to match real model columns; re-run until green.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/models/tracking_plan.py app/models/__init__.py tests/services/tracking_plan/
ruff check app/models/tracking_plan.py app/models/__init__.py tests/services/tracking_plan/
git add app/models/tracking_plan.py app/models/__init__.py tests/services/tracking_plan/
git commit -m "feat(tracking-plan): add tp_* relational models"
```

---

### Task 2: Alembic migration for the 13 tables

**Files:**
- Create: `app/db/migrations/versions/054_tracking_plan_schema.py`
- Test: (manual upgrade/downgrade round-trip)

- [ ] **Step 1: Confirm the current alembic head**

Run: `python -m alembic heads`
Expected: a single head `053_dashboard_filter_presets (head)`. If it differs, use that value as `down_revision` below.

- [ ] **Step 2: Write the migration**

```python
# app/db/migrations/versions/054_tracking_plan_schema.py
"""054 — Tracking Plan revamp: relational source-of-truth schema (tp_*)

Creates the 13 tp_* tables that replace the markdown-as-truth SDR model.
Branch-scoped content; published snapshots in tp_versions (JSONB).

Revision ID: 054_tracking_plan_schema
Revises: 053_dashboard_filter_presets
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "054_tracking_plan_schema"
down_revision = "053_dashboard_filter_presets"
branch_labels = None
depends_on = None


def _id_col():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"))


def upgrade() -> None:
    # tp_plans (FKs to tp_branches / tp_versions added later — circular)
    op.create_table(
        "tp_plans",
        _id_col(),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_branch_id", UUID(as_uuid=True), nullable=True),
        sa.Column("current_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("intake_answers", JSONB(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_tp_plan_per_project"),
    )

    op.create_table(
        "tp_branches",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("base_branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id"), nullable=True),
        sa.Column("base_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        _ts("created_at"),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("plan_id", "name", name="uq_tp_branch_name"),
        sa.CheckConstraint("status IN ('active', 'merged', 'abandoned')", name="ck_tp_branch_status"),
    )

    op.create_table(
        "tp_versions",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id"), nullable=False),
        sa.Column("version_number", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        _ts("published_at"),
        sa.UniqueConstraint("plan_id", "version_number", name="uq_tp_version"),
    )

    op.create_foreign_key(
        "fk_tp_plan_default_branch", "tp_plans", "tp_branches", ["default_branch_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_tp_plan_current_version", "tp_plans", "tp_versions", ["current_version_id"], ["id"]
    )

    op.create_table(
        "tp_categories",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_category_name"),
    )

    op.create_table(
        "tp_events",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("tp_categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tags", ARRAY(sa.Text()), nullable=True),
        sa.Column("trigger_type", sa.Text(), nullable=True),
        sa.Column("trigger_config", JSONB(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("owner_business", sa.Text(), nullable=True),
        sa.Column("owner_technical", sa.Text(), nullable=True),
        sa.Column("consent_required", ARRAY(sa.Text()), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_event_name"),
    )

    op.create_table(
        "tp_properties",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="event"),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("constraints", JSONB(), nullable=True),
        sa.Column("parent_property_id", UUID(as_uuid=True), sa.ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_pii", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "kind", "name", name="uq_tp_property_name"),
        sa.CheckConstraint("kind IN ('event', 'user', 'group', 'system')", name="ck_tp_property_kind"),
        sa.CheckConstraint(
            "data_type IN ('string', 'int', 'float', 'boolean', 'object', 'array')",
            name="ck_tp_property_data_type",
        ),
    )

    op.create_table(
        "tp_event_properties",
        _id_col(),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True), sa.ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("override_description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("event_id", "property_id", name="uq_tp_event_property"),
    )

    op.create_table(
        "tp_sources",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("platform_type", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("connector_ref", JSONB(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_source_name"),
    )

    op.create_table(
        "tp_destinations",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("platform_account_id", sa.Text(), nullable=True),
        sa.Column("config", JSONB(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_destination_name"),
    )

    op.create_table(
        "tp_source_destinations",
        _id_col(),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("tp_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", UUID(as_uuid=True), sa.ForeignKey("tp_destinations.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("source_id", "destination_id", name="uq_tp_source_destination"),
    )

    op.create_table(
        "tp_event_sources",
        _id_col(),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("tp_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("implementation_status", sa.Text(), nullable=False, server_default="planned"),
        sa.UniqueConstraint("event_id", "source_id", name="uq_tp_event_source"),
        sa.CheckConstraint(
            "implementation_status IN ('planned', 'implemented', 'verified', 'deprecated')",
            name="ck_tp_event_source_status",
        ),
    )

    op.create_table(
        "tp_event_destinations",
        _id_col(),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", UUID(as_uuid=True), sa.ForeignKey("tp_destinations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dest_event_name", sa.Text(), nullable=True),
        sa.Column("property_mappings", JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_id", "destination_id", name="uq_tp_event_destination"),
    )

    op.create_table(
        "tp_metrics",
        _id_col(),
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", UUID(as_uuid=True), sa.ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False, server_default="count"),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("tp_events.id", ondelete="CASCADE"), nullable=True),
        sa.Column("property_id", UUID(as_uuid=True), sa.ForeignKey("tp_properties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filters", JSONB(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("branch_id", "name", name="uq_tp_metric_name"),
        sa.CheckConstraint("type IN ('count', 'sum', 'unique', 'average', 'ratio')", name="ck_tp_metric_type"),
    )

    # Helpful indexes for branch-scoped reads
    op.create_index("ix_tp_events_branch", "tp_events", ["branch_id"])
    op.create_index("ix_tp_properties_branch", "tp_properties", ["branch_id"])
    op.create_index("ix_tp_event_properties_event", "tp_event_properties", ["event_id"])
    op.create_index("ix_tp_event_sources_event", "tp_event_sources", ["event_id"])
    op.create_index("ix_tp_event_destinations_event", "tp_event_destinations", ["event_id"])


def downgrade() -> None:
    op.drop_constraint("fk_tp_plan_current_version", "tp_plans", type_="foreignkey")
    op.drop_constraint("fk_tp_plan_default_branch", "tp_plans", type_="foreignkey")
    for table in (
        "tp_metrics",
        "tp_event_destinations",
        "tp_event_sources",
        "tp_source_destinations",
        "tp_destinations",
        "tp_sources",
        "tp_event_properties",
        "tp_properties",
        "tp_events",
        "tp_categories",
        "tp_versions",
        "tp_branches",
        "tp_plans",
    ):
        op.drop_table(table)
```

- [ ] **Step 3: Apply, then reverse, then re-apply the migration**

```bash
python -m alembic upgrade head
python -m alembic downgrade -1
python -m alembic upgrade head
```
Expected: all three succeed with no error. `\dt tp_*` in psql shows 13 tables after upgrade and none after downgrade.

- [ ] **Step 4: Commit**

```bash
git add app/db/migrations/versions/054_tracking_plan_schema.py
git commit -m "feat(tracking-plan): alembic migration for tp_* schema"
```

---

### Task 3: Service scaffolding — exceptions, common helpers, package

**Files:**
- Create: `app/services/tracking_plan/exceptions.py`
- Create: `app/services/tracking_plan/common.py`
- Create: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_common.py`

- [ ] **Step 1: Write exceptions**

```python
# app/services/tracking_plan/exceptions.py
"""Typed errors raised by the tracking-plan service. Adapters (MCP/HTTP) map
these to tool errors / HTTP status codes."""


class TrackingPlanError(Exception):
    """Base class for all tracking-plan service errors."""


class NotFoundError(TrackingPlanError):
    """A referenced entity does not exist (or is on a different branch)."""


class ValidationError(TrackingPlanError):
    """A write was rejected because it is structurally invalid."""


class ConflictError(TrackingPlanError):
    """A write violates a uniqueness rule (e.g. duplicate name)."""
```

- [ ] **Step 2: Write common helpers**

```python
# app/services/tracking_plan/common.py
"""Shared helpers for the tracking-plan service."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import NotFoundError

# Sentinel meaning "caller did not provide this field" (vs. explicitly None).
_UNSET: Any = object()


def coerce_uuid(value: Any) -> uuid.UUID:
    """Accept a UUID or its string form; raise ValueError on garbage."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def get_or_raise(
    session: AsyncSession, model: type, obj_id: Any, *, branch_id: uuid.UUID | None = None
):
    """Load a row by id or raise NotFoundError. If branch_id is given, also
    require the row's branch_id to match (prevents cross-branch references)."""
    obj = await session.get(model, coerce_uuid(obj_id))
    if obj is None:
        raise NotFoundError(f"{model.__name__} {obj_id} not found")
    if branch_id is not None and getattr(obj, "branch_id", None) != branch_id:
        raise NotFoundError(f"{model.__name__} {obj_id} not on branch {branch_id}")
    return obj


def apply_fields(obj: Any, fields: dict[str, Any], allowed: set[str]) -> None:
    """Set attributes from `fields` whose key is in `allowed` and whose value is
    not the _UNSET sentinel. Lets update_* funcs distinguish 'omit' from 'set None'."""
    for key, value in fields.items():
        if key in allowed and value is not _UNSET:
            setattr(obj, key, value)
```

- [ ] **Step 3: Write the package `__init__` (start minimal; later tasks extend it)**

```python
# app/services/tracking_plan/__init__.py
"""Tracking-plan service: the only module that mutates tp_* tables.

Public API is re-exported here so callers do `from app.services.tracking_plan
import create_event` etc. Later tasks append to this file."""

from .bootstrap import get_main_branch, get_or_create_plan
from .exceptions import ConflictError, NotFoundError, TrackingPlanError, ValidationError

__all__ = [
    "ConflictError",
    "NotFoundError",
    "TrackingPlanError",
    "ValidationError",
    "get_main_branch",
    "get_or_create_plan",
]
```

> The `__init__` imports `bootstrap`, written in Task 4. Write Task 4's `bootstrap.py` before running tests for this task, or temporarily comment the bootstrap import. Recommended: do Step 4 of this task together with Task 4.

- [ ] **Step 4: Write the failing test**

```python
# tests/services/tracking_plan/test_common.py
import uuid

import pytest

from app.services.tracking_plan.common import _UNSET, apply_fields, coerce_uuid
from app.services.tracking_plan.exceptions import NotFoundError


def test_coerce_uuid_accepts_str_and_uuid():
    u = uuid.uuid4()
    assert coerce_uuid(u) == u
    assert coerce_uuid(str(u)) == u


def test_apply_fields_skips_unset_and_unknown():
    class Box:
        a = 1
        b = 2

    box = Box()
    apply_fields(box, {"a": 10, "b": _UNSET, "c": 99}, allowed={"a", "b"})
    assert box.a == 10  # set
    assert box.b == 2  # _UNSET -> untouched
    assert not hasattr(box, "c")  # not in allowed -> untouched


def test_notfound_is_tracking_plan_error():
    from app.services.tracking_plan.exceptions import TrackingPlanError

    assert issubclass(NotFoundError, TrackingPlanError)
```

- [ ] **Step 5: Run and verify it fails, then passes**

Run: `python -m pytest tests/services/tracking_plan/test_common.py -v`
Expected after Task 4 exists: PASS.

- [ ] **Step 6: Format + commit (commit together with Task 4)**

---

### Task 4: Plan/branch bootstrap

**Files:**
- Create: `app/services/tracking_plan/bootstrap.py`
- Test: `tests/services/tracking_plan/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_bootstrap.py
import pytest

from app.models.tracking_plan import TPBranch, TPPlan
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_get_or_create_plan_is_idempotent_and_makes_main_branch(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)

        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="My Plan")
        assert plan.name == "My Plan"
        assert plan.default_branch_id is not None

        branch = await get_main_branch(session, plan)
        assert branch.is_main is True
        assert branch.name == "main"
        assert plan.default_branch_id == branch.id

        # Second call returns the same plan (idempotent), creates no second branch
        plan2 = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        assert plan2.id == plan.id
        from sqlalchemy import func, select

        count = await session.scalar(select(func.count()).select_from(TPBranch).where(TPBranch.plan_id == plan.id))
        assert count == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.tracking_plan.bootstrap`.

- [ ] **Step 3: Implement bootstrap**

```python
# app/services/tracking_plan/bootstrap.py
"""Plan + branch lifecycle. Phase 1 guarantees exactly one `main` branch."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan

from .common import coerce_uuid
from .exceptions import NotFoundError


async def get_or_create_plan(
    session: AsyncSession, *, project_id: Any, user_id: Any, name: str | None = None
) -> TPPlan:
    """Return the project's plan, creating it (and its `main` branch) if absent."""
    pid = coerce_uuid(project_id)
    existing = await session.execute(select(TPPlan).where(TPPlan.project_id == pid))
    plan = existing.scalar_one_or_none()
    if plan is not None:
        return plan

    uid = coerce_uuid(user_id)
    plan = TPPlan(project_id=pid, name=name or "Tracking Plan", created_by=uid)
    session.add(plan)
    await session.flush()  # populate plan.id

    main = TPBranch(plan_id=plan.id, name="main", is_main=True, created_by=uid)
    session.add(main)
    await session.flush()  # populate main.id

    plan.default_branch_id = main.id
    await session.flush()
    return plan


async def get_main_branch(session: AsyncSession, plan: TPPlan) -> TPBranch:
    """Return the plan's `main` branch or raise NotFoundError."""
    result = await session.execute(
        select(TPBranch).where(TPBranch.plan_id == plan.id, TPBranch.is_main.is_(True))
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        raise NotFoundError(f"main branch missing for plan {plan.id}")
    return branch
```

- [ ] **Step 4: Run both Task 3 + Task 4 tests to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_common.py tests/services/tracking_plan/test_bootstrap.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit (Tasks 3 + 4 together)**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/test_common.py tests/services/tracking_plan/test_bootstrap.py
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_common.py tests/services/tracking_plan/test_bootstrap.py
git commit -m "feat(tracking-plan): service scaffolding + plan/branch bootstrap"
```

---

### Task 5: Category CRUD

**Files:**
- Create: `app/services/tracking_plan/taxonomy.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_taxonomy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_taxonomy.py
import pytest

from app.services.tracking_plan import create_category, delete_category, update_category
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, NotFoundError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_category_crud(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        cat = await create_category(session, branch, name="Commerce", color="#0af")
        assert cat.name == "Commerce"

        with pytest.raises(ConflictError):
            await create_category(session, branch, name="Commerce")

        updated = await update_category(session, branch, cat.id, description="Buy flow")
        assert updated.description == "Buy flow"

        await delete_category(session, branch, cat.id)
        with pytest.raises(NotFoundError):
            await update_category(session, branch, cat.id, name="X")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_taxonomy.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_category'`.

- [ ] **Step 3: Implement taxonomy**

```python
# app/services/tracking_plan/taxonomy.py
"""Category (event grouping) CRUD."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPCategory

from .common import _UNSET, apply_fields, get_or_raise
from .exceptions import ConflictError, ValidationError

_CATEGORY_FIELDS = {"name", "description", "color"}


async def _name_taken(session: AsyncSession, branch_id, name: str, *, exclude_id=None) -> bool:
    stmt = select(TPCategory.id).where(TPCategory.branch_id == branch_id, TPCategory.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TPCategory.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_category(
    session: AsyncSession, branch: TPBranch, *, name: str, description: str | None = None, color: str | None = None
) -> TPCategory:
    if not name or not name.strip():
        raise ValidationError("category name is required")
    name = name.strip()
    if await _name_taken(session, branch.id, name):
        raise ConflictError(f"category '{name}' already exists")
    cat = TPCategory(plan_id=branch.plan_id, branch_id=branch.id, name=name, description=description, color=color)
    session.add(cat)
    await session.flush()
    return cat


async def update_category(session: AsyncSession, branch: TPBranch, category_id: Any, **fields: Any) -> TPCategory:
    cat = await get_or_raise(session, TPCategory, category_id, branch_id=branch.id)
    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("category name cannot be empty")
        fields["name"] = new_name.strip()
        if await _name_taken(session, branch.id, fields["name"], exclude_id=cat.id):
            raise ConflictError(f"category '{fields['name']}' already exists")
    apply_fields(cat, fields, _CATEGORY_FIELDS)
    await session.flush()
    return cat


async def delete_category(session: AsyncSession, branch: TPBranch, category_id: Any) -> None:
    cat = await get_or_raise(session, TPCategory, category_id, branch_id=branch.id)
    await session.delete(cat)
    await session.flush()
```

- [ ] **Step 4: Re-export in `__init__.py`**

Add to the imports and `__all__` in `app/services/tracking_plan/__init__.py`:

```python
from .taxonomy import create_category, delete_category, update_category
```
Add `"create_category", "update_category", "delete_category",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_taxonomy.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/test_taxonomy.py
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_taxonomy.py
git commit -m "feat(tracking-plan): category CRUD"
```

---

### Task 6: Property library CRUD + attach/detach

**Files:**
- Create: `app/services/tracking_plan/properties.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_properties.py`

The validation rules enforced here (write-time): `data_type` must be one of the allowed values; an `enum`-style constraint (`constraints.allowed_values`) must be a non-empty list; attaching a property requires both the event and the property to be on the given branch.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_properties.py
import pytest

from app.services.tracking_plan import (
    attach_property,
    create_event,
    create_property,
    detach_property,
    update_property,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_property_create_validation(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        prop = await create_property(session, branch, name="currency", data_type="string")
        assert prop.kind == "event"

        with pytest.raises(ValidationError):
            await create_property(session, branch, name="bad", data_type="nope")

        with pytest.raises(ValidationError):
            await create_property(
                session, branch, name="plan_tier", data_type="string", constraints={"allowed_values": []}
            )

        # Same name allowed under a different kind, blocked under the same kind
        await create_property(session, branch, name="currency", data_type="string", kind="user")
        with pytest.raises(ConflictError):
            await create_property(session, branch, name="currency", data_type="string")


@pytest.mark.anyio
async def test_attach_detach_with_override(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        event = await create_event(session, branch, name="purchase")
        prop = await create_property(session, branch, name="value", data_type="float")

        link = await attach_property(session, event.id, prop.id, required=True, example="9.99")
        assert link.required is True
        assert link.example == "9.99"

        # Re-attaching the same property updates the override rather than duplicating
        link2 = await attach_property(session, event.id, prop.id, required=False)
        assert link2.id == link.id
        assert link2.required is False

        await detach_property(session, event.id, prop.id)
        from sqlalchemy import func, select

        from app.models.tracking_plan import TPEventProperty

        n = await session.scalar(
            select(func.count()).select_from(TPEventProperty).where(TPEventProperty.event_id == event.id)
        )
        assert n == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_properties.py -v`
Expected: FAIL (`create_property` not importable). *(Depends on `create_event` from Task 7 — if running this task first, the event import fails; do Task 7's `create_event` alongside, or temporarily build a TPEvent inline. Recommended order: implement `properties.py` and `events.py` together, commit at the end of Task 7.)*

- [ ] **Step 3: Implement properties**

```python
# app/services/tracking_plan/properties.py
"""Property library CRUD + event<->property attachment."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    PROPERTY_DATA_TYPES,
    PROPERTY_KINDS,
    TPEvent,
    TPEventProperty,
    TPProperty,
)

from .common import _UNSET, apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, NotFoundError, ValidationError

_PROPERTY_FIELDS = {"name", "description", "data_type", "constraints", "is_pii", "parent_property_id"}


def _validate_property_shape(*, data_type: str, constraints: dict | None) -> None:
    if data_type not in PROPERTY_DATA_TYPES:
        raise ValidationError(f"data_type must be one of {PROPERTY_DATA_TYPES}, got {data_type!r}")
    if constraints and "allowed_values" in constraints:
        allowed = constraints["allowed_values"]
        if not isinstance(allowed, list) or len(allowed) == 0:
            raise ValidationError("constraints.allowed_values must be a non-empty list")


async def _prop_name_taken(session, branch_id, kind, name, *, exclude_id=None) -> bool:
    stmt = select(TPProperty.id).where(
        TPProperty.branch_id == branch_id, TPProperty.kind == kind, TPProperty.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(TPProperty.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_property(
    session: AsyncSession,
    branch,
    *,
    name: str,
    data_type: str,
    kind: str = "event",
    description: str | None = None,
    constraints: dict | None = None,
    is_pii: bool = False,
    parent_property_id: Any = None,
) -> TPProperty:
    if not name or not name.strip():
        raise ValidationError("property name is required")
    if kind not in PROPERTY_KINDS:
        raise ValidationError(f"kind must be one of {PROPERTY_KINDS}, got {kind!r}")
    _validate_property_shape(data_type=data_type, constraints=constraints)
    name = name.strip()
    if await _prop_name_taken(session, branch.id, kind, name):
        raise ConflictError(f"property '{name}' ({kind}) already exists")
    parent_id = None
    if parent_property_id is not None:
        parent = await get_or_raise(session, TPProperty, parent_property_id, branch_id=branch.id)
        parent_id = parent.id
    prop = TPProperty(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        kind=kind,
        data_type=data_type,
        description=description,
        constraints=constraints,
        is_pii=is_pii,
        parent_property_id=parent_id,
    )
    session.add(prop)
    await session.flush()
    return prop


async def update_property(session: AsyncSession, branch, property_id: Any, **fields: Any) -> TPProperty:
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)
    new_dt = fields.get("data_type", _UNSET)
    new_constraints = fields.get("constraints", _UNSET)
    _validate_property_shape(
        data_type=prop.data_type if new_dt is _UNSET else new_dt,
        constraints=prop.constraints if new_constraints is _UNSET else new_constraints,
    )
    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("property name cannot be empty")
        fields["name"] = new_name.strip()
        if await _prop_name_taken(session, branch.id, prop.kind, fields["name"], exclude_id=prop.id):
            raise ConflictError(f"property '{fields['name']}' ({prop.kind}) already exists")
    apply_fields(prop, fields, _PROPERTY_FIELDS)
    await session.flush()
    return prop


async def delete_property(session: AsyncSession, branch, property_id: Any) -> None:
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)
    await session.delete(prop)
    await session.flush()


async def attach_property(
    session: AsyncSession,
    event_id: Any,
    property_id: Any,
    *,
    required: bool = False,
    example: str | None = None,
    override_description: str | None = None,
    sort_order: int = 0,
) -> TPEventProperty:
    """Attach a library property to an event. Idempotent: re-attaching updates
    the existing link's overrides instead of inserting a duplicate."""
    event = await session.get(TPEvent, coerce_uuid(event_id))
    if event is None:
        raise NotFoundError(f"event {event_id} not found")
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=event.branch_id)

    existing = await session.execute(
        select(TPEventProperty).where(
            TPEventProperty.event_id == event.id, TPEventProperty.property_id == prop.id
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        link = TPEventProperty(event_id=event.id, property_id=prop.id)
        session.add(link)
    link.required = required
    link.example = example
    link.override_description = override_description
    link.sort_order = sort_order
    await session.flush()
    return link


async def detach_property(session: AsyncSession, event_id: Any, property_id: Any) -> None:
    existing = await session.execute(
        select(TPEventProperty).where(
            TPEventProperty.event_id == coerce_uuid(event_id),
            TPEventProperty.property_id == coerce_uuid(property_id),
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        raise NotFoundError(f"property {property_id} is not attached to event {event_id}")
    await session.delete(link)
    await session.flush()
```

- [ ] **Step 4: Re-export in `__init__.py`**

```python
from .properties import (
    attach_property,
    create_property,
    delete_property,
    detach_property,
    update_property,
)
```
Add those five names to `__all__`.

- [ ] **Step 5 & 6:** Run + commit together with Task 7 (events), since the property test imports `create_event`.

---

### Task 7: Event CRUD + source scoping + destination mapping

**Files:**
- Create: `app/services/tracking_plan/events.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_events.py
import pytest

from app.services.tracking_plan import (
    create_destination,
    create_event,
    create_source,
    delete_event,
    set_event_destination,
    set_event_sources,
    update_event,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, NotFoundError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_event_crud_and_uniqueness(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase", purpose="money")
        assert ev.purpose == "money"
        with pytest.raises(ConflictError):
            await create_event(session, branch, name="purchase")
        with pytest.raises(ValidationError):
            await create_event(session, branch, name="  ")
        ev2 = await update_event(session, branch, ev.id, display_name="Purchase")
        assert ev2.display_name == "Purchase"
        await delete_event(session, branch, ev.id)
        with pytest.raises(NotFoundError):
            await update_event(session, branch, ev.id, name="x")


@pytest.mark.anyio
async def test_set_event_sources_replaces_and_sets_status(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase")
        web = await create_source(session, branch, name="web")
        ios = await create_source(session, branch, name="ios")

        links = await set_event_sources(
            session, branch, ev.id, [{"source_id": web.id, "implementation_status": "implemented"}]
        )
        assert len(links) == 1
        assert links[0].implementation_status == "implemented"

        # Replacing the set drops web, adds ios with default status
        links = await set_event_sources(session, branch, ev.id, [{"source_id": ios.id}])
        assert len(links) == 1
        assert links[0].source_id == ios.id
        assert links[0].implementation_status == "planned"

        with pytest.raises(ValidationError):
            await set_event_sources(
                session, branch, ev.id, [{"source_id": ios.id, "implementation_status": "bogus"}]
            )


@pytest.mark.anyio
async def test_set_event_destination_mapping(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase")
        dest = await create_destination(session, branch, name="GA4 prod", platform="ga4")

        mapping = await set_event_destination(
            session, branch, ev.id, dest.id, dest_event_name="purchase", property_mappings={"value": "value"}
        )
        assert mapping.dest_event_name == "purchase"
        # Upsert: calling again updates the same row
        mapping2 = await set_event_destination(session, branch, ev.id, dest.id, enabled=False)
        assert mapping2.id == mapping.id
        assert mapping2.enabled is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_events.py -v`
Expected: FAIL (imports missing). `create_source`/`create_destination` come from Task 8 (`routing.py`) — implement Tasks 6, 7, 8 as one batch, then run all three test modules and commit once.

- [ ] **Step 3: Implement events**

```python
# app/services/tracking_plan/events.py
"""Event CRUD, source scoping (+ per-source status), and destination mapping rules."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    IMPL_STATUSES,
    TPBranch,
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventSource,
    TPSource,
)

from .common import _UNSET, apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, ValidationError

_EVENT_FIELDS = {
    "name",
    "display_name",
    "description",
    "category_id",
    "tags",
    "trigger_type",
    "trigger_config",
    "purpose",
    "owner_business",
    "owner_technical",
    "consent_required",
}
_EVENT_DEST_FIELDS = {"dest_event_name", "property_mappings", "enabled", "notes"}


async def _event_name_taken(session, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(TPEvent.id).where(TPEvent.branch_id == branch_id, TPEvent.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TPEvent.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_event(session: AsyncSession, branch: TPBranch, *, name: str, **fields: Any) -> TPEvent:
    if not name or not name.strip():
        raise ValidationError("event name is required")
    name = name.strip()
    if await _event_name_taken(session, branch.id, name):
        raise ConflictError(f"event '{name}' already exists")
    if fields.get("category_id"):
        await get_or_raise(session, TPCategory, fields["category_id"], branch_id=branch.id)
    event = TPEvent(plan_id=branch.plan_id, branch_id=branch.id, name=name)
    apply_fields(event, fields, _EVENT_FIELDS - {"name"})
    session.add(event)
    await session.flush()
    return event


async def update_event(session: AsyncSession, branch: TPBranch, event_id: Any, **fields: Any) -> TPEvent:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("event name cannot be empty")
        fields["name"] = new_name.strip()
        if await _event_name_taken(session, branch.id, fields["name"], exclude_id=event.id):
            raise ConflictError(f"event '{fields['name']}' already exists")
    if fields.get("category_id"):
        await get_or_raise(session, TPCategory, fields["category_id"], branch_id=branch.id)
    apply_fields(event, fields, _EVENT_FIELDS)
    await session.flush()
    return event


async def delete_event(session: AsyncSession, branch: TPBranch, event_id: Any) -> None:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    await session.delete(event)
    await session.flush()


async def set_event_sources(
    session: AsyncSession, branch: TPBranch, event_id: Any, scopes: list[dict]
) -> list[TPEventSource]:
    """Replace an event's source-scoping set. Each scope dict: {source_id,
    implementation_status?}. Status defaults to 'planned'."""
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)

    # Validate first (all-or-nothing)
    resolved = []
    for scope in scopes:
        status = scope.get("implementation_status", "planned")
        if status not in IMPL_STATUSES:
            raise ValidationError(f"implementation_status must be one of {IMPL_STATUSES}, got {status!r}")
        source = await get_or_raise(session, TPSource, scope["source_id"], branch_id=branch.id)
        resolved.append((source.id, status))

    # Delete the current set, then insert the new one
    existing = await session.execute(select(TPEventSource).where(TPEventSource.event_id == event.id))
    for link in existing.scalars().all():
        await session.delete(link)
    await session.flush()

    out = []
    for source_id, status in resolved:
        link = TPEventSource(event_id=event.id, source_id=source_id, implementation_status=status)
        session.add(link)
        out.append(link)
    await session.flush()
    return out


async def set_event_destination(
    session: AsyncSession,
    branch: TPBranch,
    event_id: Any,
    destination_id: Any,
    **fields: Any,
) -> TPEventDestination:
    """Upsert the (event x destination) mapping rule."""
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)

    existing = await session.execute(
        select(TPEventDestination).where(
            TPEventDestination.event_id == event.id, TPEventDestination.destination_id == dest.id
        )
    )
    mapping = existing.scalar_one_or_none()
    if mapping is None:
        mapping = TPEventDestination(event_id=event.id, destination_id=dest.id)
        session.add(mapping)
    apply_fields(mapping, fields, _EVENT_DEST_FIELDS)
    await session.flush()
    return mapping


async def remove_event_destination(session: AsyncSession, branch: TPBranch, event_id: Any, destination_id: Any) -> None:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPEventDestination).where(
            TPEventDestination.event_id == event.id,
            TPEventDestination.destination_id == coerce_uuid(destination_id),
        )
    )
    mapping = existing.scalar_one_or_none()
    if mapping is not None:
        await session.delete(mapping)
        await session.flush()
```

- [ ] **Step 4: Re-export in `__init__.py`**

```python
from .events import (
    create_event,
    delete_event,
    remove_event_destination,
    set_event_destination,
    set_event_sources,
    update_event,
)
```
Add those six names to `__all__`.

- [ ] **Step 5 & 6:** Run + commit with Task 8.

---

### Task 8: Source + destination CRUD + routing

**Files:**
- Create: `app/services/tracking_plan/routing.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_routing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_routing.py
import pytest

from app.services.tracking_plan import (
    connect_source_destination,
    create_destination,
    create_source,
    disconnect_source_destination,
    update_destination,
    update_source,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_source_destination_crud_and_routing(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        src = await create_source(session, branch, name="web", platform_type="web")
        assert src.platform_type == "web"
        with pytest.raises(ConflictError):
            await create_source(session, branch, name="web")

        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        with pytest.raises(ValidationError):
            await create_destination(session, branch, name="bad")  # platform required

        route = await connect_source_destination(session, branch, src.id, dest.id)
        assert route.source_id == src.id
        # Idempotent
        route2 = await connect_source_destination(session, branch, src.id, dest.id)
        assert route2.id == route.id

        await disconnect_source_destination(session, branch, src.id, dest.id)
        from sqlalchemy import func, select

        from app.models.tracking_plan import TPSourceDestination

        n = await session.scalar(select(func.count()).select_from(TPSourceDestination))
        assert n == 0

        await update_source(session, branch, src.id, description="primary web")
        await update_destination(session, branch, dest.id, platform_account_id="G-123")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_routing.py -v`
Expected: FAIL (imports missing).

- [ ] **Step 3: Implement routing**

```python
# app/services/tracking_plan/routing.py
"""Source + destination CRUD and source->destination routing."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    TPBranch,
    TPDestination,
    TPSource,
    TPSourceDestination,
)

from .common import apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, ValidationError

_SOURCE_FIELDS = {"name", "platform_type", "description", "connector_ref"}
_DEST_FIELDS = {"name", "platform", "platform_account_id", "config"}


async def _taken(session, model, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(model.id).where(model.branch_id == branch_id, model.name == name)
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_source(session: AsyncSession, branch: TPBranch, *, name: str, **fields: Any) -> TPSource:
    if not name or not name.strip():
        raise ValidationError("source name is required")
    name = name.strip()
    if await _taken(session, TPSource, branch.id, name):
        raise ConflictError(f"source '{name}' already exists")
    src = TPSource(plan_id=branch.plan_id, branch_id=branch.id, name=name)
    apply_fields(src, fields, _SOURCE_FIELDS - {"name"})
    session.add(src)
    await session.flush()
    return src


async def update_source(session: AsyncSession, branch: TPBranch, source_id: Any, **fields: Any) -> TPSource:
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    if "name" in fields:
        if not fields["name"] or not fields["name"].strip():
            raise ValidationError("source name cannot be empty")
        fields["name"] = fields["name"].strip()
        if await _taken(session, TPSource, branch.id, fields["name"], exclude_id=src.id):
            raise ConflictError(f"source '{fields['name']}' already exists")
    apply_fields(src, fields, _SOURCE_FIELDS)
    await session.flush()
    return src


async def delete_source(session: AsyncSession, branch: TPBranch, source_id: Any) -> None:
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    await session.delete(src)
    await session.flush()


async def create_destination(session: AsyncSession, branch: TPBranch, *, name: str, platform: str | None = None, **fields: Any) -> TPDestination:
    if not name or not name.strip():
        raise ValidationError("destination name is required")
    if not platform or not platform.strip():
        raise ValidationError("destination platform is required")
    name = name.strip()
    if await _taken(session, TPDestination, branch.id, name):
        raise ConflictError(f"destination '{name}' already exists")
    dest = TPDestination(plan_id=branch.plan_id, branch_id=branch.id, name=name, platform=platform.strip())
    apply_fields(dest, fields, _DEST_FIELDS - {"name", "platform"})
    session.add(dest)
    await session.flush()
    return dest


async def update_destination(session: AsyncSession, branch: TPBranch, destination_id: Any, **fields: Any) -> TPDestination:
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    if "name" in fields:
        if not fields["name"] or not fields["name"].strip():
            raise ValidationError("destination name cannot be empty")
        fields["name"] = fields["name"].strip()
        if await _taken(session, TPDestination, branch.id, fields["name"], exclude_id=dest.id):
            raise ConflictError(f"destination '{fields['name']}' already exists")
    apply_fields(dest, fields, _DEST_FIELDS)
    await session.flush()
    return dest


async def delete_destination(session: AsyncSession, branch: TPBranch, destination_id: Any) -> None:
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    await session.delete(dest)
    await session.flush()


async def connect_source_destination(session: AsyncSession, branch: TPBranch, source_id: Any, destination_id: Any) -> TPSourceDestination:
    """Route a source to a destination. Idempotent."""
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPSourceDestination).where(
            TPSourceDestination.source_id == src.id, TPSourceDestination.destination_id == dest.id
        )
    )
    route = existing.scalar_one_or_none()
    if route is None:
        route = TPSourceDestination(source_id=src.id, destination_id=dest.id)
        session.add(route)
        await session.flush()
    return route


async def disconnect_source_destination(session: AsyncSession, branch: TPBranch, source_id: Any, destination_id: Any) -> None:
    existing = await session.execute(
        select(TPSourceDestination).where(
            TPSourceDestination.source_id == coerce_uuid(source_id),
            TPSourceDestination.destination_id == coerce_uuid(destination_id),
        )
    )
    route = existing.scalar_one_or_none()
    if route is not None:
        await session.delete(route)
        await session.flush()
```

- [ ] **Step 4: Re-export in `__init__.py`**

```python
from .routing import (
    connect_source_destination,
    create_destination,
    create_source,
    delete_destination,
    delete_source,
    disconnect_source_destination,
    update_destination,
    update_source,
)
```
Add those eight names to `__all__`.

- [ ] **Step 5: Run Tasks 6, 7, 8 tests together**

Run: `python -m pytest tests/services/tracking_plan/test_properties.py tests/services/tracking_plan/test_events.py tests/services/tracking_plan/test_routing.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_properties.py tests/services/tracking_plan/test_events.py tests/services/tracking_plan/test_routing.py app/services/tracking_plan/__init__.py
git commit -m "feat(tracking-plan): property/event/source/destination CRUD + routing + mappings"
```

---

### Task 9: Metric CRUD

**Files:**
- Create: `app/services/tracking_plan/metrics.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_metrics.py
import pytest

from app.services.tracking_plan import create_event, create_metric, delete_metric, update_metric
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_metric_crud(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase")

        m = await create_metric(session, branch, name="Revenue", type="sum", event_id=ev.id)
        assert m.type == "sum"
        with pytest.raises(ConflictError):
            await create_metric(session, branch, name="Revenue", type="count")
        with pytest.raises(ValidationError):
            await create_metric(session, branch, name="Bad", type="nope")

        await update_metric(session, branch, m.id, description="total money")
        await delete_metric(session, branch, m.id)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_metrics.py -v`
Expected: FAIL (`create_metric` not importable).

- [ ] **Step 3: Implement metrics**

```python
# app/services/tracking_plan/metrics.py
"""Event-based metric CRUD."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import METRIC_TYPES, TPBranch, TPEvent, TPMetric, TPProperty

from .common import apply_fields, get_or_raise
from .exceptions import ConflictError, ValidationError

_METRIC_FIELDS = {"name", "description", "type", "event_id", "property_id", "filters"}


async def _metric_name_taken(session, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(TPMetric.id).where(TPMetric.branch_id == branch_id, TPMetric.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TPMetric.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


def _validate_type(metric_type: str) -> None:
    if metric_type not in METRIC_TYPES:
        raise ValidationError(f"metric type must be one of {METRIC_TYPES}, got {metric_type!r}")


async def create_metric(
    session: AsyncSession,
    branch: TPBranch,
    *,
    name: str,
    type: str = "count",
    description: str | None = None,
    event_id: Any = None,
    property_id: Any = None,
    filters: dict | None = None,
) -> TPMetric:
    if not name or not name.strip():
        raise ValidationError("metric name is required")
    _validate_type(type)
    name = name.strip()
    if await _metric_name_taken(session, branch.id, name):
        raise ConflictError(f"metric '{name}' already exists")
    ev_id = None
    if event_id is not None:
        ev_id = (await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)).id
    prop_id = None
    if property_id is not None:
        prop_id = (await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)).id
    metric = TPMetric(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        type=type,
        description=description,
        event_id=ev_id,
        property_id=prop_id,
        filters=filters,
    )
    session.add(metric)
    await session.flush()
    return metric


async def update_metric(session: AsyncSession, branch: TPBranch, metric_id: Any, **fields: Any) -> TPMetric:
    metric = await get_or_raise(session, TPMetric, metric_id, branch_id=branch.id)
    if "type" in fields:
        _validate_type(fields["type"])
    if "name" in fields:
        if not fields["name"] or not fields["name"].strip():
            raise ValidationError("metric name cannot be empty")
        fields["name"] = fields["name"].strip()
        if await _metric_name_taken(session, branch.id, fields["name"], exclude_id=metric.id):
            raise ConflictError(f"metric '{fields['name']}' already exists")
    if fields.get("event_id"):
        await get_or_raise(session, TPEvent, fields["event_id"], branch_id=branch.id)
    if fields.get("property_id"):
        await get_or_raise(session, TPProperty, fields["property_id"], branch_id=branch.id)
    apply_fields(metric, fields, _METRIC_FIELDS)
    await session.flush()
    return metric


async def delete_metric(session: AsyncSession, branch: TPBranch, metric_id: Any) -> None:
    metric = await get_or_raise(session, TPMetric, metric_id, branch_id=branch.id)
    await session.delete(metric)
    await session.flush()
```

- [ ] **Step 4: Re-export in `__init__.py`**

```python
from .metrics import create_metric, delete_metric, update_metric
```
Add `"create_metric", "update_metric", "delete_metric",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/test_metrics.py
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_metrics.py
git commit -m "feat(tracking-plan): metric CRUD"
```

---

### Task 10: The `plan_to_dict` serializer

This is the canonical read shape consumed by MCP, UI, markdown/xlsx export, and version snapshots in later plans. It queries every table explicitly (no async lazy-loading).

**Files:**
- Create: `app/services/tracking_plan/serializer.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_serializer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_serializer.py
import pytest

from app.services.tracking_plan import (
    attach_property,
    connect_source_destination,
    create_category,
    create_destination,
    create_event,
    create_metric,
    create_property,
    create_source,
    plan_to_dict,
    set_event_destination,
    set_event_sources,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_plan_to_dict_full_shape(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="P")
        branch = await get_main_branch(session, plan)

        cat = await create_category(session, branch, name="Commerce")
        ev = await create_event(session, branch, name="purchase", category_id=cat.id, tags=["money"])
        prop = await create_property(session, branch, name="value", data_type="float")
        await attach_property(session, ev.id, prop.id, required=True, example="9.99")
        user_prop = await create_property(session, branch, name="plan_tier", data_type="string", kind="user")
        src = await create_source(session, branch, name="web", platform_type="web")
        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        await connect_source_destination(session, branch, src.id, dest.id)
        await set_event_sources(session, branch, ev.id, [{"source_id": src.id, "implementation_status": "implemented"}])
        await set_event_destination(session, branch, ev.id, dest.id, dest_event_name="purchase")
        await create_metric(session, branch, name="Revenue", type="sum", event_id=ev.id)

        data = await plan_to_dict(session, plan, branch)

        assert data["plan"]["name"] == "P"
        assert data["branch"]["name"] == "main"
        assert [c["name"] for c in data["categories"]] == ["Commerce"]

        assert len(data["events"]) == 1
        event = data["events"][0]
        assert event["name"] == "purchase"
        assert event["category"] == "Commerce"
        assert event["tags"] == ["money"]
        assert event["properties"][0]["name"] == "value"
        assert event["properties"][0]["required"] is True
        assert event["properties"][0]["example"] == "9.99"
        assert event["sources"][0]["name"] == "web"
        assert event["sources"][0]["implementation_status"] == "implemented"
        assert event["destinations"][0]["destination"] == "GA4"
        assert event["destinations"][0]["dest_event_name"] == "purchase"

        assert [p["name"] for p in data["properties"]["event"]] == ["value"]
        assert [p["name"] for p in data["properties"]["user"]] == ["plan_tier"]
        assert data["sources"][0]["destinations"] == ["GA4"]
        assert data["destinations"][0]["name"] == "GA4"
        assert data["metrics"][0]["name"] == "Revenue"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_serializer.py -v`
Expected: FAIL (`plan_to_dict` not importable).

- [ ] **Step 3: Implement the serializer**

```python
# app/services/tracking_plan/serializer.py
"""plan_to_dict — the canonical structured read shape for a plan/branch.

Queries every table explicitly (no ORM lazy-loading under async) and assembles
a stable, JSON-serializable dict. This is the single source consumed by MCP
reads, the UI, markdown/xlsx export, and tp_versions snapshots."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    TPBranch,
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventProperty,
    TPEventSource,
    TPMetric,
    TPPlan,
    TPProperty,
    TPSource,
    TPSourceDestination,
)


async def plan_to_dict(session: AsyncSession, plan: TPPlan, branch: TPBranch) -> dict:
    bid = branch.id

    async def rows(model):
        result = await session.execute(select(model).where(model.branch_id == bid))
        return list(result.scalars().all())

    categories = await rows(TPCategory)
    events = await rows(TPEvent)
    properties = await rows(TPProperty)
    sources = await rows(TPSource)
    destinations = await rows(TPDestination)
    metrics = await rows(TPMetric)

    cat_by_id = {c.id: c for c in categories}
    prop_by_id = {p.id: p for p in properties}
    src_by_id = {s.id: s for s in sources}
    dest_by_id = {d.id: d for d in destinations}
    event_ids = [e.id for e in events]
    source_ids = [s.id for s in sources]

    # Child rows scoped via parents (filter by the branch's event/source ids)
    def _by_event(items):
        out: dict = {}
        for it in items:
            out.setdefault(it.event_id, []).append(it)
        return out

    ep_rows = (
        (await session.execute(select(TPEventProperty).where(TPEventProperty.event_id.in_(event_ids)))).scalars().all()
        if event_ids
        else []
    )
    es_rows = (
        (await session.execute(select(TPEventSource).where(TPEventSource.event_id.in_(event_ids)))).scalars().all()
        if event_ids
        else []
    )
    ed_rows = (
        (await session.execute(select(TPEventDestination).where(TPEventDestination.event_id.in_(event_ids)))).scalars().all()
        if event_ids
        else []
    )
    sd_rows = (
        (await session.execute(select(TPSourceDestination).where(TPSourceDestination.source_id.in_(source_ids)))).scalars().all()
        if source_ids
        else []
    )

    ep_by_event = _by_event(ep_rows)
    es_by_event = _by_event(es_rows)
    ed_by_event = _by_event(ed_rows)
    routes_by_source: dict = {}
    for r in sd_rows:
        routes_by_source.setdefault(r.source_id, []).append(r.destination_id)

    def _property_dict(p: TPProperty) -> dict:
        return {
            "id": str(p.id),
            "name": p.name,
            "kind": p.kind,
            "data_type": p.data_type,
            "description": p.description,
            "constraints": p.constraints,
            "parent_property_id": str(p.parent_property_id) if p.parent_property_id else None,
            "is_pii": p.is_pii,
        }

    def _event_dict(e: TPEvent) -> dict:
        attached = sorted(ep_by_event.get(e.id, []), key=lambda link: (link.sort_order, str(link.property_id)))
        return {
            "id": str(e.id),
            "name": e.name,
            "display_name": e.display_name,
            "description": e.description,
            "category": cat_by_id[e.category_id].name if e.category_id in cat_by_id else None,
            "tags": e.tags or [],
            "trigger_type": e.trigger_type,
            "trigger_config": e.trigger_config,
            "purpose": e.purpose,
            "owner_business": e.owner_business,
            "owner_technical": e.owner_technical,
            "consent_required": e.consent_required or [],
            "properties": [
                {
                    "name": prop_by_id[link.property_id].name,
                    "data_type": prop_by_id[link.property_id].data_type,
                    "required": link.required,
                    "example": link.example,
                    "override_description": link.override_description,
                }
                for link in attached
                if link.property_id in prop_by_id
            ],
            "sources": [
                {
                    "name": src_by_id[link.source_id].name,
                    "implementation_status": link.implementation_status,
                }
                for link in es_by_event.get(e.id, [])
                if link.source_id in src_by_id
            ],
            "destinations": [
                {
                    "destination": dest_by_id[link.destination_id].name,
                    "dest_event_name": link.dest_event_name,
                    "property_mappings": link.property_mappings,
                    "enabled": link.enabled,
                    "notes": link.notes,
                }
                for link in ed_by_event.get(e.id, [])
                if link.destination_id in dest_by_id
            ],
        }

    return {
        "plan": {
            "id": str(plan.id),
            "project_id": str(plan.project_id),
            "name": plan.name,
            "description": plan.description,
            "current_version_id": str(plan.current_version_id) if plan.current_version_id else None,
        },
        "branch": {"id": str(branch.id), "name": branch.name, "is_main": branch.is_main},
        "categories": [
            {"id": str(c.id), "name": c.name, "description": c.description, "color": c.color} for c in categories
        ],
        "events": [_event_dict(e) for e in sorted(events, key=lambda x: x.name)],
        "properties": {
            "event": [_property_dict(p) for p in properties if p.kind == "event"],
            "user": [_property_dict(p) for p in properties if p.kind == "user"],
            "group": [_property_dict(p) for p in properties if p.kind == "group"],
            "system": [_property_dict(p) for p in properties if p.kind == "system"],
        },
        "sources": [
            {
                "id": str(s.id),
                "name": s.name,
                "platform_type": s.platform_type,
                "description": s.description,
                "connector_ref": s.connector_ref,
                "destinations": sorted(
                    dest_by_id[d].name for d in routes_by_source.get(s.id, []) if d in dest_by_id
                ),
            }
            for s in sorted(sources, key=lambda x: x.name)
        ],
        "destinations": [
            {
                "id": str(d.id),
                "name": d.name,
                "platform": d.platform,
                "platform_account_id": d.platform_account_id,
                "config": d.config,
            }
            for d in sorted(destinations, key=lambda x: x.name)
        ],
        "metrics": [
            {
                "id": str(m.id),
                "name": m.name,
                "description": m.description,
                "type": m.type,
                "event": next((e.name for e in events if e.id == m.event_id), None),
                "property": prop_by_id[m.property_id].name if m.property_id in prop_by_id else None,
                "filters": m.filters,
            }
            for m in sorted(metrics, key=lambda x: x.name)
        ],
    }
```

- [ ] **Step 4: Re-export in `__init__.py`**

```python
from .serializer import plan_to_dict
```
Add `"plan_to_dict",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_serializer.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/test_serializer.py
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_serializer.py
git commit -m "feat(tracking-plan): plan_to_dict canonical serializer"
```

---

### Task 11: `validate_plan` completeness/consistency report

**Files:**
- Create: `app/services/tracking_plan/validation.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_validation.py`

Findings produced (each `{severity, code, message, entity}`):
- `event_no_source` (warning) — an event scoped to zero sources.
- `event_no_destination` (warning) — an event with no destination mapping.
- `event_no_properties` (info) — an event with no attached properties.
- `unused_property` (info) — an `event`-kind property attached to no event.
- `required_property_no_example` (info) — a required attachment with no example.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_validation.py
import pytest

from app.services.tracking_plan import (
    create_event,
    create_property,
    create_source,
    set_event_sources,
    validate_plan,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_validate_plan_flags_gaps(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        ev = await create_event(session, branch, name="purchase")  # no source, no dest, no props
        await create_property(session, branch, name="orphan", data_type="string")  # unused

        report = await validate_plan(session, plan, branch)
        codes = {f["code"] for f in report["findings"]}
        assert "event_no_source" in codes
        assert "event_no_destination" in codes
        assert "event_no_properties" in codes
        assert "unused_property" in codes
        assert report["counts"]["events"] == 1

        # Once the event has a source, that finding clears
        src = await create_source(session, branch, name="web")
        await set_event_sources(session, branch, ev.id, [{"source_id": src.id}])
        report2 = await validate_plan(session, plan, branch)
        assert "event_no_source" not in {f["code"] for f in report2["findings"]}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_validation.py -v`
Expected: FAIL (`validate_plan` not importable).

- [ ] **Step 3: Implement validation (built on `plan_to_dict`)**

```python
# app/services/tracking_plan/validation.py
"""validate_plan — a completeness/consistency report over a branch.

Built on plan_to_dict so it sees exactly what consumers see."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan

from .serializer import plan_to_dict


def _finding(severity: str, code: str, message: str, entity: str | None = None) -> dict:
    return {"severity": severity, "code": code, "message": message, "entity": entity}


async def validate_plan(session: AsyncSession, plan: TPPlan, branch: TPBranch) -> dict:
    data = await plan_to_dict(session, plan, branch)
    findings: list[dict] = []

    used_event_props: set[str] = set()
    for event in data["events"]:
        name = event["name"]
        if not event["sources"]:
            findings.append(_finding("warning", "event_no_source", f"Event '{name}' is not scoped to any source", name))
        if not event["destinations"]:
            findings.append(
                _finding("warning", "event_no_destination", f"Event '{name}' is mapped to no destination", name)
            )
        if not event["properties"]:
            findings.append(_finding("info", "event_no_properties", f"Event '{name}' has no properties", name))
        for prop in event["properties"]:
            used_event_props.add(prop["name"])
            if prop["required"] and not prop["example"]:
                findings.append(
                    _finding(
                        "info",
                        "required_property_no_example",
                        f"Required property '{prop['name']}' on '{name}' has no example",
                        name,
                    )
                )

    for prop in data["properties"]["event"]:
        if prop["name"] not in used_event_props:
            findings.append(
                _finding("info", "unused_property", f"Event property '{prop['name']}' is attached to no event", prop["name"])
            )

    return {
        "findings": findings,
        "counts": {
            "events": len(data["events"]),
            "event_properties": len(data["properties"]["event"]),
            "user_properties": len(data["properties"]["user"]),
            "sources": len(data["sources"]),
            "destinations": len(data["destinations"]),
            "metrics": len(data["metrics"]),
        },
        "is_publishable": not any(f["severity"] == "warning" for f in findings),
    }
```

- [ ] **Step 4: Re-export in `__init__.py`**

```python
from .validation import validate_plan
```
Add `"validate_plan",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_validation.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/test_validation.py
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_validation.py
git commit -m "feat(tracking-plan): validate_plan completeness report"
```

---

### Task 12: Full-suite green + typecheck

**Files:** none (verification only).

- [ ] **Step 1: Run the whole tracking-plan test package**

Run: `python -m pytest tests/services/tracking_plan/ -v`
Expected: all tests PASS.

- [ ] **Step 2: Lint + format check the new code**

Run:
```bash
ruff check app/models/tracking_plan.py app/services/tracking_plan/ tests/services/tracking_plan/
ruff format --check app/models/tracking_plan.py app/services/tracking_plan/ tests/services/tracking_plan/
```
Expected: no errors. If `format --check` fails, run `ruff format` on the listed paths and re-commit.

- [ ] **Step 3: Typecheck (if these modules are in the pinned mypy set)**

Run: `python -m mypy app/services/tracking_plan/` (or `tox -e typecheck`).
Expected: no errors. Fix any `Mapped`/Optional typing issues surfaced. If the new modules are not in the pinned set, this is informational.

- [ ] **Step 4: Final commit if anything changed**

```bash
git add -A
git commit -m "chore(tracking-plan): plan 1A green — data foundation complete"
```

---

## Self-Review (performed against the spec)

**1. Spec coverage (Phase-1 §4 schema + service):**
- 13 tables (spec §4) → Tasks 1–2. ✅
- Property library + per-attachment overrides (spec §4.3) → Task 6. ✅
- Sources/destinations/routing/event-scoping/mapping (spec §4.4) → Tasks 7–8. ✅
- Categories (spec §4.2), metrics (spec §4.5) → Tasks 5, 9. ✅
- Per-source implementation status (spec decision #5) → `TPEventSource.implementation_status`, Task 7. ✅
- Rich property constraints (enum/regex/min-max + object/array nesting) (decision #5) → `TPProperty.constraints` JSONB + `parent_property_id`; enum non-empty rule enforced in Task 6. ✅
- Branch-ready schema + `main` branch (decision #4/#7) → `branch_id` everywhere + Task 4. ✅
- Canonical `plan_to_dict` serializer (spec §5) → Task 10. ✅
- `validate` (spec §6 reads) → Task 11. ✅
- *Deferred to later plans (correctly out of 1A scope):* publish/versions (1D/§9), MCP tools (1B/§6), HTTP+UI (1C/§8), markdown/xlsx export + downstream repoint + cutover (1D/§10–11), `scan_source` (1B/§6).

**2. Placeholder scan:** No "TBD"/"add validation"/"similar to". The one implementer note (matching real `Project`/`User` kwargs in `_make_project_and_user`) is a concrete instruction, not a code placeholder. ✅

**3. Type/name consistency:** Service function names used in tests match exports added to `__init__.py` (`create_event`, `create_property`, `attach_property`, `set_event_sources`, `set_event_destination`, `create_source`, `create_destination`, `connect_source_destination`, `create_metric`, `plan_to_dict`, `validate_plan`). Model class names consistent across models/migration/service. `apply_fields`/`_UNSET`/`get_or_raise`/`coerce_uuid` defined in Task 3 and used consistently thereafter. ✅

**Cross-task ordering note (intentional):** Tasks 6–8 are interdependent (tests reference each other's factories), so they are implemented as a batch and committed at the end of Task 8. This is called out in each task's Step 2/5.
