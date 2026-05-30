# Vertical accelerator: Lead generation

Use when the conversion is a lead handed to sales (value realized offline/later).

**Primary conversion:** `form_submit` (qualified), often with downstream `lead_qualified` (MQL/SQL).
**Core events:** `form_view → form_start → form_submit → lead_qualified`.

**Proof params (specificity is everything here):**
- `form_submit`: `form_id`, `form_name`, `lead_type`; capture qualifiers like
  `company_size`, corporate-email flag — "a form submit" is not a lead.
- `lead_qualified`: `lead_id`, `qualification_type`, `score` (usually a CRM/server event).

**KPIs:** cost-per-lead, lead→MQL→SQL rates, lead quality, pipeline value.

**The big watch-out:** the value is offline. The most important signal — did the
lead become a customer — comes back from the CRM, not the website. Model
`lead_qualified` / closed-won as **server-side** events and reconcile in the
warehouse. Flag if only raw `form_submit` is tracked (optimizes for volume, not quality).

**Cross-platform:** `form_submit→Lead` (Meta), Google Ads lead conversion + enhanced
conversions for offline import; offline conversion import for closed deals.
