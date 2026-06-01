"""
Template Library MCP Tools

Pre-built and user-created dashboard recipes that users can browse, deploy,
and save. Templates dramatically reduce time-to-value by showing users what's
possible with their connected platforms.

Tools:
  template_list   — browse templates, filtered by category/tier, with compatibility info
  template_deploy — deploy a template as a live dashboard
  template_save   — save queries as a reusable template (Pro/Team)
"""

import logging
import secrets
import uuid
from typing import Literal

from sqlalchemy import select, update

import app.app_state as state
from app.config import settings
from app.models.template import Template
from app.tools.shared_helpers import get_current_user

logger = logging.getLogger(__name__)


def _get_user():
    return get_current_user()


def _check_platform_compatibility(user, required_platforms: list) -> dict:
    """Check which required platforms the user has connected."""
    platform_flags = {
        "ga4": getattr(user, "has_ga4", False),
        "gtm": getattr(user, "has_gtm", False),
        "google_ads": getattr(user, "has_ads", False),
        "meta": getattr(user, "has_meta", False),
        "tiktok": getattr(user, "has_tiktok", False),
        "snap": getattr(user, "has_snap", False),
        "bigquery": getattr(user, "has_bq", False),
        "amplitude": getattr(user, "has_amplitude", False),
        "adobe_analytics": getattr(user, "has_adobe_analytics", False),
        "adobe_launch": getattr(user, "has_adobe_launch", False),
        "adobe_marketo": getattr(user, "has_adobe_marketo", False),
        "redshift": getattr(user, "has_redshift", False),
        "snowflake": getattr(user, "has_snowflake", False),
    }
    connected = []
    missing = []
    for p in required_platforms:
        if platform_flags.get(p, False):
            connected.append(p)
        else:
            missing.append(p)
    return {
        "all_connected": len(missing) == 0,
        "connected": connected,
        "missing": missing,
    }


def register_template_tools(mcp_server):
    @mcp_server.tool("template_list")
    async def template_list(
        category: str | None = None,
        show_all: bool = False,
    ) -> dict:
        """Browse the template library. Returns templates with compatibility info.

        category: filter by category — ecommerce, ppc, seo, gtm, analytics,
                  cross_channel, warehouse, custom. Omit for all.
        show_all: if False (default), only show templates the user can deploy
                  (has required platforms connected — no plan-tier check).
                  True shows all templates with their compatibility status.
        """
        user = _get_user()

        async with state.db_session_factory() as db:
            query = select(Template).where(Template.is_active == True)

            if category:
                query = query.where(Template.category == category.lower())

            # Order: featured first, then by use_count, then newest
            query = query.order_by(
                Template.is_featured.desc(),
                Template.use_count.desc(),
                Template.created_at.desc(),
            )

            result = await db.execute(query)
            templates = result.scalars().all()

        base = settings.APP_BASE_URL
        items = []

        for t in templates:
            compat = (
                _check_platform_compatibility(user, t.required_platforms)
                if user
                else {"all_connected": False, "connected": [], "missing": t.required_platforms}
            )

            # Determine if user can deploy
            can_deploy = compat["all_connected"]

            if not show_all and not can_deploy:
                continue

            item = {
                "id": str(t.id),
                "slug": t.slug,
                "title": t.title,
                "description": t.description,
                "category": t.category,
                "icon": t.icon,
                "type": t.template_type,
                "required_platforms": t.required_platforms,
                "min_tier": t.min_tier,
                "is_featured": t.is_featured,
                "use_count": t.use_count,
                "step_count": len(t.steps),
                "variables": t.variables,
                "compatibility": compat,
                "can_deploy": can_deploy,
            }

            if not can_deploy and compat["missing"]:
                item["connect_url"] = f"{base}/connect"
                item["missing_platforms"] = compat["missing"]

            items.append(item)

        categories = sorted(set(t.category for t in templates))

        return {
            "templates": items,
            "total": len(items),
            "categories": categories,
            "browse_url": f"{base}/templates",
        }

    @mcp_server.tool("template_deploy")
    async def template_deploy(
        template_slug: str,
        dashboard_title: str | None = None,
        variables: dict | None = None,
    ) -> dict:
        """Deploy a template as a live dashboard.

        template_slug: the slug of the template to deploy.
        dashboard_title: optional custom title (defaults to template title).
        variables: key/value overrides for template variables (e.g. {"date_range_start": "2026-03-01"}).
        """
        user = _get_user()

        if not user:
            return {
                "error": True,
                "error_type": "unauthenticated",
                "message": "No active session. Please sign in.",
            }

        # Load template
        async with state.db_session_factory() as db:
            result = await db.execute(
                select(Template).where(
                    Template.slug == template_slug,
                    Template.is_active == True,
                )
            )
            template = result.scalar_one_or_none()

        if not template:
            return {
                "error": True,
                "message": f"Template '{template_slug}' not found.",
                "browse_url": f"{settings.APP_BASE_URL}/templates",
            }

        # Check platform compatibility
        compat = _check_platform_compatibility(user, template.required_platforms)
        if not compat["all_connected"]:
            return {
                "error": True,
                "error_type": "missing_platforms",
                "message": (
                    f"This template requires platforms you haven't connected yet: "
                    f"{', '.join(compat['missing'])}."
                ),
                "connect_url": f"{settings.APP_BASE_URL}/connect",
                "missing_platforms": compat["missing"],
            }

        uid = uuid.UUID(user.user_id)

        # Resolve variables ---------------------------------------------------------
        # 1. Start with defaults from the template variable definitions
        # 2. Merge ALL user-provided variables on top (not just ones that
        #    match template definitions — templates reference vars like
        #    property_id / account_id that aren't in the definitions list)
        from datetime import date, timedelta

        resolved_vars: dict = {}

        # Phase 1: populate defaults from template variable definitions
        for var_def in template.variables or []:
            key = var_def["key"]
            default = var_def.get("default")
            if default is not None:
                if default == "today":
                    resolved_vars[key] = date.today().isoformat()
                elif isinstance(default, str) and default.startswith("-") and default.endswith("d"):
                    days = int(default[1:-1])
                    resolved_vars[key] = (date.today() - timedelta(days=days)).isoformat()
                else:
                    resolved_vars[key] = default

        # Phase 2: overlay every user-supplied variable (wins over defaults)
        if variables:
            resolved_vars.update(variables)

        # Create dashboard
        from app.models.dashboard import Dashboard, DashboardCard

        title = dashboard_title or template.title
        slug = secrets.token_urlsafe(8)
        base = settings.APP_BASE_URL

        proj_ctx = state.current_project_ctx.get()

        async with state.db_session_factory() as db:
            dashboard = Dashboard(
                user_id=uid,
                project_id=uuid.UUID(proj_ctx.project_id) if proj_ctx else None,
                title=title,
                description=template.description,
                share_slug=slug,
                owner_email=user.email,
            )
            db.add(dashboard)
            await db.flush()

            # Tool-name → default platform mapping (matches card_generator _FETCHERS keys)
            _TOOL_PLATFORM_MAP = {
                "analytics_read": "ga4",
                "analytics_audit": "ga4",
                "analytics_write": "ga4",
                "tagmanager_read": "gtm",
                "tagmanager_audit": "gtm",
                "tagmanager_write": "gtm",
                "warehouse_read": "bigquery",
                "warehouse_query": "bigquery",
                "warehouse_audit": "bigquery",
            }

            # --- Recursive variable substitution helper --------------------
            # Supports both {key} and {{key}} placeholder styles, recurses
            # into dicts and lists so nested structures (filters, metrics,
            # dimensions) also get resolved.
            def _substitute(value):
                if isinstance(value, str):
                    out = value
                    for var_key, var_val in resolved_vars.items():
                        # Try both styles: {{key}} first (so a double-brace
                        # placeholder doesn't get half-consumed by the {key}
                        # pass), then {key}.
                        for placeholder in ("{{" + var_key + "}}", "{" + var_key + "}"):
                            if placeholder in out:
                                out = out.replace(placeholder, str(var_val))
                    # If the whole string is now a numeric literal that
                    # originated from a numeric variable, keep it as a string
                    # — downstream tools coerce types as needed.
                    return out
                if isinstance(value, dict):
                    return {k: _substitute(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [_substitute(v) for v in value]
                return value

            # Create cards from template steps
            created_cards = []
            for i, step in enumerate(template.steps):
                # --- (a) Substitute variables into params -----------------
                params = _substitute(dict(step.get("params", {})))

                # --- (b) Infer platform from tool name --------------------
                tool_name = step.get("tool", "")
                card_platform = (
                    params.get("platform")  # best: explicit in params
                    or _TOOL_PLATFORM_MAP.get(tool_name)  # fallback: infer from tool
                    or step.get("platform")  # last resort: step metadata
                    or "unknown"
                )

                card = DashboardCard(
                    dashboard_id=dashboard.id,
                    title=step.get("card_title", f"Card {i + 1}"),
                    platform=card_platform,
                    tool_name=tool_name,
                    query_params=params,
                    result_cache={},
                    position=i,
                )
                db.add(card)
                await db.flush()
                created_cards.append((card, card_platform, tool_name, params))

            # Increment template use count
            await db.execute(
                update(Template).where(Template.id == template.id).values(use_count=Template.use_count + 1)
            )

            await db.commit()
            await db.refresh(dashboard)

            # Cards store their full tool-call spec in query_params at deploy time;
            # live refresh dispatches through the MCP tool registry — no scripts needed.

        return {
            "success": True,
            "dashboard_id": str(dashboard.id),
            "title": title,
            "live_url": f"{base}/live-dashboards/{slug}",
            "manage_url": f"{base}/dashboards",
            "cards_created": len(template.steps),
            "template_used": template.title,
            "note": (
                f"Dashboard '{title}' created with {len(template.steps)} cards. "
                f"View it live at {base}/live-dashboards/{slug}. Cards will fetch fresh data on each view."
            ),
        }

    @mcp_server.tool("template_save")
    async def template_save(
        title: str,
        description: str,
        category: Literal[
            "ecommerce", "ppc", "seo", "gtm", "analytics", "cross_channel", "warehouse", "custom"
        ],
        steps: list,
        required_platforms: list[str],
        variables: list | None = None,
        icon: str | None = None,
    ) -> dict:
        """Save a set of queries as a reusable template.

        title: template name (e.g. "E-commerce Weekly Review").
        description: what the template does.
        category: one of ecommerce, ppc, seo, gtm, analytics, cross_channel, warehouse, custom.
        steps: list of step dicts — each with tool, params, card_title, card_type.
          Example: [{"tool": "analytics_read", "params": {...}, "card_title": "Sessions", "card_type": "TABLE"}]
        required_platforms: which platforms must be connected (e.g. ["ga4", "google_ads"]).
        variables: optional list of variable defs for user-fillable fields.
          Example: [{"key": "property_id", "label": "GA4 Property", "type": "string"}]
        icon: optional icon slug (e.g. "ga4", "meta", "cross_channel").
        """
        user = _get_user()

        if not user:
            return {
                "error": True,
                "error_type": "unauthenticated",
                "message": "No active session. Please sign in.",
            }

        # Validate steps
        if not steps or not isinstance(steps, list):
            return {"error": True, "message": "steps must be a non-empty list of step dicts."}

        if len(steps) > 20:
            return {"error": True, "message": "Templates can have at most 20 steps."}

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                return {"error": True, "message": f"Step {i} must be a dict."}
            if "tool" not in step:
                return {"error": True, "message": f"Step {i} missing 'tool' field."}

        # Generate slug
        import re

        base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]
        slug = f"{base_slug}-{secrets.token_urlsafe(4)}"

        uid = uuid.UUID(user.user_id)
        base = settings.APP_BASE_URL

        async with state.db_session_factory() as db:
            template = Template(
                user_id=uid,
                title=title,
                description=description,
                category=category,
                template_type="user",
                slug=slug,
                icon=icon or category,
                required_platforms=required_platforms,
                steps=steps,
                variables=variables or [],
                min_tier="pro",  # user templates always require Pro
            )
            db.add(template)
            await db.commit()
            await db.refresh(template)

        return {
            "success": True,
            "template_id": str(template.id),
            "slug": template.slug,
            "title": template.title,
            "category": template.category,
            "steps": len(template.steps),
            "browse_url": f"{base}/templates",
            "note": (
                f"Template '{title}' saved with {len(steps)} steps. "
                f"You or other users can deploy it from the template library."
            ),
        }
