# Build Dashboards and Use Templates

Fluxito dashboards are live, model-authored web applications. The AI produces a
production HTML/JS/CSS build; Fluxito hosts that build on an isolated origin and
routes live queries through the project's bound connections.

Templates are pre-built dashboard recipes you can deploy faster than starting from scratch.

## When to use dashboards

Use dashboards for repeated reporting:

- Weekly marketing performance.
- Checkout funnel monitoring.
- Paid media ROAS.
- SEO performance.
- Data quality scorecards.
- Executive KPI summaries.

Use ad hoc AI questions for one-off analysis. Use dashboards when people need the same view repeatedly.

## 1. Connect data sources

Dashboards can use any supported source, but you need at least one connected platform.

Common starting points:

- GA4 for traffic, engagement, events, and conversions.
- Google Ads and Meta Ads for paid media.
- Search Console for SEO.
- BigQuery, Snowflake, or Redshift for modeled business data.

## 2. Use a template

Go to:

```text
Reporting → Templates
```

Choose a template, review its purpose and required platforms, then deploy it to your project.

After deployment, open the dashboard and confirm the date range, properties,
accounts, and connection bindings.

## 3. Ask the AI to build a dashboard

Example prompts:

```text
Build a weekly marketing dashboard with GA4 sessions, conversions, Google Ads spend, Meta spend, ROAS, and SEO clicks. Make the date range filterable.
```

```text
Create an executive KPI dashboard using our approved KPI Library definitions.
```

```text
Build a checkout funnel dashboard from GA4 and include a diagnostics section for missing purchase events.
```

The AI should call `get_dashboard_authoring_guide` first, discover the project's
connections, build the production output (for example, Vite `dist/` with
`base: './'`), run `validate_dashboard_artifact`, then deploy or update it.
Fluxito does not compile JSX/TSX, run React/Streamlit, or provide Chart.js,
ECharts, fonts, icons, or other UI dependencies. Every file referenced by the
HTML, CSS, or JavaScript must be included in `files`, and
`manifest.artifact_files` must explicitly list every uploaded path.

## 4. Preserve the authored design

The hosted page is the same authored frontend, inside a thin Fluxito viewer
shell. Fluxito does not translate the layout or restyle the page. To preserve
an Antigravity or local preview exactly:

- send the latest production build, not source `.jsx`, `.tsx`, `.ts`, or `.py`;
- include bundled chart libraries, fonts, images, and lazy-loaded chunks;
- use relative asset paths and bundle dependencies instead of CDN URLs; and
- call `update_dashboard` after visual changes with the complete new `files` object.

Do not mistake a complete file inventory or an HTTP 200 response for a working
runtime. Never hand-write or truncate a library file, copy a Chart.js/ECharts
banner onto a stub, or leave chart `render()`/`update()` methods as no-ops. Run
`node --check` (or the equivalent module syntax check), open the final
production build in a browser, confirm every chart paints, and verify each
query succeeds. `validate_dashboard_artifact` checks the static contract and
asset graph; it does not execute JavaScript or validate chart behavior.

The only host-provided runtime file is `/fluxito.js`. Live data is requested with
`fluxito.query(alias, action, params)` and normalized with `fluxito.rows(result)`.

## 5. Manage dashboard scopes

Dashboard live queries are alias- and connection-gated. If a section cannot
refresh, check the dashboard's bindings and the query recipe for the connection
type it uses.

Ask the AI:

```text
Check this dashboard's connection bindings and repair any missing connection.
```

## 6. Share, schedule, or export

From the dashboard UI you can:

- Open the live dashboard.
- Create a signed public link.
- Schedule email or Slack reports.
- Export a PDF.

Sharing and scheduling are user-triggered from the web UI.

## 7. Improve a dashboard over time

Useful prompts:

```text
Review this dashboard and remove sections that duplicate the same insight.
```

```text
Add benchmarks and expected ranges from our KPI Library where possible.
```

```text
Add a diagnostics section that explains tracking or data-quality issues behind the numbers.
```

```text
Create a public link for stakeholder review after I approve the dashboard.
```

## Common issues

| Issue | Fix |
|---|---|
| Chart area is blank | Check browser asset failures and rerun `validate_dashboard_artifact`; a referenced chart library or chunk may be missing from `files`. |
| Chart library returns 200 but charts are blank | Inspect for a syntax error, truncated/placeholder bundle, or no-op renderer; replace it with the real compiled library and run the production browser smoke test. |
| Page looks different from the local/Antigravity preview | Deploy the latest production `dist/` with `update_dashboard`; Fluxito hosts the sent build and does not compile source. |
| Section does not refresh | Check the connection binding, exact query recipe, and `result.error`. |
| KPI numbers differ from stakeholder expectations | Define the KPI in the KPI Library and rebuild the relevant query/section. |
| Public link shows old data | Open the returned `/live-dashboards/{slug}` URL and confirm the update completed. |
| AI chooses the wrong property/account | Set the active project and explicitly name the property/account in your prompt. |

## Recommended dashboard structure

Start with:

1. Outcome KPIs.
2. Funnel or channel drivers.
3. Trend charts.
4. Top movers or anomalies.
5. Diagnostics or data-quality notes.
6. Next actions.
