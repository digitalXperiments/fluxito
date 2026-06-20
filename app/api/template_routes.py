"""
Template Library Routes

Browse, preview, and deploy pre-built dashboard templates.

  GET  /templates              — Template library page (HTML)
  GET  /templates/{slug}       — Template detail page (HTML)
  POST /api/templates/deploy   — Deploy a template (JSON)
"""

import logging
import secrets
import uuid
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select, update

import app.app_state as app_state
from app.auth.uid_cookie import get_uid_from_request
from app.models.dashboard import Dashboard, DashboardCard
from app.models.template import Template
from app.models.user import User
from app.templating import render

router = APIRouter()


async def _load_user_view(uid: str) -> dict | None:
    """Return {id, email, display_name, is_superadmin} for template context.

    ``is_superadmin`` gates the sidebar Admin link (base.html); omitting it
    made the link vanish on the templates page for super-admins.
    """
    try:
        user_uuid = uuid.UUID(uid)
    except ValueError:
        return None

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_uuid))
        user = result.scalar_one_or_none()
        if not user:
            return None
        return {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name or "",
            "is_superadmin": bool(user.is_superadmin),
        }


async def _get_connected_platforms(uid: str, project_id: str | None = None) -> list:
    """Get list of platform slugs connected to the active project (or user fallback)."""
    try:
        user_uuid = uuid.UUID(uid)
    except ValueError:
        return []

    from app.models.connection import OAuthConnection

    connected = []

    async with app_state.db_session_factory() as db:
        # Scope to active project when available
        stmt = select(OAuthConnection).where(
            OAuthConnection.is_active == True,
        )
        if project_id:
            try:
                stmt = stmt.where(OAuthConnection.project_id == uuid.UUID(project_id))
            except ValueError:
                stmt = stmt.where(OAuthConnection.user_id == user_uuid)
        else:
            stmt = stmt.where(OAuthConnection.user_id == user_uuid)

        result = await db.execute(stmt)
        connections = result.scalars().all()

        for conn in connections:
            provider = getattr(conn, "provider", "google")
            # ``conn.scopes`` is an ARRAY(String) — a list of full scope URLs.
            # Join into a single string so substring checks below work.
            scopes_str = " ".join(conn.scopes or [])

            if provider == "google":
                # Google suite — detect via scopes
                if "analytics" in scopes_str and "ga4" not in connected:
                    connected.append("ga4")
                if "tagmanager" in scopes_str and "gtm" not in connected:
                    connected.append("gtm")
                if "adwords" in scopes_str and "google_ads" not in connected:
                    connected.append("google_ads")
            elif provider == "meta" and "meta" not in connected:
                connected.append("meta")
            elif provider == "tiktok" and "tiktok" not in connected:
                connected.append("tiktok")
            elif provider == "snap" and "snap" not in connected:
                connected.append("snap")

        # Check BigQuery (separate model)
        try:
            from app.models.bq_connection import BQConnection

            bq_result = await db.execute(
                select(func.count())
                .select_from(BQConnection)
                .where(
                    BQConnection.user_id == user_uuid,
                    BQConnection.is_active == True,
                )
            )
            if bq_result.scalar() > 0:
                connected.append("bigquery")
        except Exception:
            pass

        # Check credential-based connections (Amplitude, Adobe, Redshift, Snowflake)
        try:
            from app.models.credential_connection import (
                AdobeConnection,
                AmplitudeConnection,
                MixpanelConnection,
                PostHogConnection,
                RedshiftConnection,
                SnowflakeConnection,
            )

            for ConnModel, slug in [
                (AmplitudeConnection, "amplitude"),
                (MixpanelConnection, "mixpanel"),
                (PostHogConnection, "posthog"),
                (RedshiftConnection, "redshift"),
                (SnowflakeConnection, "snowflake"),
            ]:
                cnt = await db.execute(
                    select(func.count())
                    .select_from(ConnModel)
                    .where(
                        ConnModel.user_id == user_uuid,
                        ConnModel.is_active == True,
                    )
                )
                if cnt.scalar() > 0 and slug not in connected:
                    connected.append(slug)

            # Adobe — check analytics and launch flags separately
            adobe_result = await db.execute(
                select(AdobeConnection).where(
                    AdobeConnection.user_id == user_uuid,
                    AdobeConnection.is_active == True,
                )
            )
            for ac in adobe_result.scalars().all():
                if getattr(ac, "has_analytics", False) and "adobe_analytics" not in connected:
                    connected.append("adobe_analytics")
                if getattr(ac, "has_launch", False) and "adobe_launch" not in connected:
                    connected.append("adobe_launch")
        except Exception:
            pass

    return connected


def _format_template(t: Template) -> dict:
    """Format a Template ORM object for display in templates."""
    return {
        "id": str(t.id),
        "slug": t.slug,
        "title": t.title,
        "description": t.description or "",
        "category": t.category,
        "icon": t.icon or "template",
        "type": t.template_type,
        "required_platforms": t.required_platforms or [],
        "min_tier": t.min_tier,
        "is_featured": t.is_featured,
        "use_count": t.use_count or 0,
        "step_count": len(t.steps or []),
        "steps": t.steps or [],
        "variables": t.variables or [],
    }


# ── Browse page ──────────────────────────────────────────────────────────────


@router.get("/templates", response_class=HTMLResponse)
async def templates_page(
    request: Request,
    category: str | None = None,
    mine: str | None = None,
):
    """Template library browse page with system/user split."""
    from app.api.project_routes import get_active_project_id

    uid = get_uid_from_request(request)
    if not uid:
        return RedirectResponse("/signin?next=/templates", status_code=302)

    user = await _load_user_view(uid) if uid else None
    active_pid = get_active_project_id(request) if uid else None
    show_mine = mine == "1" and active_pid is not None

    async with app_state.db_session_factory() as db:
        # Base query — active templates only
        query = select(Template).where(Template.is_active == True)

        if show_mine and active_pid:
            # Show only custom templates owned by the active project
            query = query.where(
                Template.project_id == uuid.UUID(active_pid),
                Template.template_type == "user",
            )
        else:
            # Show system templates (and shared templates in the future)
            query = query.where(
                Template.template_type.in_(["system", "shared"]),
            )

        if category:
            query = query.where(Template.category == category.lower())

        query = query.order_by(
            Template.is_featured.desc(),
            Template.use_count.desc(),
            Template.created_at.desc(),
        )
        result = await db.execute(query)
        templates = result.scalars().all()

        # Get counts for the toggle tabs
        sys_count_result = await db.execute(
            select(func.count())
            .select_from(Template)
            .where(
                Template.is_active == True,
                Template.template_type.in_(["system", "shared"]),
            )
        )
        system_count = sys_count_result.scalar() or 0

        user_count = 0
        if active_pid:
            user_count_result = await db.execute(
                select(func.count())
                .select_from(Template)
                .where(
                    Template.is_active == True,
                    Template.project_id == uuid.UUID(active_pid),
                    Template.template_type == "user",
                )
            )
            user_count = user_count_result.scalar() or 0

    # Categories for filter (from visible templates)
    categories = sorted(set(t.category for t in templates if t.category))

    items = [_format_template(t) for t in templates]

    return render(
        request,
        "templates.html",
        {
            "user": user,
            "active": "templates",
            "templates": items,
            "categories": categories,
            "current_category": category,
            "show_mine": show_mine,
            "system_count": system_count,
            "user_count": user_count,
        },
    )


# ── Detail page ──────────────────────────────────────────────────────────────


@router.get("/templates/{slug}", response_class=HTMLResponse)
async def template_detail_page(request: Request, slug: str):
    """Template detail page — shows full step breakdown and deploy CTA."""
    uid = get_uid_from_request(request)
    if not uid:
        from urllib.parse import quote

        return RedirectResponse(f"/signin?next={quote(f'/templates/{slug}', safe='/')}", status_code=302)
    user = await _load_user_view(uid) if uid else None

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Template).where(
                Template.slug == slug,
                Template.is_active == True,
            )
        )
        template = result.scalar_one_or_none()

    if not template:
        return render(
            request,
            "error.html",
            {
                "user": user,
                "error_title": "Template Not Found",
                "error_message": "This template doesn't exist or has been removed.",
            },
            status_code=404,
        )

    item = _format_template(template)

    # Check which platforms the user has connected
    active_project_id = request.cookies.get("active_project_id")
    connected_platforms = await _get_connected_platforms(uid, project_id=active_project_id) if uid else []

    # Can the user deploy this template?
    all_platforms_connected = all(p in connected_platforms for p in item["required_platforms"])
    can_deploy = bool(uid) and all_platforms_connected

    return render(
        request,
        "template_detail.html",
        {
            "user": user,
            "active": "templates",
            "template": item,
            "can_deploy": can_deploy,
            "connected_platforms": connected_platforms,
        },
    )


# ── Deploy API ───────────────────────────────────────────────────────────────


@router.post("/api/templates/deploy")
async def api_deploy_template(request: Request):
    """Deploy a template as a new dashboard."""
    uid = get_uid_from_request(request)
    if not uid:
        return JSONResponse({"error": True, "message": "Not signed in"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": True, "message": "Invalid JSON"}, status_code=400)

    slug = body.get("template_slug")
    if not slug:
        return JSONResponse({"error": True, "message": "template_slug required"}, status_code=400)

    try:
        user_uuid = uuid.UUID(uid)
    except ValueError:
        return JSONResponse({"error": True, "message": "Invalid user ID"}, status_code=400)

    # Load template
    user_view = await _load_user_view(uid)
    user_email = user_view.get("email", "") if user_view else ""

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Template).where(Template.slug == slug, Template.is_active == True))
        template = result.scalar_one_or_none()

        if not template:
            return JSONResponse({"error": True, "message": "Template not found"}, status_code=404)

        # Create dashboard from template
        share_slug = secrets.token_urlsafe(8)
        dashboard_title = body.get("title") or template.title
        dashboard_desc = template.description or ""

        dashboard = Dashboard(
            user_id=user_uuid,
            title=dashboard_title,
            description=dashboard_desc,
            share_slug=share_slug,
            owner_email=user_email,
        )
        db.add(dashboard)
        await db.flush()

        # Create cards from template steps
        for i, step in enumerate(template.steps or []):
            params = dict(step.get("params", {}))

            # Resolve relative date defaults from variables
            for var_def in template.variables or []:
                key = var_def.get("key")
                default = var_def.get("default", "")
                if isinstance(default, str) and default.startswith("-") and default.endswith("d"):
                    try:
                        days = int(default[1:-1])
                        resolved = (date.today() - timedelta(days=days)).isoformat()
                        # Substitute into params if they reference this variable
                        for pk, pv in list(params.items()):
                            if isinstance(pv, str) and pv.strip("{}").strip() == key:
                                params[pk] = resolved
                    except (ValueError, TypeError):
                        pass

            card_platform = step.get("platform", params.get("platform", "unknown"))
            card_tool = step.get("tool", "")
            card_title = step.get("card_title", f"Card {i + 1}")

            card = DashboardCard(
                dashboard_id=dashboard.id,
                title=card_title,
                platform=card_platform,
                tool_name=card_tool,
                query_params=params,
                result_cache={},
                position=i,
            )
            db.add(card)

        # Increment use count
        await db.execute(
            update(Template).where(Template.id == template.id).values(use_count=Template.use_count + 1)
        )
        await db.commit()

    return JSONResponse(
        {
            "success": True,
            "dashboard_id": str(dashboard.id),
            "live_url": f"/live-dashboards/{share_slug}",
        }
    )
