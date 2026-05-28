# SDR Markdown Contract

The SDR is parsed back into a database (`sdr_events` / `sdr_parameters` /
`sdr_destinations`). The document **MUST** use this exact structure or events
will silently fail to project and downstream audits will break. This mirrors the
server's `generate_sdr_markdown` / `parse_sdr_markdown`.

YAML frontmatter first, then one H1 title, then these H2 sections IN ORDER, each
followed by a `---` separator line:

```
## Business Context
## User Journeys
## Data Layer Schema
## Event Catalog
## User Properties / Custom Dimensions
## Destinations Matrix
## Consent & Privacy
## Ownership & Governance
## Changelog
```

Each event in the Event Catalog is an H3 block in EXACTLY this shape (the parser
keys off these literal labels — keep them verbatim):

```
### `event_name`

*Status:* `implemented` | *Last verified:* `never`

**Business Purpose:** <one or two sentences tying the event to a KPI/journey>

**Triggers:**
- Type: `datalayer_event`        (one of: pageview, click, form_submit, datalayer_event, scroll, timer, custom)
- Configuration: <where/how it fires>
- Conditions: <edge cases — refunds, renewals, internal traffic — when relevant>

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `transaction_id` | string | yes | dataLayer.ecommerce.transaction_id | `T-12345` | unique per order |

**Destinations:**

- **GA4**: event name `purchase`
- **GOOGLE_ADS** (`AW-123`): event name `purchase`
- **META**: event name `Purchase`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: <team> · Technical: <team>

**Related KPIs:** <comma-separated KPI names from the intake>

**Edge Cases & Notes:** <anything a smart analyst would want flagged>
```

**Status values:** `planned | implemented | verified | deprecated`. Mark an event
`implemented`/`verified` only when a live source scan actually showed volume;
otherwise `planned`. Leave a `[TODO: ...]` marker anywhere you genuinely lack
information — do NOT invent facts to remove a TODO.

**Destinations notes:** `- **GA4**: event name \`x\`` always parses. An account id
may be given as `(\`AW-123\`)` or `(customer \`AW-123\`)`. Platform labels are
case/underscore tolerant (`GOOGLE_ADS` → `google_ads`).

Validate by round-tripping: the example SDRs in `../examples/` parse cleanly with
zero unresolved TODOs (enforced by the repo's anti-drift test).
