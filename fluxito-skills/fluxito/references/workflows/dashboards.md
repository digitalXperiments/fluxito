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

## 4. Share & rotate

- `dashboard_manage_scopes` controls who can see it.
- `dashboard_rotate_token` issues a fresh share token — **irreversible** (the old token
  dies immediately) and it forces token-required access. Surface the new token to the user
  once; don't paste it where it gets logged. Confirm before rotating.

## 5. Verify

`dashboard_read(action="get", params={"dashboard_id":…})` to confirm the cards saved as
intended, then share the link with the user.
