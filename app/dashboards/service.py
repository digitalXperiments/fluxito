"""Deploy / update / delete / describe hosted web dashboards."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

import app.app_state as state
from app.config import settings
from app.dashboards.artifact import (
    ArtifactError,
    ValidatedArtifact,
    validate_artifact,
)
from app.dashboards.connections import bind_requirements, list_bindable_connections
from app.dashboards.origin import dash_src
from app.dashboards.runtime import (
    delete_workdir,
    ensure_house_files,
    workdir_for,
    write_artifact,
)
from app.models.dashboard import DASHBOARD_MAX_PER_USER, Dashboard

MAX_TITLE_LEN = 120
MAX_DESC_LEN = 400


def _make_slug() -> str:
    return secrets.token_urlsafe(6)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _public_url(slug: str) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/live-dashboards/{slug}"


def _artifact_ready(dash: Dashboard) -> bool:
    workdir = workdir_for(dash.user_id, dash.id)
    entry = (dash.manifest or {}).get("entrypoint") or "index.html"
    return (workdir / entry).is_file()


def hosted_payload(dash: Dashboard, *, include_manifest: bool = True) -> dict:
    bindings = list(dash.connection_bindings or [])
    ready = _artifact_ready(dash) if getattr(dash, "kind", None) == "hosted" else False
    host_status = "ready" if ready else (dash.host_error and "error") or dash.host_status or "missing"
    if ready:
        host_status = "ready"
    out = {
        "id": str(dash.id),
        "dashboard_id": str(dash.id),
        "title": dash.title,
        "description": dash.description,
        "kind": getattr(dash, "kind", None) or "legacy_cards",
        "slug": dash.share_slug,
        "share_slug": dash.share_slug,
        "is_public": dash.is_public,
        "url": _public_url(dash.share_slug),
        "live_url": _public_url(dash.share_slug),
        "embed_url": dash_src(dash.share_slug),
        "host_status": host_status,
        "host_error": None if ready else dash.host_error,
        "bindings": bindings,
        "connection_bindings": bindings,
        "artifact_hash": getattr(dash, "artifact_hash", None),
        "created_at": dash.created_at.isoformat() if dash.created_at else None,
        "updated_at": dash.updated_at.isoformat() if dash.updated_at else None,
    }
    if include_manifest:
        out["manifest"] = getattr(dash, "manifest", None) or {}
    return out


async def _count_user_dashboards(db, uid: UUID) -> int:
    result = await db.execute(select(func.count()).select_from(Dashboard).where(Dashboard.user_id == uid))
    return int(result.scalar() or 0)


async def persist_and_write(
    dash: Dashboard,
    artifact: ValidatedArtifact,
    *,
    uid: UUID,
) -> dict:
    available = await list_bindable_connections(dash.project_id, uid)
    bindings = bind_requirements(artifact.manifest.connections, available)

    dash.kind = "hosted"
    dash.manifest = artifact.manifest.to_dict()
    dash.artifact_hash = artifact.digest
    dash.connection_bindings = bindings
    dash.title = artifact.manifest.title[:MAX_TITLE_LEN]
    dash.updated_at = _now()
    dash.host_port = None
    dash.runtime_token = None

    workdir = workdir_for(dash.user_id, dash.id)
    try:
        write_artifact(
            workdir,
            artifact,
            bindings=bindings,
            dashboard_id=str(dash.id),
            slug=dash.share_slug,
        )
        dash.host_status = "ready"
        dash.host_error = None
    except Exception as exc:
        dash.host_status = "error"
        dash.host_error = str(exc)[:500]

    return hosted_payload(dash)


async def deploy_hosted(
    *,
    title: str,
    files,
    description: str | None,
    manifest,
    user,
    project_id,
) -> dict:
    try:
        artifact = validate_artifact(files, manifest, fallback_title=title)
    except ArtifactError as exc:
        return {"error": True, "error_type": "invalid_artifact", "message": str(exc), "errors": exc.errors}

    uid = UUID(user.user_id)
    proj = UUID(project_id) if project_id else None

    async with state.db_session_factory() as db:
        if await _count_user_dashboards(db, uid) >= DASHBOARD_MAX_PER_USER:
            return {
                "error": True,
                "error_type": "limit_reached",
                "message": f"Maximum {DASHBOARD_MAX_PER_USER} dashboards per user. Delete one first.",
            }
        dash = Dashboard(
            user_id=uid,
            project_id=proj,
            owner_email=getattr(user, "email", None) or "",
            owner_name=getattr(user, "display_name", None),
            title=(title or artifact.manifest.title).strip()[:MAX_TITLE_LEN],
            description=(description or "")[:MAX_DESC_LEN] or None,
            share_slug=_make_slug(),
            is_public=False,
            query_scopes=[],
            filter_presets=[],
            filters=[],
            kind="hosted",
            manifest={},
            connection_bindings=[],
        )
        db.add(dash)
        await db.flush()
        payload = await persist_and_write(dash, artifact, uid=uid)
        await db.commit()
        await db.refresh(dash)

    payload.update(hosted_payload(dash))
    if artifact.warnings:
        payload["warnings"] = artifact.warnings
    return payload


async def update_hosted(
    *,
    dashboard_id: str,
    files,
    title: str | None,
    description: str | None,
    manifest,
    user,
) -> dict:
    try:
        artifact = validate_artifact(files, manifest, fallback_title=title)
    except ArtifactError as exc:
        return {"error": True, "error_type": "invalid_artifact", "message": str(exc), "errors": exc.errors}

    uid = UUID(user.user_id)
    try:
        dash_uuid = UUID(dashboard_id)
    except (ValueError, AttributeError):
        return {"error": True, "message": f"Invalid dashboard_id format: '{dashboard_id}'."}

    async with state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(Dashboard.id == dash_uuid, Dashboard.user_id == uid)
        )
        dash = result.scalar_one_or_none()
        if not dash:
            return {"error": True, "message": f"Dashboard '{dashboard_id}' not found or not yours."}
        if title:
            dash.title = title.strip()[:MAX_TITLE_LEN]
        if description is not None:
            dash.description = (description or "")[:MAX_DESC_LEN] or None
        payload = await persist_and_write(dash, artifact, uid=uid)
        await db.commit()
        await db.refresh(dash)

    payload.update(hosted_payload(dash))
    if artifact.warnings:
        payload["warnings"] = artifact.warnings
    return payload


async def delete_hosted(dashboard_id: str, user) -> dict:
    uid = UUID(user.user_id)
    try:
        dash_uuid = UUID(dashboard_id)
    except (ValueError, AttributeError):
        return {"error": True, "message": f"Invalid dashboard_id format: '{dashboard_id}'."}

    async with state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(Dashboard.id == dash_uuid, Dashboard.user_id == uid)
        )
        dash = result.scalar_one_or_none()
        if not dash:
            return {"error": True, "message": f"Dashboard '{dashboard_id}' not found or not yours."}
        user_id = dash.user_id
        await db.delete(dash)
        await db.commit()

    delete_workdir(user_id, dash_uuid)
    return {"success": True, "deleted": dashboard_id}


async def ensure_ready(dash: Dashboard) -> Dashboard:
    """Make sure house files exist for a hosted dashboard."""
    if getattr(dash, "kind", None) != "hosted":
        return dash
    workdir = workdir_for(dash.user_id, dash.id)
    entry = (dash.manifest or {}).get("entrypoint") or "index.html"
    if not (workdir / entry).is_file():
        dash.host_status = "error"
        dash.host_error = "Artifact files are missing on disk. Redeploy the dashboard."
        return dash
    try:
        ensure_house_files(workdir)
        dash.host_status = "ready"
        dash.host_error = None
    except Exception as exc:
        dash.host_status = "error"
        dash.host_error = str(exc)[:500]
    return dash


# Back-compat name used by a few routes during the Streamlit era.
ensure_running = ensure_ready


async def rebind_dashboard(dash: Dashboard) -> list[dict]:
    from app.dashboards.artifact import ConnectionRequirement

    reqs = []
    for item in (dash.manifest or {}).get("connections") or []:
        if isinstance(item, dict) and item.get("alias") and item.get("type"):
            reqs.append(
                ConnectionRequirement(
                    alias=item["alias"],
                    type=item["type"],
                    required=item.get("required", True),
                )
            )
    available = await list_bindable_connections(dash.project_id, dash.user_id)
    bindings = bind_requirements(reqs, available)
    dash.connection_bindings = bindings
    return bindings


def _bindings_from_caller(
    requested: list,
    available: list[dict],
    manifest_connections: list,
) -> list[dict] | dict:
    """Build bindings from an explicit bind_dashboard payload."""
    from app.dashboards.artifact import CONNECTION_TOOL, ConnectionRequirement

    if not isinstance(requested, list):
        return {"error": True, "message": "bindings must be a list of {alias, type, connection_id?}."}

    reqs: list[ConnectionRequirement] = []
    want_connection: dict[str, str] = {}
    for item in requested:
        if not isinstance(item, dict):
            return {"error": True, "message": "each binding must be an object."}
        alias = str(item.get("alias") or "").strip()
        typ = str(item.get("type") or "").strip()
        if not alias or not typ:
            return {"error": True, "message": "each binding needs alias and type."}
        if item.get("tool"):
            return {
                "error": True,
                "error_type": "tool_not_allowed",
                "message": (
                    "bind_dashboard does not accept a tool name. "
                    "The host maps type → tool and injects credentials."
                ),
            }
        reqs.append(
            ConnectionRequirement(
                alias=alias,
                type=typ,
                required=item.get("required", True),
            )
        )
        conn_id = item.get("connection_id")
        if conn_id:
            want_connection[alias] = str(conn_id)

    wanted_ids = set(want_connection.values())
    preferred = [c for c in available if str(c.get("connection_id") or "") in wanted_ids]
    rest = [c for c in available if str(c.get("connection_id") or "") not in wanted_ids]
    bindings = bind_requirements(reqs, preferred + rest)
    for b in bindings:
        b["tool"] = CONNECTION_TOOL.get(b.get("type") or "")
    if manifest_connections:
        allowed = {
            str(item.get("alias"))
            for item in manifest_connections
            if isinstance(item, dict) and item.get("alias")
        }
        if allowed:
            bindings = [b for b in bindings if b.get("alias") in allowed] or bindings
    return bindings


async def bind_hosted(
    *,
    dashboard_id: str,
    bindings,
    user,
) -> dict:
    """MCP bind_dashboard: attach live connections to a hosted dashboard."""
    uid = UUID(user.user_id)
    try:
        dash_uuid = UUID(dashboard_id)
    except (ValueError, AttributeError):
        return {"error": True, "message": f"Invalid dashboard_id format: '{dashboard_id}'."}

    async with state.db_session_factory() as db:
        result = await db.execute(
            select(Dashboard).where(Dashboard.id == dash_uuid, Dashboard.user_id == uid)
        )
        dash = result.scalar_one_or_none()
        if not dash:
            return {"error": True, "message": f"Dashboard '{dashboard_id}' not found or not yours."}
        if getattr(dash, "kind", None) != "hosted":
            return {
                "error": True,
                "error_type": "hosted_only",
                "message": (
                    "bind_dashboard only works on hosted web dashboards. "
                    "Call get_dashboard_authoring_guide then deploy_dashboard."
                ),
            }

        if bindings:
            available = await list_bindable_connections(dash.project_id, uid)
            built = _bindings_from_caller(
                bindings,
                available,
                (dash.manifest or {}).get("connections") or [],
            )
            if isinstance(built, dict) and built.get("error"):
                return built
            dash.connection_bindings = built
        else:
            await rebind_dashboard(dash)

        dash.updated_at = _now()
        await db.commit()
        await db.refresh(dash)

    return hosted_payload(dash)
