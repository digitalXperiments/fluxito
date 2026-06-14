"""
Audit consumer adapter — maps the published structured tracking-plan
snapshot into the legacy expected-event shape used by the audit tools.

Public functions keep their signatures so analytics_tools / tagmanager_tools
are unaffected.

Usage in any audit tool:
    from app.tools.sdr_audit_helpers import get_sdr_expected_events

    expected = await get_sdr_expected_events(project_id)
    if expected:
        # Validate against SDR
        ...
    else:
        # Fall back to live-config heuristics
        ...
"""

from __future__ import annotations

import logging
from typing import Any

import app.app_state as app_state
from app.services.tracking_plan.publish import latest_snapshot_for_project

logger = logging.getLogger(__name__)

_STATUS_RANK = {"deprecated": 0, "planned": 1, "implemented": 2, "verified": 3}


def _rollup_status(sources: list[dict]) -> str:
    """Collapse per-source statuses into one event status (highest wins)."""
    if not sources:
        return "planned"
    statuses = [s.get("implementation_status", "planned") for s in sources]
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 1))


def _snapshot_event_to_legacy(ev: dict, dest_platform_by_name: dict[str, str]) -> dict:
    """Map a snapshot event dict into the legacy expected-event shape."""
    return {
        "name": ev["name"],
        "status": _rollup_status(ev.get("sources", [])),
        "purpose": ev.get("purpose"),
        "trigger_type": ev.get("trigger_type"),
        "trigger_config": ev.get("trigger_config"),
        "parameters": [
            {
                "name": p["name"],
                "type": p.get("data_type"),
                "required": p.get("required", False),
                "source": None,
                "example": p.get("example"),
                "validation_rule": None,
            }
            for p in ev.get("properties", [])
        ],
        "destinations": [
            {
                "platform": dest_platform_by_name.get(d["destination"], d["destination"]),
                "platform_account_id": None,
                "dest_event_name": d.get("dest_event_name"),
                "mapping": d.get("property_mappings"),
            }
            for d in ev.get("destinations", [])
        ],
    }


async def get_sdr_expected_events(project_id: Any) -> dict | None:
    """
    Load the approved SDR events for a project.

    Returns a dict with:
      - ``sdr_version``: version number string (e.g. "1.0")
      - ``sdr_id``: UUID string of the plan
      - ``events``: list of event dicts with parameters and destinations
      - ``event_index``: dict mapping event_name → event dict (for fast lookup)

    Returns None if no published tracking plan exists.
    """
    async with app_state.db_session_factory() as db:
        snapshot = await latest_snapshot_for_project(db, project_id)
    if snapshot is None:
        return None
    dest_platform_by_name = {d["name"]: d["platform"] for d in snapshot.get("destinations", [])}
    events = [_snapshot_event_to_legacy(ev, dest_platform_by_name) for ev in snapshot.get("events", [])]
    return {
        "sdr_version": snapshot.get("__version__", ""),
        "sdr_id": snapshot.get("plan", {}).get("id"),
        "events": events,
        "event_index": {e["name"]: e for e in events},
    }


async def get_sdr_expected_for_event(project_id: Any, event_name: str) -> dict | None:
    """
    Get expected configuration for a specific event from the SDR.

    Returns the event dict (with parameters, destinations, trigger_config, etc.)
    or None if the event isn't in the SDR or no SDR exists.
    """
    expected = await get_sdr_expected_events(project_id)
    if not expected:
        return None
    return expected["event_index"].get(event_name)


def compare_event_to_sdr(
    live_event: dict,
    expected_event: dict,
) -> list[dict]:
    """
    Compare a live-discovered event against its SDR specification.

    Returns a list of finding dicts, each with:
      - ``category``: "parameter" | "destination" | "trigger" | "status"
      - ``severity``: "critical" | "warning" | "info"
      - ``issue``: human-readable description
      - ``sdr_expected``: what the SDR says
      - ``live_actual``: what was found live

    Empty list means the event matches its SDR spec.
    """
    findings: list[dict] = []

    # 1) Check required parameters
    sdr_params = {p["name"]: p for p in expected_event.get("parameters", [])}
    live_params = set(live_event.get("parameters", []))
    # live_params may be a list of names or dicts
    if live_params and isinstance(next(iter(live_params), None), dict):
        live_params = {p.get("name") for p in live_event.get("parameters", [])}

    for pname, pspec in sdr_params.items():
        if pspec.get("required") and pname not in live_params:
            findings.append(
                {
                    "category": "parameter",
                    "severity": "critical",
                    "issue": f"Required parameter '{pname}' missing from live implementation",
                    "sdr_expected": f"{pname} (type={pspec.get('type', '?')}, required=True)",
                    "live_actual": "not found",
                }
            )

    # 2) Check destinations
    sdr_dests = {d["platform"] for d in expected_event.get("destinations", [])}
    live_dests = set(live_event.get("destinations", []))
    if live_dests and isinstance(next(iter(live_dests), None), dict):
        live_dests = {d.get("platform") for d in live_event.get("destinations", [])}

    for platform in sdr_dests:
        if platform not in live_dests:
            findings.append(
                {
                    "category": "destination",
                    "severity": "warning",
                    "issue": f"Event should fire to '{platform}' per SDR but not found in live config",
                    "sdr_expected": platform,
                    "live_actual": "not configured",
                }
            )

    # 3) Check event status
    sdr_status = expected_event.get("status")
    if sdr_status == "deprecated":
        findings.append(
            {
                "category": "status",
                "severity": "warning",
                "issue": "Event is marked as deprecated in SDR but still firing in live config",
                "sdr_expected": "deprecated",
                "live_actual": "active",
            }
        )

    return findings


def build_audit_sdr_summary(
    expected: dict,
    live_event_names: list[str],
) -> dict:
    """
    Build a high-level SDR compliance summary for an audit report.

    Returns a dict with:
      - ``sdr_version``: version used
      - ``total_expected``: events in SDR
      - ``total_live``: events found live
      - ``matched``: events in both
      - ``missing_from_live``: SDR events not found live
      - ``unexpected_live``: live events not in SDR
      - ``compliance_score``: 0–100
    """
    sdr_event_names = set(expected["event_index"].keys())
    live_set = set(live_event_names)

    # Exclude deprecated events from the "expected" set
    active_expected = {
        name for name, ev in expected["event_index"].items() if ev.get("status") != "deprecated"
    }

    matched = active_expected & live_set
    missing = active_expected - live_set
    unexpected = live_set - sdr_event_names

    total = len(active_expected)
    score = round((len(matched) / total * 100) if total > 0 else 100)

    return {
        "sdr_version": expected["sdr_version"],
        "total_expected": len(active_expected),
        "total_live": len(live_set),
        "matched": sorted(matched),
        "missing_from_live": sorted(missing),
        "unexpected_live": sorted(unexpected),
        "compliance_score": min(score, 100),
    }
