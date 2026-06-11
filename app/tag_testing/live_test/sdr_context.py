"""
Live Tag Test — SDR Context Loader
=====================================

Fetches the SDR (Solution Design Reference) events and parameters relevant
to a given URL, providing Claude with the "expected" event spec to cross-check
against what was actually captured in the browser session.
"""

from __future__ import annotations

import logging

import app.app_state as state

logger = logging.getLogger(__name__)


async def get_sdr_context_for_url(
    project_id: str,
    url: str | None = None,
) -> dict:
    """
    Return approved SDR events + params relevant to the given URL.

    URL matching: if the SDR event has a trigger_config.url_pattern, we
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
                    {"name": str, "type": str, "required": bool, "description": str}
                ]
            }
        ],
        "total": int,
        "error": None | str
    }
    """
    try:
        from sqlalchemy import text

        async with state.db_session_factory() as db:
            # Fetch approved SDR events with their parameters
            result = await db.execute(
                text("""
                    SELECT
                        e.id AS event_id,
                        e.event_name,
                        e.description,
                        e.trigger_config,
                        e.status,
                        ARRAY_AGG(DISTINCT d.destination_platform) FILTER (WHERE d.destination_platform IS NOT NULL)
                            AS destinations,
                        JSON_AGG(
                            JSON_BUILD_OBJECT(
                                'name',        p.parameter_name,
                                'type',        p.parameter_type,
                                'required',    p.is_required,
                                'description', p.description,
                                'example_value', p.example_value
                            )
                        ) FILTER (WHERE p.id IS NOT NULL) AS parameters
                    FROM sdr_events e
                    LEFT JOIN sdr_destinations d ON d.event_id = e.id
                    LEFT JOIN sdr_parameters p ON p.event_id = e.id
                    WHERE e.project_id = :pid
                      AND e.status = 'approved'
                    GROUP BY e.id, e.event_name, e.description, e.trigger_config, e.status
                    ORDER BY e.event_name
                """),
                {"pid": project_id},
            )
            rows = result.mappings().all()

        events_out = []
        import re

        for row in rows:
            trigger_config = row.get("trigger_config") or {}
            url_pattern = trigger_config.get("url_pattern") if isinstance(trigger_config, dict) else None

            # URL filter: if pattern is set and URL is provided, check match
            if url and url_pattern:
                try:
                    if not re.search(url_pattern, url, re.I):
                        continue  # This event doesn't apply to the given URL
                except re.error:
                    pass  # Bad pattern — include the event anyway

            destinations = row.get("destinations") or []
            parameters = row.get("parameters") or []

            events_out.append(
                {
                    "event_name": row["event_name"],
                    "description": row.get("description") or "",
                    "trigger_config": trigger_config,
                    "destinations": list(destinations),
                    "parameters": [dict(p) for p in parameters] if parameters else [],
                }
            )

        return {
            "project_id": project_id,
            "url": url,
            "events": events_out,
            "total": len(events_out),
            "error": None,
        }

    except Exception as e:
        logger.warning(f"get_sdr_context_for_url failed: {e}")
        return {
            "project_id": project_id,
            "url": url,
            "events": [],
            "total": 0,
            "error": str(e),
        }
