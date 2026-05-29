# Vertical accelerator: Ecommerce

Use if the business sells products online (transactional value-exchange). Adapt
freely; derive from the actual journeys when reality differs.

**Primary conversion:** `purchase` (paid, on order confirmation).
**Standard GA4 funnel:** `view_item_list → select_item → view_item → add_to_cart →
view_cart → begin_checkout → add_shipping_info → add_payment_info → purchase`,
plus `refund` and (if subscriptions) `subscribe`.

**Proof params (don't ship without these):**
- `purchase`: `transaction_id` (unique — dedup key), `value` (>0), `currency` (ISO 4217), `items[]`.
- cart/checkout events: `currency`, `value`, `items[]`.

**KPIs to tie events to:** revenue, AOV, conversion rate, ROAS, cart-abandonment.

**Cross-platform mapping (Meta names differ):** `view_item→ViewContent`,
`add_to_cart→AddToCart`, `begin_checkout→InitiateCheckout`,
`add_payment_info→AddPaymentInfo`, `purchase→Purchase`. Google Ads = conversion actions.

**Common pitfalls to flag:** `purchase` firing on refunds/renewals; missing
`transaction_id` (breaks dedup); `ecommerce` object not cleared between pushes;
perishable/limited delivery zones blocking checkout (track shipping drop-off).
