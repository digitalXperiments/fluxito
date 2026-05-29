---
sdr_name: BMK Eco Farms Solution Design Reference
sdr_version: 1.0-draft-1
sdr_status: draft
project_id: bmk-eco-farms
business_type: ecommerce
last_updated: 2026-05-29T00:00:00Z
last_approved_by: null
last_approved_at: null
---

# BMK Eco Farms — Solution Design Reference

> Synthesized by Fluxito SDR v2 from intake + live GA4/GTM/Google Ads scans.
> **Coverage note:** Meta is connected but not yet readable by the SDR scanner — its
> pixel mappings below are declared from intake/GTM evidence, not a Meta scan, and
> are flagged accordingly.

## Business Context

BMK Eco Farms is a direct-to-consumer ecommerce store selling certified-organic
produce, weekly farm boxes, and pantry goods. Revenue comes from one-off orders
and from recurring weekly/fortnightly produce-box subscriptions. The store runs on
a headless storefront with a server-side order pipeline; subscription renewals are
billed server-side and are **not** browser events.

*Conversion definition (from intake):* A completed checkout with successful payment
on the order-confirmation page (`purchase`), plus a started produce-box subscription
(`subscribe`). Abandoned or failed payments do not count.

**Primary KPIs:**
- Revenue and Average Order Value (AOV)
- Checkout conversion rate (sessions → `purchase`)
- Subscription activation rate and active-subscriber count
- Return on Ad Spend (ROAS) across Google Ads and Meta

**Key conversions:**
- `purchase` — one-off or first subscription order, paid, on confirmation page.
- `subscribe` — recurring produce-box plan activated.

---

## User Journeys

1. **Produce discovery → purchase** (entry: `view_item_list` on a category/season
   collection; completion: `purchase`). The core revenue path:
   `view_item_list → select_item → view_item → add_to_cart → view_cart →
   begin_checkout → add_shipping_info → add_payment_info → purchase`.
2. **Farm-box subscription** (entry: `view_item` on a subscription plan; completion:
   `subscribe`). First box is billed client-side at checkout; renewals are
   server-side and out of scope for client tagging.
3. **Newsletter nurture** (entry: `newsletter_signup` in footer/popup; completion:
   later `purchase`). Email is the dominant re-engagement channel for seasonal drops.

---

## Data Layer Schema

- **Naming convention:** GA4 recommended ecommerce event names, `snake_case`.
- **Standard push shape:** `dataLayer.push({ event, ecommerce: { currency, value, items: [...] } })`
  with `ecommerce: null` cleared before each ecommerce push to prevent item bleed.
- **Items array:** each item carries `item_id`, `item_name`, `item_category`
  (produce / box / pantry), `price`, `quantity`, and `item_variant` (e.g. `small_box`).
- **Global parameters:** `page_type`, `customer_type` (guest / member / subscriber),
  and `consent_state` are pushed on every page.

---

## Event Catalog

### `view_item_list`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Measures how seasonal collections and category pages drive
browsing. Top of the discovery journey; feeds list-to-detail click-through rate.

**Triggers:**
- Type: `pageview`
- Configuration: Category, search-results, and seasonal-collection pages.
- Conditions: Fires once per list view; not on filter re-sorts of the same list.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `item_list_id` | string | no | dataLayer | `winter_boxes` | |
| `item_list_name` | string | no | dataLayer | `Winter Boxes` | |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, item_name, index}]` | non-empty |

**Destinations:**

- **GA4**: event name `view_item_list`

**Consent Requirements:** `analytics_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate

**Edge Cases & Notes:** Recommendation carousels reuse this event with `item_list_id=recs`.

### `select_item`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Connects list performance to product interest by capturing the
specific tile a shopper clicked into.

**Triggers:**
- Type: `click`
- Configuration: Product tile/link click within any listing.
- Conditions: Excludes quick-add buttons (those fire `add_to_cart`).

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `item_list_id` | string | no | dataLayer | `winter_boxes` | |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, item_name, index}]` | exactly one item |

**Destinations:**

- **GA4**: event name `select_item`

**Consent Requirements:** `analytics_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate

**Edge Cases & Notes:** `index` preserves list position for merchandising analysis.

### `view_item`

*Status:* `implemented` | *Last verified:* `2026-05-29`

**Business Purpose:** Product/box detail interest — the key upper-funnel signal for
view-to-cart rate and for Meta/Google remarketing audiences.

**Triggers:**
- Type: `pageview`
- Configuration: Product and subscription-plan detail pages.
- Conditions: One push per detail view.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `24.00` | >= 0 |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, item_name, price, item_category}]` | non-empty |

**Destinations:**

- **GA4**: event name `view_item`
- **META**: event name `ViewContent`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate, ROAS

**Edge Cases & Notes:** Live in GA4. Meta `ViewContent` is declared from GTM tag
evidence — confirm once Meta scanning is supported. `ad_storage` gates the Meta tag.

### `add_to_cart`

*Status:* `implemented` | *Last verified:* `2026-05-29`

**Business Purpose:** Primary mid-funnel purchase-intent signal and the basis for
cart-abandonment remarketing.

**Triggers:**
- Type: `click`
- Configuration: Add-to-cart / quick-add buttons and the cart `add` dataLayer event.
- Conditions: Fires per quantity-add; quantity steppers in the cart do not refire.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `24.00` | > 0 |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, item_name, price, quantity}]` | non-empty |

**Destinations:**

- **GA4**: event name `add_to_cart`
- **META**: event name `AddToCart`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate, ROAS

**Edge Cases & Notes:** Live in GA4. Meta `AddToCart` declared from GTM evidence.

### `view_cart`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Bridges add-to-cart and checkout; isolates cart-page friction.

**Triggers:**
- Type: `pageview`
- Configuration: Cart page load and cart-drawer open.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `48.00` | >= 0 |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, quantity}]` | non-empty |

**Destinations:**

- **GA4**: event name `view_cart`

**Consent Requirements:** `analytics_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate

**Edge Cases & Notes:** Drawer open and full cart page share one event.

### `remove_from_cart`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Flags price/quantity friction in the mid-funnel.

**Triggers:**
- Type: `click`
- Configuration: Remove/delete control in cart or drawer.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `24.00` | >= 0 |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, quantity}]` | non-empty |

**Destinations:**

- **GA4**: event name `remove_from_cart`

**Consent Requirements:** `analytics_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate

**Edge Cases & Notes:** Setting quantity to zero counts as a remove.

### `begin_checkout`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Checkout-funnel entry; denominator for checkout completion rate.

**Triggers:**
- Type: `datalayer_event`
- Configuration: `begin_checkout` push on checkout step 1.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `48.00` | > 0 |
| `coupon` | string | no | dataLayer | `SPRING10` | |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, price, quantity}]` | non-empty |

**Destinations:**

- **GA4**: event name `begin_checkout`
- **META**: event name `InitiateCheckout`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate, ROAS

**Edge Cases & Notes:** Express wallets (Apple/Google Pay) skip straight to payment;
ensure they still emit `begin_checkout`.

### `add_shipping_info`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Checkout-step progression; surfaces delivery-zone drop-off (a
real factor for perishable produce with limited delivery areas).

**Triggers:**
- Type: `datalayer_event`
- Configuration: `add_shipping_info` push after delivery details accepted.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `48.00` | > 0 |
| `shipping_tier` | string | no | dataLayer | `Local Refrigerated` | |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, quantity}]` | non-empty |

**Destinations:**

- **GA4**: event name `add_shipping_info`

**Consent Requirements:** `analytics_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate

**Edge Cases & Notes:** Out-of-zone postcodes block checkout — track as a failed
shipping attempt for delivery-expansion analysis.

### `add_payment_info`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Final pre-purchase checkout step; isolates payment friction.

**Triggers:**
- Type: `datalayer_event`
- Configuration: `add_payment_info` push when a payment method is confirmed.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `48.00` | > 0 |
| `payment_type` | string | no | dataLayer | `card` | |

**Destinations:**

- **GA4**: event name `add_payment_info`
- **META**: event name `AddPaymentInfo`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate, ROAS

**Edge Cases & Notes:** Express-wallet payments emit `payment_type=wallet`.

### `purchase`

*Status:* `verified` | *Last verified:* `2026-05-29`

**Business Purpose:** The primary revenue conversion. Drives revenue, AOV, ROAS, and
all paid-channel optimization. Live and verified in GA4 and Google Ads.

**Triggers:**
- Type: `datalayer_event`
- Configuration: `purchase` push on the order-confirmation page, after server payment
  confirmation.
- Conditions: One push per order. **Refunds do not fire purchase** (see `refund`).
  **Subscription renewals are server-side and do not fire purchase.**

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `transaction_id` | string | yes | dataLayer.ecommerce.transaction_id | `BMK-100245` | unique per order; dedupe key |
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `value` | number | yes | dataLayer.ecommerce.value | `48.00` | > 0 |
| `tax` | number | no | dataLayer | `0.00` | |
| `shipping` | number | no | dataLayer | `4.50` | |
| `coupon` | string | no | dataLayer | `SPRING10` | |
| `items` | array | yes | dataLayer.ecommerce.items | `[{item_id, item_name, price, quantity, item_category}]` | non-empty |

**Destinations:**

- **GA4**: event name `purchase`
- **GOOGLE_ADS** (`AW-XXXXXXXXX`): event name `purchase`
- **META**: event name `Purchase`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: Ecommerce Marketing · Technical: Storefront Eng

**Related KPIs:** Revenue and AOV, ROAS, Checkout conversion rate

**Edge Cases & Notes:** `transaction_id` is the GA4/Ads/Meta deduplication key — must
match the server order ID. First subscription order fires `purchase` **and**
`subscribe`. Verified against the Google Ads conversion action in the live scan.

### `refund`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Corrects revenue for cancellations and spoilage credits; tracks
return/credit rate on perishable goods.

**Triggers:**
- Type: `custom`
- Configuration: Server-side refund event posted via Measurement Protocol.
- Conditions: Full or partial; partials send the affected `items` only.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `transaction_id` | string | yes | order system | `BMK-100245` | must match original purchase |
| `currency` | string | yes | order system | `GBP` | ISO 4217 |
| `value` | number | yes | order system | `12.00` | > 0 |
| `items` | array | no | order system | `[{item_id, quantity}]` | required for partial refunds |

**Destinations:**

- **GA4**: event name `refund`

**Consent Requirements:** `analytics_storage`

**Owners:** Business: Finance Ops · Technical: Backend Eng

**Related KPIs:** Revenue and AOV

**Edge Cases & Notes:** Server-side via Measurement Protocol; no consent signal in the
browser, so apply the order's stored consent state.

### `subscribe`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Produce-box subscription activation — the second primary
conversion and the core of recurring revenue.

**Triggers:**
- Type: `datalayer_event`
- Configuration: `subscribe` push on confirmation of a recurring plan.
- Conditions: Fires on activation only, not on each renewal. Renewals are tracked
  server-side in the warehouse, not on the client.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `plan_name` | string | yes | dataLayer | `weekly_medium_box` | |
| `value` | number | yes | dataLayer | `24.00` | > 0; first-cycle value |
| `currency` | string | yes | dataLayer | `GBP` | ISO 4217 |
| `billing_cycle` | string | no | dataLayer | `weekly` | |

**Destinations:**

- **GA4**: event name `subscribe`
- **GOOGLE_ADS** (`AW-XXXXXXXXX`): event name `subscribe`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: Subscriptions · Technical: Storefront Eng

**Related KPIs:** Subscription activation rate, Revenue and AOV

**Edge Cases & Notes:** Lifetime value lives in the warehouse (renewals); client
`subscribe` captures activation only — reconcile in BigQuery for true LTV/ROAS.

### `newsletter_signup`

*Status:* `planned` | *Last verified:* `never`

**Business Purpose:** Email opt-in — the dominant re-engagement channel for seasonal
produce drops.

**Triggers:**
- Type: `form_submit`
- Configuration: Footer and exit-intent newsletter forms, on validated submit.
- Conditions: Fires after server confirms a new subscriber; resubmits are suppressed.

**Parameters:**

| Name | Type | Required | Source | Example | Validation |
|---|---|---|---|---|---|
| `method` | string | no | dataLayer | `footer_form` | |

**Destinations:**

- **GA4**: event name `newsletter_signup`
- **META**: event name `Subscribe`

**Consent Requirements:** `analytics_storage` | `ad_storage`

**Owners:** Business: CRM / Lifecycle · Technical: Storefront Eng

**Related KPIs:** Checkout conversion rate

**Edge Cases & Notes:** Double opt-in — only fire after email confirmation if local law
requires it for the user's region.

---

## User Properties / Custom Dimensions

| Name | Scope | Source | Example | Platforms |
|---|---|---|---|---|
| `customer_type` | user | dataLayer (auth state) | `subscriber` | GA4 |
| `acquisition_channel` | user | first-touch attribution | `paid_search` | GA4 |
| `delivery_zone` | user | checkout postcode → zone map | `local_refrigerated` | GA4 |
| `subscription_status` | user | order system | `active` | GA4 |

---

## Destinations Matrix

| Event | GA4 | Google Ads | Meta | TikTok | LinkedIn | Custom |
|---|---|---|---|---|---|---|
| `view_item_list` | ✓ | — | — | — | — | — |
| `select_item` | ✓ | — | — | — | — | — |
| `view_item` | ✓ | — | ✓ (ViewContent) | — | — | — |
| `add_to_cart` | ✓ | — | ✓ (AddToCart) | — | — | — |
| `view_cart` | ✓ | — | — | — | — | — |
| `remove_from_cart` | ✓ | — | — | — | — | — |
| `begin_checkout` | ✓ | — | ✓ (InitiateCheckout) | — | — | — |
| `add_shipping_info` | ✓ | — | — | — | — | — |
| `add_payment_info` | ✓ | — | ✓ (AddPaymentInfo) | — | — | — |
| `purchase` | ✓ | ✓ | ✓ (Purchase) | — | — | — |
| `refund` | ✓ | — | — | — | — | — |
| `subscribe` | ✓ | ✓ | — | — | — | — |
| `newsletter_signup` | ✓ | — | ✓ (Subscribe) | — | — | — |

Meta mappings are declared from intake + GTM tag evidence; verify once Meta source
scanning is supported in Fluxito.

---

## Consent & Privacy

- **CMP:** OneTrust with Google Consent Mode v2 (advanced).
- **Categories:** `analytics_storage` and `ad_storage`; default **denied** for EEA/UK
  visitors until opt-in, granted-by-default with opt-out elsewhere.
- **Gating:** All Google Ads and Meta tags are gated on `ad_storage`; GA4 runs in
  consent-mode modeling when `analytics_storage` is denied.
- **Sensitive data:** None (no health/finance/children's data). Postcodes are stored
  as delivery zones, not raw addresses, in analytics.
- **Server-side events** (`refund`, subscription renewals) apply the consent state
  captured with the original order.

---

## Ownership & Governance

- **SDR owner:** Head of Ecommerce (accountable for this document).
- **Business owners:** Ecommerce Marketing (funnel events), Subscriptions (`subscribe`),
  Finance Ops (`refund`), CRM/Lifecycle (`newsletter_signup`).
- **Technical owners:** Storefront Eng (dataLayer + GTM), Backend Eng (server-side
  events via Measurement Protocol).
- **Change process:** Changes proposed via Fluxito `refine_sdr`; admin approval snapshots
  a new approved version that audits validate against.
- **Review cadence:** Quarterly, and on any major storefront or checkout release.

---

## Changelog

- **v1.0-draft-1** (2026-05-29) — Initial gold-standard synthesis from intake + live
  GA4/GTM/Google Ads scans. `purchase` verified against the live Google Ads conversion
  action; `view_item`/`add_to_cart` confirmed live in GA4; remaining funnel events
  modelled as `planned` pending verification. Meta flagged connected-but-not-yet-scanned.
