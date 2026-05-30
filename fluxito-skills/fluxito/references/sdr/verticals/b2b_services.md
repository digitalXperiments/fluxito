# Vertical accelerator: B2B services / consulting / bookings

Use for high-consideration B2B services or appointment/booking businesses where
the "conversion" starts a sales conversation or reserves time (not an instant sale).

**Primary conversions:** `book_meeting` / `request_quote` / `contact_sales`, or
`booking` for appointment businesses; revenue closes offline.
**Core events:** `view_service → request_quote / book_meeting → meeting_held →
proposal_sent → closed_won` (later stages are CRM/server events).

**Proof params:**
- `book_meeting` / `request_quote`: `service`, `company_size`, corporate-email flag, `deal_band` if known.
- `booking`: `booking_id`, `service`, `date`, `value`.

**KPIs:** qualified-meeting rate, quote→win rate, sales-cycle length, pipeline &
closed-won value, CAC by channel.

**The big watch-out:** the money is offline and weeks later. Model the post-meeting
funnel as **server-side / CRM** events and use offline conversion import so ad
platforms optimize for *won deals*, not raw form fills. Low absolute volumes are
normal — don't mistake low `book_meeting` counts for a tracking break (but a
*zero* on a live page still is).

**Cross-platform:** Google Ads enhanced + offline conversions; Meta `Lead` /
`Schedule`; LinkedIn conversions are common for B2B.
