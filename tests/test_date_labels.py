import pytest

from app.dashboards.date_labels import format_date_label


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("202401", "Jan 2024"),  # GA4 yearMonth (the "202401" bug)
        ("2024-01", "Jan 2024"),
        ("20240105", "Jan 5, 2024"),  # GA4 date
        ("2024-01-05", "Jan 5, 2024"),
        ("2024Q1", "Q1 2024"),
        ("2024-Q1", "Q1 2024"),
        ("2024W03", "Wk 03 '24"),  # ISO week
        ("2024", "2024"),
        ("", ""),
        ("not-a-date", "not-a-date"),  # pass-through
        (None, ""),
        (202401, "Jan 2024"),  # integer input
    ],
)
def test_format_date_label(raw, expected):
    assert format_date_label(raw) == expected


def test_invalid_month_passes_through():
    # 2024-13 is not a real month -> not formatted, returned unchanged
    assert format_date_label("202413") == "202413"


def test_eight_digit_non_date_passes_through():
    # 20249999 has an invalid month/day -> unchanged
    assert format_date_label("20249999") == "20249999"
