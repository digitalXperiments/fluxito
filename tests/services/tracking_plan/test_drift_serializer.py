# tests/services/tracking_plan/test_drift_serializer.py
"""DB-backed: the serializer folds persisted drift into the plan payload."""

import pytest

from app.models.tracking_plan import TPEventDrift, TPParamObservation
from app.services.tracking_plan import attach_property, create_event, create_property, plan_to_dict
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _plan_with_purchase(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="P")
    branch = await get_main_branch(session, plan)
    ev = await create_event(session, branch, name="purchase")
    prop = await create_property(session, branch, name="value", data_type="float")
    await attach_property(session, branch, ev.id, prop.id, required=True, example="9.99")
    return plan, branch


@pytest.mark.anyio
async def test_serializer_surfaces_drift_and_observations(db_session_factory):
    async with db_session_factory() as session:
        plan, branch = await _plan_with_purchase(session)
        session.add(
            TPEventDrift(
                plan_id=plan.id,
                event_name="purchase",
                status="drifted",
                volume_7d=6214,
                param_coverage_pct=88,
                detail={"reasons": ["Live sends unplanned parameter(s): payment_provider."]},
                source="ga4",
            )
        )
        session.add(
            TPParamObservation(
                plan_id=plan.id, event_name="purchase", param_key="value", present_pct=88, is_unplanned=False
            )
        )
        session.add(
            TPParamObservation(
                plan_id=plan.id,
                event_name="purchase",
                param_key="payment_provider",
                present_pct=50,
                sample_value="paypal",
                is_unplanned=True,
            )
        )
        await session.flush()

        data = await plan_to_dict(session, plan, branch)
        ev = next(e for e in data["events"] if e["name"] == "purchase")

        assert ev["drift"]["status"] == "drifted"
        assert ev["drift"]["volume_7d"] == 6214
        assert ev["drift"]["param_coverage_pct"] == 88.0
        value_prop = next(p for p in ev["properties"] if p["name"] == "value")
        assert value_prop["observation"]["present_pct"] == 88.0
        assert data["unplanned_params"]["purchase"][0]["param_key"] == "payment_provider"
        assert data["unplanned_params"]["purchase"][0]["sample_value"] == "paypal"


@pytest.mark.anyio
async def test_include_drift_false_omits_drift(db_session_factory):
    async with db_session_factory() as session:
        plan, branch = await _plan_with_purchase(session)
        session.add(TPEventDrift(plan_id=plan.id, event_name="purchase", status="drifted", volume_7d=10))
        await session.flush()

        data = await plan_to_dict(session, plan, branch, include_drift=False)
        ev = next(e for e in data["events"] if e["name"] == "purchase")

        assert ev["drift"] is None
        assert ev["properties"][0]["observation"] is None
        assert data["unplanned_params"] == {}
        assert data["last_audit_at"] is None
