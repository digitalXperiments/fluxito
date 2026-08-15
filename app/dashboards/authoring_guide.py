"""Single source of truth for how a model authors a Fluxito-hosted dashboard.

Returned verbatim by ``get_dashboard_authoring_guide``. Keep this document
complete and unambiguous — models are expected to fetch it first and follow
it exactly. Fluxito does not generate dashboard UI.
"""

from __future__ import annotations

from app.dashboards.artifact import (
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

You are writing a **Python Streamlit app**. Fluxito **hosts** that app. Fluxito
does **not** generate cards, charts, HTML, JavaScript, or ECharts specs.

Those card tools are **unregistered**. They do not exist. Do not call
dashboard_deploy_batch, dashboard_create, dashboard_card_upsert,
dashboard_card_preview, or dashboard_card_remove.

## Required first step

1. Call `get_dashboard_authoring_guide` (you already have this text).
2. Call `list_dashboard_connections` to see bindable aliases/types for this project.
3. For each type you will use, follow the recipe in this guide (or call
   `get_dashboard_query_recipe` with that type). Do not invent actions or params.
4. Write the Streamlit app + `manifest.json`.
5. Call `validate_dashboard_artifact` and fix every error.
6. Call `deploy_dashboard` (create) or `update_dashboard` (replace an existing one).
7. Call `bind_dashboard` so aliases attach to this project's stored credentials.
8. Give the user the returned `url`. That URL is the live hosted app.

Never skip validate. Never put secrets in source. Never emit card JSON.

## What you produce

A small Python project, sent as `files` (object of path → UTF-8 source):

    files = {{
      "manifest.json": "<json>",
      "app.py": "<streamlit app>",
    }}

Optional extra modules (imported by app.py) are allowed: `charts.py`, `theme.py`,
`README.md`, `.streamlit/config.toml`. No binaries, no `.env`, no credential files.

### Limits

- Max {MAX_FILES} files
- Max {MAX_FILE_BYTES} bytes per file
- Max {MAX_TOTAL_BYTES} bytes total
- Allowed suffixes: .py .txt .md .toml .css .json
- Paths are relative. No `..`, no absolute paths, no `.env*`

## manifest.json (required)

```json
{{
  "schema_version": {ARTIFACT_SCHEMA_VERSION},
  "title": "Acquisition overview",
  "entrypoint": "app.py",
  "connections": [
    {{"alias": "ga4", "type": "ga4", "required": true}},
    {{"alias": "ads", "type": "google_ads", "required": true}}
  ]
}}
```

Rules:

- `schema_version` must be {ARTIFACT_SCHEMA_VERSION}.
- `title` is the human name shown in the reporting UI.
- `entrypoint` must be a `.py` file present in `files`. Default `app.py`.
- `connections` is required. Each entry:
  - `alias` — snake_case identifier you will pass to `fluxito_data.query`.
  - `type` — a Fluxito connection type (see below). Must match a live project connection.
  - `required` — default true. If true and the project has no matching connection,
    deploy still succeeds but the reporting UI marks the bind as `missing` and
    `fluxito_data.query` returns an error until the user connects that platform.

Known `type` values:
  {", ".join(sorted(CONNECTION_TYPES))}

Default tool dispatched for each type:
{chr(10).join(f"  - {k} → {v}" for k, v in sorted(CONNECTION_TOOL.items()))}

You do **not** pass OAuth tokens, API keys, service-account JSON, passwords,
`DATABASE_URL`, or Fernet keys. The platform injects stored credentials at
runtime via the data helper.

## How live data works (mandatory)

Fluxito copies a helper named `fluxito_data.py` into the working directory.
Import it. Do not rewrite it. Do not vendor secrets.

```python
import streamlit as st
import fluxito_data as fx

st.set_page_config(page_title="Acquisition overview", layout="wide")
st.title("Acquisition overview")

start, end = st.date_input("Range", value=fx.default_range(30))
result = fx.query(
    "ga4",
    action="run_report",
    params={{
        "metrics": ["sessions", "totalUsers"],
        "dimensions": ["date"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }},
)
if result.get("error"):
    st.error(result["message"])
else:
    st.line_chart(fx.as_dataframe(result), x="date")

if st.button("Refresh data"):
    st.rerun()
```

`fx.query(alias, action, params)` POSTs to Fluxito's data plane. Fluxito
resolves `alias` → the bound connection → stored credentials and runs the
MCP tool **owned by that binding**. You cannot pass a tool name. The
Streamlit process never receives Fernet keys, OAuth refresh tokens, or the
app database URL.

Helper API (already on PYTHONPATH of the hosted process):

- `query(alias, action, params=None)` → dict  (alias-only; no tool argument)
- `as_dataframe(result)` → pandas.DataFrame when pandas is available, else list[dict]
- `summarize(result)` → dict of numeric totals / first-row metrics for `st.metric`
- `default_range(days=30)` → (start_date, end_date) as date objects
- `connections()` → list of {{alias, type, status}} bound for this dashboard

There is no `fx.refresh()`. Widgets trigger a Streamlit rerun; call `query` again
with the widget values.

`params` must match the recipe for the alias's `type`. Bound resource identity
(property_id, customer_id, site_url, connection_id, account_id, advertiser_id)
is **injected by the host and overwrites** whatever you send. Never pass `tool`.
"""

_GUIDE_TAIL = f"""
To attach or refresh aliases after deploy, call `bind_dashboard(dashboard_id)`
or `bind_dashboard(dashboard_id, bindings=[{{"alias": "ga4", "type": "ga4"}}])`.
Never pass a `tool` field.

## Forbidden (validation rejects the artifact)

- Any `.env`, `credentials.json`, `service-account.json`, `secrets.toml`, `*.pem`, `*.key`
- Private keys, AWS keys, GitHub/Slack tokens, `password=` / `api_key=` assignments
- `DATABASE_URL=...`, `TOKEN_ENCRYPTION_KEY=...`, postgres://user:pass@host
- `subprocess`, `os.system`, `os.popen`, `os.exec*`, `pty.spawn`
- Baking tokens into Streamlit `secrets.toml` or `st.secrets`
- Asking the user to paste a service-account JSON into the app
- Generating Fluxito card JSON / chart_type / ECharts specs — that path is dead

## Styling and interactivity expectations

Build something a stakeholder would keep open.

- `st.set_page_config(layout="wide")`
- A clear title and one-sentence description
- Date range + any dimension filters as Streamlit widgets (they trigger a rerun;
  call `fx.query` with the widget values — that **is** the refresh path)
- KPI row (`st.metric`) above charts
- Use `st.line_chart` / `st.bar_chart` / `st.altair_chart` / `st.plotly_chart` /
  `st.dataframe` — interactive, not screenshots
- Handle `result["error"]` with `st.error` and a short fix ("Connect GA4 in
  Fluxito → Connections")
- Empty state when a required alias is `missing`
- Do not block the whole page on one failed query; isolate each section

A `.streamlit/config.toml` is optional. Prefer:

```toml
[theme]
base = "light"
primaryColor = "#1a1a1a"
backgroundColor = "#faf8f5"
secondaryBackgroundColor = "#ffffff"
textColor = "#1a1a1a"
font = "sans serif"

[server]
headless = true
```

Do not set `enableCORS` / ports / browser.gatherUsageStats — the host owns those.

## Validate-then-deploy

```
validate_dashboard_artifact(files=..., manifest=optional)
```

Returns `{{ok, digest, manifest, warnings, errors?}}`. If `ok` is false, fix
every error and validate again. Do not deploy an invalid artifact.

```
deploy_dashboard(title=..., files=..., description?=..., manifest?=...)
```

Creates a new hosted dashboard, writes the artifact to an isolated working
directory, binds connection aliases to this project's stored credentials,
starts a Streamlit process, and returns:

```
{{
  "dashboard_id": "<uuid>",
  "slug": "...",
  "url": "https://…/live-dashboards/<slug>",
  "host_status": "running" | "starting" | "error",
  "bindings": [{{"alias", "type", "status", "label"}}]
}}
```

```
update_dashboard(dashboard_id=..., files=..., title?=..., description?=..., manifest?=...)
```

Replaces the artifact, restarts the host, rebinds connections. Same return shape.

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
the share `slug`.

## Failure modes (read these before retrying)

| Symptom | Cause | Fix |
| --- | --- | --- |
| validate: looks like a secret | credentials in source | delete them; use an alias |
| validate: entrypoint not in files | missing app.py | include the file |
| validate: must import streamlit | not a Streamlit app | `import streamlit as st` |
| validate: subprocess not allowed | shell-out | use `fluxito_data.query` |
| deploy: unauthenticated | no MCP session | user must reconnect Fluxito |
| deploy: too many dashboards | 10 per user cap | delete an old one |
| binding status=missing | platform not connected | tell the user to Connect that platform; do not invent tokens |
| query error "unknown alias" | alias not in manifest | add it to connections[] |
| query error from the tool | bad params / scope | fix action/params; do not catch-and-hide |
| host_status=error | Streamlit failed to start | check entrypoint syntax; reread this guide |

## Minimal complete example

manifest.json:

```json
{{
  "schema_version": {ARTIFACT_SCHEMA_VERSION},
  "title": "GA4 last 30 days",
  "entrypoint": "app.py",
  "connections": [{{"alias": "ga4", "type": "ga4"}}]
}}
```

app.py:

```python
import streamlit as st
import fluxito_data as fx

st.set_page_config(page_title="GA4 last 30 days", layout="wide")
st.title("GA4 last 30 days")
st.caption("Live data via the project's connected GA4 property. No secrets in this file.")

start, end = st.date_input("Range", value=fx.default_range(30))
data = fx.query(
    "ga4",
    action="run_report",
    params={{
        "metrics": ["sessions", "totalUsers", "bounceRate"],
        "dimensions": ["date"],
        "start_date": start.isoformat() if hasattr(start, "isoformat") else str(start),
        "end_date": end.isoformat() if hasattr(end, "isoformat") else str(end),
    }},
)
if data.get("error"):
    st.error(data.get("message") or "Query failed")
else:
    cols = st.columns(3)
    summary = fx.summarize(data)
    cols[0].metric("Sessions", summary.get("sessions", "—"))
    cols[1].metric("Users", summary.get("totalUsers", "—"))
    cols[2].metric("Bounce rate", summary.get("bounceRate", "—"))
    st.line_chart(fx.as_dataframe(data), x="date")
```

That is the entire product: you write Streamlit, Fluxito hosts it, credentials
stay in Fluxito.
"""

AUTHORING_GUIDE = (_GUIDE_HEAD + "\n" + recipes_markdown() + "\n" + _GUIDE_TAIL).strip()


def authoring_guide_payload() -> dict:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "streamlit",
        "guide": AUTHORING_GUIDE,
        "connection_types": sorted(CONNECTION_TYPES),
        "connection_tools": dict(CONNECTION_TOOL),
        "recipes": all_recipes(),
        "limits": {
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
        },
        "helper_api": [
            "query(alias, action, params=None)",
            "as_dataframe(result)",
            "summarize(result)",
            "default_range(days=30)",
            "connections()",
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
            "secrets in source or .env",
            "card JSON / chart_type / ECharts",
            "subprocess or shell-out",
            "caller-chosen tool on fluxito_data.query",
            "dashboard_deploy_batch / dashboard_card_* (unregistered)",
            "st.secrets or asking the user for tokens",
        ],
    }
