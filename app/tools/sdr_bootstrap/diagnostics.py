"""Platform-agnostic SDR diagnostic engine.

Cross-references capability roles (tag_inventory, event_volume, conversion_config)
across whatever providers a project has connected, and emits structured findings
plus a readiness summary. Pure: operates on the scans dict (scans_to_dict output),
so it is deterministic and unit-testable without a DB or live connectors.
"""

from __future__ import annotations

from typing import Any

from app.tools.sdr_bootstrap.registry import (
    DIAGNOSTIC_ROLES,
    ROLE_CONVERSION_CONFIG,
    ROLE_EVENT_VOLUME,
    ROLE_TAG_INVENTORY,
)

CRITICAL, HIGH, MEDIUM = "critical", "high", "medium"
_SEV_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2}

# Keyword families used to recognize the intake's primary conversion in any
# vertical without assuming ecommerce.
_CONVERSION_HINTS = (
    ("purchase", "order", "checkout", "transaction", "buy"),
    ("subscri", "membership", "recurring", "plan"),  # subscribe / subscription / subscriber
    ("lead", "form", "demo", "quote", "contact", "signup", "sign_up", "register"),
    ("book", "reserv", "appointment"),
    ("donat", "pledge", "contribut"),
    ("install", "activation", "trial"),
)
_CONSENT_HINTS = ("consent", "gdpr", "ccpa", "cmp", "onetrust", "cookiebot", "gate")


def _f(ftype, severity, summary, evidence, affected, recommendation, fix_location):
    return {
        "type": ftype,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
        "affected_events": affected,
        "recommendation": recommendation,
        "fix_location": fix_location,
    }


def _scans_for_role(scans: dict[str, dict], role: str) -> list[dict]:
    return [s for s in scans.values() if role in (s.get("roles") or [])]


def _primary_terms(intake_answers: dict[str, str]) -> list[str]:
    text = " ".join(
        str(intake_answers.get(k, "")) for k in ("conversion_definition", "primary_kpis", "business_model")
    ).lower()
    terms: list[str] = []
    for family in _CONVERSION_HINTS:
        if any(t in text for t in family):
            terms.extend(family)
    return terms


def diagnose(
    scans: dict[str, dict],
    intake_answers: dict[str, str] | None = None,
    current_event_names: list[str] | None = None,
) -> dict[str, Any]:
    intake_answers = intake_answers or {}

    configured: set[str] = set()
    for s in _scans_for_role(scans, ROLE_TAG_INVENTORY):
        configured.update(e["name"] for e in s.get("events", []) if e.get("name"))

    volumes: dict[str, int] = {}
    recent_volumes: dict[str, int] = {}
    has_volume = False
    has_recent = False
    for s in _scans_for_role(scans, ROLE_EVENT_VOLUME):
        has_volume = True
        meta = s.get("raw_metadata", {}) or {}
        for name, cnt in (meta.get("event_volumes") or {}).items():
            volumes[name] = max(volumes.get(name, 0), int(cnt))
        if "event_volumes_recent" in meta:
            has_recent = True
            for name, cnt in (meta.get("event_volumes_recent") or {}).items():
                recent_volumes[name] = max(recent_volumes.get(name, 0), int(cnt))

    conversion_events: set[str] = set()
    has_conv = False
    for s in _scans_for_role(scans, ROLE_CONVERSION_CONFIG):
        has_conv = True
        conversion_events.update(e["name"] for e in s.get("events", []) if e.get("name"))

    present_roles: set[str] = set()
    for s in scans.values():
        present_roles.update(s.get("roles") or [])
    unfilled_roles = [r for r in DIAGNOSTIC_ROLES if r not in present_roles]

    terms = _primary_terms(intake_answers)

    def is_primary(name: str) -> bool:
        return bool(terms) and any(t in name.lower() for t in terms)

    findings: list[dict] = []

    for s in scans.values():
        if s.get("status") in ("failed", "partial"):
            findings.append(
                _f(
                    "connector_error",
                    HIGH,
                    f"The {s['source']} connector returned {s.get('status')} data.",
                    "; ".join(s.get("errors") or []) or s.get("status", ""),
                    [],
                    "Reconnect or re-authorize the connector, then re-run diagnose.",
                    "connector",
                )
            )

    if has_volume:
        max_volume = max(volumes.values(), default=0)
        low_floor = max(5, int(0.005 * max_volume))  # peer-relative "suspiciously low"
        for name in sorted(configured):
            vol = volumes.get(name, 0)
            if vol == 0:
                findings.append(
                    _f(
                        "tag_configured_but_no_data",
                        CRITICAL if is_primary(name) else MEDIUM,
                        f"`{name}` is configured to collect but shows 0 events in the analytics platform.",
                        "configured in tag layer; 0 events in last window",
                        [name],
                        "Verify the site/app actually fires this event (dataLayer push / SDK call).",
                        "website",
                    )
                )
                continue
            # Recently stopped: had volume over 30d but nothing in the recent window.
            if has_recent and recent_volumes.get(name, 0) == 0:
                findings.append(
                    _f(
                        "event_recently_stopped",
                        CRITICAL if is_primary(name) else MEDIUM,
                        f"`{name}` fired over the last 30 days but 0 times in the recent window — it likely broke recently.",
                        f"30d: {vol}; recent: 0",
                        [name],
                        "Check for a recent site/app or tag deploy that stopped this event.",
                        "website",
                    )
                )
            # Suspiciously low volume for an important event (e.g. fires ~monthly).
            elif (is_primary(name) or name in conversion_events) and 0 < vol < low_floor:
                findings.append(
                    _f(
                        "low_volume",
                        HIGH if is_primary(name) else MEDIUM,
                        f"`{name}` fires far below peers ({vol} in 30d vs peak {max_volume}) — likely partially broken.",
                        f"30d: {vol}; floor: {low_floor}",
                        [name],
                        "Verify this event fires on every relevant interaction, not just edge cases.",
                        "website",
                    )
                )
        known = configured | set(current_event_names or [])
        for name, cnt in sorted(volumes.items(), key=lambda kv: -kv[1]):
            if cnt > 0 and name not in known and not name.startswith("("):
                findings.append(
                    _f(
                        "event_flowing_but_undocumented",
                        MEDIUM,
                        f"`{name}` is flowing ({cnt} in last window) but is not documented/configured.",
                        f"{cnt} events",
                        [name],
                        "Decide whether to document and map this event.",
                        "sdr",
                    )
                )

    primary_candidates = sorted({n for n in (configured | conversion_events) if is_primary(n)})
    primary_proven = False
    if terms:
        if not primary_candidates:
            findings.append(
                _f(
                    "primary_conversion_unproven",
                    CRITICAL,
                    "No event matching the stated primary conversion is configured anywhere.",
                    f"intake conversion: {intake_answers.get('conversion_definition', '?')[:120]}",
                    [],
                    "Define and implement the primary conversion event.",
                    "website",
                )
            )
        else:
            primary_proven = has_volume and any(volumes.get(n, 0) > 0 for n in primary_candidates)
            if has_volume and not primary_proven:
                findings.append(
                    _f(
                        "primary_conversion_unproven",
                        CRITICAL,
                        "The primary conversion is configured but no matching event is flowing.",
                        f"candidates: {', '.join(primary_candidates)}",
                        primary_candidates,
                        "Fix the site/app so the primary conversion event fires.",
                        "website",
                    )
                )
            if has_conv and not any(n in conversion_events for n in primary_candidates):
                findings.append(
                    _f(
                        "conversion_not_configured",
                        HIGH,
                        "The primary conversion is not configured as an ad-platform conversion (no ROAS).",
                        f"candidates: {', '.join(primary_candidates)}",
                        primary_candidates,
                        "Configure the conversion in the relevant ad platform(s).",
                        "config",
                    )
                )

    consent_detected = any(
        (s.get("raw_metadata", {}) or {}).get("consent_detected")
        for s in _scans_for_role(scans, ROLE_TAG_INVENTORY)
    )
    privacy_text = str(intake_answers.get("privacy_consent", "")).lower()
    if conversion_events and not consent_detected and any(h in privacy_text for h in _CONSENT_HINTS):
        findings.append(
            _f(
                "consent_gap",
                HIGH,
                "Intake says consent gating is required but no consent signal was detected in the tag layer.",
                intake_answers.get("privacy_consent", "")[:120],
                [],
                "Gate ad/analytics tags behind the consent platform.",
                "config",
            )
        )

    findings.sort(key=lambda x: _SEV_ORDER.get(x["severity"], 9))

    readiness = {
        "critical_findings_unresolved": sum(1 for f in findings if f["severity"] == CRITICAL),
        "events_volume_proven": (sum(1 for n in configured if volumes.get(n, 0) > 0) if has_volume else None),
        "primary_conversion_proven": primary_proven,
        "unfilled_roles": unfilled_roles,
    }
    return {"findings": findings, "readiness": readiness}
