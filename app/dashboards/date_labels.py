"""Canonical, human-readable date-label formatting for dashboard rendering.

This is the single source of truth for turning compact analytics date keys
(GA4 ``yearMonth`` = ``202401``, ``date`` = ``20240105``, quarters, ISO weeks)
into readable labels. The JS helper ``window.Fluxito.formatDateLabel`` in
``card_renderer_js.html`` mirrors this logic exactly for client-side charts and
tables; keep the two in sync. Unrecognized input is returned unchanged.
"""

from __future__ import annotations

import re

_MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _month(mo: int) -> str | None:
    return _MONTHS[mo - 1] if 1 <= mo <= 12 else None


def format_date_label(value: object) -> str:
    """Return a readable label for a date-like value, else the value unchanged."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""

    # YYYYMMDD (compact GA4 date) -> "Jan 5, 2024"
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        mon = _month(mo)
        if mon and 1 <= d <= 31:
            return f"{mon} {d}, {y}"

    # YYYY-MM-DD -> "Jan 5, 2024"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        mon = _month(int(m[2]))
        if mon:
            return f"{mon} {int(m[3])}, {int(m[1])}"

    # Quarter: YYYYQn / YYYY-Qn -> "Q1 2024"
    m = re.fullmatch(r"(\d{4})-?Q([1-4])", s, re.IGNORECASE)
    if m:
        return f"Q{m[2]} {m[1]}"

    # ISO week: YYYYWnn / YYYY-Wnn -> "Wk 03 '24"
    m = re.fullmatch(r"(\d{4})-?W(\d{1,2})", s, re.IGNORECASE)
    if m:
        return f"Wk {int(m[2]):02d} '{m[1][2:]}"

    # YYYYMM (GA4 yearMonth) -> "Jan 2024"  -- the headline bug
    m = re.fullmatch(r"(\d{4})(\d{2})", s)
    if m:
        mon = _month(int(m[2]))
        if mon:
            return f"{mon} {m[1]}"

    # YYYY-MM -> "Jan 2024"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
    if m:
        mon = _month(int(m[2]))
        if mon:
            return f"{mon} {int(m[1])}"

    # Plain year
    if re.fullmatch(r"\d{4}", s):
        return s

    return s
