"""
Unified Marketing MCP Tools
Consolidates Google Ads, Meta Ads, TikTok Ads, and Snapchat Ads into a single interface.
Routes to the appropriate connector based on the 'platform' argument.

Tools:
  marketing_read   — Layer 1 data access (accounts, campaigns, ad groups, keywords, conversions)
  marketing_audit  — Layer 2 intelligence (tracking setup, budget utilisation, quality scores)
  marketing_write  — Layer 3 write ops (create/pause/enable campaigns, update budgets)
                     Google writes are scope-gated via @require_scope.
"""

from typing import Literal

import app.app_state as state
from app.cache import cached_tool_response
from app.tools.shared_helpers import (
    get_current_user,
    get_google_conn_id,
    get_provider_oauth1_tokens,
    get_provider_token,
)


def _get_user():
    return get_current_user()


def _get_google_conn_id():
    return get_google_conn_id()


def _get_provider_token(provider_str: str) -> str | None:
    return get_provider_token(provider_str)


def _get_provider_oauth1_tokens(provider_str: str) -> tuple[str, str] | None:
    return get_provider_oauth1_tokens(provider_str)


def _unauthorized_response(platform: str):
    from app.auth.mcp_session_manager import no_ads_response
    from app.config import settings

    if platform == "google":
        return no_ads_response(settings.APP_BASE_URL)
    return {
        "error": True,
        "error_type": "connection_missing",
        "message": f"No {platform.title()} Ads account connected.",
        "connect_url": f"{settings.APP_BASE_URL}/connect/{platform.lower()}",
        "action_required": f"Visit {settings.APP_BASE_URL}/connect/{platform.lower()} to authenticate.",
    }


def register_marketing_tools(mcp_server):
    @mcp_server.tool("marketing_read")
    async def marketing_read(
        platform: Literal[
            "google", "meta", "tiktok", "snap", "linkedin", "pinterest", "x", "reddit", "apple"
        ],
        action: str,
        account_id: str | None = None,
        date_range_start: str | None = None,
        date_range_end: str | None = None,
        metrics: list[str] | None = None,
        campaign_id: str | None = None,
        limit: int = 50,
    ) -> dict:
        """Reads ad platform data. Use marketing_audit for health checks, marketing_write for changes.

        platform: google | meta | tiktok | snap | linkedin | pinterest | x | reddit | apple. Dates: YYYY-MM-DD.

        All platforms: list_accounts, get_campaign_performance(account_id+dates)
        Google only: get_ad_group_performance(+dates,campaign_id?), get_conversion_actions(account_id), get_keyword_performance(+dates,campaign_id?)
        Meta: get_adset_performance(+dates,campaign_id?)
        TikTok: get_adgroup_performance(+dates,campaign_id?)
        Snap: get_adsquad_performance(+dates,campaign_id?)
        """
        user = _get_user()

        # Validate action name upfront per platform
        _VALID_MARKETING_READ_ACTIONS = {
            "google": {
                "list_accounts",
                "get_campaign_performance",
                "get_ad_group_performance",
                "get_conversion_actions",
                "get_keyword_performance",
            },
            "meta": {"list_accounts", "get_campaign_performance", "get_adset_performance"},
            "tiktok": {"list_accounts", "get_campaign_performance", "get_adgroup_performance"},
            "snap": {"list_accounts", "get_campaign_performance", "get_adsquad_performance"},
            "linkedin": {"list_accounts", "get_campaign_performance", "get_adgroup_performance"},
            "pinterest": {"list_accounts", "get_campaign_performance", "get_adgroup_performance"},
            "x": {"list_accounts", "get_campaign_performance", "get_line_item_performance"},
            "reddit": {"list_accounts", "get_campaign_performance", "get_adgroup_performance"},
            "apple": {"list_accounts", "get_campaign_performance", "get_adgroup_performance"},
        }
        valid_actions = _VALID_MARKETING_READ_ACTIONS.get(platform, set())
        if action not in valid_actions:
            return {
                "error": True,
                "message": f"Unknown action '{action}' for {platform} marketing_read. "
                f"Valid actions: {', '.join(sorted(valid_actions))}",
            }

        if platform == "google":
            if not user or not user.has_ads:
                return _unauthorized_response("google")
            conn_id = _get_google_conn_id()
            ads = state.ads_connector

            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:ads:list_accounts:{conn_id}",
                    600,
                    ads.list_accounts,
                    conn_id,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}

            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:ads:campaigns:{conn_id}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    ads.get_campaign_performance,
                    conn_id,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_ad_group_performance":
                return await cached_tool_response(
                    f"cache:ads:adgroups:{conn_id}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}:{limit}",
                    60,
                    ads.get_ad_group_performance,
                    conn_id,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                    limit,
                )
            elif action == "get_conversion_actions":
                return await cached_tool_response(
                    f"cache:ads:conversions:{conn_id}:{account_id}",
                    120,
                    ads.get_conversion_actions,
                    conn_id,
                    account_id,
                )
            elif action == "get_keyword_performance":
                return await cached_tool_response(
                    f"cache:ads:keywords:{conn_id}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}:{limit}",
                    60,
                    ads.get_keyword_performance,
                    conn_id,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                    limit,
                )

            return {"error": True, "message": f"Unknown action '{action}' for Google Ads"}

        elif platform == "meta":
            token = _get_provider_token("meta")
            if not token:
                return _unauthorized_response("meta")

            meta = state.meta_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:meta:accounts:{uid}",
                    600,
                    meta.list_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:meta:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    meta.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adset_performance":
                return await cached_tool_response(
                    f"cache:meta:adsets:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}",
                    60,
                    meta.get_adset_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Meta Ads. Supported: list_accounts, get_campaign_performance, get_adset_performance",
            }

        elif platform == "tiktok":
            token = _get_provider_token("tiktok")
            if not token:
                return _unauthorized_response("tiktok")

            tiktok = state.tiktok_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:tiktok:accounts:{uid}",
                    600,
                    tiktok.list_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:tiktok:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    tiktok.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adgroup_performance":
                return await cached_tool_response(
                    f"cache:tiktok:adgroups:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}",
                    60,
                    tiktok.get_adgroup_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for TikTok Ads. Supported: list_accounts, get_campaign_performance, get_adgroup_performance",
            }

        elif platform == "snap":
            token = _get_provider_token("snap")
            if not token:
                return _unauthorized_response("snap")

            snap = state.snap_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:snap:accounts:{uid}",
                    600,
                    snap.list_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:snap:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    snap.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adsquad_performance":
                return await cached_tool_response(
                    f"cache:snap:adsquads:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}",
                    60,
                    snap.get_adsquad_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Snap Ads. Supported: list_accounts, get_campaign_performance, get_adsquad_performance",
            }

        elif platform == "linkedin":
            token = _get_provider_token("linkedin")
            if not token:
                return _unauthorized_response("linkedin")
            linkedin = state.linkedin_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:linkedin:accounts:{uid}",
                    600,
                    linkedin.list_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:linkedin:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    linkedin.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adgroup_performance":
                return await cached_tool_response(
                    f"cache:linkedin:adgroups:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}:{limit}",
                    60,
                    linkedin.get_adgroup_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for LinkedIn Ads. Supported: list_accounts, get_campaign_performance, get_adgroup_performance",
            }

        elif platform == "pinterest":
            token = _get_provider_token("pinterest")
            if not token:
                return _unauthorized_response("pinterest")
            pinterest = state.pinterest_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:pinterest:accounts:{uid}",
                    600,
                    pinterest.list_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:pinterest:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    pinterest.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adgroup_performance":
                return await cached_tool_response(
                    f"cache:pinterest:adgroups:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}:{limit}",
                    60,
                    pinterest.get_adgroup_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Pinterest Ads. Supported: list_accounts, get_campaign_performance, get_adgroup_performance",
            }

        elif platform == "x":
            oauth1_tokens = _get_provider_oauth1_tokens("x")
            if not oauth1_tokens:
                return _unauthorized_response("x")
            from app.auth.oauth_app_credentials import get_oauth_app_credentials_cached
            from app.connectors.x_ads import XAdsConnector, XOAuth1Token

            async with state.db_session_factory() as db:
                creds = await get_oauth_app_credentials_cached(db, "x")
            x_ads = XAdsConnector(creds.client_id, creds.client_secret)
            x_token = XOAuth1Token(token=oauth1_tokens[0], token_secret=oauth1_tokens[1])
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:x:accounts:{uid}",
                    600,
                    x_ads.list_accounts,
                    x_token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:x:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    x_ads.get_campaign_performance,
                    x_token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_line_item_performance":
                return await cached_tool_response(
                    f"cache:x:line_items:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}",
                    60,
                    x_ads.get_line_item_performance,
                    x_token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for X Ads. Supported: list_accounts, get_campaign_performance, get_line_item_performance",
            }

        elif platform == "reddit":
            token = _get_provider_token("reddit")
            if not token:
                return _unauthorized_response("reddit")
            reddit = state.reddit_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:reddit:accounts:{uid}",
                    600,
                    reddit.list_ad_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:reddit:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    reddit.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adgroup_performance":
                return await cached_tool_response(
                    f"cache:reddit:adgroups:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}:{limit}",
                    60,
                    reddit.get_adgroup_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Reddit Ads. Supported: list_accounts, get_campaign_performance, get_adgroup_performance",
            }

        elif platform == "apple":
            token = _get_provider_token("apple")
            if not token:
                return _unauthorized_response("apple")
            apple = state.apple_connector
            uid = user.user_id if user else "anon"
            if action == "list_accounts":
                return await cached_tool_response(
                    f"cache:apple:accounts:{uid}",
                    600,
                    apple.list_accounts,
                    token,
                )
            if not account_id:
                return {"error": True, "message": f"account_id is required for '{action}'"}
            if action == "get_campaign_performance":
                mets_key = ",".join(sorted(metrics)) if metrics else "default"
                return await cached_tool_response(
                    f"cache:apple:campaigns:{uid}:{account_id}:{date_range_start}:{date_range_end}:{mets_key}",
                    60,
                    apple.get_campaign_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    metrics,
                )
            elif action == "get_adgroup_performance":
                return await cached_tool_response(
                    f"cache:apple:adgroups:{uid}:{account_id}:{date_range_start}:{date_range_end}:{campaign_id}:{limit}",
                    60,
                    apple.get_adgroup_performance,
                    token,
                    account_id,
                    date_range_start,
                    date_range_end,
                    campaign_id,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Apple Ads. Supported: list_accounts, get_campaign_performance, get_adgroup_performance",
            }

        return {"error": True, "message": f"Unknown platform: {platform}"}

    @mcp_server.tool("marketing_audit")
    async def marketing_audit(
        platform: Literal[
            "google", "meta", "tiktok", "snap", "linkedin", "pinterest", "x", "reddit", "apple"
        ],
        action: str,
        account_id: str,
        date_range_start: str | None = None,
        date_range_end: str | None = None,
        campaign_id: str | None = None,
    ) -> dict:
        """Audits marketing health: tracking, budgets, quality scores.

        platform: google | meta | tiktok | snap | linkedin | pinterest | x | reddit | apple. All: audit_tracking_setup(account_id).
        Google only: audit_budget_utilization(account_id+dates), audit_quality_scores(account_id,campaign_id?)
        """
        user = _get_user()

        if platform == "google":
            if not user or not user.has_ads:
                return _unauthorized_response("google")
            conn_id = _get_google_conn_id()
            ads = state.ads_connector

            if action == "audit_tracking_setup":
                return await ads.audit_tracking_setup(conn_id, account_id)
            elif action == "audit_budget_utilization":
                if not date_range_start or not date_range_end:
                    return {
                        "error": True,
                        "message": "date_range_start and date_range_end are required for audit_budget_utilization",
                    }
                return await ads.audit_budget_utilization(
                    conn_id, account_id, date_range_start, date_range_end
                )
            elif action == "audit_quality_scores":
                return await ads.audit_quality_scores(conn_id, account_id, campaign_id)
            return {"error": True, "message": f"Unknown action '{action}' for Google Ads audit"}

        elif platform == "meta":
            token = _get_provider_token("meta")
            if not token:
                return _unauthorized_response("meta")
            if action == "audit_tracking_setup":
                return await state.meta_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Meta Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "tiktok":
            token = _get_provider_token("tiktok")
            if not token:
                return _unauthorized_response("tiktok")
            if action == "audit_tracking_setup":
                return await state.tiktok_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for TikTok Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "snap":
            token = _get_provider_token("snap")
            if not token:
                return _unauthorized_response("snap")
            if action == "audit_tracking_setup":
                return await state.snap_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Snap Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "linkedin":
            token = _get_provider_token("linkedin")
            if not token:
                return _unauthorized_response("linkedin")
            if action == "audit_tracking_setup":
                return await state.linkedin_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for LinkedIn Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "pinterest":
            token = _get_provider_token("pinterest")
            if not token:
                return _unauthorized_response("pinterest")
            if action == "audit_tracking_setup":
                return await state.pinterest_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Pinterest Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "x":
            oauth1_tokens = _get_provider_oauth1_tokens("x")
            if not oauth1_tokens:
                return _unauthorized_response("x")
            if action == "audit_tracking_setup":
                from app.auth.oauth_app_credentials import get_oauth_app_credentials_cached
                from app.connectors.x_ads import XAdsConnector, XOAuth1Token

                async with state.db_session_factory() as db:
                    creds = await get_oauth_app_credentials_cached(db, "x")
                return await XAdsConnector(creds.client_id, creds.client_secret).audit_tracking_setup(
                    XOAuth1Token(token=oauth1_tokens[0], token_secret=oauth1_tokens[1]), account_id
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for X Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "reddit":
            token = _get_provider_token("reddit")
            if not token:
                return _unauthorized_response("reddit")
            if action == "audit_tracking_setup":
                return await state.reddit_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Reddit Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "apple":
            token = _get_provider_token("apple")
            if not token:
                return _unauthorized_response("apple")
            if action == "audit_tracking_setup":
                return await state.apple_connector.audit_tracking_setup(token, account_id)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Apple Ads audit. Supported: audit_tracking_setup",
            }

        elif platform == "gtm":
            return {
                "error": True,
                "message": (
                    "GTM audit actions (suggest_improvements, benchmark_health, "
                    "generate_audit_report, etc.) live in the 'tagmanager_audit' tool. "
                    "Please call tagmanager_audit with platform='gtm' instead of marketing_audit."
                ),
            }

        return {"error": True, "message": f"Unknown platform: {platform}"}

    @mcp_server.tool("marketing_write")
    async def marketing_write(
        platform: Literal[
            "google", "meta", "tiktok", "snap", "linkedin", "pinterest", "x", "reddit", "apple"
        ],
        action: str,
        account_id: str,
        campaign_id: str | None = None,
        campaign_name: str | None = None,
        status: str | None = None,
        daily_budget_usd: float | None = None,
        advertising_channel_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        bidding_strategy_type: str | None = None,
    ) -> dict:
        """Write operations for ad platforms. Google requires 'full' tier.

        platform: google | meta | tiktok | snap | linkedin | pinterest | x | reddit | apple.
        create_campaign: campaign_name, advertising_channel_type(SEARCH|DISPLAY|SHOPPING|VIDEO|PERFORMANCE_MAX), daily_budget_usd, start_date
        update_campaign_status: campaign_id, status(ENABLED|PAUSED)
        update_campaign_budget: campaign_id, daily_budget_usd
        """
        user = _get_user()

        if platform == "google":
            if not user or not user.has_ads:
                return _unauthorized_response("google")

            # Scope check — Google Ads writes require the full analytics/adwords scope
            from app.auth.scopes import SCOPE_GA4_FULL

            conn = next(
                (c for c in (user.connections or []) if getattr(c, "provider", "google") in ("google", "")),
                None,
            )
            granted_scopes = getattr(conn, "scopes", []) if conn else []
            if SCOPE_GA4_FULL not in granted_scopes:
                from app.config import settings

                return {
                    "error": True,
                    "error_type": "insufficient_scope",
                    "message": "Google Ads write operations require 'Full Access' permission.",
                    "action_required": f"Reconnect Google at {settings.APP_BASE_URL}/connect with 'Full Access' tier.",
                }

            conn_id = _get_google_conn_id()
            ads = state.ads_connector

            if action == "create_campaign":
                missing = [
                    f
                    for f in ["campaign_name", "advertising_channel_type", "daily_budget_usd", "start_date"]
                    if locals()[f] is None
                ]
                if missing:
                    return {
                        "error": True,
                        "message": f"Missing required fields for create_campaign: {missing}",
                    }
                return await ads.create_campaign(
                    connection_id=conn_id,
                    customer_id=account_id,
                    campaign_name=campaign_name,
                    advertising_channel_type=advertising_channel_type,
                    daily_budget_micros=int(daily_budget_usd * 1_000_000),
                    start_date=start_date,
                    end_date=end_date,
                    bidding_strategy_type=bidding_strategy_type or "MAXIMIZE_CLICKS",
                )

            elif action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await ads.update_campaign_status(conn_id, account_id, campaign_id, status.upper())

            elif action == "update_campaign_budget":
                if not campaign_id or daily_budget_usd is None:
                    return {
                        "error": True,
                        "message": "campaign_id and daily_budget_usd are required for update_campaign_budget",
                    }
                return await ads.update_campaign_budget(
                    conn_id, account_id, campaign_id, int(daily_budget_usd * 1_000_000)
                )

            return {"error": True, "message": f"Unknown action '{action}' for Google Ads write"}

        elif platform == "meta":
            token = _get_provider_token("meta")
            if not token:
                return _unauthorized_response("meta")

            if action == "create_campaign":
                missing = [f for f in ["campaign_name", "advertising_channel_type"] if locals()[f] is None]
                if missing:
                    return {
                        "error": True,
                        "message": f"Missing required fields for Meta create_campaign: {missing}. advertising_channel_type maps to Meta objective (e.g. OUTCOME_SALES, OUTCOME_TRAFFIC).",
                    }
                return await state.meta_connector.create_campaign(
                    access_token=token,
                    account_id=account_id,
                    name=campaign_name,
                    objective=advertising_channel_type,  # reuse field as objective
                    status=(status or "PAUSED").upper(),
                    daily_budget=daily_budget_usd,
                )
            elif action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await state.meta_connector.update_campaign_status(token, campaign_id, status.upper())
            return {"error": True, "message": f"Unknown action '{action}' for Meta Ads write"}

        elif platform == "tiktok":
            token = _get_provider_token("tiktok")
            if not token:
                return _unauthorized_response("tiktok")

            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await state.tiktok_connector.update_campaign_status(
                    token, account_id, campaign_id, status.upper()
                )
            elif action == "update_campaign_budget":
                if not campaign_id or daily_budget_usd is None:
                    return {
                        "error": True,
                        "message": "campaign_id and daily_budget_usd are required for update_campaign_budget",
                    }
                return await state.tiktok_connector.update_campaign_budget(
                    token, account_id, campaign_id, daily_budget_usd
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for TikTok Ads write. Supported: update_campaign_status, update_campaign_budget",
            }

        elif platform == "snap":
            token = _get_provider_token("snap")
            if not token:
                return _unauthorized_response("snap")

            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await state.snap_connector.update_campaign_status(
                    token, account_id, campaign_id, status.upper()
                )
            elif action == "update_campaign_budget":
                if not campaign_id or daily_budget_usd is None:
                    return {
                        "error": True,
                        "message": "campaign_id and daily_budget_usd are required for update_campaign_budget",
                    }
                # Snap uses micro-currency
                return await state.snap_connector.update_campaign_budget(
                    token, account_id, campaign_id, int(daily_budget_usd * 1_000_000)
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Snap Ads write. Supported: update_campaign_status, update_campaign_budget",
            }

        elif platform == "linkedin":
            token = _get_provider_token("linkedin")
            if not token:
                return _unauthorized_response("linkedin")
            linkedin = state.linkedin_connector
            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await linkedin.update_campaign_status(token, account_id, campaign_id, status)
            elif action == "update_campaign_budget":
                if not campaign_id or daily_budget_usd is None:
                    return {
                        "error": True,
                        "message": "campaign_id and daily_budget_usd are required for update_campaign_budget",
                    }
                return await linkedin.update_campaign_budget(token, account_id, campaign_id, daily_budget_usd)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for LinkedIn Ads write. Supported: update_campaign_status, update_campaign_budget",
            }

        elif platform == "pinterest":
            token = _get_provider_token("pinterest")
            if not token:
                return _unauthorized_response("pinterest")
            pinterest = state.pinterest_connector
            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await pinterest.update_campaign_status(token, account_id, campaign_id, status)
            elif action == "update_campaign_budget":
                if not campaign_id or daily_budget_usd is None:
                    return {
                        "error": True,
                        "message": "campaign_id and daily_budget_usd are required for update_campaign_budget",
                    }
                return await pinterest.update_campaign_budget(
                    token, account_id, campaign_id, daily_budget_usd
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Pinterest Ads write. Supported: update_campaign_status, update_campaign_budget",
            }

        elif platform == "x":
            oauth1_tokens = _get_provider_oauth1_tokens("x")
            if not oauth1_tokens:
                return _unauthorized_response("x")
            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                from app.auth.oauth_app_credentials import get_oauth_app_credentials_cached
                from app.connectors.x_ads import XAdsConnector, XOAuth1Token

                async with state.db_session_factory() as db:
                    creds = await get_oauth_app_credentials_cached(db, "x")
                return await XAdsConnector(creds.client_id, creds.client_secret).update_campaign_status(
                    XOAuth1Token(token=oauth1_tokens[0], token_secret=oauth1_tokens[1]),
                    account_id,
                    campaign_id,
                    status,
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for X Ads write. Supported: update_campaign_status",
            }

        elif platform == "reddit":
            token = _get_provider_token("reddit")
            if not token:
                return _unauthorized_response("reddit")
            reddit = state.reddit_connector
            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await reddit.update_campaign_status(token, account_id, campaign_id, status)
            elif action == "update_campaign_budget":
                if not campaign_id or daily_budget_usd is None:
                    return {
                        "error": True,
                        "message": "campaign_id and daily_budget_usd are required for update_campaign_budget",
                    }
                return await reddit.update_campaign_budget(token, account_id, campaign_id, daily_budget_usd)
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Reddit Ads write. Supported: update_campaign_status, update_campaign_budget",
            }

        elif platform == "apple":
            token = _get_provider_token("apple")
            if not token:
                return _unauthorized_response("apple")
            if action == "update_campaign_status":
                if not campaign_id or not status:
                    return {
                        "error": True,
                        "message": "campaign_id and status are required for update_campaign_status",
                    }
                return await state.apple_connector.update_campaign_status(
                    token, account_id, campaign_id, status
                )
            return {
                "error": True,
                "message": f"Unknown action '{action}' for Apple Ads write. Supported: update_campaign_status",
            }

        return {"error": True, "message": f"Unknown platform: {platform}"}
