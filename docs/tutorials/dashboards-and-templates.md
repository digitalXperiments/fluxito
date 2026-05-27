# Build Dashboards and Use Templates

Fluxito dashboards are live, card-based reports. The AI can create dashboard cards from connected platforms, and the web UI can render them, refresh them, share them, schedule them, and export them.

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
Reporting -> Templates
```

Choose a template, review its purpose and required platforms, then deploy it to your project.

After deployment, open the dashboard and confirm the date range, properties, accounts, and scopes.

## 3. Ask the AI to build a dashboard

Example prompts:

```text
Build a weekly marketing dashboard with GA4 sessions, conversions, Google Ads spend, Meta spend, ROAS, and SEO clicks. Make the date range filterable.
```

```text
Create an executive KPI dashboard using our approved KPI Library definitions.
```

```text
Build a checkout funnel dashboard from GA4 and include an audit card for missing purchase events.
```

The AI should create structured cards rather than pasting static screenshots or one-time tables.

## 4. Review dashboard cards

A dashboard can include:

| Card type | Use |
|---|---|
| Scorecard | One number, such as sessions or ROAS |
| Line | Trend over time |
| Bar | Channel, campaign, landing page, or event comparison |
| Pie | Share of total |
| Table | Detailed rows |
| Audit | Findings or issues |
| List | Ranked recommendations or notes |

Good cards have clear titles, live query parameters, and a useful chart type.

## 5. Manage dashboard scopes

Dashboard live queries are scope-gated. If a card cannot refresh, check that the dashboard is allowed to query the platform/property/account it references.

Ask the AI:

```text
Check this dashboard's scopes and add any missing scopes needed for its cards.
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
Review this dashboard and remove cards that duplicate the same insight.
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
| Card does not refresh | Check platform connection, required params, and dashboard scopes. |
| Dashboard has too many cards | Keep the first view focused on decisions, not every metric. |
| KPI numbers differ from stakeholder expectations | Define the KPI in the KPI Library and rebuild the card from that definition. |
| Public link shows old data | Refresh the dashboard and confirm card queries are live. |
| AI chooses the wrong property/account | Set the active project and explicitly name the property/account in your prompt. |

## Recommended dashboard structure

Start with:

1. Outcome KPIs.
2. Funnel or channel drivers.
3. Trend charts.
4. Top movers or anomalies.
5. Audit or data-quality notes.
6. Next actions.
