# Workflow — Build & deploy a dashboard

Use when the user wants a live Fluxito dashboard. The dashboard is a set of **cards**,
each card a saved tool call rendered as a chart/table. `dashboard_deploy_batch` is a
direct (typed) tool — call `get_session_context(tool_name="dashboard_deploy_batch")` or
read its schema for the exact card shape; this is the method around it.

## 1. Decide what the dashboard answers

Anchor on the user's KPIs. Pull definitions from the knowledge base first:
`get_knowledge(action="list_kpis")` → `get_knowledge(action="get_kpi", params={"slug":…})`.
Prefer `compute_kpi` over hand-composing formulas. Each KPI usually maps to one card.

## 2. Build the cards

Each card names a `platform`, a tool `action`, and that action's `params`. The deploy
validates required params **per (platform, action)** before saving — if it rejects a card,
the error names the missing field. Common cards:

- GA4 report → `platform:"ga4"`, `action:"run_report"`, params `property_id`, `metrics`,
  `dimensions`, `start_date`, `end_date`.
- GA4 realtime → `action:"get_realtime"`, params `property_id`, `metrics`.
- Warehouse → `platform:"bigquery"|"redshift"|"snowflake"`, `action:"run_query"`,
  params `query` (Redshift/Snowflake also need `connection_id`).
- Ads → the relevant `marketing_read` action with `ad_account_ids` etc.

Match each card's params to what the underlying read tool needs — confirm with that tool's
`describe` if unsure. Give every card a unique `key` and a clear `title`.

## 3. Deploy

`dashboard_deploy_batch(title=…, cards=[…])` creates the dashboard; pass an existing
`dashboard_id` to update in place. Use `query_token_required` + `filter_presets` if the
schema offers them.

## 3b. Or build incrementally, card-by-card

Prefer this over step 3 when the user wants to see a card before committing it, or
wants to keep adding cards over several turns (a chat-based build). These are direct
tools — call `get_session_context(tool_name=…)` for each one's exact schema.

1. `dashboard_create(title=…)` — makes an empty dashboard shell (zero cards is fine;
   it renders normally everywhere). Returns `dashboard_id`, `slug`, `url`.
2. `dashboard_card_preview(platform, tool, action, params, chart_type, chart_config?)`
   — runs the query and validates the chart spec, returns a live `snap` + the
   normalized spec. **Persists nothing** — no dashboard needed, safe to call
   repeatedly while iterating on chart_type/params with the user.
3. Once the user approves the preview, `dashboard_card_upsert(dashboard_slug, card)`
   — adds it (or updates it, if `card.key` matches an existing card) to the dashboard
   from step 1. This also auto-extends the dashboard's `query_scopes` for that card's
   data source — no separate `dashboard_manage_scopes` call needed for cards you just
   added. Repeat steps 2–3 for each additional card (cap: 20 cards/dashboard).
4. `dashboard_card_remove(dashboard_slug, card_key)` — drops a card by its `key` if
   the user wants to take one back out.

Same card shape and per-platform param rules as `dashboard_deploy_batch` (step 2 above)
apply to `dashboard_card_preview`/`dashboard_card_upsert`'s `card`/`params` — only the
call cadence differs (one card at a time vs. the whole batch up front).

## 4. Share & rotate

- `dashboard_manage_scopes` controls who can see it.
- `dashboard_rotate_token` issues a fresh share token — **irreversible** (the old token
  dies immediately) and it forces token-required access. Surface the new token to the user
  once; don't paste it where it gets logged. Confirm before rotating.

## 5. Verify

`dashboard_read(action="get", params={"dashboard_id":…})` to confirm the cards saved as
intended, then share the link with the user.
