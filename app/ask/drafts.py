"""Persistence + rendering for Flux-drafted changes (Conversation approve flow).

A "draft" is a concrete change Flux has proposed but not yet applied — today
exclusively GTM workspace/tag edits (design: `Flux - Conversation.dc.html`).
It is persisted as a `FluxDraft` row (`pending` -> `published` | `rejected`)
and surfaced to the client two ways:

1. Live turn: the harness yields ``StreamEvent(type="draft", draft=...)``
   (see ``draft_to_stream_payload`` below) and the client renders the diff
   card inline, right where the marker appears in the stream.
2. History reload: ``GET /api/ask/conversations/{id}`` includes a top-level
   ``drafts`` array (one entry per FluxDraft row for that conversation, in
   whatever state it was left). The client matches each draft's
   ``message_id`` to the assistant message it was attached to and re-renders
   the card in its current state — see ``openConversation()`` in ask.js.

Wiring in a live creation point
--------------------------------
Ask Flux's tool surface is currently **read-only** (see
``app.ask.tools.READ_ONLY_TOOLS`` / ``AskToolBridge``) — nothing calls
``DraftService.create`` yet, so no draft is ever produced by a real
conversation today. This module is deliberately usable ahead of that: once a
write-capable GTM tool is added to the ask allowlist (the natural candidate
is ``tagmanager_write`` action ``propose_change``, which already returns a
concrete, non-live proposal — see ``app/tools/tagmanager_tools.py:2005``),
the tool dispatch loop should, right after receiving that proposal back from
``AskToolBridge.dispatch``:

    from app.ask.drafts import DraftService

    draft = await DraftService().create(
        project_id=uuid.UUID(project_id),
        conversation_id=d.conversation_id,
        message_id=None,  # fill in once the assistant message has been persisted
        created_by=uuid.UUID(uid),
        kind="gtm_workspace_change",
        title="Fix Meta CAPI dedup",
        payload={
            "workspace_label": "GTM-K2X9 · workspace: capi-dedup-fix",
            "target": "TAG: Meta Pixel — Purchase",
            "diff": [
                {"kind": "context", "text": "  fbq('track', 'Purchase', {"},
                {"kind": "context", "text": "    value: {{DLV - value}},"},
                {"kind": "removed", "text": "-  });"},
                {"kind": "added", "text": "+  }, { eventID: {{DLV - transaction_id}} });"},
            ],
        },
    )

and the harness (``app.ask.harness.Harness.run``) should ``yield
StreamEvent(type="draft", draft=draft_to_stream_payload(draft))`` alongside
the assistant's text for that turn.

Approving a draft today only *records* the approval (status -> published,
a synthetic version label) and logs to the activity trail — it does **not**
touch the live GTM container yet. See the ``TODO(gtm-publish)`` in
``DraftService.approve`` for the exact real write call
(``GTMConnector.publish_container`` in ``app/connectors/gtm.py:588``).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import func, select, update

from app import app_state
from app.connectors.errors import ConnectorError
from app.models.flux_draft import FluxDraft


class DraftPublishError(Exception):
    """Raised when approving a draft fails to publish to the live GTM container.
    The draft is left ``pending`` so the user can retry; the API surfaces the
    message."""


_GTM_PUBLISH_SCOPE = "https://www.googleapis.com/auth/tagmanager.publish"


async def _connection_has_publish_scope(connection_id: Any) -> bool:
    """True iff the OAuth connection holds the elevated GTM publish scope.

    The ``tagmanager_write`` tool refuses ``publish_container`` without this
    scope (tagmanager_tools.py); this direct connector publish must enforce the
    same guard so approving a draft can't bypass it."""
    from app.models.connection import OAuthConnection

    try:
        conn_uuid = uuid.UUID(str(connection_id))
    except (ValueError, TypeError, AttributeError):
        return False
    async with app_state.db_session_factory() as db:
        conn = (
            await db.execute(select(OAuthConnection).where(OAuthConnection.id == conn_uuid))
        ).scalar_one_or_none()
    return bool(conn and (conn.scopes or []) and _GTM_PUBLISH_SCOPE in conn.scopes)


def _gtm_ids(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the four GTM identifiers from a draft payload iff all present,
    else None (legacy / mock-only drafts)."""
    gtm = (payload or {}).get("gtm") or {}
    keys = ("connection_id", "account_id", "container_id", "workspace_id")
    if all(gtm.get(k) for k in keys):
        return {k: gtm[k] for k in keys}
    return None


# ---------------------------------------------------------------------------
# Materialization plan — pure, unit-testable normalization of the two draft
# payload shapes (chat ``propose_change`` and the Implement hub's
# ``build_deploy_proposal``) into an ordered list of create/update ops that
# ``DraftService.approve`` executes against the GTM connector before publishing.
#
# An op is a plain dict:
#   {
#     "op":       "create" | "update",
#     "entity":   "variable" | "trigger" | "tag",
#     "name":     str,
#     "gtm_type": str,                 # GTM API v2 resource type, e.g. "gaawe"
#     "parameters": list,              # tag/variable parameter bodies
#     "filters": list | None,          # non-custom-event trigger filters
#     "custom_event_filter": list | None,  # customEvent trigger filter body
#     "event_name": str | None,
#     "firing_trigger_ids": list,      # pre-existing trigger ids for a tag
#     "trigger_ref": str | None,       # name of a trigger op created in THIS plan
#     "tag_id": str | None,            # target of an update op
#     "updates": dict | None,          # field updates for an update op
#   }
#
# Execution order is always variables -> triggers -> tags so a tag can
# reference the ids of triggers created earlier in the same plan.
# ---------------------------------------------------------------------------


def _custom_event_filter(event_name: str) -> list[dict[str, Any]]:
    """A GTM v2 ``customEventFilter`` body matching ``{{_event}}`` == event_name.

    Mirrors the shape the coverage/audit code reads back (``arg0`` holds the
    built-in ``{{_event}}`` variable, ``arg1`` the literal event name)."""
    return [
        {
            "type": "equals",
            "parameter": [
                {"type": "template", "key": "arg0", "value": "{{_event}}"},
                {"type": "template", "key": "arg1", "value": str(event_name)},
            ],
        }
    ]


def _ga4_tag_parameters(
    event_name: str | None, event_parameters: list[Any] | None, cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build the GTM v2 ``parameter`` list for a GA4 event ("gaawe") tag.

    Emits the ``eventName`` template param the audit code keys on, an optional
    measurement/config reference when the proposal carries one, and an
    ``eventSettingsTable`` mapping the plan's event parameters."""
    params: list[dict[str, Any]] = []
    if event_name:
        params.append({"type": "template", "key": "eventName", "value": str(event_name)})
    # Measurement / config reference, if the proposal supplied one (a raw
    # measurement id or a GTM config tag / Google-tag reference).
    mid = cfg.get("measurement_id") or cfg.get("measurementId") or cfg.get("measurement_id_override")
    if mid:
        params.append({"type": "template", "key": "measurementIdOverride", "value": str(mid)})
    config_ref = cfg.get("config_tag") or cfg.get("measurementId_ref") or cfg.get("tag_id_ref")
    if config_ref:
        params.append({"type": "tagReference", "key": "measurementId", "value": str(config_ref)})

    rows: list[dict[str, Any]] = []
    for p in event_parameters or []:
        if not isinstance(p, dict):
            continue
        pname = p.get("name")
        if not pname:
            continue
        pval = p.get("value")
        rows.append(
            {
                "type": "map",
                "map": [
                    {"type": "template", "key": "parameter", "value": str(pname)},
                    {"type": "template", "key": "parameterValue", "value": "" if pval is None else str(pval)},
                ],
            }
        )
    if rows:
        params.append({"type": "list", "key": "eventSettingsTable", "list": rows})
    return params


def _plan_from_ga4_spec(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape A — the Implement hub's ``build_deploy_proposal`` config:
    ``{"tag": {...}, "trigger": {...}, "workspace_id": ...}`` (create-only)."""
    ops: list[dict[str, Any]] = []
    trigger_ref: str | None = None

    trig = cfg.get("trigger")
    if isinstance(trig, dict):
        ev = trig.get("event_filter") or trig.get("event_name")
        name = trig.get("name") or (f"CE — {ev}" if ev else "CE — custom event")
        ttype = trig.get("type") or "customEvent"
        op: dict[str, Any] = {
            "op": "create",
            "entity": "trigger",
            "name": name,
            "gtm_type": ttype,
            "event_name": ev,
            "filters": None,
            "custom_event_filter": _custom_event_filter(ev) if ev and ttype == "customEvent" else None,
        }
        ops.append(op)
        trigger_ref = name

    tag = cfg.get("tag")
    if isinstance(tag, dict):
        ev = tag.get("event_name")
        name = tag.get("name") or (f"GA4 Event — {ev}" if ev else "GA4 Event")
        ops.append(
            {
                "op": "create",
                "entity": "tag",
                "name": name,
                "gtm_type": tag.get("type") or "gaawe",
                "event_name": ev,
                "parameters": _ga4_tag_parameters(ev, tag.get("event_parameters"), tag),
                "firing_trigger_ids": list(tag.get("firing_trigger_ids") or []),
                "trigger_ref": trigger_ref,
            }
        )
    return ops


def _plan_from_entity_spec(
    cfg: dict[str, Any], payload: dict[str, Any], change_type: str
) -> list[dict[str, Any]]:
    """Shape B — the chat ``propose_change`` spec: a single entity described by
    ``entity_type`` / ``name`` / ``change_type`` / ``changes``."""
    entity = str(cfg.get("entity_type") or "tag").lower()
    name = cfg.get("name") or payload.get("entity_name") or "change"
    changes = cfg.get("changes")
    if not isinstance(changes, dict):
        changes = cfg

    if change_type == "update":
        tag_id = cfg.get("tag_id") or changes.get("tag_id")
        if entity != "tag" or not tag_id:
            # Only tag updates are materializable today; anything else falls
            # back to the legacy publish-as-is path (empty plan).
            return []
        updates = {
            k: v for k, v in changes.items() if k not in ("entity_type", "name", "change_type", "tag_id")
        }
        return [
            {
                "op": "update",
                "entity": "tag",
                "name": str(name),
                "tag_id": str(tag_id),
                "updates": updates,
            }
        ]

    if change_type == "delete":
        # Deletes are not auto-materialized — leave to the legacy path.
        return []

    # change_type == "create"
    if entity == "trigger":
        ev = changes.get("event_filter") or changes.get("event_name") or name
        ttype = changes.get("trigger_type") or changes.get("type") or "customEvent"
        return [
            {
                "op": "create",
                "entity": "trigger",
                "name": str(name),
                "gtm_type": str(ttype),
                "event_name": ev,
                "filters": changes.get("filters") if ttype != "customEvent" else None,
                "custom_event_filter": _custom_event_filter(ev) if ttype == "customEvent" else None,
            }
        ]

    if entity == "variable":
        return [
            {
                "op": "create",
                "entity": "variable",
                "name": str(name),
                "gtm_type": str(changes.get("variable_type") or changes.get("type") or "c"),
                "parameters": list(changes.get("parameters") or []),
            }
        ]

    # entity == "tag"
    ev = changes.get("event_name")
    ttype = str(changes.get("tag_type") or changes.get("type") or "gaawe")
    params = changes.get("parameters")
    if not params and ttype == "gaawe":
        params = _ga4_tag_parameters(ev, changes.get("event_parameters"), changes)
    firing = list(changes.get("firing_trigger_ids") or changes.get("firing_triggers") or [])
    return [
        {
            "op": "create",
            "entity": "tag",
            "name": str(name),
            "gtm_type": ttype,
            "event_name": ev,
            "parameters": list(params or []),
            "firing_trigger_ids": firing,
            "trigger_ref": None,
        }
    ]


def _materialization_plan(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize either draft payload shape into an ordered create/update plan.

    Returns ``[]`` for legacy / underspecified drafts (no ``proposed_config``),
    which keeps ``approve`` on the legacy publish-as-is behavior. Pure function
    — no I/O — so it can be unit-tested in isolation."""
    cfg = (payload or {}).get("proposed_config")
    if not isinstance(cfg, dict):
        return []
    change_type = str((payload or {}).get("change_type") or cfg.get("change_type") or "create").lower()

    # Shape A: structured GA4 tag + trigger spec (Implement hub).
    if isinstance(cfg.get("tag"), dict) or isinstance(cfg.get("trigger"), dict):
        return _plan_from_ga4_spec(cfg)

    # Shape B: entity/changes spec (chat propose_change).
    if cfg.get("entity_type"):
        return _plan_from_entity_spec(cfg, payload or {}, change_type)

    return []


def draft_to_stream_payload(draft: FluxDraft) -> dict[str, Any]:
    """The JSON shape sent over SSE (`type: "draft"`) and in the conversation-
    history `drafts` array. Kept intentionally flat so the client's renderer
    can be shared between the live-stream path and the history-reload path.
    """
    return {
        "id": str(draft.id),
        "message_id": str(draft.message_id) if draft.message_id else None,
        "kind": draft.kind,
        "title": draft.title,
        "status": draft.status,  # 'pending' | 'published' | 'rejected'
        "payload": draft.payload,  # {workspace_label, target, diff: [{kind, text}]}
        "published_version": draft.published_version,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
    }


async def _log_activity(
    *, project_id: uuid.UUID, user_id: uuid.UUID | None, event_type: str, description: str
) -> None:
    """Best-effort write to the shared activity trail. Never raises."""
    if user_id is None:
        return
    try:
        from app.models.activity import ActivityEvent

        async with app_state.db_session_factory() as db:
            db.add(
                ActivityEvent(
                    project_id=project_id,
                    user_id=user_id,
                    event_type=event_type,
                    description=description,
                )
            )
            await db.commit()
    except Exception:
        # Auditing must never break the approve/reject flow.
        pass


class DraftService:
    """CRUD + state transitions for FluxDraft rows. Modeled on ConversationService."""

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID | None,
        created_by: uuid.UUID | None,
        kind: str,
        title: str,
        payload: dict[str, Any],
    ) -> FluxDraft:
        async with app_state.db_session_factory() as db:
            draft = FluxDraft(
                project_id=project_id,
                conversation_id=conversation_id,
                message_id=message_id,
                created_by=created_by,
                kind=kind,
                title=title,
                payload=payload,
                status="pending",
            )
            db.add(draft)
            await db.commit()
            await db.refresh(draft)
            return draft

    async def get(self, draft_id: uuid.UUID) -> FluxDraft | None:
        async with app_state.db_session_factory() as db:
            return (await db.execute(select(FluxDraft).where(FluxDraft.id == draft_id))).scalar_one_or_none()

    async def list_for_conversation(self, conversation_id: uuid.UUID) -> list[FluxDraft]:
        async with app_state.db_session_factory() as db:
            rows = (
                await db.execute(
                    select(FluxDraft)
                    .where(FluxDraft.conversation_id == conversation_id)
                    .order_by(FluxDraft.created_at.asc())
                )
            ).scalars()
            return list(rows)

    async def attach_message(self, draft_id: uuid.UUID, message_id: uuid.UUID) -> None:
        """Link a draft to the assistant ChatMessage row it was rendered under,
        once that message has been persisted (message ids aren't known until
        ConversationService.append() runs)."""
        async with app_state.db_session_factory() as db:
            await db.execute(update(FluxDraft).where(FluxDraft.id == draft_id).values(message_id=message_id))
            await db.commit()

    async def approve(self, draft_id: uuid.UUID, *, user_id: uuid.UUID) -> FluxDraft | None:
        """Mark a pending draft published.

        When the draft payload carries the four GTM identifiers (connection,
        account, container, workspace), this runs the *real* publish via
        ``GTMConnector.publish_container`` and records the live version. Legacy
        drafts that lack those identifiers fall back to a synthetic version
        label. On a real publish failure the draft is left ``pending`` and a
        ``DraftPublishError`` is raised for the API to surface."""
        draft = await self.get(draft_id)
        if draft is None or draft.status != "pending":
            return draft

        ids = _gtm_ids(draft.payload)
        new_payload: dict[str, Any] | None = None
        if ids is not None:
            connector = getattr(app_state, "gtm_connector", None)
            if connector is None:
                raise DraftPublishError("GTM is not connected — cannot publish this change.")
            if not await _connection_has_publish_scope(ids["connection_id"]):
                raise DraftPublishError(
                    "Publishing requires the GTM publish scope. Reconnect Google with the 'GTM Publish' tier."
                )

            plan = _materialization_plan(draft.payload)
            if plan:
                # The proposal describes entities to create/update — build them in
                # a dedicated scratch workspace, then publish that workspace so the
                # proposed change actually materializes (previously publish alone
                # never created the tag/trigger).
                published_version, materialization = await self._materialize_and_publish(
                    connector, ids, draft, plan
                )
                new_payload = {
                    **(draft.payload or {}),
                    "materialization": materialization,
                    "published_at": datetime.date.today().isoformat(),
                }
            else:
                # Legacy publish-as-is: no proposal to build, just cut a version
                # from the payload's own workspace.
                try:
                    result = await connector.publish_container(
                        ids["connection_id"],
                        ids["account_id"],
                        ids["container_id"],
                        ids["workspace_id"],
                        draft.title,
                        "Approved via Flux conversation",
                    )
                except Exception as exc:  # network / API error — keep the draft pending
                    raise DraftPublishError(f"GTM publish failed: {exc}") from exc
                if isinstance(result, dict) and result.get("error"):
                    raise DraftPublishError(str(result.get("message") or "GTM publish failed."))
                version = (
                    (result or {}).get("version_id") or (result or {}).get("containerVersionId")
                    if isinstance(result, dict)
                    else None
                )
                published_version = (
                    str(version) if version else await self._next_mock_version(draft.project_id)
                )
        else:
            # Legacy / underspecified draft — no live target, use a mock version.
            published_version = await self._next_mock_version(draft.project_id)

        values: dict[str, Any] = {
            "status": "published",
            "resolved_by": user_id,
            "resolved_at": func.now(),
            "published_version": published_version,
        }
        if new_payload is not None:
            values["payload"] = new_payload
        async with app_state.db_session_factory() as db:
            await db.execute(update(FluxDraft).where(FluxDraft.id == draft_id).values(**values))
            await db.commit()
        draft = await self.get(draft_id)

        await _log_activity(
            project_id=draft.project_id,
            user_id=user_id,
            event_type="draft_published",
            description=f"Approved & published Flux draft '{draft.title}' (v{published_version})",
        )
        return draft

    async def _materialize_and_publish(
        self,
        connector: Any,
        ids: dict[str, Any],
        draft: FluxDraft,
        plan: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """Create the proposed entities in a scratch workspace, then publish it.

        Orchestration:
          1. Create a dedicated ``flux-draft-<short id>`` workspace (isolating
             the publish from any unrelated in-flight edits in the default
             workspace). A caller may opt to reuse a pre-staged workspace by
             setting ``payload.gtm.dedicated_workspace`` truthy.
          2. Create entities in dependency order (variables -> triggers -> tags);
             tags link the ids of triggers created earlier in the plan.
          3. Publish the workspace and return ``(published_version, record)``.

        On ANY create failure this raises ``DraftPublishError`` and does NOT
        publish — the scratch workspace is left unpublished (safer than partial
        deletes; nothing goes live). The caller keeps the draft ``pending``."""
        conn = ids["connection_id"]
        account_id = ids["account_id"]
        container_id = ids["container_id"]

        reuse = bool((draft.payload or {}).get("gtm", {}).get("dedicated_workspace"))
        if reuse:
            workspace_id = str(ids["workspace_id"])
            scratch_created = False
        else:
            short = str(draft.id)[:8]
            try:
                ws = await connector.create_workspace(
                    conn, account_id, container_id, f"flux-draft-{short}", f"Flux draft: {draft.title}"
                )
            except Exception as exc:
                raise DraftPublishError(f"GTM workspace create failed: {exc}") from exc
            workspace_id = str((ws or {}).get("workspace_id"))
            scratch_created = True

        record: dict[str, Any] = {
            "workspace_id": workspace_id,
            "scratch_workspace_created": scratch_created,
            "variables": [],
            "triggers": [],
            "tags": [],
        }
        trigger_id_by_name: dict[str, str] = {}

        # Idempotency guard for the reuse path. A fresh scratch workspace is
        # empty, so every create runs exactly once. But when reusing a fixed
        # workspace_id (``payload.gtm.dedicated_workspace``), a retry after a
        # partial failure would re-run every create against a workspace that
        # already holds the entities from the first attempt — publishing
        # duplicates live. Pre-scan the workspace by name so already-created
        # entities are skipped (and still linked, for the trigger→tag ref).
        existing_variables: set[str] = set()
        existing_triggers: dict[str, str] = {}
        existing_tags: set[str] = set()
        if reuse:
            try:
                vars_r = await connector.list_variables(conn, account_id, container_id, workspace_id)
                existing_variables = {
                    str(v.get("variable_name")) for v in (vars_r or {}).get("variables", [])
                }
                trigs_r = await connector.list_triggers(conn, account_id, container_id, workspace_id)
                existing_triggers = {
                    str(t.get("trigger_name")): str(t.get("trigger_id"))
                    for t in (trigs_r or {}).get("triggers", [])
                }
                tags_r = await connector.list_tags(conn, account_id, container_id, workspace_id)
                existing_tags = {str(t.get("tag_name")) for t in (tags_r or {}).get("tags", [])}
            except ConnectorError as exc:
                raise DraftPublishError(f"GTM workspace read failed: {exc}") from exc

        try:
            for op in (o for o in plan if o["entity"] == "variable" and o["op"] == "create"):
                if op["name"] in existing_variables:
                    continue
                r = await connector.create_variable(
                    conn,
                    account_id,
                    container_id,
                    workspace_id,
                    op["name"],
                    op["gtm_type"],
                    op.get("parameters") or [],
                )
                record["variables"].append((r or {}).get("variable_id"))

            for op in (o for o in plan if o["entity"] == "trigger" and o["op"] == "create"):
                if op["name"] in existing_triggers:
                    # Already created on a prior attempt — reuse its id so tags
                    # created below still link to it.
                    trigger_id_by_name[op["name"]] = existing_triggers[op["name"]]
                    continue
                r = await connector.create_trigger(
                    conn,
                    account_id,
                    container_id,
                    workspace_id,
                    op["name"],
                    op["gtm_type"],
                    op.get("filters"),
                    op.get("custom_event_filter"),
                )
                tid = str((r or {}).get("trigger_id"))
                record["triggers"].append(tid)
                trigger_id_by_name[op["name"]] = tid

            for op in (o for o in plan if o["entity"] == "tag"):
                if op["op"] == "update":
                    r = await connector.update_tag(
                        conn,
                        account_id,
                        container_id,
                        workspace_id,
                        op["tag_id"],
                        op.get("updates") or {},
                    )
                    record["tags"].append((r or {}).get("tag_id"))
                    continue
                if op["name"] in existing_tags:
                    continue
                firing = [str(t) for t in (op.get("firing_trigger_ids") or [])]
                ref = op.get("trigger_ref")
                if ref and ref in trigger_id_by_name:
                    firing.append(trigger_id_by_name[ref])
                r = await connector.create_tag(
                    conn,
                    account_id,
                    container_id,
                    workspace_id,
                    op["name"],
                    op["gtm_type"],
                    op.get("parameters") or [],
                    firing,
                    None,
                    "Created via Flux draft",
                )
                record["tags"].append((r or {}).get("tag_id"))
        except DraftPublishError:
            # Leave the scratch workspace unpublished — nothing goes live.
            record["published"] = False
            raise
        except ConnectorError as exc:
            # A connector create/update failed (it raises ConnectorError rather
            # than returning an error dict). Surface it as a publish failure so
            # the API returns 502 publish_failed, not a generic 500. The scratch
            # workspace is left unpublished — nothing goes live.
            record["published"] = False
            raise DraftPublishError(f"GTM entity create failed: {exc}") from exc

        try:
            result = await connector.publish_container(
                conn,
                account_id,
                container_id,
                workspace_id,
                draft.title,
                "Approved via Flux conversation",
            )
        except Exception as exc:
            raise DraftPublishError(f"GTM publish failed: {exc}") from exc
        if isinstance(result, dict) and result.get("error"):
            raise DraftPublishError(str(result.get("message") or "GTM publish failed."))

        version = (
            (result or {}).get("version_id") or (result or {}).get("containerVersionId")
            if isinstance(result, dict)
            else None
        )
        record["version_id"] = str(version) if version else None
        published_version = str(version) if version else await self._next_mock_version(draft.project_id)
        return published_version, record

    async def reject(self, draft_id: uuid.UUID, *, user_id: uuid.UUID) -> FluxDraft | None:
        draft = await self.get(draft_id)
        if draft is None or draft.status != "pending":
            return draft
        async with app_state.db_session_factory() as db:
            await db.execute(
                update(FluxDraft)
                .where(FluxDraft.id == draft_id)
                .values(status="rejected", resolved_by=user_id, resolved_at=func.now())
            )
            await db.commit()
        draft = await self.get(draft_id)
        await _log_activity(
            project_id=draft.project_id,
            user_id=user_id,
            event_type="draft_rejected",
            description=f"Rejected Flux draft '{draft.title}' — kept as workspace draft",
        )
        return draft

    async def reset(self, draft_id: uuid.UUID, *, user_id: uuid.UUID) -> FluxDraft | None:
        """Undo a rejection, putting the draft back to pending (design's
        "Undo" link on the rejected footer). Does not undo an approval —
        once published there is no undo short of a real GTM rollback."""
        draft = await self.get(draft_id)
        if draft is None or draft.status != "rejected":
            return draft
        async with app_state.db_session_factory() as db:
            await db.execute(
                update(FluxDraft)
                .where(FluxDraft.id == draft_id)
                .values(status="pending", resolved_by=None, resolved_at=None)
            )
            await db.commit()
        draft = await self.get(draft_id)
        await _log_activity(
            project_id=draft.project_id,
            user_id=user_id,
            event_type="draft_reset",
            description=f"Undid rejection of Flux draft '{draft.title}' — back to pending",
        )
        return draft

    async def _next_mock_version(self, project_id: uuid.UUID) -> str:
        """Placeholder version numbering until real GTM publish is wired in.
        Monotonic per project so the UI's "PUBLISHED · vN" header is at least
        internally consistent across repeated approvals."""
        async with app_state.db_session_factory() as db:
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(FluxDraft)
                    .where(FluxDraft.project_id == project_id, FluxDraft.status == "published")
                )
            ).scalar_one()
        return str(148 + count)
