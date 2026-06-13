from app.dashboards.compare import merge_compare, pct_delta, previous_range


def test_pct_delta():
    assert pct_delta(110, 100) == 10.0
    assert pct_delta(90, 100) == -10.0
    assert pct_delta(100, 0) is None  # undefined baseline
    assert pct_delta(None, 100) is None
    assert pct_delta("1,200", "1,000") == 20.0  # comma-formatted


def test_previous_range_previous_period():
    # June 1-30 (30 days) -> May 2-31 (immediately preceding 30 days)
    assert previous_range("2024-06-01", "2024-06-30", "previous_period") == (
        "2024-05-02",
        "2024-05-31",
    )


def test_previous_range_previous_year():
    assert previous_range("2024-06-01", "2024-06-30", "previous_year") == (
        "2023-06-01",
        "2023-06-30",
    )


def test_previous_range_leap_day():
    assert previous_range("2024-02-29", "2024-02-29", "previous_year") == (
        "2023-02-28",
        "2023-02-28",
    )


def test_scorecard_merge_adds_delta():
    cur = {"metrics": [{"key": "sessions", "value": 110}]}
    prev = {"metrics": [{"key": "sessions", "value": 100}]}
    out = merge_compare(cur, prev, "scorecard")
    assert out["metrics"][0]["previous"] == 100
    assert out["metrics"][0]["delta_pct"] == 10.0
    assert out["metrics"][0]["delta_abs"] == 10
    assert out["compare"] is True


def test_table_merge_matches_rows_by_dimension():
    cur = {"columns": ["channel", "sessions"], "rows": [{"channel": "Organic", "sessions": 21400}]}
    prev = {"columns": ["channel", "sessions"], "rows": [{"channel": "Organic", "sessions": 19900}]}
    out = merge_compare(cur, prev, "table")
    row = out["rows"][0]
    assert row["sessions"] == 21400
    assert row["sessions__prev"] == 19900
    assert round(row["sessions__delta_pct"], 1) == 7.5
    assert out["compare_columns"] == ["sessions"]


def test_table_merge_unmatched_row_has_none_prev():
    cur = {"columns": ["channel", "sessions"], "rows": [{"channel": "New", "sessions": 5}]}
    prev = {"columns": ["channel", "sessions"], "rows": [{"channel": "Old", "sessions": 9}]}
    out = merge_compare(cur, prev, "table")
    assert out["rows"][0]["sessions__prev"] is None
    assert out["rows"][0]["sessions__delta_pct"] is None


def test_timeseries_aligns_by_relative_index():
    cur = {
        "columns": ["date", "sessions"],
        "rows": [{"date": "20240601", "sessions": 10}, {"date": "20240602", "sessions": 20}],
    }
    prev = {
        "columns": ["date", "sessions"],
        "rows": [{"date": "20240501", "sessions": 8}, {"date": "20240502", "sessions": 12}],
    }
    out = merge_compare(cur, prev, "line")
    assert out["compare_series"]["sessions"] == [8.0, 12.0]  # previous aligned by index


def test_pie_is_noop_but_flagged():
    cur = {"columns": ["x", "y"], "rows": [{"x": "a", "y": 1}]}
    out = merge_compare(cur, {"rows": []}, "pie")
    assert out["compare"] is True
    assert "compare_series" not in out
    assert "compare_columns" not in out
