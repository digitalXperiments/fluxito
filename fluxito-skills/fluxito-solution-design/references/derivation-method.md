# The 5-Step Derivation Method (works for ANY business)

Do **not** guess a vertical and copy a template. Derive the event taxonomy from
the intake the user gave you. Templates/cheatsheets are accelerators, not truth.

### 1. Classify the value-exchange model
From `business_model`, name *how this business creates value/money*. It's a lens,
not a fixed list: transactional · subscription/recurring · lead→offline-sale ·
marketplace (two-sided) · media/engagement · freemium/PLG · booking/reservation ·
donation · account-based B2B. This sets the macro-conversion and the funnel shape.

### 2. Map each key journey to an event sequence
For every journey in `key_journeys`, lay out: **entry → milestones → completion**.
The `conversion_definition` anchors the completion event. A journey with no
completion event is a gap to flag.

### 3. Derive the events
Build the events from those sequences. If a `references/verticals/*.md` cheatsheet
genuinely fits the model, use it to accelerate. **If none fits, derive from the
journeys — never force-fit a vertical.** Layer on the universal backbone that
applies to almost any web/app business: page/screen view, session start, auth (if
accounts exist), search (if applicable), the primary conversion, and consent events.

### 4. Attach proof to every event
Give each event the parameters that *prove* the conversion/KPI — this is what the
server's `primary_conversion_unproven` finding checks:
- purchase → `transaction_id` (unique), `value` (>0), `currency` (ISO 4217), `items[]`
- qualified lead → `lead_type`, `company_size` / qualifier
- booking → `booking_id`, `date`, `value`
- subscription → `plan_name`, `value`, `billing_cycle`
- donation → `amount`, `recurring`
Plus destinations, consent categories, owners, and the KPI it serves.

### 5. Reconcile with live data + findings
Overlay the diagnostic `findings` and scan results: mark `implemented`/`verified`
only where volume was proven; set `planned` otherwise; write each finding into the
data-quality notes. Prioritise: primary conversion + revenue first, then events on
the stated key journeys, then everything else.

> Output goes into the contract in `markdown-schema.md`. Self-check against
> `quality-rubric.md` before saving.
