"""build_coverage — the Implement hub read model.

Assembles one row per planned event from the tracking-plan serializer
(``plan_to_dict`` with ``include_drift=True``) and cross-references the live
GTM container to annotate each event as deployed / not-found, plus a list of
GTM tags/triggers that fire events with no matching plan event.

The GTM fetch (one Tag Manager API round-trip) is cached in Redis for ~10min
under ``implement:gtm:{project_id}`` so repeated page loads are cheap.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select

import app.app_state as app_state
from app.models.connection import OAuthConnection
from app.models.token import GTMContainer
from app.models.tracking_plan import TPPlan
from app.services.tracking_plan.bootstrap import get_main_branch
from app.services.tracking_plan.serializer import plan_to_dict

logger = logging.getLogger(__name__)

_GTM_CACHE_TTL = 600  # seconds


# ---------------------------------------------------------------------------
# Name normalization + GTM extraction helpers
# ---------------------------------------------------------------------------


def normalize_event_name(name: str) -> str:
    """snake_case-normalize an event name for fuzzy plan<->GTM matching.

    Lowercases, splits camelCase, and collapses any run of non-alphanumerics
    to a single underscore. ``"AddToCart"`` and ``"Add to cart"`` both become
    ``"add_to_cart"``.
    """
    s = (name or "").strip()
    if not s:
        return ""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    return s.strip("_").lower()


# GTM tag types that carry a GA4 event name in an ``eventName`` parameter.
_GA4_EVENT_TAG_TYPES = {"gaawe"}
# GTM custom-event trigger types (raw API casing varies across exports).
_CUSTOM_EVENT_TRIGGER_TYPES = {"customEvent", "CUSTOM_EVENT"}


def _param_value(params: list[dict], key: str) -> str | None:
    for p in params or []:
        if p.get("key") == key:
            v = p.get("value")
            return str(v) if v is not None else None
    return None


def _extract_gtm_events(tags: list[dict], triggers: list[dict]) -> list[dict]:
    """Pull firing event names out of raw GTM tags + triggers.

    Returns a list of ``{"event_name", "source"}`` where source is either
    ``"tag"`` (a GA4 event tag's ``eventName`` parameter) or ``"trigger"``
    (a custom-event trigger's ``arg1`` filter value). Names wrapped entirely
    in a ``{{variable}}`` reference are skipped — they aren't literal events.
    """
    found: list[dict] = []

    for t in tags or []:
        if t.get("type") not in _GA4_EVENT_TAG_TYPES:
            continue
        ev = _param_value(t.get("parameter", []), "eventName")
        if ev and not (ev.startswith("{{") and ev.endswith("}}")):
            found.append({"event_name": ev, "source": "tag", "label": t.get("name")})

    for tr in triggers or []:
        if tr.get("type") not in _CUSTOM_EVENT_TRIGGER_TYPES:
            continue
        # Custom-event triggers encode the event name as the ``arg1`` value of a
        # filter whose ``arg0`` is the built-in ``{{_event}}`` variable. The GTM
        # API stores that condition under ``customEventFilter`` (not ``filter``),
        # which is where our own draft materialization writes it too.
        for filt in (tr.get("filter") or []) + (tr.get("customEventFilter") or []):
            params = filt.get("parameter", [])
            arg0 = _param_value(params, "arg0")
            arg1 = _param_value(params, "arg1")
            if arg0 and "_event" in arg0 and arg1 and not (arg1.startswith("{{") and arg1.endswith("}}")):
                found.append({"event_name": arg1, "source": "trigger", "label": tr.get("name")})

    return found


# ---------------------------------------------------------------------------
# GTM connection / container resolution
# ---------------------------------------------------------------------------


async def resolve_gtm_target(session: Any, project_id: uuid.UUID) -> dict | None:
    """Resolve the project's live GTM target the way the tagmanager tools do.

    Returns ``{connection_id, account_id, container_id, public_id,
    container_name}`` (all strings) or ``None`` when the project has no active
    Google connection with a discovered GTM container.
    """
    conn = (
        await session.execute(
            select(OAuthConnection).where(
                OAuthConnection.project_id == project_id,
                OAuthConnection.provider == "google",
                OAuthConnection.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        return None

    container = (
        (
            await session.execute(
                select(GTMContainer).where(
                    GTMContainer.connection_id == conn.id,
                    GTMContainer.is_active.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )
    if container is None:
        return None

    return {
        "connection_id": str(conn.id),
        "account_id": container.account_id,
        "container_id": container.container_id,
        "public_id": container.public_id,
        "container_name": container.container_name,
    }


async def _fetch_gtm_snapshot(target: dict) -> dict:
    """One Tag Manager round-trip → normalized event snapshot.

    Prefers the live (published) container version — a single API call that
    carries full raw tag parameters + trigger filters. Falls back to the
    default workspace's list_tags/list_triggers when there's no published
    version yet. Returns ``{"events": [...], "public_id": ...}`` or
    ``{"error": ...}``.
    """
    connector = getattr(app_state, "gtm_connector", None)
    if connector is None:
        return {"error": "GTM connector unavailable"}

    conn_id = target["connection_id"]
    account_id = target["account_id"]
    container_id = target["container_id"]

    tags: list[dict] = []
    triggers: list[dict] = []
    try:
        live = await connector.get_live_version(conn_id, account_id, container_id)
        if isinstance(live, dict) and not live.get("error"):
            tags = live.get("tag", []) or []
            triggers = live.get("trigger", []) or []
    except Exception as exc:
        logger.info("implement: live GTM version fetch failed (%s); trying workspace", exc)

    if not tags and not triggers:
        # No published version (or it failed) — read the default workspace. The
        # simplified connector shapes lose tag parameters, so event names come
        # from custom-event trigger filters; GA4 tag eventName params are only
        # available via the raw live version above.
        try:
            tags_r = await connector.list_tags(conn_id, account_id, container_id)
            triggers_r = await connector.list_triggers(conn_id, account_id, container_id)
            triggers = triggers_r.get("triggers", []) if isinstance(triggers_r, dict) else []
            # Re-key the simplified trigger shape to the raw fields _extract expects.
            triggers = [
                {"type": t.get("trigger_type"), "name": t.get("trigger_name"), "filter": t.get("filters", [])}
                for t in triggers
            ]
            tags = []  # simplified tags carry no eventName parameter
            _ = tags_r  # fetched to surface connector errors symmetrically
        except Exception as exc:
            return {"error": f"GTM read failed: {exc}"}

    return {"events": _extract_gtm_events(tags, triggers), "public_id": target.get("public_id")}


async def _gtm_snapshot_cached(project_id: uuid.UUID, target: dict) -> dict:
    """Redis-cached wrapper around ``_fetch_gtm_snapshot`` (TTL ~10min)."""
    key = f"implement:gtm:{project_id}"
    redis = getattr(app_state, "redis_client", None)

    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached:
                loaded: dict = json.loads(cached)
                return loaded
        except Exception:
            pass

    snapshot = await _fetch_gtm_snapshot(target)

    if redis is not None and not snapshot.get("error"):
        try:
            await redis.setex(key, _GTM_CACHE_TTL, json.dumps(snapshot, default=str))
        except Exception:
            pass

    return snapshot


# ---------------------------------------------------------------------------
# Public: build_coverage
# ---------------------------------------------------------------------------


async def build_coverage(session: Any, project_id: uuid.UUID) -> dict:
    """Assemble the Implement hub coverage read model for a project.

    Handles missing plan / missing GTM connection gracefully — the returned
    ``flags`` describe what data is available so the UI can render the right
    empty states. Never mutates the plan; safe to call read-only.
    """
    plan = (await session.execute(select(TPPlan).where(TPPlan.project_id == project_id))).scalar_one_or_none()

    if plan is None:
        return {
            "rows": [],
            "summary": _empty_summary(),
            "unplanned_in_gtm": [],
            "gtm": {"connected": False, "public_id": None, "container_name": None},
            "flags": {"has_plan": False, "has_gtm": False},
        }

    branch = await get_main_branch(session, plan)
    data = await plan_to_dict(session, plan, branch, include_drift=True)

    # --- Resolve + fetch the live GTM comparison (cached) --------------------
    target = await resolve_gtm_target(session, project_id)
    has_gtm = target is not None
    # Keyed by normalized event name → the list of GTM events that collapse to
    # it. A list (not a single value) so two distinct GTM events sharing a
    # normalized key (e.g. a tag and a trigger both firing 'purchase') don't
    # overwrite each other and drop out of the unplanned report.
    gtm_events_norm: dict[str, list[dict]] = {}
    gtm_error: str | None = None
    public_id: str | None = None
    container_name: str | None = None

    if target is not None:
        public_id = target.get("public_id")
        container_name = target.get("container_name")
        snapshot = await _gtm_snapshot_cached(project_id, target)
        if snapshot.get("error"):
            gtm_error = snapshot["error"]
        else:
            for ge in snapshot.get("events", []):
                gtm_events_norm.setdefault(normalize_event_name(ge["event_name"]), []).append(ge)

    # --- Build one row per planned event -------------------------------------
    matched_gtm_keys: set[str] = set()
    rows: list[dict] = []
    for ev in data.get("events", []):
        name = ev["name"]
        norm_key = normalize_event_name(name)

        if not has_gtm:
            gtm_status = "no_connection"
        elif norm_key in gtm_events_norm:
            matched_gtm_keys.add(norm_key)
            gtm_status = "deployed"
        else:
            gtm_status = "not_found"

        rows.append(
            {
                "event_id": ev["id"],
                "name": name,
                "display_name": ev.get("display_name"),
                "category": ev.get("category"),
                "sources": [
                    {"name": s["name"], "implementation_status": s["implementation_status"]}
                    for s in ev.get("sources", [])
                ],
                "drift": ev.get("drift"),
                "destinations": [d["destination"] for d in ev.get("destinations", [])],
                "gtm": gtm_status,
                # Param shape for the client-side snippet generator (Get code).
                "properties": [
                    {
                        "name": p["name"],
                        "data_type": p.get("data_type"),
                        "is_list": p.get("is_list", False),
                        "required": p.get("required", False),
                        "example": p.get("example"),
                    }
                    for p in ev.get("properties", [])
                ],
            }
        )

    # --- GTM tags/triggers firing events with no matching plan event ---------
    unplanned: list[dict] = []
    seen_unplanned: set[tuple[str, str]] = set()
    for norm_key, ges in gtm_events_norm.items():
        if norm_key in matched_gtm_keys:
            continue
        for ge in ges:
            sig = (ge["event_name"], ge["source"])
            if sig in seen_unplanned:
                continue
            seen_unplanned.add(sig)
            unplanned.append(
                {
                    "event_name": ge["event_name"],
                    "source": ge["source"],
                    "label": ge.get("label"),
                }
            )

    summary = _summarize(rows, unplanned)

    return {
        "rows": rows,
        "summary": summary,
        "unplanned_in_gtm": unplanned,
        "gtm": {
            "connected": has_gtm,
            "public_id": public_id,
            "container_name": container_name,
            "error": gtm_error,
        },
        "flags": {"has_plan": True, "has_gtm": has_gtm},
    }


def _empty_summary() -> dict:
    return {"planned": 0, "implemented": 0, "verified": 0, "drifted": 0, "unplanned": 0}


def _summarize(rows: list[dict], unplanned: list[dict]) -> dict:
    implemented = 0
    verified = 0
    drifted = 0
    for r in rows:
        statuses = {s["implementation_status"] for s in r["sources"]}
        if statuses & {"implemented", "verified"}:
            implemented += 1
        if "verified" in statuses:
            verified += 1
        drift = r.get("drift")
        if drift and drift.get("status") in ("drifted", "broken"):
            drifted += 1
    return {
        "planned": len(rows),
        "implemented": implemented,
        "verified": verified,
        "drifted": drifted,
        "unplanned": len(unplanned),
    }
