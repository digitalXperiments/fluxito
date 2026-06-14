# tests/tools/test_audit_repoint.py
"""
Contract tests for Task 2 (audit helpers) and Task 3 (live-tag-test context)
of Plan 1D: verify the repointed helpers read the published snapshot and
return the legacy shapes their callers expect.
"""

import pytest

import app.app_state as app_state
from app.services.tracking_plan import (
    attach_property,
    create_destination,
    create_event,
    create_property,
    create_source,
    get_main_branch,
    get_or_create_plan,
    publish_branch,
    set_event_destination,
    set_event_sources,
)
from app.tools.sdr_audit_helpers import build_audit_sdr_summary, get_sdr_expected_events
from tests.services.tracking_plan.test_models import _make_project_and_user

# ---------------------------------------------------------------------------
# Task 2 — audit helpers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_audit_reads_published_snapshot(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        ev = await create_event(session, branch, name="purchase")
        prop = await create_property(session, branch, name="value", data_type="float")
        await attach_property(session, branch, ev.id, prop.id, required=True, example="9.99")

        src = await create_source(session, branch, name="web")
        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        await set_event_sources(
            session, branch, ev.id, [{"source_id": src.id, "implementation_status": "implemented"}]
        )
        await set_event_destination(session, branch, ev.id, dest.id, dest_event_name="purchase")

        await publish_branch(session, plan, branch, user_id=user_id, changelog="v1")
        await session.commit()

    expected = await get_sdr_expected_events(project_id)

    assert expected is not None
    assert expected["sdr_version"] == "1.0"

    ev_dict = expected["event_index"]["purchase"]

    # Legacy parameter shape
    assert ev_dict["parameters"][0]["name"] == "value"
    assert ev_dict["parameters"][0]["required"] is True
    assert ev_dict["parameters"][0]["type"] == "float"
    assert ev_dict["parameters"][0]["source"] is None
    assert ev_dict["parameters"][0]["example"] == "9.99"
    assert ev_dict["parameters"][0]["validation_rule"] is None

    # Legacy destination shape — platform resolved from dest_platform_by_name
    assert ev_dict["destinations"][0]["platform"] == "ga4"
    assert ev_dict["destinations"][0]["dest_event_name"] == "purchase"
    assert ev_dict["destinations"][0]["platform_account_id"] is None

    # Status rollup: one source with "implemented" → "implemented"
    assert ev_dict["status"] == "implemented"

    # build_audit_sdr_summary works with the returned shape
    summary = build_audit_sdr_summary(expected, live_event_names=["purchase", "page_view"])
    assert "purchase" in summary["matched"]
    assert "page_view" in summary["unexpected_live"]
    assert summary["sdr_version"] == "1.0"


@pytest.mark.anyio
async def test_audit_returns_none_without_publish(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        await session.commit()

    assert await get_sdr_expected_events(project_id) is None


@pytest.mark.anyio
async def test_status_rollup_highest_wins(db_session_factory, monkeypatch):
    """Multiple sources: highest implementation_status wins."""
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        ev = await create_event(session, branch, name="checkout")
        src1 = await create_source(session, branch, name="web")
        src2 = await create_source(session, branch, name="ios")
        await set_event_sources(
            session,
            branch,
            ev.id,
            [
                {"source_id": src1.id, "implementation_status": "planned"},
                {"source_id": src2.id, "implementation_status": "verified"},
            ],
        )
        await publish_branch(session, plan, branch, user_id=user_id)
        await session.commit()

    expected = await get_sdr_expected_events(project_id)
    assert expected is not None
    assert expected["event_index"]["checkout"]["status"] == "verified"


# ---------------------------------------------------------------------------
# Task 3 — live-tag-test context
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sdr_context_for_url(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        ev = await create_event(
            session,
            branch,
            name="purchase",
            trigger_config={"url_pattern": r"/checkout"},
        )
        prop = await create_property(session, branch, name="value", data_type="float")
        await attach_property(session, branch, ev.id, prop.id, required=True, example="9.99")

        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        await set_event_destination(session, branch, ev.id, dest.id, dest_event_name="purchase")

        await publish_branch(session, plan, branch, user_id=user_id)
        await session.commit()

    from app.tag_testing.live_test.sdr_context import get_sdr_context_for_url

    # URL that matches the pattern — event included
    ctx = await get_sdr_context_for_url(str(project_id), "https://example.com/checkout")
    assert ctx["error"] is None
    assert ctx["total"] == 1
    e = ctx["events"][0]
    assert e["event_name"] == "purchase"
    assert e["parameters"][0]["name"] == "value"
    assert e["parameters"][0]["type"] == "float"
    assert e["parameters"][0]["required"] is True
    assert e["parameters"][0]["example_value"] == "9.99"
    assert e["destinations"] == ["ga4"]

    # URL that doesn't match the pattern — event excluded
    ctx2 = await get_sdr_context_for_url(str(project_id), "https://example.com/home")
    assert ctx2["total"] == 0
    assert ctx2["error"] is None


@pytest.mark.anyio
async def test_sdr_context_no_publish_returns_empty(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        await session.commit()

    from app.tag_testing.live_test.sdr_context import get_sdr_context_for_url

    ctx = await get_sdr_context_for_url(str(project_id))
    assert ctx["events"] == []
    assert ctx["total"] == 0
    assert ctx["error"] is None


@pytest.mark.anyio
async def test_sdr_context_no_url_filter_returns_all(db_session_factory, monkeypatch):
    """Events with url_pattern are included when no URL is provided."""
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        await create_event(session, branch, name="page_view", trigger_config={"url_pattern": r"/shop"})
        await create_event(session, branch, name="global_event")

        await publish_branch(session, plan, branch, user_id=user_id)
        await session.commit()

    from app.tag_testing.live_test.sdr_context import get_sdr_context_for_url

    ctx = await get_sdr_context_for_url(str(project_id), url=None)
    assert ctx["total"] == 2
