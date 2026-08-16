# Workflow — Build & deploy a hosted dashboard

Use when the user wants a live Fluxito dashboard. You write a **production
HTML/JS app**. Fluxito **hosts** the build on an isolated origin and injects
stored credentials. Fluxito does **not** compile JSX, run Streamlit, or
generate card JSON / ECharts specs.

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

## 3. Write and build the artifact

A static production project, sent as `files` (path → UTF-8 source):

- `manifest.json` — `schema_version`, `kind: "web"`, `title`, `entrypoint` (`index.html`), `connections[]`
- `index.html` + hashed `.js` / `.css` — Vite `base: './'`. **No `.jsx` / `.tsx`.**
- Live data: `fluxito.query(alias, action, params)`. The host injects `/fluxito.js`.
  **No `tool=` argument.**

Never put secrets, `.env`, service-account JSON, or tokens in the files.
Never send `node_modules` or remote `<script src="https://…">`.

## 4. Validate, deploy, bind

1. `validate_dashboard_artifact(files=…)` — fix every error, then continue.
2. `deploy_dashboard(title=…, files=…)` to create, or `update_dashboard(dashboard_id=…, files=…)`
   to replace. Returns `dashboard_id`, `slug`, `url`, `bindings`.
3. `bind_dashboard(dashboard_id)` (or pass `bindings=[{alias, type, connection_id?}]`)
   to attach this project's stored credentials. You cannot pass a tool name —
   the host maps `type` → tool.

## 5. Live data

The hosted page runs on a **separate origin**. It cannot see Fluxito cookies
or call `/api/*`. It POSTs `{alias, action, params}` with a short-lived embed
token. Fluxito resolves the bound alias and injects credentials.

## 6. Verify

`dashboard_read(action="get", params={"dashboard_id":…})` and share the `url`
(`/live-dashboards/{slug}`) with the user. Only logged-in project members can open it.
