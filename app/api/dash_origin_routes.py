"""Dash-origin surface: static files + scoped query. No Fluxito session APIs."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

import app.app_state as app_state
from app.dashboards.data_plane import run_alias_query
from app.dashboards.embed_token import verify_embed_token
from app.dashboards.origin import content_security_policy
from app.dashboards.runtime import resolve_artifact_path, workdir_for
from app.models.dashboard import Dashboard

router = APIRouter()

_CSP = None


def _csp() -> str:
    global _CSP
    if _CSP is None:
        _CSP = content_security_policy()
    return _CSP


def _dash_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Content-Security-Policy": _csp(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "ALLOWALL",
        "Cache-Control": "no-store",
    }
    # ALLOWALL is not a real token; browsers ignore unknown X-Frame-Options.
    # CSP frame-ancestors is the real control. Drop the dummy header.
    headers.pop("X-Frame-Options", None)
    if extra:
        headers.update(extra)
    return headers


def _sdk_bytes() -> bytes:
    from app.dashboards.origin import app_origin as _app_origin
    from app.dashboards.runtime import _sdk_source

    return _sdk_source(_app_origin()).encode("utf-8")


@router.get("/dash-health")
async def dash_health():
    return JSONResponse({"ok": True, "surface": "dash"}, headers=_dash_headers())


@router.get("/fluxito.js")
async def fluxito_sdk():
    return Response(
        content=_sdk_bytes(),
        media_type="text/javascript; charset=utf-8",
        headers=_dash_headers({"Cache-Control": "no-store"}),
    )


async def _load_dash(slug: str) -> Dashboard | None:
    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        return result.scalar_one_or_none()


def _guess_type(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json; charset=utf-8",
        ".map": "application/json; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
    }
    if suffix in mapping:
        return mapping[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


@router.get("/s/{slug}")
@router.get("/s/{slug}/{path:path}")
async def serve_artifact(slug: str, path: str = ""):
    dash = await _load_dash(slug)
    if not dash or getattr(dash, "kind", None) != "hosted":
        return JSONResponse({"error": "Not found"}, status_code=404, headers=_dash_headers())

    workdir = workdir_for(dash.user_id, dash.id)
    rel = path.strip()
    if not rel or rel.endswith("/"):
        rel = (dash.manifest or {}).get("entrypoint") or "index.html"

    dest = resolve_artifact_path(workdir, rel)
    if dest is None and not path:
        dest = resolve_artifact_path(workdir, "index.html")
    if dest is None:
        return JSONResponse({"error": "Not found"}, status_code=404, headers=_dash_headers())

    data = dest.read_bytes()
    media = _guess_type(dest)
    if dest.suffix.lower() == ".html":
        from app.dashboards.runtime import inject_sdk_tag, rewrite_absolute_assets

        text = data.decode("utf-8", errors="replace")
        text = inject_sdk_tag(text)
        text = rewrite_absolute_assets(text, slug)
        data = text.encode("utf-8")
    return Response(content=data, media_type=media, headers=_dash_headers())


@router.post("/query")
async def dash_query(request: Request):
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    payload = verify_embed_token(token)
    if payload is None:
        return JSONResponse(
            {"error": True, "error_type": "unauthorized", "message": "Bad or expired embed token"},
            status_code=403,
            headers=_dash_headers(),
        )

    slug = str(payload.get("slug") or "")
    dash = await _load_dash(slug)
    if not dash or getattr(dash, "kind", None) != "hosted":
        return JSONResponse(
            {"error": True, "message": "Not found"},
            status_code=404,
            headers=_dash_headers(),
        )
    if str(dash.id) != str(payload.get("dashboard_id") or ""):
        return JSONResponse(
            {"error": True, "error_type": "unauthorized", "message": "Token does not match dashboard"},
            status_code=403,
            headers=_dash_headers(),
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": True, "message": "JSON body required"},
            status_code=400,
            headers=_dash_headers(),
        )
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": True, "message": "JSON object required"},
            status_code=400,
            headers=_dash_headers(),
        )

    alias = str(body.get("alias") or "").strip()
    action = str(body.get("action") or "").strip()
    if not alias or not action:
        return JSONResponse(
            {
                "error": True,
                "error_type": "invalid_request",
                "message": "alias and action are required",
            },
            status_code=400,
            headers=_dash_headers(),
        )
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    if isinstance(params, dict):
        params = {k: v for k, v in params.items() if k != "tool"}

    result = await run_alias_query(dash, alias=alias, action=action, params=params)
    status = 200 if not result.get("error") else 400
    return JSONResponse(result, status_code=status, headers=_dash_headers())
