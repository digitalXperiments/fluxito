# app/models/tracking_plan.py
"""Tracking Plan (revamped SDR) relational models — the structured source of truth.

A tracking plan is the industry-standard structured definition of a project's analytics:
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
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Allowed enum-ish values (enforced by CHECK constraints + service validation)
BRANCH_STATUSES = ("active", "merged", "abandoned")
REVIEW_STATUSES = ("draft", "ready_for_review", "changes_requested", "approved")
PROPERTY_KINDS = ("event", "user", "group", "system")
PROPERTY_DATA_TYPES = ("string", "integer", "float", "boolean", "object")
IMPL_STATUSES = ("planned", "implemented", "verified", "deprecated")
# NOTE: METRIC_TYPES removed in migration 064 — metrics no longer carry measurement columns.
COMMENT_ENTITY_TYPES = ("event", "property", "source", "destination", "metric", "category", "plan", "branch")
VALIDATION_SEVERITIES = ("error", "warning", "info")
RULE_TYPES = (
    "event_name_casing",
    "event_name_regex",
    "event_requires_description",
    "event_requires_owner",
    "required_property",
    "property_type_consistency",
    "pii_must_be_flagged",
)


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
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="SET NULL"), nullable=True
    )
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("plan_id", "name", name="uq_tp_branch_name"),
        CheckConstraint("status IN ('active', 'merged', 'abandoned')", name="ck_tp_branch_status"),
        CheckConstraint(
            "review_status IN ('draft', 'ready_for_review', 'changes_requested', 'approved')",
            name="ck_tp_branch_review_status",
        ),
    )


class TPVersion(Base):
    """Immutable published snapshot of a branch (full plan serialized to JSONB)."""

    __tablename__ = "tp_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
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
    is_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # True when the value is an array of `data_type` (list property).
    # "List of objects" = data_type='object', is_list=True.
    is_list: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("branch_id", "kind", "name", name="uq_tp_property_name"),
        CheckConstraint("kind IN ('event', 'user', 'group', 'system')", name="ck_tp_property_kind"),
        CheckConstraint(
            "data_type IN ('string', 'integer', 'float', 'boolean', 'object')",
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


class TPPropertyMember(Base):
    """Shared-reference link between an object property and its member properties (shared-pool nesting).

    ``parent_property_id`` must be an ``object`` (or ``object + is_list=True``) property.
    ``member_property_id`` is any library property referenced as a key of that object.
    The same global property can be a member of multiple object properties across the plan.
    """

    __tablename__ = "tp_property_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=False
    )
    member_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=False
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("parent_property_id", "member_property_id", name="uq_tp_property_member"),
    )


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
    """A named success metric linked to an event — branch-scoped.

    Measurement columns (type, property_id, filters, dashboard_card_id) were
    dropped in migration 064. Metrics are now lightweight intent markers:
    name + description + event association. Measurement lives in the dashboard layer.
    """

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
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_events.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_tp_metric_name"),)


class TPComment(Base):
    """A comment thread item on any tracking-plan entity — branch-scoped.

    Comments live on the branch they were posted on and are not automatically
    migrated when a branch is merged (that is left as future work). The UI is
    expected to thread replies client-side via parent_id and to resolve threads
    using the ``resolved`` flag.
    """

    __tablename__ = "tp_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    # The type of entity this comment is attached to (CHECK-constrained).
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    # The UUID of the commented entity (event id, property id, …).
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Non-null → this row is a reply to an existing comment on the same branch.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tp_comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # List of user UUIDs @-mentioned in the body.
    mentions: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('event','property','source','destination','metric','category','plan','branch')",
            name="ck_tp_comment_entity_type",
        ),
    )


class TPPropertyBundle(Base):
    """A named, reusable group of properties — branch-scoped.

    Bundles are template-copy: ``attach_bundle_to_event`` copies each bundle
    property into ``tp_event_properties`` at attach time. Editing the bundle
    afterwards does NOT retroactively update events it was already attached to
    (live-link is future work).
    """

    __tablename__ = "tp_property_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_tp_property_bundle_name"),)


class TPBundleProperty(Base):
    """M2M link: a property belongs to a bundle, with a per-link required flag."""

    __tablename__ = "tp_bundle_properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_property_bundles.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_properties.id", ondelete="CASCADE"), nullable=False
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (UniqueConstraint("bundle_id", "property_id", name="uq_tp_bundle_property"),)


class TPActivity(Base):
    """Append-only change log for tracking-plan mutations — branch-scoped.

    Written centrally by the action dispatcher (``run_action``) on every
    successful write, so the per-entity Activity feed and the branch-review
    timeline share one source. Reads never write here. Not surfaced in
    ``plan_to_dict`` / version snapshots.
    """

    __tablename__ = "tp_activity"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_branches.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_tp_activity_entity", "plan_id", "branch_id", "entity_type", "entity_id"),
        Index("ix_tp_activity_feed", "plan_id", "branch_id", "created_at"),
    )


class TPValidationRule(Base):
    """A configurable validation rule for a plan. Evaluated by validate_plan().

    Rules are plan-scoped (not branch-scoped): the same rule set applies to
    whichever branch is being validated. ``scope_category_id`` optionally
    restricts evaluation to events belonging to a specific category.
    """

    __tablename__ = "tp_validation_rule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    rule_type: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    scope_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_categories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name="ck_tp_validation_rule_severity",
        ),
        UniqueConstraint(
            "plan_id",
            "rule_type",
            "scope_category_id",
            name="uq_tp_validation_rule",
        ),
        Index("ix_tp_validation_rule_plan", "plan_id", "enabled"),
    )


# Drift = live-vs-plan reconciliation. These three tables hold the OBSERVED reality
# (from GA4 / BigQuery), computed by the drift service and read back by the serializer.
# They are keyed by event NAME, not tp_events.id, because live analytics data speaks
# event names — a name may be "unplanned" (seen live, no matching tp_events row) or a
# plan event may be "broken" (defined, never firing). Rows are plan-scoped and rebuilt
# on each drift run; they are cache, not source of truth.
DRIFT_STATUSES = ("verified", "in_plan", "drifted", "broken", "unplanned")


class TPEventDrift(Base):
    """One reconciliation row per (plan, event name) — the observed state of an event."""

    __tablename__ = "tp_event_drift"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="in_plan")
    # Live event volume over the drift window (last 7 days). NULL when GA4 is unavailable.
    volume_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Aggregate parameter fill coverage (0–100). NULL when no BigQuery observations exist.
    param_coverage_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Human-readable drift reasons, e.g. {"reasons": ["gained unplanned param payment_provider"]}.
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("plan_id", "event_name", name="uq_tp_event_drift"),
        CheckConstraint(
            "status IN ('verified', 'in_plan', 'drifted', 'broken', 'unplanned')",
            name="ck_tp_event_drift_status",
        ),
        Index("ix_tp_event_drift_plan", "plan_id"),
    )


class TPParamObservation(Base):
    """Observed presence of one parameter on one live event (BigQuery-sourced)."""

    __tablename__ = "tp_param_observation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tp_plans.id", ondelete="CASCADE"), nullable=False
    )
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    param_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Fraction of live events (0–100) that carried this parameter.
    present_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    sample_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_type_observed: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the parameter fires live but is absent from the plan (the UNPLANNED row).
    is_unplanned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("plan_id", "event_name", "param_key", name="uq_tp_param_observation"),
        Index("ix_tp_param_observation_event", "plan_id", "event_name"),
    )


class TPDriftConfig(Base):
    """Per-project drift wiring: which GA4 property + BigQuery export dataset to observe.

    Resolves the credential gap between GA4 (OAuth, project-scoped) and BigQuery
    (service-account, user-scoped) — nothing else links a GA4 property to its export
    dataset. Auto-populated best-effort on first run; users can override explicitly.
    """

    __tablename__ = "tp_drift_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    ga4_property_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    bq_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bq_connections.id", ondelete="SET NULL"), nullable=True
    )
    bq_dataset: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("project_id", name="uq_tp_drift_config_project"),)
