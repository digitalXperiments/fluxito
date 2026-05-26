"""
Seed the automation library with curated, cross-platform monitoring recipes.

A *automation* is a self-contained prompt that Claude in Cowork executes on a
cron schedule. Unlike templates (which deploy as one-shot dashboards) and
report schedules (which render a deployed dashboard as PDF on OUR
APScheduler), automations are pure prompt + variable + cron — they cost us
zero compute because Cowork's scheduler runs them.

Curation principles (mirrors seed_templates.py):

  • Ship a small flagship set, not a sprawling catalog.
  • Every system automation is cross-platform-ready or solves a real pain
    nobody wants to babysit (digests, anomaly watching, pacing, tag drift).
  • Each prompt is fully self-contained: it tells Claude what platforms
    to query, what to check for, when to fire, how to dedup via a
    workspace state file, and where to post the result.
  • Each prompt names a PROJECT_NAME and CHANNEL_LABEL placeholder that
    the install flow substitutes with the user's choices at install time.

Seed runs on every app start and is idempotent:

  • Existing system automations matching a curated slug are UPDATED in
    place (title/description/prompt/variables/etc. refresh, use_count
    preserved).
  • Any legacy system automations that are NOT in the curated list are
    soft-deactivated (is_active=False) so they disappear from the UI
    without breaking install records.

Run manually via:
    python -m app.db.seed_automations
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.models.automation import (
    THEME_ANOMALY,
    THEME_DAILY_DIGEST,
    THEME_EXEC_SUMMARY,
    THEME_LAUNCH_MONITOR,
    THEME_PACING,
    THEME_TAG_HEALTH,
)

logger = logging.getLogger(__name__)


# ============================================================================ #
# Shared variable blocks
# ============================================================================ #

# Every automation accepts these two variables — the install flow always
# fills them from project context + the user's channel pick.
_BASE_VARS = [
    {
        "key": "project_name",
        "label": "Project name",
        "type": "string",
        "required": True,
        "help": "The Fluxito project this automation should query against.",
    },
    {
        "key": "channel_label",
        "label": "Where to post results",
        "type": "string",
        "required": True,
        "help": "Free-text label for the destination — e.g. 'Slack #growth' or 'alerts@acme.com'.",
    },
]


def _vars(*extra: dict) -> list[dict]:
    """Compose the base variables with automation-specific extras."""
    return list(_BASE_VARS) + list(extra)


# Standard postscript that every automation prompt uses for state / dedup.
_STATE_POSTSCRIPT = """

State and deduplication:
  Read the file `automation-state-{slug}.json` from your Cowork workspace folder
  if it exists. Use it to avoid re-firing the same alert. After running, write
  the latest state back to that file in the form:
    {{ "last_run_at": "<iso8601>", "last_alert": {{ ... }}, "consecutive_quiet_runs": <int> }}
  If the automation has a cooldown of {cooldown_hours}h, do not re-send the same
  alert until that cooldown has elapsed. If the underlying metric has clearly
  recovered, post a brief 'resolved' message and clear the alert from state.
"""


def _wrap_prompt(slug: str, cooldown_hours: int, body: str) -> str:
    """Append the state-handling postscript with the slug interpolated."""
    suffix = _STATE_POSTSCRIPT.format(slug=slug, cooldown_hours=cooldown_hours)
    return body.rstrip() + "\n" + suffix


# ============================================================================ #
# Curated System Automations
# ============================================================================ #
#
# 10 flagship automations across four themes:
#
#   Daily KPI digest                  → 2 automations
#   Per-platform anomaly detectors    → 3 automations
#   Budget pacing + weekly exec       → 3 automations
#   Tag health + campaign launch      → 2 automations
# ============================================================================ #

SYSTEM_PLAYBOOKS: list[dict] = [
    # ────────────────────────────────────────────────────────────────────── #
    # 1. Daily KPI Health Check  (theme: daily_digest)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "daily-kpi-health-check",
        "title": "Daily KPI Health Check",
        "description": (
            "Every weekday morning, pull yesterday's spend, revenue, conversions, "
            "and CPA across every paid channel and GA4. Compare each to the 28-day "
            "median, flag anything that moved more than 25%, and post a one-screen "
            "summary to your team channel. Green checkmark if everything is normal."
        ),
        "theme": THEME_DAILY_DIGEST,
        "icon": "trending-up",
        "required_platforms": ["ga4"],
        "channel_hints": ["slack", "email"],
        "default_cron": "0 8 * * 1-5",
        "default_schedule_label": "Weekday mornings at 8:00 AM",
        "default_task_name": "daily-kpi-health-check",
        "cooldown_hours": 0,  # digest, always fires
        "min_tier": "free",
        "is_featured": True,
        "variables": _vars(),
        "prompt_template": _wrap_prompt(
            "daily-kpi-health-check",
            0,
            """
You are running the Daily KPI Health Check automation for the Fluxito project "{{project_name}}".

Goal: produce a short, scannable digest of yesterday's marketing performance and post it to {{channel_label}}.

Steps:
  1. Call Fluxito's `set_active_project` with "{{project_name}}".
  2. For every connected paid channel (Google Ads, Meta Ads, TikTok Ads, Snap Ads),
     pull yesterday's spend, conversions, and CPA, plus the same metrics for the
     trailing 28 days so you can compute a baseline.
  3. From GA4, pull yesterday's sessions, active users, and total revenue, plus
     the 28-day baseline.
  4. For each metric, compute (yesterday vs 28-day median) percent change.
  5. Build a Slack/email-friendly digest with:
       • A one-line headline ("All green ✅" or "2 channels need attention ⚠️")
       • Per-channel rows: channel | spend | conversions | CPA | Δ vs baseline
       • A GA4 row at the bottom: sessions | users | revenue | Δ vs baseline
       • Highlight any row whose CPA moved >25% or whose revenue moved >25%.
  6. Post the digest to {{channel_label}} using the appropriate channel —
     Slack webhook if it begins with "Slack" or "#", otherwise email.
  7. If any connector returned an error or no data, list the error inline so
     the user knows to fix it instead of silently producing an incomplete digest.

This is a *digest* (always fires) — do not deduplicate. Always post even when
everything is normal.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 2. Weekly Marketing Snapshot  (theme: daily_digest)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "weekly-marketing-snapshot",
        "title": "Weekly Marketing Snapshot",
        "description": (
            "Every Monday morning, send a WoW comparison of every paid channel "
            "plus GA4 — spend, conversions, CPA, sessions, revenue. Includes a "
            "one-paragraph AI-written narrative explaining the biggest movers."
        ),
        "theme": THEME_DAILY_DIGEST,
        "icon": "calendar",
        "required_platforms": ["ga4"],
        "channel_hints": ["slack", "email"],
        "default_cron": "0 9 * * 1",
        "default_schedule_label": "Monday mornings at 9:00 AM",
        "default_task_name": "weekly-marketing-snapshot",
        "cooldown_hours": 0,
        "min_tier": "free",
        "is_featured": True,
        "variables": _vars(),
        "prompt_template": _wrap_prompt(
            "weekly-marketing-snapshot",
            0,
            """
You are running the Weekly Marketing Snapshot for the Fluxito project "{{project_name}}".

Goal: produce a Monday-morning week-over-week comparison and post it to {{channel_label}}.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. Define "last week" as the previous Mon–Sun and "prior week" as the Mon–Sun
     before that.
  3. For each connected paid channel, pull spend / conversions / CPA / clicks
     for both windows.
  4. From GA4, pull sessions / active users / total revenue / new users for
     both windows.
  5. Compute the WoW % change for every metric and identify the 3 biggest
     movers in absolute impact (not just %).
  6. Write a concise narrative (3-5 sentences) explaining what changed and
     why it likely happened, drawing on the dimension breakdowns if useful.
  7. Format the digest as:
       *Marketing snapshot — week of <date>*
       <one-line headline>
       <narrative paragraph>
       <table: channel | spend WoW | conversions WoW | CPA WoW | revenue WoW>
       <footer: link to live dashboard if one exists for this project>
  8. Post to {{channel_label}}. If the channel is a Slack webhook, use Slack
     formatting; if email, use plain HTML.

This is a *digest* — always fires every Monday, no dedup needed.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 3. Meta Ads CPA Spike Watcher  (theme: anomaly)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "meta-cpa-spike-watcher",
        "title": "Meta Ads CPA Spike Watcher",
        "description": (
            "Every 4 hours, check each active Meta Ads campaign's CPA against "
            "its trailing 7-day baseline. Alert only if a campaign's CPA has "
            "spiked more than 40% with at least $50 of recent spend. Suppresses "
            "duplicates within a 12-hour cooldown."
        ),
        "theme": THEME_ANOMALY,
        "icon": "alert-triangle",
        "required_platforms": ["meta"],
        "channel_hints": ["slack"],
        "default_cron": "0 */4 * * *",
        "default_schedule_label": "Every 4 hours",
        "default_task_name": "meta-cpa-spike-watcher",
        "cooldown_hours": 12,
        "min_tier": "free",
        "is_featured": True,
        "variables": _vars(
            {
                "key": "spike_threshold_pct",
                "label": "Spike threshold (%)",
                "type": "number",
                "default": 40,
                "help": "How much above the 7-day baseline counts as a spike.",
            },
            {
                "key": "min_recent_spend_usd",
                "label": "Minimum recent spend ($)",
                "type": "number",
                "default": 50,
                "help": "Ignore campaigns with less than this much spend in the window.",
            },
        ),
        "prompt_template": _wrap_prompt(
            "meta-cpa-spike-watcher",
            12,
            """
You are running the Meta Ads CPA Spike Watcher for the Fluxito project "{{project_name}}".

Goal: detect Meta campaigns whose CPA has spiked materially and post an alert
to {{channel_label}} only when something is actually wrong.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. Pull Meta campaign performance for the last 24 hours and for the trailing
     7 days (excluding the last 24h) so you can compute the baseline cleanly.
  3. For every campaign with at least ${{min_recent_spend_usd}} of spend in the
     last 24 hours, compute the CPA delta vs the 7-day baseline.
  4. A campaign is an "alert candidate" if its CPA is more than
     {{spike_threshold_pct}}% above its 7-day baseline.
  5. Read your state file. For each candidate, decide whether it has already
     fired in the cooldown window — if so, skip it.
  6. If at least one new candidate exists, post ONE message summarising all of
     them to {{channel_label}}. Use the format:
       *Meta CPA spike alert — {{project_name}}*
       <count> campaign(s) above {{spike_threshold_pct}}% baseline:
         • <campaign name>: CPA $X (was $Y, +Z%) — last 24h spend $S
         • ...
       Inspect at: <link to Meta Ads Manager>
  7. Update the state file with the new alert fingerprints and a
     `last_run_at` timestamp.
  8. If everything is below threshold AND the state file shows a previously
     firing alert is now resolved, post a single "✅ Meta CPA back to normal"
     message and clear the state.
  9. If no candidates and no resolution, do not post anything — silent runs
     are OK and expected.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 4. Google Ads CTR / Conv Drop Detector  (theme: anomaly)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "google-ads-ctr-conv-drop",
        "title": "Google Ads CTR & Conversion Drop Detector",
        "description": (
            "Every 6 hours, watch every Google Ads campaign for unusual CTR or "
            "conversion drops vs its 14-day baseline. Alerts only on meaningful "
            "moves on campaigns with material spend. 8-hour cooldown."
        ),
        "theme": THEME_ANOMALY,
        "icon": "trending-down",
        "required_platforms": ["google_ads"],
        "channel_hints": ["slack"],
        "default_cron": "0 */6 * * *",
        "default_schedule_label": "Every 6 hours",
        "default_task_name": "google-ads-ctr-conv-drop",
        "cooldown_hours": 8,
        "min_tier": "free",
        "is_featured": False,
        "variables": _vars(
            {
                "key": "ctr_drop_pct",
                "label": "CTR drop threshold (%)",
                "type": "number",
                "default": 30,
            },
            {
                "key": "conv_drop_pct",
                "label": "Conversion drop threshold (%)",
                "type": "number",
                "default": 35,
            },
        ),
        "prompt_template": _wrap_prompt(
            "google-ads-ctr-conv-drop",
            8,
            """
You are running the Google Ads CTR & Conversion Drop Detector for the
Fluxito project "{{project_name}}".

Goal: catch meaningful CTR or conversion drops on Google Ads campaigns and
alert {{channel_label}} only when there is a real signal.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. Pull Google Ads campaign performance for the last 24h and the trailing
     14 days as baseline.
  3. For each enabled campaign with at least 200 impressions in the last 24h:
       • Compute today's CTR and conversion rate.
       • Compute the 14-day baseline CTR and conversion rate.
       • Flag the campaign if CTR dropped by more than {{ctr_drop_pct}}% OR
         conversion rate dropped by more than {{conv_drop_pct}}%.
  4. Use the state file to deduplicate: skip any campaign that already alerted
     within the {{cooldown_hours}}h cooldown window.
  5. If new alert candidates exist, post ONE Slack/email message summarising
     them — campaign name, baseline → today, % change, last-24h spend.
  6. Include a one-sentence likely-cause hypothesis (e.g. "auction pressure
     up, look at impression share lost to rank") for each campaign.
  7. Update state. Post resolution messages when previously alerting
     campaigns return to normal.
  8. Stay quiet on healthy runs.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 5. GA4 Conversion Drop Sentinel  (theme: anomaly)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "ga4-conversion-drop-sentinel",
        "title": "GA4 Conversion Drop Sentinel",
        "description": (
            "Every 4 hours, compare the last 4 hours of GA4 conversions to the "
            "same window over the last 4 weekdays. If conversions are down more "
            "than 50% with no obvious traffic explanation, alert and break down "
            "by source / medium / device to surface likely cause."
        ),
        "theme": THEME_ANOMALY,
        "icon": "shield-alert",
        "required_platforms": ["ga4"],
        "channel_hints": ["slack"],
        "default_cron": "0 */4 * * *",
        "default_schedule_label": "Every 4 hours",
        "default_task_name": "ga4-conversion-drop-sentinel",
        "cooldown_hours": 6,
        "min_tier": "free",
        "is_featured": True,
        "variables": _vars(
            {
                "key": "drop_threshold_pct",
                "label": "Drop threshold (%)",
                "type": "number",
                "default": 50,
            },
        ),
        "prompt_template": _wrap_prompt(
            "ga4-conversion-drop-sentinel",
            6,
            """
You are running the GA4 Conversion Drop Sentinel for the Fluxito project
"{{project_name}}".

Goal: catch genuine conversion collapses (often caused by tracking outages or
checkout breakage) and alert {{channel_label}} fast.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. Define the *current window* as the last 4 hours.
  3. Define the *baseline* as the same 4-hour window of day-of-week over the
     previous 4 occurrences (e.g. last 4 Tuesdays at 10am-2pm).
  4. Pull GA4 conversions and sessions for the current window and the baseline.
  5. Compute (current_conversions / baseline_median_conversions). If the drop
     exceeds {{drop_threshold_pct}}% AND sessions did NOT drop by a similar
     amount, treat it as an anomaly worth alerting on.
  6. If sessions dropped by a similar amount, do NOT alert — that's just
     normal traffic variance, not a tracking/conversion problem.
  7. On a real anomaly, break the conversions down by source/medium and
     device to identify which slice is collapsing — include the 2 worst
     offenders in the alert.
  8. Use the state file to dedupe within the cooldown window.
  9. Post the alert as:
       *GA4 conversion drop — {{project_name}}*
       Conversions in last 4h: <current> (baseline ~<baseline>, -<%>)
       Sessions are <flat/down> — likely a *<tracking|conversion|paid spend>* issue.
       Worst-hit slices: <slice 1>, <slice 2>
       Inspect: <link>
 10. On recovery, post a single resolution message and clear state. Stay
     silent on healthy runs.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 6. Monthly Budget Pacing Watch  (theme: pacing)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "monthly-budget-pacing",
        "title": "Monthly Budget Pacing Watch",
        "description": (
            "Daily check on each active campaign's pace toward its monthly "
            "budget. Projects end-of-month spend based on the last 7 days and "
            "alerts when any campaign is on track to over- or under-spend by "
            "more than 15%."
        ),
        "theme": THEME_PACING,
        "icon": "gauge",
        "required_platforms": [],
        "channel_hints": ["slack", "email"],
        "default_cron": "0 9 * * *",
        "default_schedule_label": "Every day at 9:00 AM",
        "default_task_name": "monthly-budget-pacing",
        "cooldown_hours": 20,
        "min_tier": "free",
        "is_featured": True,
        "variables": _vars(
            {
                "key": "pacing_tolerance_pct",
                "label": "Pacing tolerance (%)",
                "type": "number",
                "default": 15,
                "help": "Off-pace by more than this triggers an alert.",
            },
        ),
        "prompt_template": _wrap_prompt(
            "monthly-budget-pacing",
            20,
            """
You are running the Monthly Budget Pacing Watch for the Fluxito project
"{{project_name}}".

Goal: warn {{channel_label}} when any campaign is going to materially miss its
monthly budget.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. For every connected paid platform (Google Ads, Meta Ads, TikTok, Snap),
     fetch each active campaign and:
       • Its monthly budget (or daily budget × days-in-month if monthly is unset).
       • Month-to-date spend.
       • Last-7-day average daily spend.
  3. Project end-of-month spend as:
       MTD spend + (days remaining in current month × last_7d_avg_daily_spend)
  4. Compute (projected / monthly_budget) - 1 for each campaign.
  5. Flag campaigns where the projection is more than {{pacing_tolerance_pct}}%
     above OR below budget.
  6. Read state — only alert on campaigns whose projection delta has CHANGED
     by more than 5 percentage points since the last alert (so you don't
     re-alert daily on a campaign that's been steadily over).
  7. Post a single grouped message:
       *Pacing alert — {{project_name}}*
       <N> campaigns off-pace:
         🔥 <campaign> · Meta · projected $X (budget $Y, +Z%)
         💤 <campaign> · Google · projected $A (budget $B, -C%)
       Recommendations: throttle the over-pacing ones and check why the
       under-pacing ones aren't delivering.
  8. Update state with the latest projections per campaign.
  9. Stay silent if nothing crosses the threshold.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 7. Weekly Executive Summary  (theme: exec_summary)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "weekly-exec-summary",
        "title": "Weekly Executive Summary",
        "description": (
            "Every Monday morning, produce an exec-ready narrative covering "
            "every connected platform — WoW deltas, top movers, what changed "
            "and why, plus 3 'things to watch' for the upcoming week. "
            "Designed to land in a leadership inbox or channel as-is."
        ),
        "theme": THEME_EXEC_SUMMARY,
        "icon": "briefcase",
        "required_platforms": [],
        "channel_hints": ["email", "slack"],
        "default_cron": "30 8 * * 1",
        "default_schedule_label": "Monday mornings at 8:30 AM",
        "default_task_name": "weekly-exec-summary",
        "cooldown_hours": 0,
        "min_tier": "pro",
        "is_featured": True,
        "variables": _vars(),
        "prompt_template": _wrap_prompt(
            "weekly-exec-summary",
            0,
            """
You are writing the Weekly Executive Summary for the Fluxito project
"{{project_name}}". This goes to leadership at {{channel_label}} — write it
like a polished memo, not a data dump.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. Define "last week" (Mon-Sun just ended) and "prior week".
  3. For every connected platform, pull headline metrics for both weeks:
       • GA4: sessions, users, revenue, conversions
       • Each paid channel: spend, conversions, CPA, ROAS
       • Search Console (if connected): clicks, impressions, position
  4. Compute the cross-platform blended numbers:
       • Total marketing spend (sum across paid)
       • Total revenue
       • Blended ROAS
       • Total conversions
  5. Identify the 3 biggest WoW movers in absolute impact.
  6. Write a 4-section memo:
       (a) Headline — one sentence on whether last week was good/bad/flat.
       (b) The numbers — a compact table with blended totals + WoW deltas.
       (c) What changed — narrative paragraph explaining the 3 biggest movers
           with hypothesis on root cause.
       (d) Things to watch — 3 forward-looking bullets for this week.
  7. Format for {{channel_label}}: HTML if email, Slack-formatted if Slack.
     Keep it under one screen — leadership should not need to scroll.
  8. Post to {{channel_label}}.

Always fires Monday mornings — this is a digest, not an alert. No dedup.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 8. Spend Anomaly Detector  (theme: pacing)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "spend-anomaly-detector",
        "title": "Spend Anomaly Detector",
        "description": (
            "Every 6 hours, check each platform's total spend in the last 24h "
            "vs its 14-day baseline. Catches both runaway-spend incidents and "
            "delivery stalls (spend collapsing to near zero)."
        ),
        "theme": THEME_PACING,
        "icon": "dollar-sign",
        "required_platforms": [],
        "channel_hints": ["slack"],
        "default_cron": "0 */6 * * *",
        "default_schedule_label": "Every 6 hours",
        "default_task_name": "spend-anomaly-detector",
        "cooldown_hours": 8,
        "min_tier": "free",
        "is_featured": False,
        "variables": _vars(
            {
                "key": "spike_pct",
                "label": "Spike threshold (%)",
                "type": "number",
                "default": 50,
            },
            {
                "key": "stall_pct",
                "label": "Stall threshold (%)",
                "type": "number",
                "default": 60,
                "help": "Trigger if spend drops below this percentage of baseline.",
            },
        ),
        "prompt_template": _wrap_prompt(
            "spend-anomaly-detector",
            8,
            """
You are running the Spend Anomaly Detector for the Fluxito project
"{{project_name}}".

Goal: notice both runaway-spend incidents and delivery stalls (spend collapsing
to near zero) on any connected paid platform, and alert {{channel_label}}.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. For every connected paid platform, pull total account-level spend for
     the last 24h and for the trailing 14 days.
  3. Compute the 14-day median daily spend per platform as the baseline.
  4. For each platform, flag if either:
       • Last-24h spend is more than {{spike_pct}}% above baseline, OR
       • Last-24h spend is below {{stall_pct}}% of baseline (delivery stall).
  5. Use state file dedup against the cooldown window.
  6. Post a single grouped alert if there are new findings:
       *Spend anomaly — {{project_name}}*
       🔥 Spike: Meta $4,200 (baseline $1,900 — +121%)
       💤 Stall: Google Ads $80 (baseline $1,250 — -94%)
       Inspect: <links to each platform>
  7. Stalls often mean a billing issue, a paused campaign, or a tracking
     outage. Spikes often mean a budget bug or a runaway auction. Add a
     one-line hypothesis next to each finding.
  8. Update state. Post resolution messages on recovery. Stay quiet on
     healthy runs.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 9. GTM & GA4 Tag Health Monitor  (theme: tag_health)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "gtm-ga4-tag-health",
        "title": "GTM & GA4 Tag Health Monitor",
        "description": (
            "Daily audit of GTM tags + GA4 event collection. Flags tags that "
            "have errors, GA4 events whose volume has dropped >40% vs the "
            "7-day baseline (likely tracking break), and GTM containers with "
            "unpublished changes lingering more than 7 days."
        ),
        "theme": THEME_TAG_HEALTH,
        "icon": "shield-check",
        "required_platforms": ["ga4"],
        "channel_hints": ["slack", "email"],
        "default_cron": "0 9 * * *",
        "default_schedule_label": "Every day at 9:00 AM",
        "default_task_name": "gtm-ga4-tag-health",
        "cooldown_hours": 20,
        "min_tier": "free",
        "is_featured": True,
        "variables": _vars(),
        "prompt_template": _wrap_prompt(
            "gtm-ga4-tag-health",
            20,
            """
You are running the GTM & GA4 Tag Health Monitor for the Fluxito project
"{{project_name}}".

Goal: catch tracking breakage early and alert {{channel_label}}.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. From GA4, pull the count of every event for yesterday and the 7-day
     median per event.
  3. For every event with a 7-day median above 50, flag any whose yesterday
     count is more than 40% below the median — this almost always indicates
     a tracking break, not real user behaviour.
  4. From GTM (if connected), call the GTM audit tools to:
       • List any tags reporting errors in the most recent workspace
       • List any tags or variables flagged as 'paused' or referenced but missing
       • List any container with workspace changes unpublished for more than 7 days
  5. Use state to suppress duplicates for events / tags already flagged in
     the cooldown window.
  6. If there are findings, post a single grouped alert:
       *Tracking health — {{project_name}}*
       GA4 events with suspicious drops:
         • purchase: 120 yesterday (baseline ~310, -61%)
         • add_to_cart: ...
       GTM tag issues:
         • Tag "Meta CAPI Purchase" is firing with errors (last seen <date>)
         • Workspace "main" has 4 unpublished changes from 12 days ago
  7. Include a one-line hypothesis + a "where to look" hint next to each item.
  8. Update state. Post recovery messages on resolution. Stay silent if all
     is well.
            """,
        ),
    },
    # ────────────────────────────────────────────────────────────────────── #
    # 10. Campaign Launch Monitor  (theme: launch_monitor)
    # ────────────────────────────────────────────────────────────────────── #
    {
        "slug": "campaign-launch-monitor",
        "title": "Campaign Launch Monitor (72h watch)",
        "description": (
            "For any campaign launched in the last 72 hours across any "
            "connected paid platform, post a daily early-signal report — "
            "spend pace, click volume, conversion rate, CPA — flagging any "
            "that look broken in the critical first 3 days."
        ),
        "theme": THEME_LAUNCH_MONITOR,
        "icon": "rocket",
        "required_platforms": [],
        "channel_hints": ["slack"],
        "default_cron": "0 10 * * *",
        "default_schedule_label": "Every day at 10:00 AM",
        "default_task_name": "campaign-launch-monitor",
        "cooldown_hours": 20,
        "min_tier": "pro",
        "is_featured": True,
        "variables": _vars(),
        "prompt_template": _wrap_prompt(
            "campaign-launch-monitor",
            20,
            """
You are running the Campaign Launch Monitor for the Fluxito project
"{{project_name}}".

Goal: babysit campaigns in their first 72 hours. Post a daily early-signal
report to {{channel_label}}, flagging any campaign that looks broken so the
ad ops team can fix it before it wastes more budget.

Steps:
  1. Call `set_active_project` with "{{project_name}}".
  2. For each connected paid platform, list every campaign created within
     the last 72 hours.
  3. For each new campaign, pull lifetime metrics since launch:
       • Spend
       • Impressions, clicks, CTR
       • Conversions, conversion rate, CPA
       • Spend pace vs daily budget × hours-elapsed
  4. Flag a campaign as "needs attention" if any of the following are true:
       • Spend pace is below 30% (under-delivering — wasted budget allocation)
       • Spend pace is above 200% (likely budget bug or unintended bid)
       • Click volume is < 50 with budget-pace > 80% (delivery without engagement)
       • Conversion rate is exactly 0 with > 200 clicks (likely tracking break)
       • CPA is more than 3x the account average for similar campaigns
  5. Read state to know which campaigns you've already flagged today.
  6. Post a single grouped report to {{channel_label}}:
       *New campaign launch report — {{project_name}}*
       Tracking <N> campaigns launched in the last 72h:
         ✅ <campaign>: pacing 95%, CTR 1.4%, CVR 2.1%, CPA $14 — healthy
         ⚠️ <campaign>: 0 conversions on 412 clicks — *check tracking*
         🔥 <campaign>: pacing 320% — *budget bug?*
  7. Update state with each campaign's status. Post a "all healthy" message
     ONCE per day even if nothing is wrong, since this is also a digest for
     ad ops to know launches are tracked. Skip the day entirely if no
     campaigns launched in the last 72h.
            """,
        ),
    },
]


# ============================================================================ #
# Seed Function
# ============================================================================ #


async def seed_automations(db_session_factory) -> int:
    """
    Upsert curated system automations.

      • New slugs are INSERTED.
      • Existing curated slugs are UPDATED in place (preserving id, use_count,
        and created_at).
      • Any other ``system`` automations not in the curated list are marked
        ``is_active=False`` so they disappear from the UI without breaking
        existing install records.

    Returns the number of rows inserted + updated.
    """
    from app.models.automation import AUTOMATION_TYPE_SYSTEM, Automation

    curated_slugs = {p["slug"] for p in SYSTEM_PLAYBOOKS}
    touched = 0

    async with db_session_factory() as db:
        result = await db.execute(
            select(Automation).where(Automation.playbook_type == AUTOMATION_TYPE_SYSTEM)
        )
        existing = {p.slug: p for p in result.scalars().all()}

        for data in SYSTEM_PLAYBOOKS:
            slug = data["slug"]
            if slug in existing:
                pb = existing[slug]
                for key, val in data.items():
                    if key == "slug":
                        continue
                    setattr(pb, key, val)
                # Make sure it's marked as system + active
                pb.playbook_type = AUTOMATION_TYPE_SYSTEM
                pb.is_active = True
                touched += 1
            else:
                db.add(
                    Automation(
                        playbook_type=AUTOMATION_TYPE_SYSTEM,
                        is_active=True,
                        **data,
                    )
                )
                touched += 1

        deactivated = 0
        for slug, pb in existing.items():
            if slug not in curated_slugs and pb.is_active:
                pb.is_active = False
                deactivated += 1

        await db.commit()

        logger.info(
            "Automation seed complete: %d curated upserted, %d legacy deactivated",
            touched,
            deactivated,
        )

    return touched


# ============================================================================ #
# Main (manual seeding)
# ============================================================================ #


async def _main():
    from app.db.database import async_session_factory

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    n = await seed_automations(async_session_factory)
    logger.info("Seed complete: %d automations processed", n)


if __name__ == "__main__":
    asyncio.run(_main())
