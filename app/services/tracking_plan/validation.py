# app/services/tracking_plan/validation.py
"""validate_plan — a completeness/consistency + rules report over a branch.

Built on plan_to_dict so it sees exactly what consumers see.

Finding shape
-------------
Every finding (structural or rule-based) carries:
    rule_id        str | None   — None for structural findings
    severity       str          — "error" | "warning" | "info"
    code           str | None   — code for structural findings, None for rule findings
    entity_type    str | None   — "event" | "property"
    entity_id      str | None   — serializer id of the offending entity
    message        str
    suggested_fix  str | None

is_publishable is True when no finding has severity == "error".
"""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan, TPValidationRule

from .rules import get_or_seed_rules, matches_casing, rule_to_dict, to_camel, to_snake, to_title
from .serializer import plan_to_dict

# ---------------------------------------------------------------------------
# Finding constructors
# ---------------------------------------------------------------------------


def _finding(
    severity: str,
    code: str,
    message: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    suggested_fix: str | None = None,
) -> dict:
    return {
        "rule_id": None,
        "severity": severity,
        "code": code,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "message": message,
        "suggested_fix": suggested_fix,
    }


def _rule_finding(
    rule_id: str,
    severity: str,
    entity_type: str,
    entity_id: str | None,
    message: str,
    suggested_fix: str | None = None,
) -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "code": None,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "message": message,
        "suggested_fix": suggested_fix,
    }


# ---------------------------------------------------------------------------
# Structural checks (formerly the body of validate_plan)
# ---------------------------------------------------------------------------


def _structural_findings(data: dict) -> list[dict]:
    """Run the 5 structural completeness checks that are always evaluated."""
    findings: list[dict] = []

    used_event_props: set[str] = set()
    for event in data["events"]:
        name = event["name"]
        ev_id = event["id"]
        if not event["sources"]:
            findings.append(
                _finding(
                    "warning",
                    "event_no_source",
                    f"Event '{name}' is not scoped to any source",
                    entity_type="event",
                    entity_id=ev_id,
                )
            )
        if not event["destinations"]:
            findings.append(
                _finding(
                    "warning",
                    "event_no_destination",
                    f"Event '{name}' is mapped to no destination",
                    entity_type="event",
                    entity_id=ev_id,
                )
            )
        if not event["properties"]:
            findings.append(
                _finding(
                    "info",
                    "event_no_properties",
                    f"Event '{name}' has no properties",
                    entity_type="event",
                    entity_id=ev_id,
                )
            )
        for prop in event["properties"]:
            used_event_props.add(prop["name"])
            if prop["required"] and not prop["example"]:
                findings.append(
                    _finding(
                        "info",
                        "required_property_no_example",
                        f"Required property '{prop['name']}' on '{name}' has no example",
                        entity_type="event",
                        entity_id=ev_id,
                    )
                )

    for prop in data["properties"]["event"]:
        if prop["name"] not in used_event_props:
            findings.append(
                _finding(
                    "info",
                    "unused_property",
                    f"Event property '{prop['name']}' is attached to no event",
                    entity_type="property",
                    entity_id=prop["id"],
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Rule evaluators
# ---------------------------------------------------------------------------


def _scope_events(data: dict, rule: TPValidationRule) -> list[dict]:
    """Return the subset of events the rule applies to (respects scope_category_id)."""
    if rule.scope_category_id is None:
        return data["events"]
    # Resolve category name from serialized categories list.
    cat_name: str | None = next(
        (c["name"] for c in data["categories"] if c["id"] == str(rule.scope_category_id)),
        None,
    )
    if cat_name is None:
        return []
    return [ev for ev in data["events"] if ev.get("category") == cat_name]


def _rule_event_name_casing(data: dict, rule: TPValidationRule) -> list[dict]:
    config = rule.config or {}
    casing = config.get("casing", "snake_case")
    rid = str(rule.id)
    findings = []
    for ev in _scope_events(data, rule):
        if not matches_casing(ev["name"], casing):
            if casing == "snake_case":
                fix = to_snake(ev["name"])
            elif casing == "camelCase":
                fix = to_camel(ev["name"])
            elif casing in ("Title", "TitleCase"):
                fix = to_title(ev["name"])
            else:
                fix = None
            findings.append(
                _rule_finding(
                    rid,
                    rule.severity,
                    "event",
                    ev["id"],
                    f"Event '{ev['name']}' does not match required casing '{casing}'",
                    suggested_fix=fix,
                )
            )
    return findings


def _rule_event_name_regex(data: dict, rule: TPValidationRule) -> list[dict]:
    config = rule.config or {}
    pattern = config.get("pattern", "")
    if not pattern:
        return []
    rid = str(rule.id)
    findings = []
    try:
        compiled = re.compile(pattern)
    except re.error:
        return []
    for ev in _scope_events(data, rule):
        if not compiled.fullmatch(ev["name"]):
            findings.append(
                _rule_finding(
                    rid,
                    rule.severity,
                    "event",
                    ev["id"],
                    f"Event '{ev['name']}' does not match required pattern '{pattern}'",
                )
            )
    return findings


def _rule_event_requires_description(data: dict, rule: TPValidationRule) -> list[dict]:
    rid = str(rule.id)
    findings = []
    for ev in _scope_events(data, rule):
        if not ev.get("description"):
            findings.append(
                _rule_finding(
                    rid,
                    rule.severity,
                    "event",
                    ev["id"],
                    f"Event '{ev['name']}' has no description",
                    suggested_fix="Add a description to this event",
                )
            )
    return findings


def _rule_event_requires_owner(data: dict, rule: TPValidationRule) -> list[dict]:
    config = rule.config or {}
    need_business = config.get("business", True)
    need_technical = config.get("technical", False)
    rid = str(rule.id)
    findings = []
    for ev in _scope_events(data, rule):
        missing = []
        if need_business and not ev.get("owner_business"):
            missing.append("business owner")
        if need_technical and not ev.get("owner_technical"):
            missing.append("technical owner")
        if missing:
            findings.append(
                _rule_finding(
                    rid,
                    rule.severity,
                    "event",
                    ev["id"],
                    f"Event '{ev['name']}' is missing: {', '.join(missing)}",
                    suggested_fix="Set the required owner field(s) on this event",
                )
            )
    return findings


def _rule_required_property(data: dict, rule: TPValidationRule) -> list[dict]:
    config = rule.config or {}
    prop_name = config.get("property_name", "")
    if not prop_name:
        return []
    rid = str(rule.id)
    findings = []
    for ev in _scope_events(data, rule):
        names_on_event = {p["name"] for p in ev["properties"]}
        if prop_name not in names_on_event:
            findings.append(
                _rule_finding(
                    rid,
                    rule.severity,
                    "event",
                    ev["id"],
                    f"Event '{ev['name']}' is missing required property '{prop_name}'",
                    suggested_fix=f"Attach property '{prop_name}' to this event",
                )
            )
    return findings


def _rule_property_type_consistency(data: dict, rule: TPValidationRule) -> list[dict]:
    """Flag events where a property name has a different data_type than another
    event's property with the same name (cross-event inconsistency)."""
    rid = str(rule.id)
    # Build name → set of data_types across all scoped events.
    name_to_types: dict[str, set[str]] = {}
    for ev in _scope_events(data, rule):
        for prop in ev["properties"]:
            name_to_types.setdefault(prop["name"], set()).add(prop["data_type"])

    # Collect names that have more than one data_type.
    inconsistent = {n: types for n, types in name_to_types.items() if len(types) > 1}
    if not inconsistent:
        return []

    findings = []
    for ev in _scope_events(data, rule):
        for prop in ev["properties"]:
            if prop["name"] in inconsistent:
                types_str = "/".join(sorted(inconsistent[prop["name"]]))
                findings.append(
                    _rule_finding(
                        rid,
                        rule.severity,
                        "event",
                        ev["id"],
                        (
                            f"Property '{prop['name']}' on event '{ev['name']}' has inconsistent "
                            f"data_type across events (found: {types_str})"
                        ),
                        suggested_fix="Ensure all uses of this property share the same data_type",
                    )
                )
    return findings


def _rule_pii_must_be_flagged(data: dict, rule: TPValidationRule) -> list[dict]:
    """Flag library event/user properties whose name matches a PII pattern but
    is_pii is False.  Uses library arrays (they carry `id`; per-event arrays do not).
    """
    config = rule.config or {}
    patterns: list[str] = config.get(
        "patterns",
        ["email", "phone", "ssn", "first_name", "last_name", "address", "ip", "user_id"],
    )
    rid = str(rule.id)
    findings = []
    for kind in ("event", "user"):
        for p in data["properties"][kind]:
            name_lower = p["name"].lower()
            if any(pat in name_lower for pat in patterns) and not p.get("is_pii", False):
                findings.append(
                    _rule_finding(
                        rid,
                        rule.severity,
                        "property",
                        p["id"],
                        f"Property '{p['name']}' appears to contain PII but is_pii is not set",
                        suggested_fix="set is_pii=true",
                    )
                )
    return findings


def _rule_event_name_components(data: dict, rule: TPValidationRule) -> list[dict]:
    """Validate that each event name is composed of the expected ordered
    components, uses an allowed separator, and each token matches the required
    casing.

    Config keys (all optional with sensible defaults):
      components  – ordered list of component labels; used only for the
                    suggested-fix message, not for positional enforcement
                    (names are free-form tokens). Defaults to [].
      separators  – list of allowed separators; the first one is used in
                    suggested-fix formatting. Defaults to ["_"].
      casing      – one of snake_case | camelCase | TitleCase | lower | upper
                    | any. Applied to each split token. Defaults to "lower".
      min_parts   – minimum number of separator-split tokens. Defaults to
                    len(components) if components provided, else 1.
      max_parts   – maximum number of tokens; None means no upper limit.
    """
    config = rule.config or {}
    components: list[str] = config.get("components", [])
    separators: list[str] = config.get("separators", ["_"])
    casing: str = config.get("casing", "lower")
    min_parts: int = config.get("min_parts", len(components) if components else 1)
    max_parts: int | None = config.get("max_parts", None)

    if not separators:
        separators = ["_"]

    primary_sep = separators[0]
    rid = str(rule.id)
    findings = []

    for ev in _scope_events(data, rule):
        name: str = ev["name"]
        ev_id: str = ev["id"]

        # Check that the name uses only one of the allowed separators.  We pick
        # whichever allowed separator splits into the most tokens (greedy match),
        # or fall back to the primary separator for the error message.
        best_sep = primary_sep
        best_parts: list[str] = [name]
        for sep in separators:
            parts = name.split(sep)
            if len(parts) > len(best_parts):
                best_parts = parts
                best_sep = sep

        parts = best_parts
        issues: list[str] = []

        # --- Part count check ---
        if len(parts) < min_parts:
            issues.append(
                f"expected at least {min_parts} component(s) separated by "
                f"'{primary_sep}' (got {len(parts)})"
            )
        if max_parts is not None and len(parts) > max_parts:
            issues.append(
                f"expected at most {max_parts} component(s) separated by "
                f"'{primary_sep}' (got {len(parts)})"
            )

        # --- Casing check on each token ---
        bad_tokens: list[str] = []
        for token in parts:
            if not token:
                # Empty token means a leading/trailing/double separator.
                bad_tokens.append(repr(token))
                continue
            if (casing == "lower" and token != token.lower()) or (
                casing == "upper" and token != token.upper()
            ):
                bad_tokens.append(token)
            elif casing in ("snake_case", "camelCase", "TitleCase", "Title"):
                if not matches_casing(token, casing):
                    bad_tokens.append(token)
            # casing == "any" → always passes

        if bad_tokens:
            issues.append(f"token(s) {bad_tokens} do not match required casing '{casing}'")

        if issues:
            # Build a suggested fix using the component labels as a template.
            if components:
                suggested = primary_sep.join(f"<{c}>" for c in components[:min_parts])
                suggested_fix = (
                    f"Name should follow: {suggested} "
                    f"(e.g. '{primary_sep.join(c.lower() for c in components[:min_parts])}')"
                )
            else:
                suggested_fix = (
                    f"Name should use '{primary_sep}' as separator with "
                    f"at least {min_parts} component(s) in '{casing}' casing"
                )

            findings.append(
                _rule_finding(
                    rid,
                    rule.severity,
                    "event",
                    ev_id,
                    f"Event '{name}' does not follow the naming convention: " + "; ".join(issues),
                    suggested_fix=suggested_fix,
                )
            )

    return findings


_RULE_EVALUATORS = {
    "event_name_casing": _rule_event_name_casing,
    "event_name_regex": _rule_event_name_regex,
    "event_requires_description": _rule_event_requires_description,
    "event_requires_owner": _rule_event_requires_owner,
    "required_property": _rule_required_property,
    "property_type_consistency": _rule_property_type_consistency,
    "pii_must_be_flagged": _rule_pii_must_be_flagged,
    "event_name_components": _rule_event_name_components,
}


def _evaluate_rules(data: dict, rules: list[TPValidationRule]) -> list[dict]:
    """Dispatch each enabled rule to its evaluator and collect findings."""
    findings: list[dict] = []
    for rule in rules:
        if not rule.enabled:
            continue
        evaluator = _RULE_EVALUATORS.get(rule.rule_type)
        if evaluator is None:
            continue
        findings.extend(evaluator(data, rule))
    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def validate_plan(session: AsyncSession, plan: TPPlan, branch: TPBranch) -> dict:
    data = await plan_to_dict(session, plan, branch)
    rules = await get_or_seed_rules(session, plan)

    findings: list[dict] = _structural_findings(data)
    findings += _evaluate_rules(data, rules)

    return {
        "findings": findings,
        "rules": [rule_to_dict(r) for r in rules],
        "counts": {
            "events": len(data["events"]),
            "event_properties": len(data["properties"]["event"]),
            "user_properties": len(data["properties"]["user"]),
            "sources": len(data["sources"]),
            "destinations": len(data["destinations"]),
            "metrics": len(data["metrics"]),
        },
        "is_publishable": not any(f["severity"] == "error" for f in findings),
    }
