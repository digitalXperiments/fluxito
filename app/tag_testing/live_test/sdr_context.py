"""
Live-tag-testing SDR context — reads the published structured tracking-plan
snapshot, filtered by URL pattern. Signature preserved for live_tag_test_tools.
"""

from __future__ import annotations

import logging
import re

import app.app_state as app_state
from app.services.tracking_plan.publish import latest_snapshot_for_project

logger = logging.getLogger(__name__)


async def get_sdr_context_for_url(
    project_id: str,
    url: str | None = None,
) -> dict:
    """
    Return approved SDR events + params relevant to the given URL.

    URL matching: if the snapshot event has a trigger_config.url_pattern, we
    filter to events whose pattern matches the URL.  If no pattern is set,
    the event is returned for all URLs.

    Returns:
    {
        "project_id": str,
        "url": str | None,
        "events": [
            {
                "event_name": str,
                "description": str,
                "trigger_config": dict,
                "destinations": [str],
                "parameters": [
                    {"name": str, "type": str, "required": bool,
                     "description": str, "example_value": str}
                ]
            }
        ],
        "total": int,
        "error": None | str
    }
    """
    try:
        async with app_state.db_session_factory() as db:
            snapshot = await latest_snapshot_for_project(db, project_id)
    except Exception as exc:
        logger.warning(f"get_sdr_context_for_url failed: {exc}")
        return {
            "project_id": project_id,
            "url": url,
            "events": [],
            "total": 0,
            "error": str(exc),
        }

    if snapshot is None:
        return {
            "project_id": project_id,
            "url": url,
            "events": [],
            "total": 0,
            "error": None,
        }

    dest_platform_by_name = {d["name"]: d["platform"] for d in snapshot.get("destinations", [])}
    out_events = []

    for ev in snapshot.get("events", []):
        trigger_config = ev.get("trigger_config") or {}
        pattern = trigger_config.get("url_pattern") if isinstance(trigger_config, dict) else None

        # URL filter: if pattern is set and URL is provided, check match
        if pattern and url:
            try:
                if not re.search(pattern, url, re.I):
                    continue  # This event doesn't apply to the given URL
            except re.error:
                pass  # Bad pattern — include the event anyway

        out_events.append(
            {
                "event_name": ev["name"],
                "description": ev.get("description") or "",
                "trigger_config": trigger_config,
                "destinations": [
                    dest_platform_by_name.get(d["destination"], d["destination"])
                    for d in ev.get("destinations", [])
                ],
                "parameters": [
                    {
                        "name": p["name"],
                        "type": p.get("data_type"),
                        "required": p.get("required", False),
                        "description": p.get("override_description") or "",
                        "example_value": p.get("example") or "",
                    }
                    for p in ev.get("properties", [])
                ],
            }
        )

    return {
        "project_id": project_id,
        "url": url,
        "events": out_events,
        "total": len(out_events),
        "error": None,
    }
