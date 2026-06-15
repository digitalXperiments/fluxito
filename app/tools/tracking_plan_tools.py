# app/tools/tracking_plan_tools.py
"""MCP surface for the structured tracking plan.

Registers a single `tracking_plan_v2` legacy tool wired into the `tracking_plan`
unified dispatcher (see TRACKING_PLAN_ROUTES in app/tools/unified.py). The tool
resolves the active project/user/main-branch, then delegates to `run_action`, a
thin testable router over the Plan-1A service functions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

import app.app_state as state
from app.auth.mcp_session_manager import no_active_project_response, require_project_ctx
from app.services.tracking_plan import (
    add_comment,
    add_property_to_bundle,
    attach_bundle_to_event,
    attach_property,
    comment_to_dict,
    connect_source_destination,
    create_bundle,
    create_category,
    create_destination,
    create_event,
    create_metric,
    create_property,
    create_source,
    delete_bundle,
    delete_category,
    delete_comment,
    delete_destination,
    delete_event,
    delete_metric,
    delete_property,
    delete_source,
    detach_property,
    diff_events,
    disconnect_source_destination,
    edit_comment,
    get_main_branch,
    get_or_create_plan,
    get_or_seed_rules,
    list_comments,
    list_rules,
    match_key,
    normalize_name,
    plan_to_dict,
    publish_branch,
    record_activity,
    remove_event_destination,
    remove_property_from_bundle,
    resolve_comment,
    rule_to_dict,
    set_event_destination,
    set_event_sources,
    set_rule_enabled,
    update_bundle,
    update_category,
    update_destination,
    update_event,
    update_metric,
    update_property,
    update_rule,
    update_source,
    validate_plan,
)
from app.services.tracking_plan import branches as _branches
from app.services.tracking_plan.branches import get_branch
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


def _serialize_branch(b: Any) -> dict:
    """Serialize a TPBranch to a plain dict for MCP/HTTP responses."""
    return {
        "id": str(b.id),
        "name": b.name,
        "is_main": b.is_main,
        "status": b.status,
        "review_status": b.review_status,
        "description": b.description,
        "base_branch_id": str(b.base_branch_id) if b.base_branch_id else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "merged_at": b.merged_at.isoformat() if b.merged_at else None,
    }


async def resolve_branch(session: Any, plan: Any, ref: Any) -> Any:
    """Return the main branch when ref is falsy, otherwise resolve by id or name."""
    if not ref:
        return await get_main_branch(session, plan)
    return await get_branch(session, plan, ref)


async def _dispatch(session, branch, ctx: _Ctx, action: str, params: dict) -> dict:
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

        if action == "run_coverage_audit":
            return await _run_coverage_audit(session, branch, ctx)

        if action == "list_dashboard_cards":
            return await _list_dashboard_cards(session, ctx)

        if action == "reconcile_preview":
            return await _reconcile_preview(session, ctx.plan, branch, p)

        if action == "get_overview":
            return await _build_overview(session, ctx.plan, branch)

        # ---- validation rules --------------------------------------------
        if action == "list_rules":
            rules = await list_rules(session, ctx.plan)
            if not rules:
                rules = await get_or_seed_rules(session, ctx.plan)
            return {"rules": [rule_to_dict(r) for r in rules]}

        if action == "update_rule":
            _sentinel = object()
            config_val = p.get("config", _sentinel)
            config_provided = config_val is not _sentinel
            r = await update_rule(
                session,
                ctx.plan,
                p["rule_id"],
                config=config_val if config_provided else None,
                config_provided=config_provided,
                severity=p.get("severity"),
            )
            return _ok(id=str(r.id), rule_type=r.rule_type)

        if action == "set_rule_enabled":
            r = await set_rule_enabled(session, ctx.plan, p["rule_id"], enabled=bool(p["enabled"]))
            return _ok(id=str(r.id), enabled=r.enabled)

        # ---- events ------------------------------------------------------
        if action == "create_event":
            fields = _event_fields(p)
            ev = await create_event(session, branch, name=fields.pop("name", p.get("name", "")), **fields)
            return _ok(id=str(ev.id), name=ev.name)
        if action == "create_event_with_properties":
            return await _create_event_with_properties(session, branch, p)
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
                is_list=bool(p.get("is_list", False)),
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

        # ---- property bundles --------------------------------------------
        if action == "create_bundle":
            b = await create_bundle(session, branch, name=p.get("name", ""), description=p.get("description"))
            return _ok(id=str(b.id), name=b.name)
        if action == "update_bundle":
            b = await update_bundle(session, branch, p["bundle_id"], **_bundle_fields(p))
            return _ok(id=str(b.id), name=b.name)
        if action == "delete_bundle":
            await delete_bundle(session, branch, p["bundle_id"])
            return _ok(deleted=str(p["bundle_id"]))
        if action == "add_property_to_bundle":
            link = await add_property_to_bundle(
                session,
                branch,
                p["bundle_id"],
                p["property_id"],
                required=bool(p.get("required", False)),
                sort_order=int(p.get("sort_order", 0)),
            )
            return _ok(id=str(link.id))
        if action == "remove_property_from_bundle":
            await remove_property_from_bundle(session, branch, p["bundle_id"], p["property_id"])
            return _ok(removed=True)
        if action == "attach_bundle_to_event":
            links = await attach_bundle_to_event(session, branch, p["event_id"], p["bundle_id"])
            return _ok(event_id=str(p["event_id"]), property_count=len(links))

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

        # ---- branch management -------------------------------------------
        if action == "create_branch":
            from_ref = p.get("from")
            from_branch = await resolve_branch(session, ctx.plan, from_ref) if from_ref else None
            b = await _branches.create_branch(
                session,
                ctx.plan,
                name=p["name"],
                user_id=ctx.user_id,
                from_branch=from_branch,
                description=p.get("description"),
            )
            return _ok(id=str(b.id), name=b.name)

        if action == "list_branches":
            bs = await _branches.list_branches(session, ctx.plan)
            return {"branches": [_serialize_branch(b) for b in bs]}

        if action == "get_branch":
            b = await _branches.get_branch(session, ctx.plan, p["branch_id"])
            return _serialize_branch(b)

        if action == "diff":
            base_branch = await resolve_branch(session, ctx.plan, p.get("base"))
            head_branch = await _branches.get_branch(session, ctx.plan, p["head"])
            return await _branches.diff_branches(session, ctx.plan, base_branch, head_branch)

        if action == "merge_branch":
            if ctx.role not in _ADMIN_ROLES:
                return _err(
                    "permission_denied",
                    f"Only project admins (owner/admin) can merge branches. Your role is '{ctx.role}'.",
                )
            b = await _branches.get_branch(session, ctx.plan, p["branch_id"])
            result = await _branches.merge_branch(
                session, ctx.plan, b, user_id=ctx.user_id, changelog=p.get("changelog")
            )
            return _ok(**result)

        if action == "set_review_status":
            b = await _branches.get_branch(session, ctx.plan, p["branch_id"])
            b = await _branches.set_review_status(
                session, b, p["review_status"], reviewer_id=p.get("reviewer_id")
            )
            return _ok(id=str(b.id), review_status=b.review_status)

        if action == "abandon_branch":
            b = await _branches.get_branch(session, ctx.plan, p["branch_id"])
            await _branches.abandon_branch(session, b)
            return _ok()

        # ---- comments -------------------------------------------------------
        if action == "add_comment":
            c = await add_comment(
                session,
                branch,
                entity_type=p["entity_type"],
                entity_id=p["entity_id"],
                author_id=ctx.user_id,
                body=p.get("body", ""),
                parent_id=p.get("parent_id"),
                mentions=p.get("mentions"),
            )
            return _ok(id=str(c.id))

        if action == "list_comments":
            comments = await list_comments(
                session,
                branch,
                entity_type=p.get("entity_type"),
                entity_id=p.get("entity_id"),
            )
            return {"comments": [comment_to_dict(c) for c in comments]}

        if action == "resolve_comment":
            await resolve_comment(
                session,
                p["comment_id"],
                resolved=bool(p.get("resolved", True)),
            )
            return _ok()

        if action == "edit_comment":
            await edit_comment(session, p["comment_id"], body=p.get("body", ""))
            return _ok()

        if action == "delete_comment":
            await delete_comment(session, p["comment_id"])
            return _ok()

        # ---- reconcile ---------------------------------------------------
        if action == "reconcile_apply":
            return await _reconcile_apply(session, branch, ctx, p)

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


# Successful write actions log one tp_activity row. Read + comment actions are
# intentionally absent → skipped. Map: action -> the entity the change is about.
_ENTITY_BY_ACTION: dict[str, str] = {
    "create_event": "event",
    "create_event_with_properties": "event",
    "update_event": "event",
    "delete_event": "event",
    "set_event_sources": "event",
    "set_event_destination": "event",
    "remove_event_destination": "event",
    "attach_property": "event",
    "detach_property": "event",
    "attach_bundle_to_event": "event",
    "create_property": "property",
    "update_property": "property",
    "delete_property": "property",
    "create_source": "source",
    "update_source": "source",
    "delete_source": "source",
    "connect_source_destination": "source",
    "disconnect_source_destination": "source",
    "create_destination": "destination",
    "update_destination": "destination",
    "delete_destination": "destination",
    "create_metric": "metric",
    "update_metric": "metric",
    "delete_metric": "metric",
    "create_category": "category",
    "update_category": "category",
    "delete_category": "category",
    "create_bundle": "bundle",
    "update_bundle": "bundle",
    "delete_bundle": "bundle",
    "add_property_to_bundle": "bundle",
    "remove_property_from_bundle": "bundle",
    "create_branch": "branch",
    "merge_branch": "branch",
    "set_review_status": "branch",
    "abandon_branch": "branch",
    "publish": "plan",
    "update_rule": "rule",
    "set_rule_enabled": "rule",
}
_ID_PARAM_BY_ENTITY: dict[str, str] = {
    "event": "event_id",
    "property": "property_id",
    "source": "source_id",
    "destination": "destination_id",
    "metric": "metric_id",
    "category": "category_id",
    "bundle": "bundle_id",
    "rule": "rule_id",
}
_VERB_BY_PREFIX: dict[str, str] = {
    "create": "created",
    "update": "updated",
    "delete": "deleted",
    "attach": "updated",
    "detach": "updated",
    "set": "updated",
    "remove": "updated",
    "connect": "linked",
    "disconnect": "unlinked",
    "add": "updated",
    "merge": "merged",
    "publish": "published",
    "abandon": "abandoned",
}


def _activity_entity_id(action: str, entity_type: str, result: dict, params: dict, branch: Any) -> str | None:
    rid = result.get("id")
    if action.startswith("create_") and rid:
        return str(rid)
    key = _ID_PARAM_BY_ENTITY.get(entity_type)
    if key and params.get(key):
        return str(params[key])
    if entity_type == "branch":
        return str(branch.id)
    return str(rid) if rid else None


def _log_activity(session: Any, ctx: _Ctx, branch: Any, action: str, result: dict, params: dict) -> None:
    if not result.get("ok"):
        return
    entity_type = _ENTITY_BY_ACTION.get(action)
    if not entity_type:
        return
    raw_id = _activity_entity_id(action, entity_type, result, params, branch)
    name = result.get("name") or params.get("name")
    verb = _VERB_BY_PREFIX.get(action.split("_", 1)[0], "changed")
    summary = f"{verb} {entity_type}" + (f" '{name}'" if name else "")

    def _as_uuid(v: Any) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(v))
        except (ValueError, TypeError, AttributeError):
            return None

    record_activity(
        session,
        plan_id=ctx.plan.id,
        branch_id=branch.id,
        entity_type=entity_type,
        entity_id=_as_uuid(raw_id),
        actor_id=_as_uuid(ctx.user_id),
        action=action,
        summary=summary,
    )


async def run_action(session, branch, ctx: _Ctx, action: str, params: dict) -> dict:
    """Dispatch one action, then (for successful writes) append a tp_activity row.
    Single choke point shared by the MCP tool and the HTTP API."""
    result = await _dispatch(session, branch, ctx, action, params)
    if not result.get("error"):
        _log_activity(session, ctx, branch, action, result, params or {})
    return result


async def _run_coverage_audit(session: Any, branch: Any, ctx: _Ctx) -> dict:
    """Assemble a tracking-plan coverage audit run from validate_plan findings
    and persist it as an AuditRun + AuditFinding rows on the same session.

    Severity mapping: TP 'error' → AuditFinding 'critical' (so critical_count
    increments correctly); 'warning' and 'info' pass through unchanged.

    Coverage score: 100 minus penalties for open issues weighted by severity
    (critical=10pts, warning=5pts, info=1pt), floored at 0.
    """
    from datetime import UTC, datetime

    from app.models.auditing import AuditFinding, AuditRun

    report = await validate_plan(session, ctx.plan, branch)
    findings = report.get("findings") or []

    # Map TP severity → AuditFinding severity
    def _map_sev(sev: str | None) -> str | None:
        if sev == "error":
            return "critical"
        return sev  # warning | info pass through

    critical = sum(1 for f in findings if f.get("severity") == "error")
    warning = sum(1 for f in findings if f.get("severity") == "warning")
    info = sum(1 for f in findings if f.get("severity") == "info")

    # Coverage score: start at 100 and deduct per finding
    penalty = critical * 10 + warning * 5 + info * 1
    score = max(0, 100 - penalty)

    counts = report.get("counts") or {}
    title = f"Tracking Plan Coverage — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC"

    run = AuditRun(
        project_id=uuid.UUID(ctx.project_id),
        audit_type="tracking_plan_coverage",
        title=title,
        score=score,
        critical_count=critical,
        warning_count=warning,
        info_count=info,
        passed_count=0,
        status="complete",
        triggered_by="claude",
        created_by=uuid.UUID(ctx.user_id),
    )
    session.add(run)
    await session.flush()  # populate run.id

    for f in findings:
        sev = _map_sev(f.get("severity"))
        finding = AuditFinding(
            run_id=run.id,
            project_id=uuid.UUID(ctx.project_id),
            domain="tracking_plan_coverage",
            severity=sev,
            rule_id=f.get("rule_id") or f.get("code"),
            entity_type=f.get("entity_type"),
            entity_id=str(f["entity_id"]) if f.get("entity_id") else None,
            entity_label=f.get("entity_label"),
            message=f.get("message"),
            remediation=f.get("suggested_fix"),
            source="tracking_plan",
            passed=False,
        )
        session.add(finding)

    return _ok(
        audit_run_id=str(run.id),
        score=score,
        critical=critical,
        warning=warning,
        info=info,
        counts=counts,
    )


async def _list_dashboard_cards(session: Any, ctx: _Ctx) -> dict:
    """Return dashboard cards for the active project (lightweight picker list).

    Used by the metrics UI to let users link a TPMetric to a live dashboard card.
    Joins DashboardCard → Dashboard filtered by project_id.
    """
    from app.models.dashboard import Dashboard, DashboardCard

    project_id = uuid.UUID(ctx.project_id)
    result = await session.execute(
        select(DashboardCard, Dashboard.title.label("dashboard_title"))
        .join(Dashboard, DashboardCard.dashboard_id == Dashboard.id)
        .where(Dashboard.project_id == project_id)
        .order_by(Dashboard.title, DashboardCard.title)
    )
    rows = result.all()
    return {
        "cards": [
            {
                "id": str(card.id),
                "title": card.title,
                "dashboard_title": dashboard_title,
                "platform": getattr(card, "platform", None),
            }
            for card, dashboard_title in rows
        ]
    }


async def _build_overview(session: Any, plan: Any, branch: Any) -> dict:
    """Compose a concise, reasoning-ready plan summary purely from the existing
    plan_to_dict + validate_plan reads (no new service/persistence code)."""
    data = await plan_to_dict(session, plan, branch)
    report = await validate_plan(session, plan, branch)

    events = data["events"]
    sources = data["sources"]
    destinations = data["destinations"]

    event_count_by_category: dict[str, int] = {}
    for ev in events:
        cat = ev.get("category")
        if cat:
            event_count_by_category[cat] = event_count_by_category.get(cat, 0) + 1

    by_severity: dict[str, int] = {"warning": 0, "info": 0}
    for finding in report["findings"]:
        sev = finding.get("severity")
        if sev in by_severity:
            by_severity[sev] += 1

    return {
        "plan": {"name": data["plan"]["name"]},
        "branch": {"name": data["branch"]["name"], "is_main": data["branch"]["is_main"]},
        "counts": {
            "events": len(events),
            "event_properties": len(data["properties"]["event"]),
            "user_properties": len(data["properties"]["user"]),
            "sources": len(sources),
            "destinations": len(destinations),
            "metrics": len(data["metrics"]),
            "categories": len(data["categories"]),
            "bundles": len(data["bundles"]),
        },
        "categories": [
            {"name": c["name"], "event_count": event_count_by_category.get(c["name"], 0)}
            for c in data["categories"]
        ],
        "events": [
            {
                "name": ev["name"],
                "category": ev.get("category"),
                "property_count": len(ev["properties"]),
                "source_count": len(ev["sources"]),
                "destination_count": len(ev["destinations"]),
            }
            for ev in events
        ],
        "sources": [
            {
                "name": s["name"],
                "platform_type": s["platform_type"],
                "destination_count": len(s["destinations"]),
            }
            for s in sources
        ],
        "destinations": [{"name": d["name"], "platform": d["platform"]} for d in destinations],
        "health": {
            "findings_by_severity": by_severity,
            "is_publishable": report["is_publishable"],
        },
    }


async def _find_event_property_by_name(session: Any, branch: Any, name: str) -> Any:
    """Return an existing event-kind library property on the branch by name, or
    None. Mirrors the (branch_id, kind, name) uniqueness used by the service."""
    from app.models.tracking_plan import TPProperty

    result = await session.execute(
        select(TPProperty).where(
            TPProperty.branch_id == branch.id,
            TPProperty.kind == "event",
            TPProperty.name == name,
        )
    )
    return result.scalar_one_or_none()


async def _create_event_with_properties(session: Any, branch: Any, p: dict) -> dict:
    """Create an event, then find-or-create + attach each requested property.

    Orchestrates existing services only (create_event / create_property /
    attach_property). The whole MCP call commits once, so the event + every
    attachment are atomic. Property resolution per item:
      • property_id given  → attach that library property.
      • else, by name      → reuse an existing event-kind property if present,
                             otherwise create one (find-or-create).
    """
    fields = _event_fields(p)
    ev = await create_event(session, branch, name=fields.pop("name", p.get("name", "")), **fields)

    attached: list[dict] = []
    skipped: list[dict] = []
    for spec in p.get("properties") or []:
        if not isinstance(spec, dict):
            skipped.append({"property": spec, "reason": "not an object"})
            continue
        pname = (spec.get("name") or "").strip()
        prop_id = spec.get("property_id")
        created = False
        if prop_id:
            prop = await get_or_raise_property(session, branch, prop_id)
            pname = prop.name
        else:
            if not pname:
                skipped.append({"property": spec, "reason": "missing name and property_id"})
                continue
            prop = await _find_event_property_by_name(session, branch, pname)
            if prop is None:
                prop = await create_property(
                    session,
                    branch,
                    name=pname,
                    data_type=spec.get("data_type", "string"),
                    kind="event",
                    is_pii=bool(spec.get("is_pii", False)),
                    is_list=bool(spec.get("is_list", False)),
                )
                created = True
        await attach_property(
            session,
            branch,
            ev.id,
            prop.id,
            required=bool(spec.get("required", False)),
            example=spec.get("example"),
        )
        attached.append({"name": pname, "property_id": str(prop.id), "created": created})

    return _ok(id=str(ev.id), name=ev.name, attached=attached, skipped=skipped)


async def get_or_raise_property(session: Any, branch: Any, property_id: Any) -> Any:
    """Resolve a branch-scoped library property by id (raises NotFoundError)."""
    from app.models.tracking_plan import TPProperty
    from app.services.tracking_plan.common import get_or_raise

    return await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)


# ---------------------------------------------------------------------------
# Reconcile helpers (Phase C)
# ---------------------------------------------------------------------------


def _norm_opts(p: dict) -> tuple[bool, str, str]:
    """Extract normalize_casing, casing, and match_strategy from params."""
    opts = p.get("options") or {}
    normalize = bool(opts.get("normalize_casing", True))
    casing = opts.get("casing") or "snake_case"
    strategy = opts.get("match_strategy") or "fuzzy"
    return normalize, casing, strategy


def _normalized_incoming(events: list, normalize: bool, casing: str) -> tuple[list[dict], dict[str, str]]:
    """Normalize event names and return (normalized_events, name_map).

    name_map maps each raw input name to its resolved (normalized) name.
    When normalize=False the name is unchanged (identity).
    """
    normalized_names: dict[str, str] = {}
    result: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        raw = ev.get("name") or ""
        resolved = normalize_name(raw, casing) if normalize else raw.strip()
        normalized_names[raw] = resolved
        norm_ev = dict(ev)
        norm_ev["name"] = resolved
        result.append(norm_ev)
    return result, normalized_names


def _resolve_category_id(data: dict, name: str | None) -> str | None:
    """Look up a category id by name from a plan_to_dict result.

    Returns None when the category is absent (no auto-create; scope kept tight).
    """
    if not name:
        return None
    for c in data["categories"]:
        if c["name"] == name:
            return c["id"]
    return None


async def _attach_props(
    session: Any,
    branch: Any,
    event_id: Any,
    specs: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Find-or-create event-kind library properties and attach to *event_id*.

    This is the same find-or-create+attach logic that lives in
    _create_event_with_properties, extracted so reconcile_apply can reuse it
    without duplication.

    Returns (attached, skipped) — same shape as _create_event_with_properties.
    """
    attached: list[dict] = []
    skipped: list[dict] = []
    for spec in specs:
        if not isinstance(spec, dict):
            skipped.append({"property": spec, "reason": "not an object"})
            continue
        pname = (spec.get("name") or "").strip()
        prop_id = spec.get("property_id")
        created = False
        if prop_id:
            prop = await get_or_raise_property(session, branch, prop_id)
            pname = prop.name
        else:
            if not pname:
                skipped.append({"property": spec, "reason": "missing name and property_id"})
                continue
            prop = await _find_event_property_by_name(session, branch, pname)
            if prop is None:
                prop = await create_property(
                    session,
                    branch,
                    name=pname,
                    data_type=spec.get("data_type", "string"),
                    kind="event",
                    is_pii=bool(spec.get("is_pii", False)),
                    is_list=bool(spec.get("is_list", False)),
                )
                created = True
        await attach_property(
            session,
            branch,
            event_id,
            prop.id,
            required=bool(spec.get("required", False)),
            example=spec.get("example"),
        )
        attached.append({"name": pname, "property_id": str(prop.id), "created": created})
    return attached, skipped


async def _reconcile_preview(session: Any, plan: Any, branch: Any, p: dict) -> dict:
    """Dry-run diff of an incoming event list against the current plan.

    Pure read: no writes at all. Deterministic and idempotent — repeated calls
    with the same input and unchanged plan return identical output.
    """
    events = p.get("events") or []
    if not events:
        return _err("validation_failed", "events is required")

    normalize, casing, strategy = _norm_opts(p)
    norm_events, normalized_names = _normalized_incoming(events, normalize, casing)

    data = await plan_to_dict(session, plan, branch)
    diff = diff_events(norm_events, data["events"], match_strategy=strategy)

    return {
        "new": diff["new"],
        "updated": diff["updated"],
        "unchanged": diff["unchanged"],
        "conflicts": diff["conflicts"],
        "normalized_names": normalized_names,
        "summary": {
            "new": len(diff["new"]),
            "updated": len(diff["updated"]),
            "unchanged": len(diff["unchanged"]),
            "conflicts": len(diff["conflicts"]),
        },
    }


def _as_uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


async def _reconcile_apply(session: Any, branch: Any, ctx: _Ctx, p: dict) -> dict:
    """Apply per-event create/update/skip decisions.

    Runs against the resolved branch. Creates events with their properties
    (find-or-create event-kind properties), updates changed scalar fields and
    adds new properties to matched events, and records one activity row per
    applied event.  Skips events whose decision is "skip" or missing.

    The session commits once in _run_tracking_plan_v2 on success, so all
    creates/updates are atomic for the batch.
    """
    events = p.get("events") or []
    if not events:
        return _err("validation_failed", "events is required")

    decisions: dict[str, str] = p.get("decisions") or {}
    normalize, casing, strategy = _norm_opts(p)
    norm_events, _ = _normalized_incoming(events, normalize, casing)

    # Re-read current state so apply is self-contained (not trusting preview ids).
    data = await plan_to_dict(session, ctx.plan, branch)

    def _current_key(name: str) -> str:
        return match_key(name) if strategy == "fuzzy" else name

    current_by_key: dict[str, dict] = {_current_key(ev["name"]): ev for ev in data["events"]}

    created_list: list[dict] = []
    updated_list: list[dict] = []
    skipped_list: list[dict] = []
    errors_list: list[dict] = []

    # Stable ordering for determinism
    norm_events_sorted = sorted(norm_events, key=lambda e: e.get("name") or "")

    for ev in norm_events_sorted:
        norm_name = ev.get("name") or ""
        decision = decisions.get(norm_name, "skip")

        if decision == "skip":
            skipped_list.append({"name": norm_name, "reason": "decision"})
            continue

        if decision == "create":
            # Resolve optional category id from plan data (no auto-create)
            cat_name = ev.get("category")
            cat_id = _resolve_category_id(data, cat_name)
            cat_note = (
                f"category '{cat_name}' not found and was not applied"
                if cat_name and cat_id is None
                else None
            )

            # Build create_event kwargs — only scalar fields create_event knows
            create_kwargs: dict[str, Any] = {}
            if ev.get("display_name") is not None:
                create_kwargs["display_name"] = ev["display_name"]
            if ev.get("description") is not None:
                create_kwargs["description"] = ev["description"]
            if cat_id is not None:
                create_kwargs["category_id"] = cat_id
            # incoming "trigger" maps to trigger_type
            if ev.get("trigger") is not None:
                create_kwargs["trigger_type"] = ev["trigger"]
            # "source" is a separate link, not a scalar — skip with a note

            try:
                new_ev = await create_event(session, branch, name=norm_name, **create_kwargs)
            except ConflictError:
                errors_list.append({"name": norm_name, "error": "conflict"})
                continue

            # Attach properties
            props_specs = ev.get("properties") or []
            if props_specs:
                await _attach_props(session, branch, new_ev.id, props_specs)

            record_activity(
                session,
                plan_id=ctx.plan.id,
                branch_id=branch.id,
                entity_type="event",
                entity_id=_as_uuid(str(new_ev.id)),
                actor_id=_as_uuid(ctx.user_id),
                action="reconcile_apply",
                summary=f"reconciled (created) event '{norm_name}'",
            )
            entry: dict[str, Any] = {"name": norm_name, "id": str(new_ev.id)}
            if cat_note:
                entry["note"] = cat_note
            created_list.append(entry)

        elif decision == "update":
            key = _current_key(norm_name)
            current = current_by_key.get(key)
            if current is None:
                errors_list.append({"name": norm_name, "error": "not_found"})
                continue

            event_id = current["id"]

            # Build update kwargs from changed scalar fields
            update_kwargs: dict[str, Any] = {}
            for field in ("display_name", "description"):
                incoming_val = ev.get(field)
                if incoming_val is not None and incoming_val != current.get(field):
                    update_kwargs[field] = incoming_val

            # Category rename
            cat_name = ev.get("category")
            if cat_name is not None and cat_name != current.get("category"):
                cat_id = _resolve_category_id(data, cat_name)
                if cat_id is not None:
                    update_kwargs["category_id"] = cat_id

            # Casing rename: if normalized name differs from current, rename
            if norm_name != current["name"]:
                update_kwargs["name"] = norm_name

            if update_kwargs:
                try:
                    upd_ev = await update_event(session, branch, event_id, **update_kwargs)
                except (ConflictError, NotFoundError) as exc:
                    errors_list.append({"name": norm_name, "error": str(exc)})
                    continue
            else:
                # Nothing to update on scalars; still may have properties to add
                upd_ev = None

            # Add new properties not yet on the event
            current_prop_names = {p["name"] for p in (current.get("properties") or [])}
            props_to_add = [
                spec
                for spec in (ev.get("properties") or [])
                if (spec.get("name") or "") not in current_prop_names
            ]
            if props_to_add:
                await _attach_props(session, branch, event_id, props_to_add)

            record_activity(
                session,
                plan_id=ctx.plan.id,
                branch_id=branch.id,
                entity_type="event",
                entity_id=_as_uuid(event_id),
                actor_id=_as_uuid(ctx.user_id),
                action="reconcile_apply",
                summary=f"reconciled (updated) event '{norm_name}'",
            )
            updated_list.append({"name": norm_name, "id": str(upd_ev.id) if upd_ev else event_id})

        else:
            # Unknown decision value — treat as skip
            skipped_list.append({"name": norm_name, "reason": f"unknown decision '{decision}'"})

    return _ok(
        created=created_list,
        updated=updated_list,
        skipped=skipped_list,
        errors=errors_list,
    )


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
    return _pick(
        p,
        ("name", "description", "data_type", "constraints", "is_pii", "is_list", "parent_property_id"),
    )


def _bundle_fields(p: dict) -> dict:
    return _pick(p, ("name", "description"))


def _category_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "color"))


def _source_fields(p: dict) -> dict:
    return _pick(p, ("name", "platform_type", "description", "connector_ref"))


def _dest_fields(p: dict) -> dict:
    return _pick(p, ("name", "platform", "platform_account_id", "config"))


def _metric_fields(p: dict) -> dict:
    return _pick(
        p, ("name", "description", "type", "event_id", "property_id", "filters", "dashboard_card_id")
    )


async def _run_tracking_plan_v2(action: str, params: dict) -> dict:
    """Resolve project/user/target-branch from the MCP session context, then run
    one structured action through `run_action`. Commits on success, rolls back
    on error. Shared by the dispatcher-facing shim below.

    The caller may pass ``branch`` (id or name) in params to target a specific
    branch; omitting it (or passing null/empty) defaults to main.
    """
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()
    user_ctx = state.current_user_ctx.get()
    if not user_ctx:
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    # Pop the branch selector before forwarding params to run_action so it
    # doesn't appear as an unknown field to the entity-level service calls.
    params = dict(params or {})
    branch_ref = params.pop("branch", None)

    async with state.db_session_factory() as session:
        plan = await get_or_create_plan(session, project_id=project_ctx.project_id, user_id=user_ctx.user_id)
        branch = await resolve_branch(session, plan, branch_ref)
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
