# Workflow — Build & deploy a hosted dashboard

Use when the user wants a live Fluxito dashboard. You write a **production
HTML/JS/CSS app**. Fluxito **hosts the exact build** on an isolated origin and
routes live queries through bound connections. Fluxito does **not** run npm,
Vite, React, compile JSX/TSX, run Streamlit, provide Chart.js/ECharts, or
generate card JSON / ECharts specs.

This applies equally to Antigravity, Claude, Gemini, Grok, and every other MCP
client: a local IDE preview is not the hosted runtime. If the UI is authored in
React/JSX/TSX, build it first and send the complete `dist/` output. The only
file the host adds is `/fluxito.js`; every stylesheet, chart library, font,
image, and lazy-loaded chunk referenced by the build must be included.

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

- `manifest.json` — `schema_version`, `kind: "web"`, `title`, `entrypoint` (`index.html`), exact `artifact_files[]` inventory, `connections[]`
- `index.html` + all hashed `.js` / `.css` / static assets — Vite `base: './'`.
  **No `.jsx` / `.tsx` / `.ts` / `.py`.**
- Live data: `fluxito.query(alias, action, params)`. The host injects `/fluxito.js`.
  **No `tool=` argument.**
- `artifact_files` must exactly equal the paths in the outer `files` object. The
  validator rejects both uploaded files omitted from the inventory and
  inventory entries that were not uploaded.

Never put secrets, `.env`, service-account JSON, or tokens in the files.
Never send `node_modules` or remote `<script src="https://…">`; bundle all
runtime dependencies instead of relying on a CDN.

## 4. Validate, deploy, bind

1. `validate_dashboard_artifact(files=…)` — fix every error, including missing
   local asset references, then continue.
2. `deploy_dashboard(title=…, files=…)` to create, or `update_dashboard(dashboard_id=…, files=…)`
   to replace. After a visual change, always send the complete latest build to
   `update_dashboard`; Fluxito does not read files from the local IDE. Returns
   `dashboard_id`, `slug`, `url`, `bindings`.
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
