# Workflow — Build & deploy a hosted dashboard

Use when the user wants a live Fluxito dashboard. You write a **Python Streamlit
app**. Fluxito **hosts** it and injects stored credentials. Fluxito does **not**
generate cards, charts, HTML, or ECharts specs.

Do **not** call `dashboard_deploy_batch`, `dashboard_create`,
`dashboard_card_preview`, `dashboard_card_upsert`, or `dashboard_card_remove`.
Those tools are unregistered.

## 1. Read the contract

`get_dashboard_authoring_guide` — call this first. Follow it exactly.

## 2. See what can be bound

`list_dashboard_connections` returns bindable project connections (type, suggested
alias, label, and a `recipe` with action + example_params). No secrets. Put each
needed source in `manifest.connections` as
`{"alias": suggested_alias, "type": type}`.

If the action or params are unclear, call `get_dashboard_query_recipe` with that
type. Do not invent actions.

## 3. Write the artifact

A small Python project, sent as `files` (path → UTF-8 source):

- `manifest.json` — `schema_version`, `title`, `entrypoint` (`app.py`), `connections[]`
- `app.py` — Streamlit app that imports `fluxito_data as fx` and calls
  `fx.query(alias, action, params)` for live data. **No `tool=` argument.**

Never put secrets, `.env`, service-account JSON, or tokens in the files.

## 4. Validate, deploy, bind

1. `validate_dashboard_artifact(files=…)` — fix every error, then continue.
2. `deploy_dashboard(title=…, files=…)` to create, or `update_dashboard(dashboard_id=…, files=…)`
   to replace. Returns `dashboard_id`, `slug`, `url`, `bindings`.
3. `bind_dashboard(dashboard_id)` (or pass `bindings=[{alias, type, connection_id?}]`)
   to attach this project's stored credentials. You cannot pass a tool name —
   the host maps `type` → tool.

## 5. Live data

The hosted process POSTs `{alias, action, params}` to Fluxito. Fluxito resolves
the bound alias and injects credentials. Caller `tool` is ignored. Bound
`property_id` / `customer_id` / `connection_id` overwrite whatever the app sent.

## 6. Verify

`dashboard_read(action="get", params={"dashboard_id":…})` and share the `url`
(`/live-dashboards/{slug}`) with the user.
