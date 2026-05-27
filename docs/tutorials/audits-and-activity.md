# Run Audits and Review Activity

Fluxito audits help the AI inspect tracking, analytics, ads, SEO, and warehouse health. The Activity Log records every AI tool call, including reads, audits, writes, arguments, responses, status, and latency.

Use audits to find problems. Use the Activity Log to understand what the AI actually did.

## What audits can check

Audits vary by platform, but common checks include:

| Area | Examples |
|---|---|
| GA4 | Data streams, conversion events, custom definitions, ecommerce, anomalies |
| GTM | Tags without triggers, duplicate configs, GA4 implementation, consent mode, SDR drift |
| Paid media | Tracking setup, budget utilization, quality scores |
| Search Console | Top movers, striking-distance queries, CTR outliers, sitemap health |
| Warehouses | Connection health, stale tables, empty tables, schema health, clustering |
| Adobe | Report suite health, Launch property checks, consent heuristics |

## 1. Connect the platform

Audits need live access to the relevant platform.

For example:

- GTM audit needs Google Tag Manager.
- GA4 audit needs Google Analytics 4.
- SEO audit needs Search Console.
- Warehouse audit needs BigQuery, Snowflake, or Redshift.

## 2. Ask the AI to run an audit

Examples:

```text
Audit my GTM container and show broken tags, missing triggers, duplicate GA4 tags, and consent issues.
```

```text
Audit GA4 ecommerce implementation for the last 30 days and compare it against our SDR.
```

```text
Check Search Console for top movers and CTR outliers this week.
```

```text
Audit BigQuery for stale or empty tables in the analytics dataset.
```

```text
Investigate why Google Ads conversions are lower than GA4. Use both sources and list the most likely causes.
```

## 3. Use the SDR as the expected plan

Audits become much stronger after you generate and approve an SDR.

Without an SDR, Fluxito audits live implementation heuristically. With an approved SDR, audits can compare actual implementation against expected events, parameters, and destinations.

Recommended prompt:

```text
Run a GTM and GA4 audit against the approved SDR. Separate implementation drift from data-quality issues.
```

## 4. Review results

Ask the AI to group findings by severity:

```text
Group audit findings into critical, warning, and informational. For each critical issue, include the affected tag/event, likely impact, and fix.
```

Good audit output should include:

- What is wrong.
- Why it matters.
- Where it was found.
- How confident the diagnosis is.
- Suggested fix.
- Whether the issue affects reporting, activation, privacy, or cost.

## 5. Review the Activity Log

Go to:

```text
Activity Log
```

Or open:

```text
/activity-log
```

The Activity Log shows:

- Tool calls grouped by day and platform.
- Read versus write operations.
- Failed calls.
- Source AI client.
- Request summaries.
- Full arguments and responses on detail pages.

Use it when you need to answer: "What did the AI do?"

## 6. Check write operations carefully

Writes are flagged in the Activity Log. For GTM, ads, or other write-capable platforms, review:

- Which account/container/property was changed.
- Which tool performed the change.
- The arguments.
- The response status.
- The timestamp and AI client.

## Common issues

| Issue | Fix |
|---|---|
| Audit returns too many findings | Ask the AI to prioritize by business impact. |
| Audit cannot access an account | Reconnect the platform or pick the right active project. |
| SDR comparison is missing | Generate and approve an SDR first. |
| Findings seem generic | Add Business Context and KPI definitions. |
| Need compliance traceability | Use Activity Log detail pages for arguments and responses. |

## Recommended audit cadence

| Cadence | Audit |
|---|---|
| Daily | Critical conversion and spend anomaly checks |
| Weekly | GA4/GTM health, paid tracking setup, SEO movers |
| Monthly | SDR drift, warehouse freshness, dashboard quality |
| Before launches | Consent, conversion, checkout, paid media tracking |
