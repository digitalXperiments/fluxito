"""
Seed the template library with curated, cross-platform system templates.

Rather than a sprawling catalog of single-platform recipes, Fluxito ships a
small set of flagship templates that are genuinely hard to build by hand:
blended multi-platform reports, attribution comparisons, and executive-ready
rollups. Every system template is cross-platform (2+ connectors) and each is
something an analyst would otherwise spend hours wiring up manually.

The seed runs on every app start and is idempotent:
  • Existing system templates matching a new slug are UPDATED in place
    (title/description/steps refresh, use_count preserved).
  • Any legacy system templates that are NOT in the curated list are
    soft-deactivated (is_active=False) so they disappear from the UI
    without deleting user references.

Run manually via:
    python -m app.db.seed_templates
"""

import asyncio
import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)


# ============================================================================
# Shared variable blocks — reused across templates to keep the shape consistent
# ============================================================================

_DATE_7D = [
    {"key": "date_range_start", "label": "Start date", "type": "date", "default": "-7d"},
    {"key": "date_range_end", "label": "End date", "type": "date", "default": "today"},
]
_DATE_30D = [
    {"key": "date_range_start", "label": "Start date", "type": "date", "default": "-30d"},
    {"key": "date_range_end", "label": "End date", "type": "date", "default": "today"},
]
_DATE_90D = [
    {"key": "date_range_start", "label": "Start date", "type": "date", "default": "-90d"},
    {"key": "date_range_end", "label": "End date", "type": "date", "default": "today"},
]


# ============================================================================
# Curated Cross-Platform Templates
# ============================================================================

SYSTEM_TEMPLATES = [
    # ------------------------------------------------------------------
    # 1.  Cross-Channel Marketing Pulse (flagship weekly view)
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Cross-Channel Marketing Pulse",
        "description": (
            "Flagship weekly view that stitches GA4 sessions and revenue together "
            "with spend and conversions from every paid channel you've connected. "
            "One place to see whether last week was a good week across the entire "
            "marketing stack."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "cross-channel-marketing-pulse",
        "icon": "activity",
        "required_platforms": ["ga4", "google_ads", "meta"],
        "variables": list(_DATE_7D),
        "steps": [
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "GA4 — Sessions, users & revenue",
                "card_type": "METRIC",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["sessions", "activeUsers", "totalRevenue", "conversions"],
                    "dimensions": ["date"],
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Ads — Spend & conversions",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_campaign_performance",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "meta",
                "card_title": "Meta Ads — Spend & results",
                "card_type": "TABLE",
                "params": {
                    "platform": "meta",
                    "action": "get_campaign_performance",
                    "account_id": "{{meta_account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                },
            },
            {
                "tool": "cross_platform_report",
                "card_title": "Blended spend vs revenue",
                "card_type": "METRIC",
                "params": {
                    "report": "blended_channel_pulse",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 2. Paid Acquisition ROI
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Paid Acquisition ROI",
        "description": (
            "Spend, conversions, and revenue across every paid channel, blended "
            "into a single ROAS table. Shows which platforms are actually earning "
            "their budget — and which are coasting."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "paid-acquisition-roi",
        "icon": "dollar-sign",
        "required_platforms": ["ga4", "google_ads", "meta"],
        "variables": list(_DATE_30D),
        "steps": [
            {
                "tool": "cross_platform_report",
                "card_title": "Blended ROAS by channel",
                "card_type": "TABLE",
                "params": {
                    "report": "paid_roi_by_channel",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Ads — Top campaigns by ROAS",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_campaign_performance",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                    "order_by": "roas_desc",
                    "limit": 10,
                },
            },
            {
                "tool": "marketing_read",
                "platform": "meta",
                "card_title": "Meta Ads — Top campaigns by ROAS",
                "card_type": "TABLE",
                "params": {
                    "platform": "meta",
                    "action": "get_campaign_performance",
                    "account_id": "{{meta_account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                    "order_by": "roas_desc",
                    "limit": 10,
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "GA4 — Paid-source revenue attribution",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["totalRevenue", "sessions", "ecommercePurchases"],
                    "dimensions": ["sessionSource", "sessionMedium"],
                    "filter": "sessionMedium in (cpc, paid, ppc)",
                    "limit": 20,
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 3. Executive Monthly Report (CMO-ready)
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Executive Monthly Report",
        "description": (
            "CMO-ready monthly rollup: topline KPIs, month-over-month deltas, "
            "paid vs organic split, and channel-level contribution. Designed to "
            "be exported straight to a leadership deck."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "executive-monthly-report",
        "icon": "briefcase",
        "required_platforms": ["ga4", "google_ads"],
        "variables": [
            {"key": "date_range_start", "label": "Start date", "type": "date", "default": "-30d"},
            {"key": "date_range_end", "label": "End date", "type": "date", "default": "today"},
            {"key": "compare_start", "label": "Compare start", "type": "date", "default": "-60d"},
            {"key": "compare_end", "label": "Compare end", "type": "date", "default": "-31d"},
        ],
        "steps": [
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Topline KPIs — this period",
                "card_type": "METRIC",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["activeUsers", "sessions", "totalRevenue", "conversions", "conversionRate"],
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Topline KPIs — previous period",
                "card_type": "METRIC",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{compare_start}}",
                    "end_date": "{{compare_end}}",
                    "metrics": ["activeUsers", "sessions", "totalRevenue", "conversions", "conversionRate"],
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Revenue by channel group",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["totalRevenue", "sessions", "conversionRate"],
                    "dimensions": ["sessionDefaultChannelGroup"],
                },
            },
            {
                "tool": "cross_platform_report",
                "card_title": "Paid spend summary (all platforms)",
                "card_type": "TABLE",
                "params": {
                    "report": "paid_spend_rollup",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 4. Tracking Health Check (cross-platform)
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Tracking Health Check",
        "description": (
            "Catches broken or missing measurement before a client meeting: GTM "
            "container audit, GA4 key-event coverage, Google Ads conversion action "
            "health, and Meta Pixel firing summary — all in one run."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "tracking-health-check",
        "icon": "shield-check",
        "required_platforms": ["gtm", "ga4", "google_ads"],
        "variables": list(_DATE_30D),
        "steps": [
            {
                "tool": "tagmanager_audit",
                "platform": "gtm",
                "card_title": "GTM — Container audit",
                "card_type": "AUDIT",
                "params": {
                    "platform": "gtm",
                    "action": "audit_container",
                    "container_id": "{{container_id}}",
                },
            },
            {
                "tool": "analytics_audit",
                "platform": "ga4",
                "card_title": "GA4 — Key-event coverage",
                "card_type": "AUDIT",
                "params": {
                    "platform": "ga4",
                    "action": "audit_key_events",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
            {
                "tool": "marketing_audit",
                "platform": "google_ads",
                "card_title": "Google Ads — Conversion action health",
                "card_type": "AUDIT",
                "params": {
                    "platform": "google",
                    "action": "audit_conversions",
                    "account_id": "{{account_id}}",
                },
            },
            {
                "tool": "marketing_audit",
                "platform": "meta",
                "card_title": "Meta Pixel — Firing summary",
                "card_type": "AUDIT",
                "params": {
                    "platform": "meta",
                    "action": "audit_pixel",
                    "account_id": "{{meta_account_id}}",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 5. E-commerce Performance Review
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "E-commerce Performance Review",
        "description": (
            "Complete e-commerce health check: purchase funnel drop-offs, top "
            "products, Google Shopping campaign performance, and Meta catalog "
            "ads side by side."
        ),
        "category": "ecommerce",
        "template_type": "system",
        "slug": "ecommerce-performance-review",
        "icon": "shopping-cart",
        "required_platforms": ["ga4", "google_ads", "meta"],
        "variables": list(_DATE_30D),
        "steps": [
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Purchase funnel events",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["eventCount", "totalUsers"],
                    "dimensions": ["eventName"],
                    "filter": "eventName in (view_item, add_to_cart, begin_checkout, purchase)",
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Top products by revenue",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["itemRevenue", "itemsPurchased", "itemsViewed"],
                    "dimensions": ["itemName"],
                    "limit": 15,
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Shopping — Campaign performance",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_shopping_performance",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "meta",
                "card_title": "Meta — Catalog ads performance",
                "card_type": "TABLE",
                "params": {
                    "platform": "meta",
                    "action": "get_catalog_performance",
                    "account_id": "{{meta_account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 6. Attribution Comparison
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Attribution Comparison",
        "description": (
            "Same conversions viewed through three lenses: GA4 data-driven, "
            "GA4 last-click, and platform-reported numbers from Google Ads and "
            "Meta. Surfaces the gaps everyone argues about in QBRs."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "attribution-comparison",
        "icon": "git-branch",
        "required_platforms": ["ga4", "google_ads", "meta"],
        "variables": list(_DATE_30D),
        "steps": [
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "GA4 — Data-driven attribution",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["conversions", "totalRevenue"],
                    "dimensions": ["sessionDefaultChannelGroup"],
                    "attribution_model": "data_driven",
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "GA4 — Last-click attribution",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["conversions", "totalRevenue"],
                    "dimensions": ["sessionDefaultChannelGroup"],
                    "attribution_model": "last_click",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Ads — Reported conversions",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_conversions",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "meta",
                "card_title": "Meta Ads — Reported conversions",
                "card_type": "TABLE",
                "params": {
                    "platform": "meta",
                    "action": "get_conversions",
                    "account_id": "{{meta_account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 7. Blended CAC Trend
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Blended CAC Trend",
        "description": (
            "Weekly blended customer acquisition cost across every paid channel. "
            "Total spend divided by new customers, trended so you can see whether "
            "efficiency is drifting."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "blended-cac-trend",
        "icon": "trending-down",
        "required_platforms": ["ga4", "google_ads", "meta"],
        "variables": list(_DATE_90D),
        "steps": [
            {
                "tool": "cross_platform_report",
                "card_title": "Blended CAC — weekly trend",
                "card_type": "CHART",
                "params": {
                    "report": "blended_cac_trend",
                    "granularity": "week",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
            {
                "tool": "cross_platform_report",
                "card_title": "New customers by channel",
                "card_type": "TABLE",
                "params": {
                    "report": "new_customers_by_channel",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "First-time vs returning customer split",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["transactions", "totalRevenue"],
                    "dimensions": ["newVsReturning"],
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 8. Budget Pacing & Reallocation
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Budget Pacing & Reallocation",
        "description": (
            "Month-to-date spend vs target pace on every paid platform, with a "
            "side-by-side efficiency comparison. Highlights which channels are "
            "over-spending and where to shift budget."
        ),
        "category": "ppc",
        "template_type": "system",
        "slug": "budget-pacing-reallocation",
        "icon": "sliders",
        "required_platforms": ["google_ads", "meta"],
        "variables": [
            {"key": "date_range_start", "label": "Month start", "type": "date", "default": "month_start"},
            {"key": "date_range_end", "label": "Today", "type": "date", "default": "today"},
            {
                "key": "monthly_budget",
                "label": "Total monthly budget ($)",
                "type": "number",
                "default": 10000,
            },
        ],
        "steps": [
            {
                "tool": "cross_platform_report",
                "card_title": "MTD spend vs target pace",
                "card_type": "METRIC",
                "params": {
                    "report": "budget_pacing",
                    "total_budget": "{{monthly_budget}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Ads — Spend & CPA by campaign",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_campaign_performance",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                    "order_by": "cost_desc",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "meta",
                "card_title": "Meta Ads — Spend & CPA by campaign",
                "card_type": "TABLE",
                "params": {
                    "platform": "meta",
                    "action": "get_campaign_performance",
                    "account_id": "{{meta_account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                    "order_by": "cost_desc",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 9. Full-Funnel Conversion Report
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Full-Funnel Conversion Report",
        "description": (
            "Top of funnel to purchase, measured consistently across GA4 and "
            "every ad platform. Drop-off rates at each stage + paid-channel "
            "contribution to the bottom of the funnel."
        ),
        "category": "cross_channel",
        "template_type": "system",
        "slug": "full-funnel-conversion",
        "icon": "filter",
        "required_platforms": ["ga4", "google_ads"],
        "variables": list(_DATE_30D),
        "steps": [
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Funnel stages (awareness → purchase)",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["eventCount", "totalUsers"],
                    "dimensions": ["eventName"],
                    "filter": "eventName in (page_view, view_item, add_to_cart, begin_checkout, purchase)",
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Conversion rate by channel",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["sessions", "conversions", "conversionRate"],
                    "dimensions": ["sessionDefaultChannelGroup"],
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Ads — Top-of-funnel vs bottom-of-funnel",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_campaign_performance",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                    "group_by": "campaign_type",
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
    # ------------------------------------------------------------------
    # 10. Paid vs Organic Overlap
    # ------------------------------------------------------------------
    {
        "user_id": None,
        "title": "Paid vs Organic Overlap",
        "description": (
            "Which queries are you paying for that you'd have won organically "
            "anyway? Cross-references GA4 organic sessions with Google Ads "
            "search-term performance to find wasted spend."
        ),
        "category": "seo",
        "template_type": "system",
        "slug": "paid-vs-organic-overlap",
        "icon": "layers",
        "required_platforms": ["ga4", "google_ads"],
        "variables": list(_DATE_30D),
        "steps": [
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Organic vs paid — revenue split",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["sessions", "totalRevenue", "conversions"],
                    "dimensions": ["sessionDefaultChannelGroup"],
                    "filter": "sessionDefaultChannelGroup in (Organic Search, Paid Search)",
                },
            },
            {
                "tool": "marketing_read",
                "platform": "google_ads",
                "card_title": "Google Ads — Search term performance",
                "card_type": "TABLE",
                "params": {
                    "platform": "google",
                    "action": "get_search_terms",
                    "account_id": "{{account_id}}",
                    "date_range_start": "{{date_range_start}}",
                    "date_range_end": "{{date_range_end}}",
                    "limit": 50,
                },
            },
            {
                "tool": "analytics_read",
                "platform": "ga4",
                "card_title": "Top organic landing pages",
                "card_type": "TABLE",
                "params": {
                    "platform": "ga4",
                    "action": "run_report",
                    "property_id": "{{property_id}}",
                    "start_date": "{{date_range_start}}",
                    "end_date": "{{date_range_end}}",
                    "metrics": ["sessions", "totalRevenue"],
                    "dimensions": ["landingPage"],
                    "filter": "sessionDefaultChannelGroup == Organic Search",
                    "limit": 20,
                },
            },
        ],
        "min_tier": "pro",
        "is_featured": True,
        "is_active": True,
    },
]


# ============================================================================
# Seed Function
# ============================================================================


async def seed_templates(db_session_factory) -> int:
    """
    Upsert curated system templates.

      • New slugs are INSERTED.
      • Existing curated slugs are UPDATED in place (preserving use_count and id).
      • Any other legacy system templates are marked is_active=False.

    Returns the number of rows inserted + updated.
    """
    from app.models.template import Template

    curated_slugs = {t["slug"] for t in SYSTEM_TEMPLATES}
    touched = 0

    async with db_session_factory() as db:
        # Load all existing system templates keyed by slug
        result = await db.execute(select(Template).where(Template.template_type == "system"))
        existing = {tpl.slug: tpl for tpl in result.scalars().all()}

        # Insert or update curated templates
        for tpl_data in SYSTEM_TEMPLATES:
            slug = tpl_data["slug"]
            if slug in existing:
                tpl = existing[slug]
                # Refresh all content fields; keep id, created_at, use_count
                for key, val in tpl_data.items():
                    if key in ("user_id", "template_type"):
                        continue
                    setattr(tpl, key, val)
                touched += 1
            else:
                db.add(Template(**tpl_data))
                touched += 1

        # Soft-deactivate legacy templates that are no longer curated
        deactivated = 0
        for slug, tpl in existing.items():
            if slug not in curated_slugs and tpl.is_active:
                tpl.is_active = False
                deactivated += 1

        await db.commit()

        logger.info(
            f"Template seed complete: {touched} curated template(s) upserted, "
            f"{deactivated} legacy template(s) deactivated"
        )

    return touched


# ============================================================================
# Main (for manual runs)
# ============================================================================


async def main():
    """Manual seed entry point."""
    from app.db.database import async_session_factory

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    result = await seed_templates(async_session_factory)
    logger.info(f"Seed complete: {result} templates processed")


if __name__ == "__main__":
    asyncio.run(main())
