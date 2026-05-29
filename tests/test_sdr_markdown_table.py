"""Unit tests for parse_markdown_table — turns a GFM pipe table into rows."""

from app.tools.sdr_parser import parse_markdown_table

SAMPLE = """GA4 has 37 event-scoped custom dimensions. Highlights:

| Name | Scope | Source | Example | Platforms |
|---|---|---|---|---|
| `email` | EVENT | Qualified Identify() | `jane@acme.com` | GA4 [CRITICAL PII] |
| `company` | EVENT | Qualified Identify() | `Acme Corp` | GA4 |

**Notes:** GA4 cap is 50 event-scoped dims.
"""


def test_parses_headers_and_rows():
    table = parse_markdown_table(SAMPLE)
    assert table is not None
    assert table["headers"] == ["Name", "Scope", "Source", "Example", "Platforms"]
    assert len(table["rows"]) == 2
    assert table["rows"][0] == ["email", "EVENT", "Qualified Identify()", "jane@acme.com", "GA4 [CRITICAL PII]"]
    assert table["rows"][1][0] == "company"


def test_returns_none_when_no_table():
    assert parse_markdown_table("Just prose, no pipes here.") is None
    assert parse_markdown_table("") is None
    assert parse_markdown_table(None) is None


def test_skips_separator_and_blank_cells():
    text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    table = parse_markdown_table(text)
    assert table["headers"] == ["A", "B"]
    assert table["rows"] == [["1", "2"]]


def test_lone_dash_data_cells_not_dropped():
    text = "| Name | Note |\n| --- | --- |\n| email | - |\n| - | n/a |\n"
    table = parse_markdown_table(text)
    assert table["rows"] == [["email", "-"], ["-", "n/a"]]
