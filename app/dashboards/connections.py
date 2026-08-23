"""List bindable project connections for hosted dashboards.

Returns alias-ready records the authoring guide and reporting UI both use.
Never includes encrypted tokens or raw credentials.
"""

from __future__ import annotations

from sqlalchemy import select

import app.app_state as app_state
from app.dashboards.artifact import CONNECTION_TOOL, ConnectionRequirement
from app.models.bq_connection import BQConnection
from app.models.connection import OAuthConnection
from app.models.credential_connection import (
    AdjustConnection,
    AdobeConnection,
    AmplitudeConnection,
    AppsFlyerConnection,
    BranchConnection,
    BrazeConnection,
    MarketoConnection,
    MixpanelConnection,
    MoengageConnection,
    PostHogConnection,
    RedshiftConnection,
    SnowflakeConnection,
)
from app.models.token import GA4Property, GoogleAdsAccount, GTMContainer, SearchConsoleSite


def _oauth_platforms(scopes: list[str] | None) -> list[str]:
    scopes_set = set(scopes or [])
    out: list[str] = []
    if scopes_set & {
        "https://www.googleapis.com/auth/analytics.readonly",
        "https://www.googleapis.com/auth/analytics",
        "https://www.googleapis.com/auth/analytics.edit",
    }:
        out.append("ga4")
    if any("tagmanager" in s for s in scopes_set):
        out.append("gtm")
    if any("adwords" in s or "ads" in s for s in scopes_set if "analytics" not in s):
        if "google_ads" not in out:
            out.append("google_ads")
    if any("webmasters" in s or "searchconsole" in s for s in scopes_set):
        out.append("search_console")
    return out


async def list_bindable_connections(project_id, user_id=None) -> list[dict]:
    """Enumerate live connections in a project. No secrets."""
    if project_id is None:
        return []
    out: list[dict] = []
    async with app_state.db_session_factory() as db:
        oauth_rows = await db.execute(
            select(OAuthConnection).where(
                OAuthConnection.project_id == project_id,
                OAuthConnection.is_active.is_(True),
            )
        )
        oauth_by_id: dict = {}
        for conn in oauth_rows.scalars().all():
            oauth_by_id[conn.id] = conn
            provider = (conn.provider or "").lower()
            platforms = _oauth_platforms(conn.scopes)
            if provider in {"meta", "meta_ads"}:
                platforms.append("meta_ads")
            elif provider in {"tiktok", "tiktok_ads"}:
                platforms.append("tiktok_ads")
            elif provider in {"snap", "snap_ads"}:
                platforms.append("snap_ads")
            elif provider in {"apple", "apple_ads"}:
                platforms.append("apple_ads")
            elif provider in {"linkedin", "linkedin_ads"}:
                platforms.append("linkedin_ads")
            elif provider in {"pinterest", "pinterest_ads"}:
                platforms.append("pinterest_ads")
            elif provider in {"reddit", "reddit_ads"}:
                platforms.append("reddit_ads")
            elif provider in {"x", "x_ads", "twitter"}:
                platforms.append("x_ads")
            elif provider in {"bing", "bing_webmaster"}:
                platforms.append("bing_webmaster")
            elif provider == "google":
                pass
            elif provider:
                platforms.append(provider)
            for platform in dict.fromkeys(platforms):
                out.append(
                    {
                        "type": platform,
                        "tool": CONNECTION_TOOL.get(platform, "analytics_read"),
                        "label": conn.google_email or f"{platform} connection",
                        "connection_id": str(conn.id),
                        "resource_key": None,
                        "resource_value": None,
                        "status": conn.connection_status or "active",
                    }
                )

        oauth_ids = list(oauth_by_id)
        ga4_rows = (
            await db.execute(select(GA4Property).where(GA4Property.connection_id.in_(oauth_ids)))
            if oauth_ids
            else None
        )
        for prop in ga4_rows.scalars().all() if ga4_rows is not None else []:
            out.append(
                {
                    "type": "ga4",
                    "tool": "analytics_read",
                    "label": f"{prop.property_name or prop.property_id} ({prop.property_id})",
                    "connection_id": str(prop.connection_id),
                    "resource_key": "property_id",
                    "resource_value": prop.property_id,
                    "status": "active",
                }
            )

        ads_rows = (
            await db.execute(select(GoogleAdsAccount).where(GoogleAdsAccount.connection_id.in_(oauth_ids)))
            if oauth_ids
            else None
        )
        for acc in ads_rows.scalars().all() if ads_rows is not None else []:
            out.append(
                {
                    "type": "google_ads",
                    "tool": "marketing_read",
                    "label": f"{acc.account_name or acc.customer_id} ({acc.customer_id})",
                    "connection_id": str(acc.connection_id),
                    "resource_key": "customer_id",
                    "resource_value": acc.customer_id,
                    "status": "active",
                }
            )

        gsc_rows = (
            await db.execute(select(SearchConsoleSite).where(SearchConsoleSite.connection_id.in_(oauth_ids)))
            if oauth_ids
            else None
        )
        for site in gsc_rows.scalars().all() if gsc_rows is not None else []:
            out.append(
                {
                    "type": "search_console",
                    "tool": "seo_read",
                    "label": site.site_url,
                    "connection_id": str(site.connection_id),
                    "resource_key": "site_url",
                    "resource_value": site.site_url,
                    "status": "active",
                }
            )

        gtm_rows = (
            await db.execute(select(GTMContainer).where(GTMContainer.connection_id.in_(oauth_ids)))
            if oauth_ids
            else None
        )
        for c in gtm_rows.scalars().all() if gtm_rows is not None else []:
            out.append(
                {
                    "type": "gtm",
                    "tool": "tagmanager_read",
                    "label": f"{c.container_name or c.container_id} ({c.container_id})",
                    "connection_id": str(c.connection_id),
                    "resource_key": "container_id",
                    "resource_value": c.container_id,
                    "status": "active",
                    "account_id": getattr(c, "account_id", None),
                }
            )

        cred_specs: list[tuple[type, str]] = [
            (AmplitudeConnection, "amplitude"),
            (MixpanelConnection, "mixpanel"),
            (PostHogConnection, "posthog"),
            (AdobeConnection, "adobe_analytics"),
            (RedshiftConnection, "redshift"),
            (SnowflakeConnection, "snowflake"),
            (BranchConnection, "branch"),
            (AppsFlyerConnection, "appsflyer"),
            (AdjustConnection, "adjust"),
            (BrazeConnection, "braze"),
            (MoengageConnection, "moengage"),
            (MarketoConnection, "adobe_marketo"),
        ]
        try:
            bq_rows = await db.execute(
                select(BQConnection).where(
                    BQConnection.fluxito_project_id == project_id,
                    BQConnection.is_active.is_(True),
                )
            )
            for row in bq_rows.scalars().all():
                out.append(
                    {
                        "type": "bigquery",
                        "tool": "warehouse_query",
                        "label": row.display_name or f"BigQuery ({row.project_id})",
                        "connection_id": str(row.id),
                        "resource_key": "connection_id",
                        "resource_value": str(row.id),
                        "status": row.connection_status or "active",
                    }
                )
        except Exception:
            pass
        for model, platform in cred_specs:
            try:
                rows = await db.execute(
                    select(model).where(
                        model.project_id == project_id,
                        model.is_active.is_(True),
                    )
                )
            except Exception:
                continue
            for row in rows.scalars().all():
                if model is AdobeConnection:
                    if getattr(row, "has_analytics", False):
                        out.append(
                            {
                                "type": "adobe_analytics",
                                "tool": "analytics_read",
                                "label": getattr(row, "display_name", None) or f"Adobe Analytics ({row.id})",
                                "connection_id": str(row.id),
                                "resource_key": "connection_id",
                                "resource_value": str(row.id),
                                "status": getattr(row, "connection_status", None) or "active",
                            }
                        )
                    if getattr(row, "has_launch", False):
                        out.append(
                            {
                                "type": "adobe_launch",
                                "tool": "tagmanager_read",
                                "label": getattr(row, "display_name", None) or f"Adobe Launch ({row.id})",
                                "connection_id": str(row.id),
                                "resource_key": "connection_id",
                                "resource_value": str(row.id),
                                "status": getattr(row, "connection_status", None) or "active",
                            }
                        )
                    continue
                label = getattr(row, "display_name", None) or f"{platform} ({row.id})"
                out.append(
                    {
                        "type": platform,
                        "tool": CONNECTION_TOOL.get(platform, "analytics_read"),
                        "label": label,
                        "connection_id": str(row.id),
                        "resource_key": "connection_id",
                        "resource_value": str(row.id),
                        "status": getattr(row, "connection_status", None) or "active",
                    }
                )

    # Dedup exact (type, resource_value, connection_id) triples
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in out:
        key = (item["type"], item.get("resource_value"), item.get("connection_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def bind_requirements(
    requirements: list[ConnectionRequirement],
    available: list[dict],
) -> list[dict]:
    """Match manifest aliases to available connections of the same type."""
    by_type: dict[str, list[dict]] = {}
    for item in available:
        by_type.setdefault(item["type"], []).append(item)

    bindings: list[dict] = []
    for req in requirements:
        candidates = by_type.get(req.type, [])
        # Prefer a resource-level row (property/account) over a bare oauth row.
        ranked = sorted(candidates, key=lambda c: 0 if c.get("resource_value") else 1)
        chosen = ranked[0] if ranked else None
        if chosen is None:
            bindings.append(
                {
                    "alias": req.alias,
                    "type": req.type,
                    "required": req.required,
                    "status": "missing",
                    "tool": CONNECTION_TOOL.get(req.type),
                    "label": None,
                    "connection_id": None,
                    "resource_key": None,
                    "resource_value": None,
                }
            )
            continue
        status = "bound"
        if (chosen.get("status") or "active") not in ("active", "ok", "connected"):
            status = "error"
        bindings.append(
            {
                "alias": req.alias,
                "type": req.type,
                "required": req.required,
                "status": status,
                "tool": CONNECTION_TOOL.get(req.type, chosen.get("tool")),
                "label": chosen.get("label"),
                "connection_id": chosen.get("connection_id"),
                "resource_key": chosen.get("resource_key"),
                "resource_value": chosen.get("resource_value"),
                "account_id": chosen.get("account_id"),
            }
        )
    return bindings
