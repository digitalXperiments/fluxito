# Use Automations

Automations are scheduled AI workflows. They are best for repeated monitoring and alerts: weekly summaries, anomaly checks, budget pacing, data quality checks, and KPI digests.

Fluxito stores automation recipes and install details. Your AI client or scheduler executes the recurring task using the rendered prompt.

## When to use automations

Use automations when the question repeats:

- Every Monday, summarize last week's biggest changes.
- Every morning, check GA4 conversions and paid spend.
- Alert when paid CAC exceeds target.
- Watch Search Console top movers.
- Check whether GTM tags or GA4 events drift from the SDR.

Do not use automations for one-time analysis. Ask the AI directly instead.

## 1. Connect required platforms

Each automation lists the platforms it needs. For example:

- A paid pacing automation may need Google Ads or Meta Ads.
- A tracking audit automation may need GA4 and GTM.
- A warehouse freshness automation may need BigQuery, Snowflake, or Redshift.

Connect the platforms first under:

```text
/connect
```

## 2. Browse the automation library

Go to:

```text
Automations
```

Or open:

```text
/automations
```

Review the theme, required platforms, default schedule, variables, and prompt preview.

## 3. Install an automation

Pick a recipe and fill required variables, such as:

- Reporting channel.
- Date range.
- Property or account.
- Alert threshold.
- Stakeholder audience.

The install stores the rendered prompt and schedule so you can inspect what the automation was asked to do.

## 4. Ask the AI to install one

Example prompts:

```text
Browse Fluxito automations that work with my connected platforms.
```

```text
Install a Monday 9am weekly performance summary for this project and send it to our marketing Slack channel.
```

```text
Create an automation that checks GA4 conversions and Google Ads spend every morning and reports only anomalies.
```

```text
Save a custom automation that audits GTM against the approved SDR every Friday.
```

## 5. Review installed automations

Open the automation detail page and inspect:

| Field | Why it matters |
|---|---|
| Rendered prompt | Shows exactly what the scheduled AI will run. |
| Cron schedule | Confirms timing. |
| Required platforms | Confirms data access. |
| Channel | Confirms where output should go. |
| Install status | Shows whether the automation is active. |

## 6. Good first automations

| Automation | Required setup |
|---|---|
| Weekly marketing pulse | GA4 plus paid platforms |
| GA4 anomaly monitor | GA4 |
| Paid budget pacing | Google Ads, Meta Ads, or another ads connector |
| SEO mover report | Search Console |
| GTM tracking health | GTM and optionally SDR |
| Warehouse freshness check | BigQuery, Snowflake, or Redshift |

## Common issues

| Issue | Fix |
|---|---|
| Automation is incompatible | Connect the required platform first. |
| Output is too noisy | Add thresholds and ask for exceptions-only reporting. |
| Wrong audience or tone | Add stakeholder expectations to Business Context. |
| Metric definitions are inconsistent | Define KPIs in the KPI Library. |
| Tracking audit lacks expected events | Generate and approve an SDR. |

## Recommended pattern

For any automation, define:

1. What to check.
2. How often to check.
3. What counts as a meaningful change.
4. Where to send the result.
5. What the AI should do when there is nothing important to report.
