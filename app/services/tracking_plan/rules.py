# app/services/tracking_plan/rules.py
"""Validation-rule CRUD and helpers.

Rules are plan-scoped (no branch_id).  The validation engine (validation.py)
calls get_or_seed_rules() to lazily provision defaults on first access, then
passes the list to _evaluate_rules().

Casing helpers (to_snake / to_camel / to_title / matches_casing) are also
defined here so they can be used by the engine and any future Phase A/C work
without creating a circular import.
"""

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import VALIDATION_SEVERITIES, TPPlan, TPValidationRule

from .common import coerce_uuid
from .exceptions import NotFoundError, ValidationError

# ---------------------------------------------------------------------------
# Default rule catalogue
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[dict] = [
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
    {
        # Structured naming rule: event names must be composed of an ordered
        # list of named components, joined by an allowed separator, and each
        # component must match the specified casing.
        #
        # Example: components=["object", "action"], separator="_",
        # casing="snake_case" would accept "page_viewed" but reject
        # "viewed_page" or "PageViewed".
        #
        # Config keys:
        #   components  – ordered list of component label strings (e.g.
        #                 ["object", "action"]).  Min 1 element.
        #   separators  – list of allowed separator strings (default ["_"]).
        #   casing      – casing each component token must match; one of
        #                 snake_case | camelCase | TitleCase | lower | upper |
        #                 any (default "lower").
        #   min_parts   – minimum number of separator-split tokens required
        #                 (defaults to len(components)).
        #   max_parts   – maximum number of tokens (default None = no limit).
        "rule_type": "event_name_components",
        "config": {
            "components": ["object", "action"],
            "separators": ["_"],
            "casing": "lower",
            "min_parts": 2,
            "max_parts": None,
        },
        "severity": "warning",
        "enabled": False,
    },
]

# ---------------------------------------------------------------------------
# Casing helpers
# ---------------------------------------------------------------------------


def to_snake(name: str) -> str:
    """Convert a name to snake_case."""
    # Insert underscores before uppercase letters that follow lowercase letters
    # or digits, then lowercase the whole thing.
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def to_camel(name: str) -> str:
    """Convert a name to camelCase."""
    parts = re.split(r"[_\s]+", name)
    if not parts:
        return name
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def to_title(name: str) -> str:
    """Convert a name to TitleCase (PascalCase)."""
    parts = re.split(r"[_\s]+", name)
    return "".join(p.capitalize() for p in parts)


def matches_casing(name: str, casing: str) -> bool:
    """Return True if *name* already matches the required casing."""
    if casing == "snake_case":
        return name == to_snake(name)
    if casing == "camelCase":
        return name == to_camel(name)
    if casing in ("Title", "TitleCase"):
        return name == to_title(name)
    # Unknown casing spec — treat as pass.
    return True


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def list_rules(session: AsyncSession, plan: TPPlan) -> list[TPValidationRule]:
    """Return all rules for *plan* ordered by rule_type. Does NOT seed."""
    result = await session.execute(
        select(TPValidationRule)
        .where(TPValidationRule.plan_id == plan.id)
        .order_by(TPValidationRule.rule_type)
    )
    return list(result.scalars().all())


async def get_or_seed_rules(session: AsyncSession, plan: TPPlan) -> list[TPValidationRule]:
    """Return the plan's rules, lazily seeding DEFAULT_RULES on first access.

    Idempotent: only inserts rule_types not already present with scope NULL.
    """
    existing = await list_rules(session, plan)
    existing_types = {r.rule_type for r in existing if r.scope_category_id is None}

    new_rules: list[TPValidationRule] = []
    for spec in DEFAULT_RULES:
        if spec["rule_type"] not in existing_types:
            rule = TPValidationRule(
                id=uuid.uuid4(),
                plan_id=plan.id,
                rule_type=spec["rule_type"],
                config=spec["config"],
                severity=spec["severity"],
                enabled=spec["enabled"],
                scope_category_id=None,
            )
            session.add(rule)
            new_rules.append(rule)

    if new_rules:
        await session.flush()

    return existing + new_rules


async def update_rule(
    session: AsyncSession,
    plan: TPPlan,
    rule_id: Any,
    *,
    config: dict | None = None,
    config_provided: bool = False,
    severity: str | None = None,
    scope_category_id: Any | None = None,
    scope_category_id_provided: bool = False,
) -> TPValidationRule:
    """Patch config, severity, and/or scope_category_id on an existing rule.

    Pass ``config_provided=True`` together with ``config`` to explicitly set
    config (including to None).  When ``config_provided`` is False the config
    field is left unchanged.

    Pass ``scope_category_id_provided=True`` together with
    ``scope_category_id`` to set (or clear) the category scope.  When
    ``scope_category_id_provided`` is False the field is left unchanged.

    ``severity`` is validated against ``VALIDATION_SEVERITIES``; pass one of
    ``'error'``, ``'warning'``, or ``'info'``.
    """
    rule = await _get_rule_for_plan(session, plan, rule_id)

    if severity is not None:
        if severity not in VALIDATION_SEVERITIES:
            raise ValidationError(f"severity must be one of {VALIDATION_SEVERITIES}, got '{severity}'")
        rule.severity = severity

    if config_provided:
        rule.config = config

    if scope_category_id_provided:
        if scope_category_id is not None:
            rule.scope_category_id = coerce_uuid(scope_category_id)
        else:
            rule.scope_category_id = None

    await session.flush()
    return rule


async def set_rule_enabled(
    session: AsyncSession,
    plan: TPPlan,
    rule_id: Any,
    *,
    enabled: bool,
) -> TPValidationRule:
    """Enable or disable a rule."""
    rule = await _get_rule_for_plan(session, plan, rule_id)
    rule.enabled = enabled
    await session.flush()
    return rule


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def rule_to_dict(r: TPValidationRule) -> dict:
    return {
        "id": str(r.id),
        "rule_type": r.rule_type,
        "config": r.config or {},
        "severity": r.severity,
        "enabled": r.enabled,
        "scope_category_id": str(r.scope_category_id) if r.scope_category_id else None,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _get_rule_for_plan(session: AsyncSession, plan: TPPlan, rule_id: Any) -> TPValidationRule:
    """Load a TPValidationRule by id and verify it belongs to *plan*."""
    rid = coerce_uuid(rule_id)
    rule = await session.get(TPValidationRule, rid)
    if rule is None or rule.plan_id != plan.id:
        raise NotFoundError(f"TPValidationRule {rule_id} not found on plan {plan.id}")
    return rule
