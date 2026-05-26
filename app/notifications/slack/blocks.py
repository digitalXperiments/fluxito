"""
Dashboard → Block Kit renderer.

The scheduler (Step 5) hydrates a dashboard once, then dispatches the
hydrated card list to every configured destination. For email we render
the PDF path; for Slack we render Block Kit here.

Input shape (``cards`` argument):
  The same dict shape ``app.dashboards.pdf_renderer._card_to_payload``
  produces, i.e. each card is a dict with keys:

    {
      "id":          "<uuid>",
      "title":       str,
      "platform":    str,
      "card_type":   "METRIC" | "TABLE" | "LIST" | "AUDIT" | "ERROR" | ...,
      "is_live":     bool,
      "snap":        dict,  # card-type-specific payload
      "refreshed_at": str | None,
    }

Output: a list of Block Kit blocks, ready to pass to ``SlackMessage.blocks``.

Rendering rules:
  * METRIC    → mrkdwn section with one or more bolded value lines.
  * TABLE     → a header section + the first ~10 rows as a preformatted
                code block (Slack does not have real tables).
  * LIST      → a section with each item as a bullet.
  * AUDIT     → section with the headline + status markers.
  * ERROR     → error-styled context block so failures are visible but
                don't derail the whole message.
  * Anything else → a placeholder context block identifying the card
    type so the user knows *something* is there.

Block Kit limits to remember:
  * Max 50 blocks per message.
  * Max 3000 chars per mrkdwn text field.
  * Divider blocks count against the 50-block limit.

We trim aggressively when a dashboard has many cards — if we exceed
roughly 45 blocks we stop rendering individual cards and add a footer
block explaining that the rest is in the attached PDF / live link.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Leave a safety margin under Slack's 50-block limit for header/footer blocks.
_MAX_CARD_BLOCKS = 40
# Block Kit mrkdwn text_field hard limit is 3000; we use a softer internal
# cap to leave room for markdown decoration.
_MRKDWN_SOFT_LIMIT = 2800
# Slack rejects messages with total text length > ~40000 chars but we'll
# never come close to that; the per-field limit is the meaningful bound.


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def render_simple_blocks(
    title: str,
    body_md: str = "",
    footer: str | None = None,
) -> list[dict[str, Any]]:
    """Minimal blocks for test messages and one-line notifications.

    Used by:
      * The Slack settings "Test" button.
      * The scheduler's error notification path if the report itself
        failed to render (we still want a heads-up in Slack).
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _truncate(title, 150), "emoji": True},
        }
    ]
    if body_md:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _truncate(body_md, _MRKDWN_SOFT_LIMIT)},
            }
        )
    if footer:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": _truncate(footer, 500)}],
            }
        )
    return blocks


def render_dashboard_blocks(
    dashboard_title: str,
    cards: list[dict[str, Any]],
    *,
    description: str | None = None,
    filter_summary: str | None = None,
    live_url: str | None = None,
    include_insights: bool = False,
    insights_md: str | None = None,
) -> list[dict[str, Any]]:
    """Render a hydrated dashboard into a list of Block Kit blocks.

    Args:
      dashboard_title  — H1 of the message
      cards            — hydrated card dicts (see module docstring for shape)
      description      — optional 1-line dashboard description
      filter_summary   — e.g. "Last 7 days · GA4, Meta" — shown as context
      live_url         — if provided, adds a "View live" button at the top
      include_insights — whether to prepend the AI insights callout
      insights_md      — the insights text (mrkdwn). Ignored if
                         ``include_insights`` is False.

    Returns:
      list of Block Kit block dicts. Can be passed straight to
      ``SlackMessage.blocks``.
    """
    blocks: list[dict[str, Any]] = []

    # Header
    blocks.append(
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _truncate(dashboard_title, 150), "emoji": True},
        }
    )

    # Optional description + live-view button
    context_text_parts: list[str] = []
    if description:
        context_text_parts.append(description)
    if filter_summary:
        context_text_parts.append(filter_summary)
    if context_text_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": _truncate(" · ".join(context_text_parts), 500),
                    }
                ],
            }
        )

    if live_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View live dashboard"},
                        "url": live_url,
                        "style": "primary",
                    }
                ],
            }
        )

    # Optional insights callout
    if include_insights and insights_md:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate(f":bulb: *Insights*\n{insights_md}", _MRKDWN_SOFT_LIMIT),
                },
            }
        )

    blocks.append({"type": "divider"})

    # Cards — honour the 40-block safety budget so we leave room for
    # headers / footers. Count starts at current block count.
    cards_rendered = 0
    cards_skipped = 0
    for card in cards:
        # Each card typically produces 1–3 blocks. Stop early if we're
        # about to overflow.
        if len(blocks) >= _MAX_CARD_BLOCKS:
            cards_skipped = len(cards) - cards_rendered
            break
        card_blocks = _render_card(card)
        blocks.extend(card_blocks)
        cards_rendered += 1

    # Footer
    blocks.append({"type": "divider"})
    footer_parts: list[str] = []
    if cards_skipped:
        footer_parts.append(
            f":page_facing_up: Showing {cards_rendered} of {cards_rendered + cards_skipped} "
            f"cards — the rest are in the attached PDF."
        )
    footer_parts.append("_Generated by Fluxito_")
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " · ".join(footer_parts)}],
        }
    )

    return blocks


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _render_card(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Dispatch to the correct renderer for a single card."""
    card_type = (card.get("card_type") or "").upper()
    title = card.get("title") or "Untitled card"
    snap = card.get("snap") or {}
    if not isinstance(snap, dict):
        snap = {}

    if card_type == "METRIC":
        return _render_metric(title, snap)
    if card_type == "TABLE":
        return _render_table(title, snap)
    if card_type == "LIST":
        return _render_list(title, snap)
    if card_type == "AUDIT":
        return _render_audit(title, snap)
    if card_type == "ERROR":
        return _render_error(title, snap)
    return _render_unknown(title, card_type or "UNKNOWN")


def _render_metric(title: str, snap: dict) -> list[dict]:
    """METRIC card → section with one-line bolded values.

    Expected snap shape (best-effort — we tolerate variants):
      {"metrics": [{"label": "Sessions", "value": 12345, "delta_pct": 4.1}, ...]}
    Older cards may store a single scalar under ``value``.
    """
    metrics = snap.get("metrics")
    lines: list[str] = []
    if isinstance(metrics, list) and metrics:
        for m in metrics[:6]:  # cap to keep the block compact
            if not isinstance(m, dict):
                continue
            label = m.get("label") or m.get("name") or "Value"
            value = m.get("value")
            delta = m.get("delta_pct")
            line = f"*{_fmt(value)}* — {label}"
            if isinstance(delta, (int, float)):
                arrow = ":arrow_up_small:" if delta >= 0 else ":arrow_down_small:"
                line += f"  {arrow} {delta:+.1f}%"
            lines.append(line)
    elif "value" in snap:
        # Single-metric variant
        lines.append(f"*{_fmt(snap.get('value'))}*")

    if not lines:
        lines.append("_(no data)_")

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(f"*{title}*\n" + "\n".join(lines), _MRKDWN_SOFT_LIMIT),
            },
        }
    ]


def _render_table(title: str, snap: dict) -> list[dict]:
    """TABLE card → header line + code block with the first ~10 rows.

    Expected shape: ``{"columns": [...], "rows": [[...], ...]}`` or
    ``{"headers": [...], "rows": [...]}``. We tolerate both because the
    LIST/TABLE cards aren't super consistent across platforms.
    """
    columns = snap.get("columns") or snap.get("headers") or []
    rows = snap.get("rows") or []
    if not isinstance(columns, list):
        columns = []
    if not isinstance(rows, list):
        rows = []

    if not columns and not rows:
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}*\n_(empty table)_"},
            }
        ]

    # Build a fixed-width text rendering — Slack code blocks use a
    # monospace font so this lines up reasonably.
    preview_rows = rows[:10]
    str_rows: list[list[str]] = [[_fmt(c) for c in columns]] if columns else []
    for r in preview_rows:
        if not isinstance(r, (list, tuple)):
            continue
        str_rows.append([_fmt(c) for c in r])

    # Column widths
    col_count = max((len(r) for r in str_rows), default=0)
    widths = [0] * col_count
    for r in str_rows:
        for i, cell in enumerate(r):
            if i < col_count:
                widths[i] = max(widths[i], min(len(cell), 24))

    def _fmt_row(r: list[str]) -> str:
        padded = []
        for i in range(col_count):
            cell = r[i] if i < len(r) else ""
            cell = cell[:24]
            padded.append(cell.ljust(widths[i]))
        return "  ".join(padded)

    lines = [_fmt_row(r) for r in str_rows]
    table_text = "\n".join(lines)
    if len(rows) > len(preview_rows):
        table_text += f"\n… and {len(rows) - len(preview_rows)} more rows"

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(
                    f"*{title}*\n```{table_text}```",
                    _MRKDWN_SOFT_LIMIT,
                ),
            },
        }
    ]


def _render_list(title: str, snap: dict) -> list[dict]:
    """LIST card → section with each item as a bullet.

    Uses item-access for ``items`` because ``snap.items`` would resolve
    to ``dict.items`` — same gotcha the PDF renderer hit.
    """
    items = snap["items"] if "items" in snap else []
    if not isinstance(items, list):
        items = []

    lines: list[str] = []
    for item in items[:12]:
        if isinstance(item, dict):
            label = item.get("label") or item.get("name") or ""
            value = item.get("value")
            if value is not None:
                lines.append(f"• {label} — *{_fmt(value)}*")
            else:
                lines.append(f"• {label}")
        else:
            lines.append(f"• {_fmt(item)}")

    if not lines:
        lines.append("_(no items)_")
    elif len(items) > 12:
        lines.append(f"_… and {len(items) - 12} more_")

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(f"*{title}*\n" + "\n".join(lines), _MRKDWN_SOFT_LIMIT),
            },
        }
    ]


def _render_audit(title: str, snap: dict) -> list[dict]:
    """AUDIT card → section with headline + pass/fail markers.

    Expected shape: ``{"headline": "…", "checks": [{"label": "…", "status": "ok"|"warn"|"fail"}]}``
    """
    headline = snap.get("headline") or snap.get("summary") or ""
    checks = snap.get("checks") or []
    if not isinstance(checks, list):
        checks = []

    lines: list[str] = []
    if headline:
        lines.append(f"_{headline}_")
    for c in checks[:10]:
        if not isinstance(c, dict):
            continue
        status = (c.get("status") or "").lower()
        icon = {
            "ok": ":white_check_mark:",
            "pass": ":white_check_mark:",
            "warn": ":warning:",
            "warning": ":warning:",
            "fail": ":x:",
            "error": ":x:",
        }.get(status, ":grey_question:")
        label = c.get("label") or c.get("name") or "(unnamed check)"
        lines.append(f"{icon} {label}")
    if len(checks) > 10:
        lines.append(f"_… and {len(checks) - 10} more checks_")

    if not lines:
        lines.append("_(no audit results)_")

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(f"*{title}*\n" + "\n".join(lines), _MRKDWN_SOFT_LIMIT),
            },
        }
    ]


def _render_error(title: str, snap: dict) -> list[dict]:
    """ERROR card → highlighted context block so failures stay visible."""
    msg = snap.get("message") or snap.get("error") or "unknown error"
    return [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": _truncate(f":x: *{title}* — {msg}", 500),
                }
            ],
        }
    ]


def _render_unknown(title: str, card_type: str) -> list[dict]:
    return [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f":grey_question: *{title}* — unsupported card type `{card_type}`",
                }
            ],
        }
    ]


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def _fmt(value: Any) -> str:
    """Best-effort human-readable stringification for Slack cell content.

    Numbers get thousands separators; floats get capped precision;
    None → em-dash; anything else → str().
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if abs(value) >= 100:
            return f"{value:,.0f}"
        return f"{value:,.2f}"
    return str(value)


def _truncate(text: str, limit: int) -> str:
    """Slice ``text`` to ``limit`` with a trailing ellipsis when needed."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
