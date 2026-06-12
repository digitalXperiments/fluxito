# app/tools/tracking_plan_tools.py
"""MCP surface for the structured tracking plan.

Registers a single `tracking_plan_v2` legacy tool wired into the `tracking_plan`
unified dispatcher (see TRACKING_PLAN_ROUTES in app/tools/unified.py). The tool
resolves the active project/user/main-branch, then delegates to `run_action`, a
thin testable router over the Plan-1A service functions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import app.app_state as state
from app.auth.mcp_session_manager import no_active_project_response, require_project_ctx
from app.services.tracking_plan import (
    attach_property,
    connect_source_destination,
    create_category,
    create_destination,
    create_event,
    create_metric,
    create_property,
    create_source,
    delete_category,
    delete_destination,
    delete_event,
    delete_metric,
    delete_property,
    delete_source,
    detach_property,
    disconnect_source_destination,
    get_main_branch,
    get_or_create_plan,
    plan_to_dict,
    publish_branch,
    remove_event_destination,
    set_event_destination,
    set_event_sources,
    update_category,
    update_destination,
    update_event,
    update_metric,
    update_property,
    update_source,
    validate_plan,
)
from app.services.tracking_plan.exceptions import (
    ConflictError,
    NotFoundError,
    TrackingPlanError,
    ValidationError,
)

logger = logging.getLogger(__name__)

_ADMIN_ROLES = frozenset(("owner", "admin"))


@dataclass
class _Ctx:
    role: str
    user_id: str
    project_id: str
    plan: Any  # TPPlan


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(error_type: str, message: str) -> dict:
    return {"error": True, "error_type": error_type, "message": message}


async def run_action(session, branch, ctx: _Ctx, action: str, params: dict) -> dict:
    """Route one structured action to the service. Pure over (session, branch,
    ctx) — the registered tool supplies these. Maps service errors to the
    standard MCP error shape."""
    p = params or {}
    try:
        # ---- reads -------------------------------------------------------
        if action == "get_plan":
            data = await plan_to_dict(session, ctx.plan, branch)
            if p.get("summary"):
                return {
                    "plan": data["plan"],
                    "counts": {
                        "events": len(data["events"]),
                        "event_properties": len(data["properties"]["event"]),
                        "user_properties": len(data["properties"]["user"]),
                        "sources": len(data["sources"]),
                        "destinations": len(data["destinations"]),
                        "metrics": len(data["metrics"]),
                    },
                }
            return data

        if action == "get_event":
            data = await plan_to_dict(session, ctx.plan, branch)
            name = p.get("name")
            eid = str(p["event_id"]) if p.get("event_id") else None
            for ev in data["events"]:
                if (name and ev["name"] == name) or (eid and ev["id"] == eid):
                    return ev
            return _err("not_found", f"event {name or eid} not found")

        if action == "validate":
            return await validate_plan(session, ctx.plan, branch)

        # ---- events ------------------------------------------------------
        if action == "create_event":
            fields = _event_fields(p)
            ev = await create_event(session, branch, name=fields.pop("name", p.get("name", "")), **fields)
            return _ok(id=str(ev.id), name=ev.name)
        if action == "update_event":
            ev = await update_event(session, branch, p["event_id"], **_event_fields(p))
            return _ok(id=str(ev.id), name=ev.name)
        if action == "delete_event":
            await delete_event(session, branch, p["event_id"])
            return _ok(deleted=str(p["event_id"]))
        if action == "set_event_sources":
            links = await set_event_sources(session, branch, p["event_id"], p.get("sources", []))
            return _ok(event_id=str(p["event_id"]), source_count=len(links))
        if action == "set_event_destination":
            m = await set_event_destination(
                session, branch, p["event_id"], p["destination_id"], **_event_dest_fields(p)
            )
            return _ok(id=str(m.id))
        if action == "remove_event_destination":
            await remove_event_destination(session, branch, p["event_id"], p["destination_id"])
            return _ok(removed=True)

        # ---- properties --------------------------------------------------
        if action == "create_property":
            pr = await create_property(
                session,
                branch,
                name=p.get("name", ""),
                data_type=p.get("data_type", ""),
                kind=p.get("kind", "event"),
                description=p.get("description"),
                constraints=p.get("constraints"),
                is_pii=bool(p.get("is_pii", False)),
                parent_property_id=p.get("parent_property_id"),
            )
            return _ok(id=str(pr.id), name=pr.name, kind=pr.kind)
        if action == "update_property":
            pr = await update_property(session, branch, p["property_id"], **_property_fields(p))
            return _ok(id=str(pr.id), name=pr.name)
        if action == "delete_property":
            await delete_property(session, branch, p["property_id"])
            return _ok(deleted=str(p["property_id"]))
        if action == "attach_property":
            link = await attach_property(
                session,
                branch,
                p["event_id"],
                p["property_id"],
                required=bool(p.get("required", False)),
                example=p.get("example"),
                override_description=p.get("override_description"),
                sort_order=int(p.get("sort_order", 0)),
            )
            return _ok(id=str(link.id))
        if action == "detach_property":
            await detach_property(session, branch, p["event_id"], p["property_id"])
            return _ok(detached=True)

        # ---- categories --------------------------------------------------
        if action == "create_category":
            c = await create_category(
                session,
                branch,
                name=p.get("name", ""),
                description=p.get("description"),
                color=p.get("color"),
            )
            return _ok(id=str(c.id), name=c.name)
        if action == "update_category":
            c = await update_category(session, branch, p["category_id"], **_category_fields(p))
            return _ok(id=str(c.id), name=c.name)
        if action == "delete_category":
            await delete_category(session, branch, p["category_id"])
            return _ok(deleted=str(p["category_id"]))

        # ---- sources / destinations / routing ----------------------------
        if action == "create_source":
            fields = _source_fields(p)
            s = await create_source(session, branch, name=fields.pop("name", p.get("name", "")), **fields)
            return _ok(id=str(s.id), name=s.name)
        if action == "update_source":
            s = await update_source(session, branch, p["source_id"], **_source_fields(p))
            return _ok(id=str(s.id), name=s.name)
        if action == "delete_source":
            await delete_source(session, branch, p["source_id"])
            return _ok(deleted=str(p["source_id"]))
        if action == "create_destination":
            fields = _dest_fields(p)
            d = await create_destination(
                session,
                branch,
                name=fields.pop("name", p.get("name", "")),
                platform=fields.pop("platform", p.get("platform")),
                **fields,
            )
            return _ok(id=str(d.id), name=d.name)
        if action == "update_destination":
            d = await update_destination(session, branch, p["destination_id"], **_dest_fields(p))
            return _ok(id=str(d.id), name=d.name)
        if action == "delete_destination":
            await delete_destination(session, branch, p["destination_id"])
            return _ok(deleted=str(p["destination_id"]))
        if action == "connect_source_destination":
            r = await connect_source_destination(session, branch, p["source_id"], p["destination_id"])
            return _ok(id=str(r.id))
        if action == "disconnect_source_destination":
            await disconnect_source_destination(session, branch, p["source_id"], p["destination_id"])
            return _ok(disconnected=True)

        # ---- metrics -----------------------------------------------------
        if action == "create_metric":
            m = await create_metric(
                session,
                branch,
                name=p.get("name", ""),
                type=p.get("type", "count"),
                description=p.get("description"),
                event_id=p.get("event_id"),
                property_id=p.get("property_id"),
                filters=p.get("filters"),
            )
            return _ok(id=str(m.id), name=m.name)
        if action == "update_metric":
            m = await update_metric(session, branch, p["metric_id"], **_metric_fields(p))
            return _ok(id=str(m.id), name=m.name)
        if action == "delete_metric":
            await delete_metric(session, branch, p["metric_id"])
            return _ok(deleted=str(p["metric_id"]))

        # ---- exports ---------------------------------------------------------
        if action == "export_markdown":
            from app.services.tracking_plan import plan_to_markdown

            data = await plan_to_dict(session, ctx.plan, branch)
            return _ok(format="markdown", content=plan_to_markdown(data))

        # ---- publish (human gate) ----------------------------------------
        if action == "publish":
            if ctx.role not in _ADMIN_ROLES:
                return {
                    "error": True,
                    "error_type": "permission_denied",
                    "message": f"Only project admins (owner/admin) can publish. Your role is '{ctx.role}'.",
                    "instructions_for_claude": (
                        "Publishing is the human approval gate. Tell the user only an owner/admin can publish, "
                        "and that the draft is otherwise ready."
                    ),
                }
            version = await publish_branch(
                session, ctx.plan, branch, user_id=ctx.user_id, changelog=p.get("changelog")
            )
            return _ok(version_id=str(version.id), version_number=version.version_number)

        return _err("unknown_action", f"unknown tracking_plan action '{action}'")

    except ConflictError as exc:
        return _err("conflict", str(exc))
    except ValidationError as exc:
        return _err("validation_failed", str(exc))
    except NotFoundError as exc:
        return _err("not_found", str(exc))
    except TrackingPlanError as exc:
        return _err("tracking_plan_error", str(exc))
    except KeyError as exc:
        return _err("missing_param", f"missing required param: {exc}")


# --- per-entity field pickers (only forward keys the caller actually sent) ---
def _pick(p: dict, keys: tuple[str, ...]) -> dict:
    return {k: p[k] for k in keys if k in p}


def _event_fields(p: dict) -> dict:
    return _pick(
        p,
        (
            "name",
            "display_name",
            "description",
            "category_id",
            "tags",
            "trigger_type",
            "trigger_config",
            "purpose",
            "owner_business",
            "owner_technical",
            "consent_required",
        ),
    )


def _event_dest_fields(p: dict) -> dict:
    return _pick(p, ("dest_event_name", "property_mappings", "enabled", "notes"))


def _property_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "data_type", "constraints", "is_pii", "parent_property_id"))


def _category_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "color"))


def _source_fields(p: dict) -> dict:
    return _pick(p, ("name", "platform_type", "description", "connector_ref"))


def _dest_fields(p: dict) -> dict:
    return _pick(p, ("name", "platform", "platform_account_id", "config"))


def _metric_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "type", "event_id", "property_id", "filters"))


async def _run_tracking_plan_v2(action: str, params: dict) -> dict:
    """Resolve project/user/main-branch from the MCP session context, then run
    one structured action through `run_action`. Commits on success, rolls back
    on error. Shared by the dispatcher-facing shim below."""
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()
    user_ctx = state.current_user_ctx.get()
    if not user_ctx:
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    async with state.db_session_factory() as session:
        plan = await get_or_create_plan(session, project_id=project_ctx.project_id, user_id=user_ctx.user_id)
        branch = await get_main_branch(session, plan)
        ctx = _Ctx(
            role=project_ctx.role,
            user_id=str(user_ctx.user_id),
            project_id=str(project_ctx.project_id),
            plan=plan,
        )
        result = await run_action(session, branch, ctx, action, params)
        if not result.get("error"):
            await session.commit()
        return result


class _TrackingPlanV2Tool:
    """Lightweight stand-in for a FastMCP `Tool`, exposing only the `.run(args)`
    contract the unified dispatcher uses.

    Why not `@mcp_server.tool`? FastMCP's `func_metadata` turns a
    `(action: str, **params)` signature into a pydantic arg-model with a
    REQUIRED field literally named `params`. The unified dispatcher
    (`_make_dispatcher`) calls every legacy tool as `legacy_tool.run(call_args)`
    with a FLAT dict like `{"action": "create_event", "name": "purchase"}` —
    which has no `params` key, so pydantic rejected the call with
    `ValidationError: params: Field required` BEFORE the body ran. (An
    explicit-arg signature is no good either: FastMCP's arg-model silently
    DROPS undeclared keys, so every action param would be lost.)

    This shim is never exposed to MCP clients — discovery happens through the
    `tracking_plan` unified dispatcher and `tracking_plan.json` spec — so it
    needs no public schema. It simply reconstructs `(action, params)` from the
    flat `call_args` and delegates to `run_action`."""

    name = "tracking_plan_v2"

    async def run(self, arguments: dict, *args: Any, **kwargs: Any) -> dict:
        args_in = dict(arguments or {})
        action = args_in.pop("action", "")
        return await _run_tracking_plan_v2(action, args_in)


def register_tracking_plan_tools(mcp_server: Any) -> None:
    """Register the structured tracking-plan tool. Wired into the unified
    `tracking_plan` dispatcher via TRACKING_PLAN_ROUTES (Task 4).

    Registered as a `.run(call_args)` shim placed directly into the tool
    manager (not via `@mcp_server.tool`) — see `_TrackingPlanV2Tool` for why.
    It lands in `tm._tools["tracking_plan_v2"]`, so the unified rewire moves it
    into `tm._legacy_tools` (it is listed in `legacy_names`) and drops it from
    the public surface."""
    mcp_server._tool_manager._tools["tracking_plan_v2"] = _TrackingPlanV2Tool()
