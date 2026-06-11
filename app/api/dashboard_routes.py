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
            "filter_presets": dash.filter_presets or [],
            "slug": page_slug,
            "is_owner": is_owner,
            "has_cards": has_cards,
            "user": user_view,
        },
    )


# ---------------------------------------------------------------------------
# Live data refresh API — JSON endpoint the frontend hits when filters change
# ---------------------------------------------------------------------------


def _normalize_snap(snap: dict, chart_type: str | None) -> dict:
    """Normalize a raw tool result into the flat format the card renderer expects.

    GA4 `run_report` returns::

        {
          "dimension_headers": ["date"],
          "metric_headers": ["sessions"],
          "rows": [{"dimensions": ["20240101"], "metrics": ["1234"]}]
        }

    The card renderer expects flat rows with named keys::

        {
          "columns": ["date", "sessions"],
          "rows": [{"date": "20240101", "sessions": "1234"}]
        }

    If the snap already has a ``columns`` key or flat rows, it is returned as-is.
    """
    if not isinstance(snap, dict):
        return snap

    dim_headers = snap.get("dimension_headers") or []
    met_headers = snap.get("metric_headers") or []
    raw_rows = snap.get("rows") or []

    # Only transform when rows come in the nested GA4 format
    if (dim_headers or met_headers) and "columns" not in snap and isinstance(raw_rows, list):
        columns = list(dim_headers) + list(met_headers)
        flat_rows = []
        for r in raw_rows:
            if isinstance(r, dict) and ("dimensions" in r or "metrics" in r):
                row: dict = {}
                for i, col in enumerate(dim_headers):
                    row[col] = (r.get("dimensions") or [])[i] if i < len(r.get("dimensions") or []) else None
                for i, col in enumerate(met_headers):
                    row[col] = (r.get("metrics") or [])[i] if i < len(r.get("metrics") or []) else None
                flat_rows.append(row)
            else:
                # Already flat — leave as-is
                flat_rows.append(r)
        snap = dict(snap)  # shallow copy to avoid mutating original
        snap["columns"] = columns
        snap["rows"] = flat_rows

    return snap


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

    uid = get_uid_from_request(request)
    start_date = _resolve_relative_date(request.query_params.get("date_range_start") or "")
    end_date = _resolve_relative_date(request.query_params.get("date_range_end") or "")
    platforms_filter = request.query_params.get("platforms") or ""
    platforms_allowed = {p.strip().lower() for p in platforms_filter.split(",") if p.strip()}

    # Build overrides dict passed to apply_overrides().
    # Reserved params (date_range_start/end, platforms) are handled explicitly.
    # Any other query param (e.g. ?country=US&device=mobile) is forwarded as a
    # flat dimension override so filter_hooks can map them to card params.
    _RESERVED_PARAMS = {"date_range_start", "date_range_end", "platforms"}
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

        # Build a synthetic MCP context for the dashboard owner so the tool
        # registry can resolve connections without an active MCP session.
        from app.main import mcp_server

        refresh_ctx = await build_refresh_context(str(dash.id))
        tm = mcp_server._tool_manager if mcp_server else None

        is_owner = uid is not None and str(dash.user_id) == uid
        _sem = asyncio.Semaphore(_LIVE_CARD_CONCURRENCY)

        async def _exec_card(c) -> dict:
            """Refresh a single card. Always returns a renderable payload —
            never raises — falling back to the cached snapshot on any error or
            timeout so one bad card can't blank the dashboard."""
            spec = c.query_params or {}
            tool_name = spec.get("tool") or c.tool_name
            platform = spec.get("platform") or c.platform or "unknown"
            action = spec.get("action")

            snap: dict = {}
            is_live = False
            live_error = None

            if not tool_name or tm is None:
                snap = c.result_cache or {}
                if not isinstance(snap, dict):
                    snap = {"card_type": "UNKNOWN", "raw": snap}
                live_error = {"error_type": "no_tool", "message": "Card has no registered tool."}
            else:
                try:
                    legacy = getattr(tm, "_legacy_tools", {})
                    tool = legacy.get(tool_name) or tm._tools.get(tool_name)
                    if tool is None:
                        raise ValueError(f"Tool '{tool_name}' not registered")

                    # Merge date overrides (respecting the date_locked flag)
                    card_date_locked = _as_bool(spec.get("date_locked"))
                    merged_spec = apply_overrides(spec, filter_overrides if not card_date_locked else None)
                    # Params are stored flattened in query_params — exclude spec metadata keys.
                    # NOTE: "platform" is intentionally NOT excluded — it is a required named
                    # parameter for analytics_read, marketing_read, etc.
                    _META_KEYS = {"key", "tool", "filter_hooks", "filter_options", "date_locked"}
                    call_args: dict = {k: v for k, v in merged_spec.items() if k not in _META_KEYS}
                    if action is not None:
                        call_args["action"] = action
                    # warehouse_query needs 'engine' (= platform) and uses 'query' not 'sql'
                    if tool_name == "warehouse_query":
                        call_args.setdefault("engine", platform)
                        if "sql" in call_args and "query" not in call_args:
                            call_args["query"] = call_args.pop("sql")
                        # Substitute {placeholder} tokens in the SQL template. Targeted
                        # str.replace (not format_map) so other curly-brace patterns in the
                        # SQL don't raise KeyError and silently swallow all substitutions.
                        if "query" in call_args:
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
                        snap = c.result_cache or raw_result
                        if not isinstance(snap, dict):
                            snap = {"card_type": "UNKNOWN", "raw": snap}
                        is_live = False
                        live_error = {
                            "error_type": raw_result.get("error_type", "tool_error"),
                            "message": raw_result.get("message", str(raw_result.get("error", ""))),
                        }
                    else:
                        snap = _normalize_snap(raw_result, c.chart_type)
                        is_live = True
                except TimeoutError:
                    logger.warning(
                        "live_dashboard_data: card %s timed out after %ss", c.id, _LIVE_CARD_TIMEOUT_S
                    )
                    snap = c.result_cache or {}
                    if not isinstance(snap, dict):
                        snap = {"card_type": "UNKNOWN", "raw": snap}
                    is_live = False
                    live_error = {
                        "error_type": "timeout",
                        "message": f"Query exceeded {_LIVE_CARD_TIMEOUT_S}s; showing last cached result.",
                    }
                except Exception as tool_exc:
                    logger.warning(
                        "live_dashboard_data: tool dispatch failed for card %s: %s", c.id, tool_exc
                    )
                    snap = c.result_cache or {}
                    if not isinstance(snap, dict):
                        snap = {"card_type": "UNKNOWN", "raw": snap}
                    is_live = False
                    live_error = {"error_type": "dispatch_error", "message": str(tool_exc)[:300]}

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

    return JSONResponse(
        {
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
        }
    )


# ---------------------------------------------------------------------------
# Share PDF — on-demand PDF export of a live dashboard
# ---------------------------------------------------------------------------
#
# No data is persisted: we hydrate the
# dashboard in memory, render a print-only HTML template, pipe it through
# WeasyPrint, and stream the bytes straight to the caller. Available to any
# viewer who can see the dashboard (public dashboards → anyone; private →
# owner only). Not plan-gated.


@router.get("/saved-dashboards/{slug}/pdf")
async def live_dashboard_pdf(slug: str, request: Request):
    """Generate and stream a PDF of the given dashboard.

    Query params:
      start     — ISO date (YYYY-MM-DD) — lower bound
      end       — ISO date (YYYY-MM-DD) — upper bound
      platforms — comma-separated list (e.g. "ga4,meta") — filter cards

    Returns a 200 with application/pdf bytes, 404 if not visible, or 500
    if rendering fails (usually: WeasyPrint shared libs missing).
    """
    uid = get_uid_from_request(request)
    start_date = request.query_params.get("start") or ""
    end_date = request.query_params.get("end") or ""
    platforms_filter = request.query_params.get("platforms") or ""
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
            # WeasyPrint missing / shared-lib dlopen failure. Surface a
            # structured error so the frontend can show a toast rather than
            # dumping a stack trace.
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
