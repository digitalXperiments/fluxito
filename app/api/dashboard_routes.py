"""
Dashboard API & Web UI Routes

Public (no auth):
  GET  /d/{slug}                    — Public shareable dashboard view (HTML)
  GET  /api/d/{slug}                — Public dashboard data (JSON)

Authenticated (requires signed uid cookie):
  GET  /live-dashboards                   — Live dashboard hub (HTML)
  GET  /live-dashboards/{slug}            — Live dashboard view (HTML)
  GET  /live-dashboards/{slug}/scopes     — Scope management page (HTML)
  DELETE /api/saved-dashboards/{id}       — Delete a dashboard
  PATCH /api/saved-dashboards/{id}/share  — Toggle sharing
  GET/PUT /api/saved-dashboards/{id}/scopes — Manage query_scopes
  GET  /api/saved-dashboards/{slug}/data          — JSON card data
  GET  /saved-dashboards/{slug}/pdf               — PDF export
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

import app.app_state as app_state
from app.auth.uid_cookie import get_uid_from_request
from app.config import settings
from app.dashboards.snapshot import normalize_snap as _normalize_snap
from app.models.connection import OAuthConnection
from app.models.dashboard import Dashboard, DashboardCard
from app.models.project import ProjectMember
from app.models.user import User
from app.templating import render
from app.utils import base_url_from_request, safe_uuid

logger = logging.getLogger(__name__)

router = APIRouter()

# Live-dashboard refresh guardrails. A single hung upstream query (GA4,
# BigQuery, …) must never hang the whole dashboard, and cards must not be
# executed one-at-a-time (slow card blocks every card behind it).
_LIVE_CARD_TIMEOUT_S = 25
_LIVE_CARD_CONCURRENCY = 6

# Live-dashboard data cache. The first load (or an explicit Refresh) populates a
# Redis entry keyed by slug + viewer-role + filters; every subsequent page
# reload then serves from it, so we don't re-hit the upstream analytics APIs
# (GA4, BigQuery, …) on each reload. The Refresh button sends ?refresh=1 to
# bypass and repopulate. A 1h safety TTL bounds staleness even if Refresh is
# never pressed.
_DASH_DATA_CACHE_TTL_S = 3600
_DASH_DATA_CACHE_PREFIX = "dashdata:v1"


def _dashdata_cache_key(
    slug: str,
    is_owner: bool,
    card_count: int,
    dash_version: str,
    filter_overrides: dict,
    platforms_allowed: set,
) -> str:
    """Deterministic Redis key for a dashboard's live-data response.

    Folds in the viewer role (owner payloads carry per-card ``live_error``),
    the dashboard version + card count (so edits/redeploys bust the cache), and
    every active filter (so each filter combination caches separately).
    """
    raw = "|".join(
        [
            slug,
            "o1" if is_owner else "o0",
            f"n{card_count}",
            f"v{dash_version}",
            json.dumps(filter_overrides, sort_keys=True, default=str),
            ",".join(sorted(platforms_allowed)),
        ]
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"{_DASH_DATA_CACHE_PREFIX}:{slug}:{digest}"


async def _dashdata_cache_get(key: str) -> dict | None:
    """Fetch a cached data response. Returns None on miss or any Redis error —
    the cache must never break the endpoint."""
    redis = app_state.redis_client
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        # Degrade gracefully to a live query — the cache must never break reads.
        logger.debug("dashdata cache read failed: %s", exc)
    return None


async def _dashdata_cache_set(key: str, body: dict) -> None:
    """Store a data response under ``key`` with the safety TTL. Silent on any
    Redis error."""
    redis = app_state.redis_client
    if redis is None:
        return
    try:
        await redis.setex(key, _DASH_DATA_CACHE_TTL_S, json.dumps(body, default=str))
    except Exception as exc:
        logger.debug("dashdata cache write failed: %s", exc)


def _as_bool(value) -> bool:
    """Coerce a stored flag to bool. JSON may round-trip a real bool, but a
    template/string default can persist ``"false"`` — and ``bool("false")`` is
    ``True``, which would silently lock dates that should move."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _resolve_relative_date(value: str) -> str:
    """Convert GA4-style relative date strings to ISO-8601 (YYYY-MM-DD).

    Handles: "today", "yesterday", "NdaysAgo" (e.g. "30daysAgo").
    ISO dates and empty strings are passed through unchanged.
    """
    if not value:
        return value
    from datetime import date, timedelta

    v = value.strip().lower()
    if v == "today":
        return date.today().isoformat()
    if v == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    import re

    m = re.match(r"^(\d+)daysago$", v)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)))).isoformat()
    return value  # already ISO or unknown — pass through


async def _load_user_view_from_uid(uid: str | None) -> dict | None:
    """Load a lightweight user view dict from a uid cookie value.

    Returns ``None`` when the cookie is missing/malformed or the user
    no longer exists in the DB. Callers must treat ``None`` as "not
    authenticated" — a previous version returned a stub dict with
    blank email/display_name on lookup failure, which masked the
    deleted-user case and let templates render with empty author info.
    """
    parsed = safe_uuid(uid)
    if parsed is None:
        return None
    try:
        async with app_state.db_session_factory() as db:
            result = await db.execute(select(User).where(User.id == parsed))
            u = result.scalar_one_or_none()
            if u:
                return {
                    "id": str(u.id),
                    "email": u.email or "",
                    "display_name": u.display_name or "",
                    "is_superadmin": bool(u.is_superadmin),
                }
    except Exception as exc:
        logger.warning("Failed to load user view from uid: %s", exc)
    return None


def _card_dict(card: DashboardCard) -> dict:
    return {
        "id": str(card.id),
        "title": card.title,
        "platform": card.platform,
        "tool_name": card.tool_name,
        "query_params": card.query_params,
        "result_cache": card.result_cache,
        "position": card.position,
        "refreshed_at": card.refreshed_at.isoformat() if card.refreshed_at else None,
        "created_at": card.created_at.isoformat() if card.created_at else None,
    }


def _dash_dict(dash: Dashboard, cards: list) -> dict:
    base = settings.APP_BASE_URL
    stored_url = getattr(dash, "share_url", None)
    return {
        "id": str(dash.id),
        "title": dash.title,
        "description": dash.description,
        "owner_email": getattr(dash, "owner_email", None) or "",
        "owner_name": getattr(dash, "owner_name", None),
        "share_slug": dash.share_slug,
        "is_public": dash.is_public,
        "share_url": stored_url or (f"{base}/d/{dash.share_slug}" if dash.is_public else None),
        "shared_at": dash.shared_at.isoformat() if getattr(dash, "shared_at", None) else None,
        "card_count": len(cards),
        "cards": [_card_dict(c) for c in cards],
        "created_at": dash.created_at.isoformat() if dash.created_at else None,
        "updated_at": dash.updated_at.isoformat() if dash.updated_at else None,
    }


def _card_template_view(card: DashboardCard, include_error: bool = False) -> dict:
    """Flatten a DashboardCard into a dict the Jinja card_render.html partial expects."""
    snap = getattr(card, "_live_result", None) or card.result_cache or {}
    view = {
        "id": str(card.id),
        "title": card.title,
        "platform": card.platform or "",
        "card_type": snap.get("card_type", "UNKNOWN"),
        "is_live": getattr(card, "_is_live", False),
        "refreshed_at": card.refreshed_at.isoformat() if card.refreshed_at else None,
        "snap": snap,
    }
    if include_error:
        view["live_error"] = getattr(card, "_live_error", None)
    return view


@router.get("/d/{slug}", response_class=HTMLResponse)
async def public_dashboard(slug: str, request: Request):
    """Shareable public dashboard page — returns a lightweight shell immediately.
    Card data is loaded client-side via /api/d/{slug} (frozen DB cache)."""
    uid = get_uid_from_request(request)

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()

        # Not found at all
        if not dash:
            return render(request, "dashboards/not_found.html", {}, status_code=404)

        # Private dashboard — only the owner may view it
        if not dash.is_public and (not uid or str(dash.user_id) != uid):
            return render(request, "dashboards/not_found.html", {}, status_code=404)

        cards_result = await db.execute(
            select(DashboardCard)
            .where(DashboardCard.dashboard_id == dash.id)
            .order_by(DashboardCard.position)
        )
        cards = list(cards_result.scalars().all())

    # NO hydration — page shell returned immediately.
    # Browser fetches frozen cache via /api/d/{slug}.

    updated = dash.updated_at.strftime("%B %d, %Y") if dash.updated_at else ""
    shared_at = dash.shared_at.strftime("%B %d, %Y") if getattr(dash, "shared_at", None) else updated
    owner_name = getattr(dash, "owner_name", None) or getattr(dash, "owner_email", None) or ""
    is_owner = uid is not None and str(dash.user_id) == uid

    user_view = await _load_user_view_from_uid(uid)

    return render(
        request,
        "dashboards/public.html",
        {
            "dash": {
                "title": dash.title,
                "description": dash.description or "",
                "is_public": dash.is_public,
                "owner_name": owner_name,
                "shared_at": shared_at,
                "updated_at_fmt": updated,
                "card_count": len(cards),
            },
            "cards": [],  # empty — loaded async via /api/d/{slug} (frozen cache)
            "slug": slug,
            "is_owner": is_owner,
            "user": user_view,
        },
    )


@router.get("/api/d/{slug}")
async def public_dashboard_json(slug: str, request: Request):
    """Frozen dashboard data as JSON — returns cached results, no live hydration.
    Public dashboards open to all; private ones owner-only."""
    uid = get_uid_from_request(request)

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()
        if not dash:
            return JSONResponse({"error": "Not found"}, status_code=404)
        if not dash.is_public and (not uid or str(dash.user_id) != uid):
            return JSONResponse({"error": "Not found"}, status_code=404)

        cards_result = await db.execute(
            select(DashboardCard)
            .where(DashboardCard.dashboard_id == dash.id)
            .order_by(DashboardCard.position)
        )
        cards = list(cards_result.scalars().all())

    # Return cards in the same shape the frontend renderCard() expects
    payload_cards = []
    for c in cards:
        snap = c.result_cache or {}
        payload_cards.append(
            {
                "id": str(c.id),
                "title": c.title,
                "platform": c.platform or "",
                "card_type": snap.get("card_type", "UNKNOWN"),
                "is_live": False,
                "refreshed_at": c.refreshed_at.isoformat() if c.refreshed_at else None,
                "snap": snap,
            }
        )

    return JSONResponse(
        {
            "dashboard": {
                "title": dash.title,
                "slug": dash.share_slug,
                "card_count": len(payload_cards),
            },
            "cards": payload_cards,
        }
    )


# ---------------------------------------------------------------------------
# Authenticated JSON API
# ---------------------------------------------------------------------------


async def _user_in_project(db, project_id, user_uuid) -> bool:
    """Project-isolation gate: True iff the user is an active member of the
    dashboard's project.

    Owning a dashboard (``user_id`` match) is not sufficient — a user removed
    from a project must lose access to its dashboards. Legacy dashboards with no
    project (``project_id IS NULL``) fall back to owner-only access, which the
    caller's ``user_id`` filter already enforces.
    """
    if project_id is None:
        return True
    result = await db.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_uuid,
            ProjectMember.is_active == True,
        )
    )
    return result.scalar_one_or_none() is not None


@router.delete("/api/saved-dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, request: Request):
    uid = get_uid_from_request(request)
    if not uid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    dash_uuid = safe_uuid(dashboard_id)
    user_uuid = safe_uuid(uid)
    if dash_uuid is None or user_uuid is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(
                Dashboard.id == dash_uuid,
                Dashboard.user_id == user_uuid,
            )
        )
        dash = result.scalar_one_or_none()
        if not dash or not await _user_in_project(db, dash.project_id, user_uuid):
            return JSONResponse({"error": "Not found"}, status_code=404)
        await db.execute(sa_delete(Dashboard).where(Dashboard.id == dash.id))
        await db.commit()

    return JSONResponse({"success": True})


@router.patch("/api/saved-dashboards/{dashboard_id}/share")
async def toggle_share(dashboard_id: str, request: Request):
    uid = get_uid_from_request(request)
    if not uid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    dash_uuid = safe_uuid(dashboard_id)
    user_uuid = safe_uuid(uid)
    if dash_uuid is None or user_uuid is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    body = await request.json()
    is_public = body.get("is_public", True)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(
                Dashboard.id == dash_uuid,
                Dashboard.user_id == user_uuid,
            )
        )
        dash = result.scalar_one_or_none()
        if not dash or not await _user_in_project(db, dash.project_id, user_uuid):
            return JSONResponse({"error": "Not found"}, status_code=404)
        base = settings.APP_BASE_URL
        computed_url = f"{base}/d/{dash.share_slug}"
        dash.is_public = is_public
        dash.updated_at = datetime.utcnow()
        if is_public:
            dash.share_url = computed_url
            dash.shared_at = datetime.utcnow()
        dash_title = dash.title or "Dashboard"
        await db.commit()

    # Notification for sharing toggle
    import asyncio

    from app.notifications import create_notification

    if is_public:
        asyncio.create_task(
            create_notification(
                user_id=uid,
                title="Dashboard Shared",
                message=f'"{dash_title}" is now publicly accessible via its share link.',
                category="dashboard",
                severity="info",
                action_url=computed_url,
            )
        )

    return JSONResponse(
        {
            "success": True,
            "is_public": is_public,
            "share_url": computed_url if is_public else None,
        }
    )


# ---------------------------------------------------------------------------
# Query-scope management — UI + JSON APIs for the scopes page
# ---------------------------------------------------------------------------
#
# Dashboards gate live-refresh traffic against ``query_scopes``. Keep these
# in sync with what the card specs actually query, or the dashboard's
# iframe will start returning 403s on refresh. The owner-only endpoints
# below power ``/live-dashboards/{slug}/scopes``.


# Per-platform resource key used in the scope fingerprint. Ordering of this
# map is preserved so the UI can display platforms in a consistent order.
_SCOPE_PLATFORM_RESOURCE_KEY = {
    "ga4": "property_id",
    "bigquery": "connection_id",
    "redshift": "connection_id",
    "snowflake": "connection_id",
    "warehouse": "connection_id",
    "amplitude": "connection_id",
    "adobe_analytics": "connection_id",
    "meta": "ad_account_id",
    "tiktok": "advertiser_id",
    "snap": "ad_account_id",
    "google_ads": "customer_id",
    "search_console": "site_url",
    "gtm": "container_id",
}


async def _load_dash_for_owner(dashboard_id: str, uid: str) -> Dashboard | None:
    dash_uuid = safe_uuid(dashboard_id)
    user_uuid = safe_uuid(uid)
    if dash_uuid is None or user_uuid is None:
        return None
    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(
                Dashboard.id == dash_uuid,
                Dashboard.user_id == user_uuid,
            )
        )
        dash = result.scalar_one_or_none()
        if dash is None or not await _user_in_project(db, dash.project_id, user_uuid):
            return None
        return dash


async def _available_resources_for_project(project_id) -> list[dict]:
    """Enumerate connected resources for the dashboard's project.

    Each entry is ``{platform, label, resource_key, resource_value}`` —
    enough for the UI to render a pick list and, on selection, build a
    scope entry of the right shape.
    """
    from app.models.connection import OAuthConnection
    from app.models.credential_connection import AmplitudeConnection
    from app.models.token import GA4Property, GoogleAdsAccount, SearchConsoleSite

    out: list[dict] = []

    async with app_state.db_session_factory() as db:
        # GA4 properties
        ga4_rows = await db.execute(
            select(GA4Property, OAuthConnection)
            .join(OAuthConnection, GA4Property.connection_id == OAuthConnection.id)
            .where(OAuthConnection.project_id == project_id, OAuthConnection.is_active.is_(True))
        )
        for prop, _ in ga4_rows.all():
            out.append(
                {
                    "platform": "ga4",
                    "label": f"{prop.display_name or prop.property_id} ({prop.property_id})",
                    "resource_key": "property_id",
                    "resource_value": prop.property_id,
                }
            )

        # Google Ads accounts
        ads_rows = await db.execute(
            select(GoogleAdsAccount, OAuthConnection)
            .join(OAuthConnection, GoogleAdsAccount.connection_id == OAuthConnection.id)
            .where(OAuthConnection.project_id == project_id, OAuthConnection.is_active.is_(True))
        )
        for acc, _ in ads_rows.all():
            out.append(
                {
                    "platform": "google_ads",
                    "label": f"{acc.descriptive_name or acc.customer_id} ({acc.customer_id})",
                    "resource_key": "customer_id",
                    "resource_value": acc.customer_id,
                }
            )

        # Search Console sites
        gsc_rows = await db.execute(
            select(SearchConsoleSite, OAuthConnection)
            .join(OAuthConnection, SearchConsoleSite.connection_id == OAuthConnection.id)
            .where(OAuthConnection.project_id == project_id, OAuthConnection.is_active.is_(True))
        )
        for site, _ in gsc_rows.all():
            out.append(
                {
                    "platform": "search_console",
                    "label": site.site_url,
                    "resource_key": "site_url",
                    "resource_value": site.site_url,
                }
            )

        # Amplitude — one entry per connection
        amp_rows = await db.execute(
            select(AmplitudeConnection).where(
                AmplitudeConnection.project_id == project_id,
                AmplitudeConnection.is_active.is_(True),
            )
        )
        for amp in amp_rows.scalars().all():
            out.append(
                {
                    "platform": "amplitude",
                    "label": amp.display_name or f"Amplitude ({amp.id})",
                    "resource_key": "connection_id",
                    "resource_value": str(amp.id),
                }
            )

    return out


@router.get("/api/saved-dashboards/{dashboard_id}/scopes")
async def get_dashboard_scopes(dashboard_id: str, request: Request):
    """Return the dashboard's current ``query_scopes`` and the set of
    connected resources available in its project for populating a picker."""
    uid = get_uid_from_request(request)
    if not uid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    dash = await _load_dash_for_owner(dashboard_id, uid)
    if not dash:
        return JSONResponse({"error": "Not found"}, status_code=404)

    available = await _available_resources_for_project(dash.project_id) if dash.project_id else []

    return JSONResponse(
        {
            "dashboard_id": dashboard_id,
            "slug": dash.share_slug,
            "query_scopes": dash.query_scopes or [],
            "query_token_required": dash.query_token_required,
            "available_resources": available,
            "platform_resource_keys": _SCOPE_PLATFORM_RESOURCE_KEY,
        }
    )


@router.put("/api/saved-dashboards/{dashboard_id}/scopes")
async def put_dashboard_scopes(dashboard_id: str, request: Request):
    """Replace the dashboard's ``query_scopes`` with the provided list.

    Body: ``{"scopes": [{"platform": "ga4", "property_id": "..."}, ...]}``.
    """
    uid = get_uid_from_request(request)
    if not uid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    dash_uuid = safe_uuid(dashboard_id)
    user_uuid = safe_uuid(uid)
    if dash_uuid is None or user_uuid is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    body = await request.json()
    scopes_in = body.get("scopes")
    if not isinstance(scopes_in, list):
        return JSONResponse({"error": "scopes must be a list"}, status_code=400)

    # Normalize + validate each entry — platform is required; any extra
    # keys act as resource filters. Drop empty-string values.
    cleaned: list[dict] = []
    for i, entry in enumerate(scopes_in):
        if not isinstance(entry, dict):
            return JSONResponse({"error": f"scopes[{i}] must be an object"}, status_code=400)
        platform = entry.get("platform")
        if not isinstance(platform, str) or not platform.strip():
            return JSONResponse(
                {"error": f"scopes[{i}].platform must be a non-empty string"}, status_code=400
            )
        out = {"platform": platform.strip()}
        for k, v in entry.items():
            if k == "platform" or v in (None, ""):
                continue
            out[k] = v
        cleaned.append(out)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(
                Dashboard.id == dash_uuid,
                Dashboard.user_id == user_uuid,
            )
        )
        dash = result.scalar_one_or_none()
        if not dash or not await _user_in_project(db, dash.project_id, user_uuid):
            return JSONResponse({"error": "Not found"}, status_code=404)
        dash.query_scopes = cleaned
        dash.updated_at = datetime.utcnow()
        await db.commit()

    return JSONResponse({"success": True, "query_scopes": cleaned})


@router.get("/live-dashboards/{slug}/scopes", response_class=HTMLResponse)
async def dashboard_scopes_page(slug: str, request: Request):
    """Owner-only UI for editing a dashboard's query_scopes."""
    uid = get_uid_from_request(request)
    if not uid:
        # URL-encode the slug into ``next=`` — an unencoded slug containing
        # ``?`` or ``#`` would let an attacker craft a path that smuggles
        # a different ``next`` target past the sanitizer.
        from urllib.parse import quote

        next_target = quote(f"/live-dashboards/{slug}/scopes", safe="/")
        return RedirectResponse(url=f"/signin?next={next_target}", status_code=303)

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()
        if not dash:
            return render(request, "dashboards/not_found.html", {}, status_code=404)
        user_uuid = safe_uuid(uid)
        if (
            user_uuid is None
            or str(dash.user_id) != uid
            or not await _user_in_project(db, dash.project_id, user_uuid)
        ):
            return render(request, "forbidden.html", {}, status_code=403)

    user_view = await _load_user_view_from_uid(uid)
    return render(
        request,
        "dashboards/scopes.html",
        {
            "user": user_view,
            "dashboard": {
                "id": str(dash.id),
                "slug": dash.share_slug,
                "title": dash.title,
            },
        },
    )


@router.get("/live-dashboards", response_class=HTMLResponse)
async def live_dashboards_hub(request: Request):
    """Live dashboards hub — lists the user's dashboards rendered natively."""
    uid = get_uid_from_request(request)
    if not uid:
        return RedirectResponse("/signin?next=/live-dashboards", status_code=302)
    active_project_id = request.cookies.get("active_project_id")
    deployed = []

    async with app_state.db_session_factory() as db:
        q = select(Dashboard).order_by(Dashboard.updated_at.desc())
        q = q.where(Dashboard.user_id == uuid.UUID(uid))
        if active_project_id:
            try:
                q = q.where(Dashboard.project_id == uuid.UUID(active_project_id))
            except ValueError:
                pass
        result = await db.execute(q)
        dashboards = result.scalars().all()

        for d in dashboards:
            cards_q = await db.execute(select(DashboardCard).where(DashboardCard.dashboard_id == d.id))
            card_count = len(list(cards_q.scalars().all()))
            deployed.append(
                {
                    "id": str(d.id),
                    "slug": d.share_slug,
                    "name": d.title,
                    "description": d.description or "",
                    "is_public": d.is_public,
                    "card_count": card_count,
                    "updated_at": d.updated_at,
                }
            )

    # Check if user has any active connections (for empty-state messaging)
    has_connections = False
    if uid:
        async with app_state.db_session_factory() as db2:
            conn_result = await db2.execute(
                select(OAuthConnection.id)
                .where(
                    OAuthConnection.user_id == uuid.UUID(uid),
                    OAuthConnection.is_active == True,
                )
                .limit(1)
            )
            has_connections = conn_result.scalar_one_or_none() is not None

    user_view = await _load_user_view_from_uid(uid)
    return render(
        request,
        "dashboards/live_hub.html",
        {
            "deployed_dashboards": deployed,
            "has_connections": has_connections,
            "user": user_view,
        },
    )


@router.get("/live-dashboards/{page_slug}", response_class=HTMLResponse)
async def live_dashboard_page(page_slug: str, request: Request):
    """Live dashboard view — returns a lightweight shell immediately.
    Card data is loaded client-side via /api/saved-dashboards/{slug}/data so the
    user sees a loading screen instead of a blank browser tab while BigQuery
    queries execute."""
    uid = get_uid_from_request(request)
    start_date = request.query_params.get("start") or ""
    end_date = request.query_params.get("end") or ""

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == page_slug))
        dash = result.scalar_one_or_none()
        if not dash:
            return render(request, "dashboards/not_found.html", {}, status_code=404)
        # Public dashboards are shareable by design. Private dashboards require
        # the owner to still be an active member of the dashboard's project.
        if not dash.is_public:
            user_uuid = safe_uuid(uid) if uid else None
            if (
                user_uuid is None
                or str(dash.user_id) != uid
                or not await _user_in_project(db, dash.project_id, user_uuid)
            ):
                return render(request, "dashboards/not_found.html", {}, status_code=404)

        # Only fetch lightweight card metadata for the filter bar — NO hydration
        cards_result = await db.execute(
            select(DashboardCard)
            .where(DashboardCard.dashboard_id == dash.id)
            .order_by(DashboardCard.position)
        )
        cards = list(cards_result.scalars().all())

    platforms_in_dash = sorted({(c.platform or "").lower() for c in cards if c.platform})

    # Collect dimension filters from all cards' filter_hooks and filter_options.
    # Date keys are excluded — only dimension keys (country, device_type, etc.).
    _DATE_HOOK_KEYS = {"date_range.start", "date_range.end"}
    _dim_keys_seen: dict[str, dict] = {}  # key → {label, options}
    for c in cards:
        qp = c.query_params or {}
        hooks = qp.get("filter_hooks") or {}
        options_map = qp.get("filter_options") or {}
        for hook_key in hooks:
            if hook_key in _DATE_HOOK_KEYS:
                continue
            if hook_key not in _dim_keys_seen:
                label = hook_key.replace("_", " ").title()
                _dim_keys_seen[hook_key] = {
                    "key": hook_key,
                    "label": label,
                    "options": options_map.get(hook_key) or [],
                }
            elif options_map.get(hook_key):
                existing = _dim_keys_seen[hook_key]["options"]
                for opt in options_map[hook_key]:
                    if opt not in existing:
                        existing.append(opt)

    dimension_filters = list(_dim_keys_seen.values())

    # Unified filter model: the dashboard's typed filters if declared, else a
    # render-only synthesis from legacy per-card filter_hooks/filter_options.
    from app.dashboards.filter_specs import synthesize_filters

    dashboard_filters = dash.filters or synthesize_filters(
        [{"query_params": c.query_params or {}} for c in cards]
    )

    user_view = await _load_user_view_from_uid(uid)
    is_owner = uid is not None and str(dash.user_id) == uid
    has_cards = len(cards) > 0

    return render(
        request,
        "dashboards/live_view.html",
        {
            "dash": {
                "id": str(dash.id),
                "title": dash.title,
                "description": dash.description or "",
                "card_count": len(cards),
                "project_id": str(dash.project_id) if dash.project_id else None,
            },
            "cards": [],
            "current_start": start_date,
            "current_end": end_date,
            "platforms": platforms_in_dash,
            "dimension_filters": dimension_filters,
            "filters": dashboard_filters,
            "filter_presets": dash.filter_presets or [],
            "slug": page_slug,
            "is_owner": is_owner,
            "has_cards": has_cards,
            "user": user_view,
            # Print mode: the headless-Chromium PDF renderer loads this same view
            # with ?print=1 to hide interactive chrome (nav, toolbar, footer) and
            # lay the cards out for paper. Auth is unchanged — ?print=1 only
            # affects presentation.
            "print_mode": (request.query_params.get("print") or "") in ("1", "true", "yes"),
        },
    )


# ---------------------------------------------------------------------------
# Live data refresh API — JSON endpoint the frontend hits when filters change
# ---------------------------------------------------------------------------


# Snapshot flattening + scorecard metric derivation live in the shared
# ``app.dashboards.snapshot`` module (imported as ``_normalize_snap`` at the top
# of this file) so the live web view, PDF export, and Slack/email reports all
# normalize identically.


@router.get("/api/saved-dashboards/{slug}/data")
async def live_dashboard_data(slug: str, request: Request):
    """Return re-executed card data for the given dashboard with optional
    date-range + platform filters applied.

    Dispatches each card through the MCP tool registry using a synthetic
    refresh context (same pattern as /api/dashboard-query/{slug}/batch).

    Query params:
      start     — ISO date (YYYY-MM-DD) — lower bound
      end       — ISO date (YYYY-MM-DD) — upper bound
      platforms — comma-separated list (e.g. "ga4,meta") — filter cards
    """

    from app.auth.mcp_session_manager import build_refresh_context
    from app.dashboards.filter_hooks import apply_overrides
    from app.dashboards.filter_translators import apply_card_filters

    uid = get_uid_from_request(request)
    start_date = _resolve_relative_date(request.query_params.get("date_range_start") or "")
    end_date = _resolve_relative_date(request.query_params.get("date_range_end") or "")
    platforms_filter = request.query_params.get("platforms") or ""
    platforms_allowed = {p.strip().lower() for p in platforms_filter.split(",") if p.strip()}
    # ?refresh=1 (the Refresh button) bypasses the cache and re-queries upstream.
    force_refresh = (request.query_params.get("refresh") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    # Build overrides dict passed to apply_overrides().
    # Reserved params (date_range_start/end, platforms, refresh) are handled
    # explicitly. Any other query param (e.g. ?country=US&device=mobile) is
    # forwarded as a flat dimension override so filter_hooks can map them to
    # card params.
    _RESERVED_PARAMS = {"date_range_start", "date_range_end", "platforms", "refresh"}
    filter_overrides: dict = {}
    if start_date or end_date:
        filter_overrides["date_range"] = {}
        if start_date:
            filter_overrides["date_range"]["start"] = start_date
        if end_date:
            filter_overrides["date_range"]["end"] = end_date
    for _qk, _qv in request.query_params.items():
        if _qk not in _RESERVED_PARAMS and _qv:
            filter_overrides[_qk] = _qv

    # Compare mode: a second date range. ?compare=previous_period|previous_year|custom
    # (custom uses ?compare_start=&compare_end=). Each card is then executed twice and
    # merged. The compare params live in filter_overrides above, so the cache key folds
    # them in automatically (compare views cache separately from non-compare views).
    compare_mode = (request.query_params.get("compare") or "").strip()
    compare_active = bool(compare_mode and start_date and end_date)
    cmp_start = cmp_end = ""
    if compare_active:
        if compare_mode == "custom":
            cmp_start = _resolve_relative_date(request.query_params.get("compare_start") or "")
            cmp_end = _resolve_relative_date(request.query_params.get("compare_end") or "")
            compare_active = bool(cmp_start and cmp_end)
        else:
            from app.dashboards.compare import previous_range

            try:
                cmp_start, cmp_end = previous_range(start_date, end_date, compare_mode)
            except ValueError:
                compare_active = False

    try:
        async with app_state.db_session_factory() as db:
            result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
            dash = result.scalar_one_or_none()
            if not dash:
                return JSONResponse({"error": "Not found"}, status_code=404)
            # Public dashboards refresh for anyone (shareable by design). Private
            # dashboards require the owner to still be an active project member.
            if not dash.is_public:
                user_uuid = safe_uuid(uid) if uid else None
                if (
                    user_uuid is None
                    or str(dash.user_id) != uid
                    or not await _user_in_project(db, dash.project_id, user_uuid)
                ):
                    return JSONResponse({"error": "Not found"}, status_code=404)

            cards_result = await db.execute(
                select(DashboardCard)
                .where(DashboardCard.dashboard_id == dash.id)
                .order_by(DashboardCard.position)
            )
            cards = list(cards_result.scalars().all())

        # Filter by platform before re-executing to avoid wasted work
        if platforms_allowed:
            cards = [c for c in cards if (c.platform or "").lower() in platforms_allowed]

        is_owner = uid is not None and str(dash.user_id) == uid

        # Typed dashboard-level filters (the six widget types). Keyed by filter key.
        # Cards consume them via their per-card filter_hooks. Date filters stay on
        # the apply_overrides path; typed dimension/metric filters are routed through
        # the per-platform translation layer (so they become real query syntax).
        dash_filter_specs = {f["key"]: f for f in (getattr(dash, "filters", None) or []) if f.get("key")}
        typed_keys = set(dash_filter_specs)
        # Values handed to apply_overrides exclude typed keys (translate() owns them),
        # but keep date_range and any legacy keys not declared as typed filters.
        overrides_for_apply = {
            k: v for k, v in filter_overrides.items() if k == "date_range" or k not in typed_keys
        }

        # ── Serve from cache unless this is an explicit Refresh ────────────
        # Keyed by slug + viewer-role + dashboard version + filters, so a plain
        # page reload returns instantly without re-querying upstream APIs.
        cache_key = _dashdata_cache_key(
            slug,
            is_owner,
            len(cards),
            str(getattr(dash, "updated_at", "") or ""),
            filter_overrides,
            platforms_allowed,
        )
        if not force_refresh:
            cached_body = await _dashdata_cache_get(cache_key)
            if cached_body is not None:
                cached_body["cached"] = True
                return JSONResponse(cached_body)

        # Build a synthetic MCP context for the dashboard owner so the tool
        # registry can resolve connections without an active MCP session.
        from app.main import mcp_server

        refresh_ctx = await build_refresh_context(str(dash.id))
        tm = mcp_server._tool_manager if mcp_server else None

        _sem = asyncio.Semaphore(_LIVE_CARD_CONCURRENCY)

        async def _run_card_once(c, ov_for_apply) -> tuple[dict, bool, dict | None]:
            """Execute one card for a single date range (``ov_for_apply`` carries the
            date_range + legacy overrides). Returns (snap, is_live, live_error).
            Never raises — falls back to the cached snapshot on any failure."""
            spec = c.query_params or {}
            tool_name = spec.get("tool") or c.tool_name
            platform = spec.get("platform") or c.platform or "unknown"
            action = spec.get("action")

            if not tool_name or tm is None:
                snap = c.result_cache if isinstance(c.result_cache, dict) else {}
                return snap, False, {"error_type": "no_tool", "message": "Card has no registered tool."}

            try:
                legacy = getattr(tm, "_legacy_tools", {})
                tool = legacy.get(tool_name) or tm._tools.get(tool_name)
                if tool is None:
                    raise ValueError(f"Tool '{tool_name}' not registered")

                # Merge date + legacy overrides (respecting the date_locked flag).
                # Typed filters are excluded here and applied via translate() below.
                card_date_locked = _as_bool(spec.get("date_locked"))
                merged_spec = apply_overrides(spec, ov_for_apply if not card_date_locked else None)
                # Params are stored flattened in query_params — exclude spec metadata keys.
                # NOTE: "platform" is intentionally NOT excluded — it is a required named
                # parameter for analytics_read, marketing_read, etc.
                _META_KEYS = {"key", "tool", "filter_hooks", "filter_options", "date_locked"}
                call_args: dict = {k: v for k, v in merged_spec.items() if k not in _META_KEYS}
                if action is not None:
                    call_args["action"] = action
                # warehouse_query needs 'engine' (= platform) and uses 'query' not 'sql'
                is_warehouse = tool_name == "warehouse_query"
                if is_warehouse:
                    call_args.setdefault("engine", platform)
                    if "sql" in call_args and "query" not in call_args:
                        call_args["query"] = call_args.pop("sql")

                # Apply typed dashboard filters (GA4 dimension/metric_filter,
                # warehouse {placeholder} substitution, marketing params). Runs
                # before the generic date substitution so typed SQL tokens get
                # properly quoted/escaped values rather than a raw card default.
                if not card_date_locked and dash_filter_specs:
                    apply_card_filters(
                        spec.get("filter_hooks"),
                        dash_filter_specs,
                        filter_overrides,
                        "warehouse" if is_warehouse else platform,
                        call_args,
                    )

                # Substitute remaining {placeholder} tokens in the SQL template
                # (date ranges, card-param defaults). Targeted str.replace (not
                # format_map) so other curly-brace patterns in the SQL don't raise
                # KeyError and silently swallow all substitutions.
                if is_warehouse and "query" in call_args:
                    q = call_args["query"]
                    for _k, _v in call_args.items():
                        if _k == "query" or not isinstance(_v, str):
                            continue
                        resolved = _resolve_relative_date(_v)
                        q = q.replace("{" + _k + "}", resolved)
                    call_args["query"] = q

                async with _sem:
                    raw_result = await asyncio.wait_for(tool.run(call_args), timeout=_LIVE_CARD_TIMEOUT_S)
                if not isinstance(raw_result, dict):
                    raw_result = {"card_type": "UNKNOWN", "raw": raw_result}

                if raw_result.get("card_type") == "ERROR" or raw_result.get("error"):
                    snap = c.result_cache if isinstance(c.result_cache, dict) else raw_result
                    return (
                        snap,
                        False,
                        {
                            "error_type": raw_result.get("error_type", "tool_error"),
                            "message": raw_result.get("message", str(raw_result.get("error", ""))),
                        },
                    )
                return _normalize_snap(raw_result, c.chart_type, c.chart_config), True, None
            except TimeoutError:
                logger.warning("live_dashboard_data: card %s timed out after %ss", c.id, _LIVE_CARD_TIMEOUT_S)
                snap = c.result_cache if isinstance(c.result_cache, dict) else {}
                return (
                    snap,
                    False,
                    {
                        "error_type": "timeout",
                        "message": f"Query exceeded {_LIVE_CARD_TIMEOUT_S}s; showing last cached result.",
                    },
                )
            except Exception as tool_exc:
                logger.warning("live_dashboard_data: tool dispatch failed for card %s: %s", c.id, tool_exc)
                snap = c.result_cache if isinstance(c.result_cache, dict) else {}
                return snap, False, {"error_type": "dispatch_error", "message": str(tool_exc)[:300]}

        async def _exec_card(c) -> dict:
            """Refresh a single card (twice when compare is on) into a payload.
            Never raises — one bad card can't blank the dashboard."""
            platform = (c.query_params or {}).get("platform") or c.platform or "unknown"
            snap, is_live, live_error = await _run_card_once(c, overrides_for_apply)

            # Compare mode: re-execute for the comparison range and merge.
            if compare_active and is_live and not _as_bool((c.query_params or {}).get("date_locked")):
                from app.dashboards.compare import merge_compare

                cmp_ov = dict(overrides_for_apply)
                cmp_ov["date_range"] = {"start": cmp_start, "end": cmp_end}
                prev_snap, prev_live, _prev_err = await _run_card_once(c, cmp_ov)
                if prev_live:
                    snap = merge_compare(snap, prev_snap, c.chart_type)

            snap["chart_type"] = c.chart_type
            snap["chart_config"] = c.chart_config or {}
            card_payload = {
                "id": str(c.id),
                "title": c.title,
                "platform": platform,
                "chart_type": c.chart_type,
                "chart_config": c.chart_config or {},
                "card_type": snap.get("card_type", "UNKNOWN"),
                "is_live": is_live,
                "refreshed_at": c.refreshed_at.isoformat() if c.refreshed_at else None,
                "snap": snap,
            }
            if is_owner:
                card_payload["live_error"] = live_error
            return card_payload

        async with refresh_ctx:
            # Cards run concurrently (bounded by the semaphore) so a slow card
            # no longer blocks the ones behind it. gather preserves card order.
            payload_cards = list(await asyncio.gather(*[_exec_card(c) for c in cards]))

    except Exception as exc:
        logger.exception("live_dashboard_data failed for slug=%s", slug)
        return JSONResponse(
            {"error": "hydrate_failed", "message": str(exc)[:300], "cards": []},
            status_code=500,
        )

    body = {
        "dashboard": {
            "title": dash.title,
            "slug": slug,
            "card_count": len(payload_cards),
        },
        "cards": payload_cards,
        "filters": {
            "start": start_date,
            "end": end_date,
            "platforms": sorted(platforms_allowed) if platforms_allowed else [],
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "cached": False,
    }
    # Compare mode: surface the biggest movers as a one-line insight banner.
    if compare_active:
        from app.dashboards.insights import biggest_movers

        movers = biggest_movers(payload_cards)
        if movers:
            body["insights"] = movers
        body["compare"] = {"start": cmp_start, "end": cmp_end, "mode": compare_mode}
    # Store for subsequent reloads (also repopulates after an explicit Refresh).
    await _dashdata_cache_set(cache_key, body)
    return JSONResponse(body)


# ---------------------------------------------------------------------------
# Share PDF — on-demand PDF export of a live dashboard
# ---------------------------------------------------------------------------
#
# No data is persisted: a headless Chromium renders the live dashboard view
# (charts included) to PDF and we stream the bytes straight to the caller.
# Available to any viewer who can see the dashboard (public dashboards →
# anyone; private → owner only). Not plan-gated.


@router.get("/saved-dashboards/{slug}/pdf")
async def live_dashboard_pdf(slug: str, request: Request):
    """Generate and stream a PDF of the given dashboard.

    Query params:
      start / date_range_start — ISO date (YYYY-MM-DD) — lower bound
      end   / date_range_end   — ISO date (YYYY-MM-DD) — upper bound

    Returns a 200 with application/pdf bytes, 404 if not visible, 503 if the
    renderer is unavailable (Chromium missing), or 500 if rendering fails.
    """
    uid = get_uid_from_request(request)
    # The Share PDF button reuses the live view's buildParams(), which emits
    # date_range_start/date_range_end; accept those as well as the short
    # start/end form so the export honours the selected range.
    qp = request.query_params
    start_date = qp.get("start") or qp.get("date_range_start") or ""
    end_date = qp.get("end") or qp.get("date_range_end") or ""
    platforms_filter = qp.get("platforms") or ""
    platforms_allowed = [p.strip().lower() for p in platforms_filter.split(",") if p.strip()]

    filter_params: dict = {}
    if start_date:
        filter_params["start_date"] = start_date
    if end_date:
        filter_params["end_date"] = end_date
    if platforms_allowed:
        filter_params["platforms"] = platforms_allowed

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()
        if not dash:
            return render(request, "dashboards/not_found.html", {}, status_code=404)
        if not dash.is_public and (not uid or str(dash.user_id) != uid):
            return render(request, "dashboards/not_found.html", {}, status_code=404)

        try:
            from app.dashboards.pdf_renderer import render_dashboard_pdf

            result_pdf = await render_dashboard_pdf(
                db,
                dash,
                filter_params=filter_params,
                include_insights=True,
                base_url=base_url_from_request(request),
                cookies=dict(request.cookies),
            )
        except RuntimeError as exc:
            # Renderer unavailable (Chromium missing) or the render failed.
            # Surface a structured error so the frontend can show a toast
            # rather than dumping a stack trace.
            logger.error("PDF render failed (runtime): %s", exc)
            return JSONResponse(
                {"error": "pdf_unavailable", "message": str(exc)},
                status_code=503,
            )
        except Exception as exc:
            logger.exception("PDF render failed for slug=%s", slug)
            return JSONResponse(
                {"error": "pdf_failed", "message": str(exc)[:300]},
                status_code=500,
            )

    # Filename: "<title>-YYYY-MM-DD.pdf", stripped of unsafe chars so
    # Content-Disposition doesn't need quoting gymnastics.
    import re

    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", (dash.title or "dashboard")).strip("_")
    if not safe_title:
        safe_title = "dashboard"
    filename = f"{safe_title}-{result_pdf.generated_at.strftime('%Y-%m-%d')}.pdf"

    return Response(
        content=result_pdf.pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-PDF-Cards": str(result_pdf.card_count),
            "X-PDF-Live-Cards": str(result_pdf.live_card_count),
        },
    )
