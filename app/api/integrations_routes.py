"""Integrations API — manage install-wide OAuth app credentials.

GET    /api/integrations               — list all 6 platforms with status
GET    /api/integrations/<platform>    — single platform details
POST   /api/integrations/<platform>    — upsert credentials
DELETE /api/integrations/<platform>    — remove (env fallback resumes)
POST   /api/integrations/<platform>/test — stub validation

All endpoints require install-admin role: project-`owner` or project-`admin`
of any project the user belongs to.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.app_state as app_state
from app.auth.oauth_app_credentials import (
    OAuthAppNotConfigured,
    SUPPORTED_PLATFORMS,
    delete_oauth_app_credentials,
    get_oauth_app_credentials,
    list_oauth_app_status,
    upsert_oauth_app_credentials,
)
from app.config import settings
from app.models.project import ProjectMember
from app.models.user import User
from app.settings_service import (
    RUNTIME_SETTING_BY_KEY,
    delete_setting,
    list_runtime_settings,
    set_setting,
)
from app.templating import render

router = APIRouter()


DEV_CONSOLE_URL = {
    "google": "https://console.cloud.google.com/apis/credentials",
    "meta": "https://developers.facebook.com/apps",
    "tiktok": "https://business-api.tiktok.com/portal",
    "snap": "https://kit.snapchat.com/manage/apps",
    "linkedin": "https://www.linkedin.com/developers/apps",
    "pinterest": "https://developers.pinterest.com/apps/",
}

# Maps each OAuth platform to its tutorial markdown file(s).
# Google is special: one foundational setup + per-product guides.
PLATFORM_TUTORIALS: dict[str, list[str]] = {
    "google": [
        "google-cloud-setup",
        "google-analytics-4",
        "google-tag-manager",
        "google-ads",
        "search-console",
        "bigquery",
    ],
    "meta": ["meta-ads"],
    "tiktok": ["tiktok-ads"],
    "snap": ["snap-ads"],
    "linkedin": ["linkedin-ads"],
    "pinterest": ["pinterest-ads"],
}


def _redirect_uris_for(platform: str) -> list[str]:
    base = (settings.APP_BASE_URL or "http://localhost:8000").rstrip("/")
    by_platform: dict[str, list[str]] = {
        "google": [
            f"{base}/auth/google/identity/callback",
            f"{base}/auth/google/data/callback",
            f"{base}/auth/google/signin/callback",
        ],
        "meta": [f"{base}/auth/meta/callback"],
        "tiktok": [f"{base}/auth/tiktok/callback"],
        "snap": [f"{base}/auth/snap/callback"],
        "linkedin": [f"{base}/auth/linkedin/callback"],
        "pinterest": [f"{base}/auth/pinterest/callback"],
    }
    return by_platform[platform]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_user(request: Request) -> User | None:
    """Resolve authenticated user from cookie or MCP bearer token.

    Mirrors the pattern used in project_routes.py / google_oauth_routes.py:
    call ``_resolve_user_ctx`` to obtain a UserContext, then load the User row.
    """
    from app.api.google_oauth_routes import _resolve_user_ctx

    user_ctx = await _resolve_user_ctx(request)
    if user_ctx is None:
        return None

    try:
        user_uuid = uuid.UUID(user_ctx.user_id)
    except (ValueError, AttributeError):
        return None

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_uuid))
        return result.scalar_one_or_none()


async def _require_install_admin(request: Request, db: AsyncSession) -> User:
    """Resolve the current user and verify they are owner or admin of any project.

    Raises HTTPException 401 if not signed in, 403 if signed in but no
    matching project membership.
    """
    user = await _resolve_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    is_admin = (
        await db.execute(
            select(ProjectMember.id)
            .where(ProjectMember.user_id == user.id)
            .where(ProjectMember.role.in_(("owner", "admin")))
            .where(ProjectMember.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none() is not None

    if not is_admin:
        raise HTTPException(status_code=403, detail="Install admin role required")

    return user


def _validate_platform(platform: str) -> None:
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform!r}")


def _mask(client_id: str) -> str:
    if not client_id:
        return ""
    if len(client_id) <= 12:
        return "***"
    return f"{client_id[:6]}…{client_id[-4:]}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/integrations")
async def list_integrations(request: Request):
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        items = await list_oauth_app_status(db)
    return JSONResponse({"items": items})


@router.get("/api/integrations/{platform}")
async def get_integration(request: Request, platform: str):
    _validate_platform(platform)
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        try:
            creds = await get_oauth_app_credentials(db, platform)
            configured = True
            source = creds.source
            client_id_masked = _mask(creds.client_id)
        except OAuthAppNotConfigured:
            configured = False
            source = "unconfigured"
            client_id_masked = None

    return JSONResponse(
        {
            "platform": platform,
            "configured": configured,
            "source": source,
            "client_id_masked": client_id_masked,
            "redirect_uris": _redirect_uris_for(platform),
            "dev_console_url": DEV_CONSOLE_URL[platform],
            "tutorial_slugs": PLATFORM_TUTORIALS.get(platform, []),
        }
    )


def _load_tutorial(slug: str) -> tuple[str, str]:
    """Load a tutorial markdown file. Returns (title, html_content).

    Raises HTTPException on bad slug or missing file.
    """
    import re
    from pathlib import Path

    import markdown as md

    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,60}", slug):
        raise HTTPException(status_code=400, detail="Invalid tutorial slug")

    tutorial_dir = Path(__file__).resolve().parent.parent.parent / "docs" / "tutorials"
    filepath = tutorial_dir / f"{slug}.md"

    try:
        filepath.resolve().relative_to(tutorial_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tutorial slug")

    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="Tutorial not found")

    raw = filepath.read_text(encoding="utf-8")

    # Extract H1 title
    lines = raw.split("\n")
    title = slug.replace("-", " ").title()
    if lines and lines[0].startswith("# "):
        title = lines[0].lstrip("# ").strip()
        lines = lines[1:]

    html_body = md.markdown(
        "\n".join(lines),
        extensions=["tables", "fenced_code"],
    )
    return title, html_body


# Tutorial metadata for the index page
TUTORIAL_CATEGORIES = [
    {
        "title": "Google platforms",
        "description": "One OAuth app covers GA4, GTM, Ads, Search Console, and BigQuery.",
        "tutorials": [
            ("google-cloud-setup", "Google Cloud setup", "~20 min", "Foundational — create the OAuth app"),
            ("google-analytics-4", "Google Analytics 4", "~10 min", "GA4 property access"),
            ("google-tag-manager", "Google Tag Manager", "~10 min", "Container read/write"),
            ("google-ads", "Google Ads", "~15 min", "Requires developer token approval"),
            ("search-console", "Search Console", "~10 min", "SEO data"),
            ("bigquery", "BigQuery", "~15 min", "Warehouse queries"),
        ],
    },
    {
        "title": "Paid media",
        "description": "Each platform needs its own OAuth app registered in the vendor's developer console.",
        "tutorials": [
            ("meta-ads", "Meta Ads", "~20 min", "Facebook & Instagram ads"),
            ("tiktok-ads", "TikTok Ads", "~20 min", "Requires app review"),
            ("linkedin-ads", "LinkedIn Ads", "~20 min", "Requires app review"),
            ("pinterest-ads", "Pinterest Ads", "~15 min", "Requires app review"),
            ("snap-ads", "Snap Ads", "~20 min", "Snapchat marketing API"),
        ],
    },
    {
        "title": "Data warehouses",
        "description": "Credential-based connections — no OAuth app needed.",
        "tutorials": [
            ("snowflake", "Snowflake", "~15 min", "Service account setup"),
            ("redshift", "Redshift", "~20 min", "IAM or password auth"),
        ],
    },
    {
        "title": "Analytics & tag management",
        "description": "Additional analytics platforms and tag managers.",
        "tutorials": [
            ("amplitude", "Amplitude", "~5 min", "API key + secret"),
            ("adobe-analytics", "Adobe Analytics", "~25 min", "Adobe I/O project"),
            ("adobe-launch", "Adobe Launch", "~15 min", "Shares Adobe I/O credentials"),
        ],
    },
]


def _tutorial_page_context(slug: str) -> dict:
    tutorial_count = sum(len(category["tutorials"]) for category in TUTORIAL_CATEGORIES)
    for category in TUTORIAL_CATEGORIES:
        for tutorial in category["tutorials"]:
            if tutorial[0] == slug:
                return {
                    "tutorial_count": tutorial_count,
                    "current_tutorial": tutorial,
                    "current_category": category,
                    "related_tutorials": [item for item in category["tutorials"] if item[0] != slug][:4],
                }
    return {
        "tutorial_count": tutorial_count,
        "current_tutorial": None,
        "current_category": None,
        "related_tutorials": [],
    }


@router.get("/tutorials", response_class=HTMLResponse)
async def tutorials_index(request: Request):
    """Tutorials index page — categorized list of all setup guides."""
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx) if user_ctx else None

    return render(
        request,
        "tutorials/index.html",
        {
            "user": user_view,
            "categories": TUTORIAL_CATEGORIES,
            "tutorial_count": sum(len(category["tutorials"]) for category in TUTORIAL_CATEGORIES),
            "active": "tutorials",
        },
    )


@router.get("/tutorials/{slug}", response_class=HTMLResponse)
async def tutorial_page(request: Request, slug: str):
    """Render a single tutorial as an HTML page."""
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    title, html_body = _load_tutorial(slug)

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx) if user_ctx else None

    return render(
        request,
        "tutorials/detail.html",
        {
            "user": user_view,
            "title": title,
            "slug": slug,
            "tutorial_html": html_body,
            **_tutorial_page_context(slug),
            "active": "tutorials",
        },
    )


@router.post("/api/integrations/{platform}")
async def upsert_integration(request: Request, platform: str):
    _validate_platform(platform)
    payload = await request.json()
    client_id = (payload.get("client_id") or "").strip()
    client_secret = (payload.get("client_secret") or "").strip()
    extra = payload.get("extra") or None

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="client_id and client_secret are required")
    if not isinstance(client_id, str) or len(client_id) > 255:
        raise HTTPException(status_code=400, detail="client_id must be a string ≤ 255 chars")
    if not isinstance(client_secret, str) or len(client_secret) < 4:
        raise HTTPException(status_code=400, detail="client_secret looks too short")

    async with app_state.db_session_factory() as db:
        user = await _require_install_admin(request, db)
        await upsert_oauth_app_credentials(
            db,
            platform=platform,
            client_id=client_id,
            client_secret=client_secret,
            extra=extra if isinstance(extra, dict) else None,
            configured_by_user_id=user.id,
        )
        await db.commit()

    return JSONResponse({"success": True, "platform": platform})


@router.delete("/api/integrations/{platform}")
async def delete_integration(request: Request, platform: str):
    _validate_platform(platform)
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        deleted = await delete_oauth_app_credentials(db, platform=platform)
        await db.commit()
    return JSONResponse({"success": True, "deleted": deleted})


@router.post("/api/integrations/{platform}/test")
async def test_integration(request: Request, platform: str):
    """Light-weight validation: confirm credentials are configured and look
    syntactically valid. Most platforms don't expose a no-code 'app exists'
    endpoint, so we keep this conservative.
    """
    _validate_platform(platform)
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        try:
            creds = await get_oauth_app_credentials(db, platform)
        except OAuthAppNotConfigured:
            raise HTTPException(status_code=404, detail=f"{platform} is not configured")

    issues: list[str] = []
    if not creds.client_id:
        issues.append("client_id is empty")
    if not creds.client_secret:
        issues.append("client_secret is empty")
    if len(creds.client_id) < 4:
        issues.append("client_id looks too short")
    if len(creds.client_secret) < 4:
        issues.append("client_secret looks too short")
    if platform == "google" and not (creds.extra or {}).get("developer_token"):
        issues.append("note: GOOGLE_ADS_DEVELOPER_TOKEN extra not set (only required for Google Ads)")

    ok = not any(i for i in issues if not i.startswith("note:"))
    return JSONResponse(
        {
            "platform": platform,
            "ok": ok,
            "source": creds.source,
            "issues": issues,
        }
    )


@router.get("/api/settings/system")
async def list_system_settings(request: Request):
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        items = await list_runtime_settings(db)
    return JSONResponse({"items": items})


@router.post("/api/settings/system/{key}")
async def upsert_system_setting(request: Request, key: str):
    spec = RUNTIME_SETTING_BY_KEY.get(key)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unsupported setting: {key!r}")

    payload = await request.json()
    value = payload.get("value")
    if spec.is_secret and (value is None or str(value) == ""):
        raise HTTPException(status_code=400, detail="Secret value cannot be blank")
    if spec.value_type == "int":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Value must be an integer")
    elif spec.value_type == "float":
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Value must be a number")
    elif value is None:
        value = ""
    else:
        value = str(value)

    async with app_state.db_session_factory() as db:
        user = await _require_install_admin(request, db)
        await set_setting(
            db,
            key=key,
            value=value,
            is_secret=spec.is_secret,
            updated_by_user_id=user.id,
        )
        await db.commit()

    return JSONResponse({"success": True, "key": key})


@router.delete("/api/settings/system/{key}")
async def reset_system_setting(request: Request, key: str):
    if key not in RUNTIME_SETTING_BY_KEY:
        raise HTTPException(status_code=400, detail=f"Unsupported setting: {key!r}")
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        deleted = await delete_setting(db, key=key)
        await db.commit()
    return JSONResponse({"success": True, "deleted": deleted})


# ---------------------------------------------------------------------------
# Settings UI page
# ---------------------------------------------------------------------------


@router.get("/settings/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request):
    """Render the install-admin integrations settings page."""
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    # Auth gate: must be signed in and an install admin
    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        items = await list_oauth_app_status(db)

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx) if user_ctx else None

    return render(
        request,
        "settings/integrations.html",
        {
            "user": user_view,
            "platforms": items,
            "active": "integrations",
        },
    )


@router.get("/settings/system", response_class=HTMLResponse)
async def system_settings_page(request: Request):
    """Render the install-admin system settings page."""
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    async with app_state.db_session_factory() as db:
        await _require_install_admin(request, db)
        items = await list_runtime_settings(db)

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx) if user_ctx else None

    # Group settings by category for the card-based UI
    from collections import OrderedDict

    category_meta = OrderedDict(
        [
            (
                "email",
                {
                    "title": "Email / SMTP",
                    "description": "Outbound email configuration for notifications and reports.",
                },
            ),
            ("rate_limiting", {"title": "Rate Limiting", "description": "API throttling defaults per user."}),
            (
                "observability",
                {"title": "Observability", "description": "Error tracking and performance monitoring."},
            ),
            (
                "platform",
                {
                    "title": "Storage & Platform",
                    "description": "Artifact storage, CORS, and tool availability.",
                },
            ),
        ]
    )
    grouped: dict[str, list] = {k: [] for k in category_meta}
    for item in items:
        cat = item.get("category", "platform")
        grouped.setdefault(cat, []).append(item)

    categories = [
        {"key": k, **category_meta[k], "settings": grouped.get(k, [])}
        for k in category_meta
        if grouped.get(k)
    ]

    return render(
        request,
        "settings/system.html",
        {
            "user": user_view,
            "settings_items": items,
            "categories": categories,
            "active": "system_settings",
        },
    )
