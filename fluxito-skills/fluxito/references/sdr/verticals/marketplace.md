# Vertical accelerator: Marketplace (two-sided)

Use for two-sided platforms (buyers + sellers/providers). The defining trait:
**two audiences with separate funnels that must be modeled distinctly.**

**Primary conversions:** the transaction (`purchase`/`booking`) on the demand side;
plus supply-side activation (`listing_created`, `provider_onboarded`).

**Core events:**
- Demand: `search → view_listing → contact_seller / add_to_cart → purchase`.
- Supply: `seller_sign_up → listing_created → first_sale`.

**Proof params:**
- transaction: `transaction_id`, `value`, `currency`, `take_rate`/`commission` if available, `seller_id`, `buyer_id`.
- `listing_created`: `listing_id`, `category`, `seller_id`.

**KPIs:** GMV, take rate, liquidity (match rate), buyer & seller retention,
time-to-first-transaction on each side.

**The big watch-out:** don't collapse both sides into one funnel. Tag a
`user_type` (buyer/seller) dimension and keep separate journeys. Attribution is
hard — the value event involves two parties; capture both IDs.

**Cross-platform:** transaction → Meta `Purchase` / Google Ads conversion; supply
acquisition often a separate campaign with `seller_sign_up` as the conversion.
