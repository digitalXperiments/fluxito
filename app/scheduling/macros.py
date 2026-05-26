"""
Filter macro resolution.

``ReportSchedule.filter_params`` is a JSONB blob stored at save time.
The UI lets users pick date ranges in two ways:

  1. Absolute — pick actual dates. Stored as ``start_date`` /
     ``end_date`` strings in ``YYYY-MM-DD``.
  2. Rolling window — pick a macro like "last 7 days". Stored as a
     sentinel string such as ``{{last_7_days}}``.

Option 2 is the common case because a weekly dashboard report "last 7
days" should mean "the 7 days leading up to the *send* date", not the
7 days at the point the schedule was created. This module does that
expansion.

At job execution time, the runner calls ``resolve_filter_macros`` with
the schedule's IANA timezone and gets back a clean dict with only
``start_date`` / ``end_date`` / ``platforms`` — exactly the shape the
PDF renderer and hydration helper already accept.

Supported macros:

    {{today}}             — today (1-day window)
    {{yesterday}}         — yesterday (1-day window)
    {{last_7_days}}       — 7 days ending yesterday
    {{last_14_days}}      — 14 days ending yesterday
    {{last_28_days}}      — 28 days ending yesterday
    {{last_30_days}}      — 30 days ending yesterday
    {{last_90_days}}      — 90 days ending yesterday
    {{this_week}}         — Mon..today (ISO week)
    {{last_week}}         — previous full Mon..Sun
    {{this_month}}        — 1st..today
    {{last_month}}        — 1st..last of the previous month
    {{month_to_date}}     — alias for this_month
    {{week_to_date}}      — alias for this_week
    {{quarter_to_date}}   — 1st of the current quarter..today
    {{year_to_date}}      — Jan 1..today

Conventions
  * All windows are *inclusive* on both ends.
  * "Today" is evaluated in the schedule's timezone. A "daily" report at
    09:00 Asia/Dubai will see ``today = today in Dubai``, which is what
    the user expects.
  * We prefer "yesterday" as the end-of-window for rolling reports so a
    report sent on Monday morning doesn't include a half-day of Monday
    data — this matches what most analytics tools do (GA4's "Last 7
    days" excludes today by default).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

try:
    # Python 3.9+ standard library
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — defensive; we require 3.11+
    ZoneInfo = None  # type: ignore[assignment]


# Public for tests — a frozen set of macro names we understand. The UI
# can query this to build its dropdown if it wants to.
KNOWN_MACROS: frozenset[str] = frozenset(
    {
        "today",
        "yesterday",
        "last_7_days",
        "last_14_days",
        "last_28_days",
        "last_30_days",
        "last_90_days",
        "this_week",
        "last_week",
        "this_month",
        "last_month",
        "month_to_date",
        "week_to_date",
        "quarter_to_date",
        "year_to_date",
    }
)


def resolve_filter_macros(
    filter_params: dict[str, Any] | None,
    *,
    tz: str = "UTC",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Expand any macros in ``filter_params`` into concrete dates.

    Args:
      filter_params — the JSONB blob from ``ReportSchedule.filter_params``.
                      May contain any of: ``start_date``, ``end_date``,
                      ``platforms``, ``macro`` (a top-level macro), or
                      ``{{...}}`` macro strings in ``start_date``.
      tz            — the schedule's IANA timezone. Defaults to UTC.
      now           — optional override for "now" (tests pin this).
                      Must be a tz-aware datetime if provided.

    Returns:
      A dict with at most three keys: ``start_date``, ``end_date``,
      ``platforms``. Ready to pass to
      ``render_dashboard_pdf(filter_params=...)``.
    """
    params = dict(filter_params or {})

    # 1) Top-level ``macro`` takes precedence — it replaces start/end.
    macro_name = _clean_macro(params.get("macro"))
    if macro_name is None and isinstance(params.get("start_date"), str):
        # 2) Otherwise look for a ``{{...}}`` marker in start_date.
        macro_name = _clean_macro(params.get("start_date"))

    if macro_name is not None and macro_name in KNOWN_MACROS:
        today = _today_in_tz(tz, now)
        start, end = _window_for_macro(macro_name, today)
        out: dict[str, Any] = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
    else:
        # No macro — pass through any literal start/end values.
        out = {}
        if params.get("start_date"):
            out["start_date"] = str(params["start_date"])
        if params.get("end_date"):
            out["end_date"] = str(params["end_date"])

    # Platforms always pass through as-is.
    platforms = params.get("platforms")
    if isinstance(platforms, list):
        out["platforms"] = [str(p) for p in platforms if p]

    return out


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _clean_macro(raw: Any) -> str | None:
    """Return the macro name if ``raw`` looks like ``{{name}}``, else None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("{{") and s.endswith("}}"):
        return s[2:-2].strip().lower()
    # Also accept bare names for the ``macro`` top-level field
    if s and "{{" not in s and "}}" not in s and " " not in s:
        return s.lower()
    return None


def _today_in_tz(tz: str, now: datetime | None) -> date:
    """Return ``today`` as a ``date`` in the given IANA timezone."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError("`now` must be a timezone-aware datetime")
        return now.date() if tz in ("UTC", "") else now.astimezone(_zoneinfo(tz)).date()
    return datetime.now(_zoneinfo(tz)).date()


def _zoneinfo(tz: str):
    """Resolve an IANA zone — fall back to UTC if zoneinfo is unavailable."""
    if ZoneInfo is None:  # pragma: no cover
        return UTC
    try:
        return ZoneInfo(tz or "UTC")
    except Exception:
        # Unknown zone names shouldn't crash the worker — log and fall back.
        return ZoneInfo("UTC")


def _window_for_macro(name: str, today: date) -> tuple[date, date]:
    """Return an inclusive (start, end) window for a macro name."""
    yesterday = today - timedelta(days=1)

    if name == "today":
        return today, today
    if name == "yesterday":
        return yesterday, yesterday
    if name == "last_7_days":
        return yesterday - timedelta(days=6), yesterday
    if name == "last_14_days":
        return yesterday - timedelta(days=13), yesterday
    if name == "last_28_days":
        return yesterday - timedelta(days=27), yesterday
    if name == "last_30_days":
        return yesterday - timedelta(days=29), yesterday
    if name == "last_90_days":
        return yesterday - timedelta(days=89), yesterday

    if name in ("this_week", "week_to_date"):
        # ISO week: Monday = 0
        start = today - timedelta(days=today.weekday())
        return start, today
    if name == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday = this_monday - timedelta(days=1)
        return last_monday, last_sunday

    if name in ("this_month", "month_to_date"):
        return today.replace(day=1), today
    if name == "last_month":
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end

    if name == "quarter_to_date":
        q_month = 3 * ((today.month - 1) // 3) + 1
        return today.replace(month=q_month, day=1), today
    if name == "year_to_date":
        return today.replace(month=1, day=1), today

    # Should never get here — KNOWN_MACROS gates the caller.
    raise ValueError(f"unknown macro {name!r}")
