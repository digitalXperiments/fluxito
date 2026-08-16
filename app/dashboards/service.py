"""Deploy / update / delete / describe hosted Streamlit dashboards."""

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
from app.dashboards.runtime import (
    build_child_env,
    delete_workdir,
    get_handle,
    start_dashboard,
    stop_dashboard,
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


def _data_url(slug: str) -> str:
    base = (settings.INTERNAL_BASE_URL or "http://127.0.0.1:8001").rstrip("/")
    return f"{base}/api/hosted-dashboards/{slug}/query"


def _public_url(slug: str) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/live-dashboards/{slug}"


def hosted_payload(dash: Dashboard, *, include_manifest: bool = True) -> dict:
    bindings = list(dash.connection_bindings or [])
    handle = (
        get_handle(str(dash.id), workdir_for(dash.user_id, dash.id))
        if getattr(dash, "kind", None) == "hosted"
        else None
    )
    host_status = dash.host_status or "stopped"
    if handle is not None:
        host_status = "running"
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
        "embed_url": f"{settings.APP_BASE_URL.rstrip('/')}/hosted/{dash.share_slug}/",
        "host_status": host_status,
        "host_error": dash.host_error,
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


async def persist_and_start(
    dash: Dashboard,
    artifact: ValidatedArtifact,
    *,
    uid: UUID,
    restart: bool = True,
) -> dict:
    available = await list_bindable_connections(dash.project_id, uid)
    bindings = bind_requirements(artifact.manifest.connections, available)
    token = dash.runtime_token or secrets.token_urlsafe(32)

    dash.kind = "hosted"
    dash.manifest = artifact.manifest.to_dict()
    dash.artifact_hash = artifact.digest
    dash.connection_bindings = bindings
    dash.runtime_token = token
    dash.title = artifact.manifest.title[:MAX_TITLE_LEN]
    dash.updated_at = _now()

    workdir = workdir_for(dash.user_id, dash.id)
    data_url = _data_url(dash.share_slug)
    if restart:
        stop_dashboard(str(dash.id), workdir=workdir)
    write_artifact(
        workdir,
        artifact,
        bindings=bindings,
        data_url=data_url,
        runtime_token=token,
        dashboard_id=str(dash.id),
        slug=dash.share_slug,
    )

    host_error = None
    host_status = "stopped"
    host_port = None
    if restart:
        env = build_child_env(
            workdir=workdir,
            data_url=data_url,
            runtime_token=token,
            dashboard_id=str(dash.id),
            bindings=bindings,
            port=0,
            base_path=f"/hosted/{dash.share_slug}",
        )
        try:
            handle = start_dashboard(
                dashboard_id=str(dash.id),
                slug=dash.share_slug,
                workdir=workdir,
                entrypoint=artifact.manifest.entrypoint,
                env=env,
            )
            host_status = "running"
            host_port = handle.port
        except Exception as exc:
            host_status = "error"
            host_error = str(exc)[:500]

    dash.host_status = host_status
    dash.host_port = host_port
    dash.host_error = host_error
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
        payload = await persist_and_start(dash, artifact, uid=uid, restart=True)
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
        payload = await persist_and_start(dash, artifact, uid=uid, restart=True)
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


async def ensure_running(dash: Dashboard) -> Dashboard:
    """Lazy-start a hosted dashboard on first view."""
    if getattr(dash, "kind", None) != "hosted":
        return dash
    workdir = workdir_for(dash.user_id, dash.id)
    if get_handle(str(dash.id), workdir) is not None:
        if dash.host_status != "running":
            dash.host_status = "running"
        return dash
    entrypoint = (dash.manifest or {}).get("entrypoint") or "app.py"
    if not (workdir / entrypoint).exists():
        dash.host_status = "error"
        dash.host_error = "Artifact files are missing on disk. Redeploy the dashboard."
        return dash
    token = dash.runtime_token or secrets.token_urlsafe(32)
    dash.runtime_token = token
    env = build_child_env(
        workdir=workdir,
        data_url=_data_url(dash.share_slug),
        runtime_token=token,
        dashboard_id=str(dash.id),
        bindings=list(dash.connection_bindings or []),
        port=dash.host_port or 0,
        base_path=f"/hosted/{dash.share_slug}",
    )
    try:
        handle = start_dashboard(
            dashboard_id=str(dash.id),
            slug=dash.share_slug,
            workdir=workdir,
            entrypoint=entrypoint,
            env=env,
            port=dash.host_port,
        )
        dash.host_status = "running"
        dash.host_port = handle.port
        dash.host_error = None
    except Exception as exc:
        dash.host_status = "error"
        dash.host_error = str(exc)[:500]
    return dash


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
    """Build bindings from an explicit bind_dashboard payload.

    Caller may name alias / type / connection_id. Tool is always assigned
    from CONNECTION_TOOL — never from the request.
    """
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

    # Prefer an explicitly requested connection_id when several of that type exist.
    wanted_ids = set(want_connection.values())
    preferred = [c for c in available if str(c.get("connection_id") or "") in wanted_ids]
    rest = [c for c in available if str(c.get("connection_id") or "") not in wanted_ids]
    bindings = bind_requirements(reqs, preferred + rest)
    for b in bindings:
        # Host-owned tool map — strip anything a caller might have smuggled.
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
                    "bind_dashboard only works on hosted Streamlit dashboards. "
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
