# SDR Markdown Contract

The SDR is parsed back into a database (`sdr_events` / `sdr_parameters` /
`sdr_destinations`). Follow this contract exactly or events will silently fail to
project and downstream audits will break.

> **Single source of truth.** The block below is embedded verbatim from the
> server's canonical contract (`app/tools/sdr_bootstrap/contract.py`). A repo test
> asserts they stay identical, so this skill can never drift from the parser.

```
The document MUST use this exact structure so it parses into the event database.

YAML frontmatter first, then one H1 title, then these H2 sections IN ORDER, each
followed by a `---` separator line:

  ## Executive Summary
  ## Business Context
  ## User Journeys
  ## Data Layer Schema
  ## Event Catalog
  ## Conversion Audit
  ## User Properties / Custom Dimensions
  ## Destinations Matrix
  ## Consent & Privacy
  ## Gap Register
  ## Remediation Roadmap
  ## Ownership & Governance
  ## Changelog

Each event in the Event Catalog is an H3 block in EXACTLY this shape (the parser
keys off these literal labels — keep them verbatim):

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

The audit sections are markdown tables with these exact header rows (the viewer and
Excel export parse them as tables — keep the columns in this order):

  ## Executive Summary — a 2-column table of | Property | Value | rows (Property, GTM
  Container, Platforms in scope, Audit date, ...), optionally followed by a short narrative.

  ## Gap Register
  | # | Severity | Finding | Evidence | Business impact | Recommended fix | Fix location | Owner |

  ## Conversion Audit
  | GA4 key event | 90d count | Unique converters | Fires? | Verdict / action |

  ## Consent & Privacy
  | # | Severity | Check | Finding | Recommendation |

  ## Remediation Roadmap
  | Phase | Action | Resolves | Effort | Impact | Owner |

These sections are optional — omit a section if you have nothing real to put in it rather
than inventing rows.

Status values: planned | implemented | verified | deprecated. Mark an event
`implemented` only when a live source scan actually showed it; otherwise `planned`.
Leave a `[TODO: ...]` marker anywhere you genuinely lack information — do NOT invent
facts to remove a TODO.
```

**Destinations notes:** `- **GA4**: event name \`x\`` always parses. An account id
may be given as `(\`AW-123\`)` or `(customer \`AW-123\`)`. Platform labels are
case/underscore tolerant (`GOOGLE_ADS` → `google_ads`).
