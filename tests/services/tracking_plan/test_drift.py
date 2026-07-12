# tests/services/tracking_plan/test_drift.py
"""Pure-logic tests for the tracking-plan drift engine (no DB / no connectors)."""

from app.services.tracking_plan.drift.bq_drift import build_param_sql, parse_param_rows
from app.services.tracking_plan.drift.ga4_drift import diff_events
from app.services.tracking_plan.drift.resolve import _derive_export_dataset
from app.services.tracking_plan.drift.service import _apply_param_tier


def test_diff_events_classifies_broken_verified_unplanned():
    plan = {"purchase", "sign_up"}
    live = [
        {"event_name": "purchase", "event_count": 6214},
        {"event_name": "search", "event_count": 10},  # firing but not planned
    ]
    rows = diff_events(plan, live)

    assert rows["purchase"].status == "verified"
    assert rows["purchase"].volume_7d == 6214
    assert rows["sign_up"].status == "broken"  # planned, zero live volume
    assert rows["sign_up"].volume_7d == 0
    assert rows["search"].status == "unplanned"
    assert rows["search"].volume_7d == 10


def test_diff_events_empty_live_marks_all_broken():
    rows = diff_events({"a", "b"}, [])
    assert {r.status for r in rows.values()} == {"broken"}


def test_build_param_sql_is_read_only_and_rejects_injection():
    sql = build_param_sql("gcp-proj", "analytics_123", ["purchase", "sign_up"])
    assert sql is not None
    assert "UNNEST(event_params)" in sql
    assert "'purchase'" in sql and "'sign_up'" in sql
    # An event name with SQL metacharacters is dropped, not escaped-in.
    assert build_param_sql("gcp", "analytics_1", ["bad'; DROP TABLE x --"]) is None
    # No safe names at all → no query.
    assert build_param_sql("gcp", "analytics_1", []) is None


def test_parse_param_rows_flags_unplanned_and_filters_noise():
    rows = [
        {"event_name": "purchase", "param_key": "value", "present": 88, "total": 100, "sample_value": 329.0},
        {
            "event_name": "purchase",
            "param_key": "payment_provider",
            "present": 50,
            "total": 100,
            "sample_value": "paypal",
        },
        {
            "event_name": "purchase",
            "param_key": "ga_session_id",
            "present": 100,
            "total": 100,
            "sample_value": 1,
        },
    ]
    obs = parse_param_rows(rows, {"purchase": {"value", "currency"}})
    by_key = {o.param_key: o for o in obs}

    assert "ga_session_id" not in by_key  # GA4 noise dropped
    assert by_key["value"].present_pct == 88.0
    assert by_key["value"].is_unplanned is False
    assert by_key["payment_provider"].is_unplanned is True
    assert by_key["payment_provider"].sample_value == "paypal"


def test_apply_param_tier_sets_coverage_and_drift():
    drift = diff_events({"purchase"}, [{"event_name": "purchase", "event_count": 100}])
    obs = parse_param_rows(
        [
            {
                "event_name": "purchase",
                "param_key": "value",
                "present": 88,
                "total": 100,
                "sample_value": "9",
            },
            {
                "event_name": "purchase",
                "param_key": "currency",
                "present": 100,
                "total": 100,
                "sample_value": "USD",
            },
            {
                "event_name": "purchase",
                "param_key": "payment_provider",
                "present": 40,
                "total": 100,
                "sample_value": "x",
            },
        ],
        {"purchase": {"value", "currency"}},
    )
    coverage = _apply_param_tier(drift, obs, {"purchase"})

    assert coverage["purchase"] == 94.0  # mean of planned params (88, 100)
    assert drift["purchase"].status == "drifted"  # unplanned param present
    assert any("payment_provider" in r for r in drift["purchase"].reasons)


def test_derive_export_dataset():
    assert _derive_export_dataset("properties/123456789", None) == "analytics_123456789"
    assert _derive_export_dataset("properties/123", ["analytics_123", "other"]) == "analytics_123"
    assert _derive_export_dataset("properties/123", ["no_match"]) is None
    assert _derive_export_dataset(None, None) is None
