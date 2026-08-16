"""Single source of truth for how a model authors a Fluxito-hosted dashboard.

Returned verbatim by ``get_dashboard_authoring_guide``. Fluxito hosts a
production HTML/JS/CSS build. It does not compile JSX and does not run Streamlit.
"""

from __future__ import annotations

from app.dashboards.artifact import (
    ALLOWED_SUFFIXES,
    ARTIFACT_KIND,
    ARTIFACT_SCHEMA_VERSION,
    CONNECTION_TOOL,
    CONNECTION_TYPES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
)
from app.dashboards.query_recipes import all_recipes, recipes_markdown

_GUIDE_HEAD = f"""
# Fluxito hosted dashboards — authoring contract (schema_version={ARTIFACT_SCHEMA_VERSION})

You are writing a **production frontend** (HTML + JS + CSS). Fluxito **hosts the
files you send as-is** on an isolated origin. It does **not** run npm, Vite,
React, JSX, TypeScript, Python, Streamlit, or any chart library, and it does
not generate cards / ECharts specs. The host injects only the small
`/fluxito.js` data SDK; your UI, CSS, chart library, fonts, icons, images, and
all other runtime assets must be in the production build.

This contract is the same for Antigravity, Claude, Gemini, Grok, and every
other MCP client. A local IDE preview is not the hosted runtime. If you author
React/JSX/TSX, run the project's production build first and send the complete
`dist/` output. Fluxito preserves the authored HTML/CSS/JS; it does not restyle
the dashboard to match Fluxito's chrome.

Those card tools are **unregistered**. They do not exist. Do not call
dashboard_deploy_batch, dashboard_create, dashboard_card_upsert,
dashboard_card_preview, or dashboard_card_remove.

## Required first step

1. Call `get_dashboard_authoring_guide` (you already have this text).
2. Call `list_dashboard_connections` to see bindable aliases/types for this project.
3. For each type you will use, follow the recipe in this guide (or call
   `get_dashboard_query_recipe` with that type). Do not invent actions or params.
4. Build locally (`vite build` / equivalent with `base: './'`).
5. Send `manifest.json`, the **latest** production `index.html`, and **every
   file referenced by it or by its JS/CSS** (including chart libraries, fonts,
   images, and lazy-loaded chunks). Put the exact uploaded path list in
   `manifest.artifact_files`.
6. Call `validate_dashboard_artifact` and fix every error (and read warnings).
7. Call `deploy_dashboard` (create) or `update_dashboard` (replace). After a
   visual change, use `update_dashboard` with the complete new build; do not
   assume the host sees local files.
8. Call `bind_dashboard` so aliases attach to this project's stored credentials.
9. Give the user the returned `url`. That URL is the live hosted app.

Never skip validate. Never put secrets in source. Never send `.jsx` / `.tsx`.
Never emit card JSON. Never write Streamlit.

## What you produce

A small static project, sent as `files` (object of path → UTF-8 source):

    files = {{
      "manifest.json": "<json>",
      "index.html": "<html>",
      "assets/index-xxxxx.js": "<bundle>",
      "assets/index-xxxxx.css": "<css>",
    }}

### Limits

- Max {MAX_FILES} files
- Max {MAX_FILE_BYTES} bytes per file
- Max {MAX_TOTAL_BYTES} bytes total
- Allowed suffixes: {" ".join(sorted(ALLOWED_SUFFIXES))}
- Paths are relative. No `..`, no absolute paths, no `node_modules`, no `.env*`
- **Rejected:** `.jsx` `.tsx` `.ts` `.py` — Fluxito does not compile. Send `dist/`.

`manifest.artifact_files` is a required inventory. It must exactly equal the
keys in the outer `files` object, including `manifest.json`, optional images or
SVGs, and lazy-loaded chunks. Fluxito rejects both an uploaded file omitted from
the inventory and an inventory entry whose file was not uploaded.

Vite (required):

```js
export default {{ base: './' }}
```

Absolute `/assets/...` URLs are rewritten for compatibility, but use relative
asset URLs (`base: './'`) so the same build works in local preview and under
`/s/<slug>/`. Do not rely on a CDN: the dash CSP blocks remote scripts and
external resources. Bundle chart libraries (for example Chart.js or ECharts)
and include the resulting file in `files`. `files` carries UTF-8 text; use SVG
or data URIs for raster/font assets rather than sending raw binary files.

## manifest.json (required)

```json
{{
  "schema_version": {ARTIFACT_SCHEMA_VERSION},
  "kind": "web",
  "title": "Acquisition overview",
  "entrypoint": "index.html",
  "artifact_files": ["assets/index-xxxxx.css", "assets/index-xxxxx.js", "index.html", "manifest.json"],
  "connections": [
    {{"alias": "ga4", "type": "ga4", "required": true}},
    {{"alias": "ads", "type": "google_ads", "required": true}}
  ]
}}
```

Rules:

- `schema_version` must be {ARTIFACT_SCHEMA_VERSION}.
- `kind` is `web`.
- `title` is the human name shown in the reporting UI.
- `entrypoint` must be an `.html` file present in `files`. Default `index.html`.
- `artifact_files` is required and must exactly list every key in `files`.
- `connections` is required. Each entry:
  - `alias` — snake_case identifier you will pass to `fluxito.query`.
  - `type` — a Fluxito connection type (see below). Must match a live project connection.
  - `required` — default true.

Known `type` values:
  {", ".join(sorted(CONNECTION_TYPES))}

Default tool dispatched for each type:
{chr(10).join(f"  - {k} → {v}" for k, v in sorted(CONNECTION_TOOL.items()))}

You do **not** pass OAuth tokens, API keys, service-account JSON, or passwords.
The platform injects stored credentials at query time.

## How live data works (mandatory)

Fluxito injects `/fluxito.js` into `index.html`. Do not vendor, replace, or
rewrite that file. It is the only runtime file supplied by the host.
Do not put tokens in the bundle.

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Acquisition overview</title>
    <link rel="stylesheet" href="./assets/app.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="./assets/app.js"></script>
  </body>
</html>
```

```js
const result = await fluxito.query("ga4", "run_report", {{
  metrics: ["sessions", "totalUsers"],
  dimensions: ["date"],
  start_date: "2026-07-16",
  end_date: "2026-08-15",
}});
if (result.error) {{
  showError(result.message);
}} else {{
  const rows = fluxito.rows(result);
}}
```

`fluxito.query(alias, action, params)` POSTs to Fluxito's data plane with a
short-lived embed token the host delivers via `postMessage`. You cannot pass a
tool name. Bound resource identity (`property_id`, `customer_id`, `connection_id`)
**overwrites** whatever you send.

Helper API (on `window.fluxito` after the host injects the script):

- `query(alias, action, params)` → Promise<dict>
- `rows(result)` → array of objects
- `whenReady()` → Promise that resolves when the embed token arrives

The dashboard runs on a **separate origin**. It cannot see Fluxito cookies,
cannot call `/api/*`, and cannot reach any connection except the aliases in
this manifest. Opening the dash URL outside Fluxito shows no live data.

There is no `fluxito.refresh()`. Re-call `query` when filters change.

The host only adds the SDK tag and serves the files. If a chart, stylesheet,
font, image, or lazy-loaded chunk is referenced but not included in `files`,
the build is incomplete and validation rejects it. If a hosted page differs
from an Antigravity/local preview, verify that the newest `dist/` files were
sent to `update_dashboard` and that the browser is opening the returned live
URL—not the local preview or the dash-origin URL directly.
"""

_GUIDE_TAIL = f"""
To attach or refresh aliases after deploy, call `bind_dashboard(dashboard_id)`
or `bind_dashboard(dashboard_id, bindings=[{{"alias": "ga4", "type": "ga4"}}])`.
Never pass a `tool` field.

## Forbidden (validation rejects the artifact)

- Any `.jsx` / `.tsx` / `.ts` / `.py` — send the production build
- Remote `<script src="https://...">` — bundle every script
- Any local JS/CSS/image/font/chunk reference whose file is not in `files`
- Any `.env`, `credentials.json`, `service-account.json`, `secrets.toml`, `*.pem`, `*.key`
- `node_modules`, lockfiles, private keys, AWS keys, GitHub/Slack tokens
- `DATABASE_URL=...`, `TOKEN_ENCRYPTION_KEY=...`, postgres://user:pass@host
- Streamlit / `fluxito_data` / Fluxito card JSON / `dashboard_card_*`
- Asking the user to paste a service-account JSON into the app

## Visual and query expectations

Build something a stakeholder would keep open.

- A clear title and one-sentence description
- Date range + dimension filters in the page (not a leftover sidebar)
- KPI row above charts
- Interactive charts from `fluxito.rows(...)` — not screenshots
- Bundle the chart/runtime dependency and verify every chart canvas/container
  renders in the production build before deploying
- Handle `result.error` with a short fix ("Connect GA4 in Fluxito → Connections")
- Empty state when a query returns 0 rows, and say why
- Do not block the whole page on one failed query; isolate each section

## Validate-then-deploy

```
validate_dashboard_artifact(files=..., manifest=optional)
```

Returns `{{ok, digest, manifest, warnings, errors?}}`. If `ok` is false, fix
every error and validate again. Read warnings (absolute `/assets/` URLs, missing
`fluxito.query`). Do not deploy an invalid artifact.

```
deploy_dashboard(title=..., files=..., description?=..., manifest?=...)
```

Creates a hosted dashboard, writes the artifact, binds connection aliases to
this project's stored credentials, and returns:

```
{{
  "dashboard_id": "<uuid>",
  "slug": "...",
  "url": "https://…/live-dashboards/<slug>",
  "host_status": "ready" | "error",
  "bindings": [{{"alias", "type", "status", "label"}}]
}}
```

```
update_dashboard(dashboard_id=..., files=..., title?=..., description?=..., manifest?=...)
```

Replaces the artifact, rebinds connections. Same return shape.

```
bind_dashboard(dashboard_id, bindings?= [{{alias, type, connection_id?}}])
```

Attaches this project's stored credentials to the manifest aliases. The host
chooses the MCP tool from `type`. You cannot pass a tool name.

```
list_dashboards() / dashboard_read(action="list")
get_dashboard(dashboard_id) / dashboard_read(action="get", params={{dashboard_id}})
delete_dashboard(dashboard_id)
list_dashboard_connections()
```

IDs are UUIDs. Use `dashboard_id` everywhere except the public URL, which uses
the share `slug`. Only a logged-in project member can open the live view.

## Failure modes (read these before retrying)

| Symptom | Cause | Fix |
| --- | --- | --- |
| validate: looks like a secret | credentials in source | delete them; use an alias |
| validate: Fluxito does not compile .jsx | sent source, not dist/ | `vite build` with `base: './'` and send those files |
| validate: remote script src | CDN script | bundle it |
| validate: local asset missing | `index.html`/CSS/JS references a file not sent | include the complete production build, including chart libraries and lazy chunks |
| validate: file inventory mismatch | `manifest.artifact_files` differs from the uploaded `files` keys | regenerate the exact inventory from the final `dist/` output |
| validate: entrypoint not in files | missing index.html | include the file |
| deploy: unauthenticated | no MCP session | user must reconnect Fluxito |
| deploy: too many dashboards | 10 per user cap | delete an old one |
| binding status=missing | platform not connected | tell the user to Connect that platform |
| query error "unknown alias" | alias not in manifest | add it to connections[] |
| query error from the tool | bad params / scope | fix action/params; do not catch-and-hide |
| live data banner "Open from Fluxito" | opened dash origin directly | use the `/live-dashboards/{{slug}}` URL |

## Minimal complete example

manifest.json:

```json
{{
  "schema_version": {ARTIFACT_SCHEMA_VERSION},
  "kind": "web",
  "title": "GA4 last 30 days",
  "entrypoint": "index.html",
  "artifact_files": ["app.js", "index.html", "manifest.json"],
  "connections": [{{"alias": "ga4", "type": "ga4"}}]
}}
```

index.html:

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>GA4 last 30 days</title>
    <style>
      body {{ font: 15px/1.45 system-ui, sans-serif; background: #0f1419; color: #e8eef7; margin: 0; }}
      main {{ max-width: 960px; margin: 0 auto; padding: 28px 20px; }}
      .kpis {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
      .card {{ background: #161c24; border: 1px solid #2a3340; border-radius: 12px; padding: 16px; }}
      .err {{ color: #f2b8b5; }}
    </style>
  </head>
  <body>
    <main>
      <h1>GA4 last 30 days</h1>
      <p>Live data via the project's connected GA4 property. No secrets in this file.</p>
      <div id="status"></div>
      <div class="kpis">
        <div class="card" id="sessions">Sessions —</div>
        <div class="card" id="users">Users —</div>
        <div class="card" id="bounce">Bounce —</div>
      </div>
    </main>
    <script src="./app.js"></script>
  </body>
</html>
```

app.js:

```js
function end() {{ return new Date().toISOString().slice(0, 10); }}
function start() {{
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d.toISOString().slice(0, 10);
}}

async function main() {{
  const data = await fluxito.query("ga4", "run_report", {{
    metrics: ["sessions", "totalUsers", "bounceRate"],
    dimensions: ["date"],
    start_date: start(),
    end_date: end(),
  }});
  const status = document.getElementById("status");
  if (data.error) {{
    status.className = "err";
    status.textContent = data.message || "Query failed";
    return;
  }}
  const rows = fluxito.rows(data);
  let sessions = 0, users = 0;
  for (const row of rows) {{
    sessions += Number(row.sessions || 0);
    users += Number(row.totalUsers || 0);
  }}
  document.getElementById("sessions").textContent = "Sessions " + sessions;
  document.getElementById("users").textContent = "Users " + users;
  const last = rows[rows.length - 1] || {{}};
  document.getElementById("bounce").textContent = "Bounce " + (last.bounceRate || "—");
}}
main();
```

That is the entire product: you write and build the UI, Fluxito hosts it on
an isolated origin, credentials stay in Fluxito.
"""

AUTHORING_GUIDE = (_GUIDE_HEAD + "\n" + recipes_markdown() + "\n" + _GUIDE_TAIL).strip()


def authoring_guide_payload() -> dict:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": ARTIFACT_KIND,
        "guide": AUTHORING_GUIDE,
        "connection_types": sorted(CONNECTION_TYPES),
        "connection_tools": dict(CONNECTION_TOOL),
        "recipes": all_recipes(),
        "limits": {
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "allowed_suffixes": sorted(ALLOWED_SUFFIXES),
        },
        "hosting": {
            "format": "static production HTML/JS/CSS build",
            "compiles_source": False,
            "injected_runtime": ["/fluxito.js"],
            "preserves_authored_ui": True,
            "requires_complete_asset_graph": True,
            "requires_explicit_file_inventory": True,
            "file_inventory_field": "artifact_files",
            "source_extensions_rejected": [".jsx", ".tsx", ".ts", ".py"],
        },
        "helper_api": [
            "fluxito.query(alias, action, params)",
            "fluxito.rows(result)",
            "fluxito.whenReady()",
        ],
        "flow": [
            "get_dashboard_authoring_guide",
            "list_dashboard_connections",
            "get_dashboard_query_recipe (per type if unsure)",
            "validate_dashboard_artifact",
            "deploy_dashboard or update_dashboard",
            "bind_dashboard",
            "list_dashboards / get_dashboard / delete_dashboard",
        ],
        "forbidden": [
            "JSX/TS source — send the production build",
            "secrets in source or .env",
            "remote script src",
            "Streamlit / fluxito_data / card JSON",
            "caller-chosen tool on fluxito.query",
            "dashboard_deploy_batch / dashboard_card_* (unregistered)",
        ],
    }
