# Gold-Standard SDR Self-Check

Run this before calling `save`. The document is not done until it passes.

## Per-event (every event in the catalog)
- [ ] Business Purpose ties the event to a specific KPI **and/or** journey — no generic filler.
- [ ] Trigger type + configuration present; edge cases noted where they matter (refunds, renewals, internal traffic, express wallets).
- [ ] Parameters include the ones that **prove** the conversion/KPI (see `derivation-method.md` step 4).
- [ ] Destinations are mapped with platform-correct names (e.g. Meta `Purchase`/`ViewContent`, not `purchase`/`view_item`).
- [ ] Consent categories set; owners (business + technical) set; related KPIs set.
- [ ] Status is honest: `implemented`/`verified` only if a scan proved volume; else `planned`.

## Document-level
- [ ] All 9 sections present and in order (see `markdown-schema.md`).
- [ ] Business Context, KPIs, conversion definition, journeys, consent, ownership reflect the **intake answers** (the user's real words), not template defaults.
- [ ] Every diagnostic **finding** is addressed — written into the data-quality notes with its fix location. No `critical` finding is glossed as "healthy".
- [ ] `connected_but_unsupported` sources and `unfilled_roles` are stated honestly ("not yet covered"), never implied as analysed.
- [ ] No invented facts used to clear a `[TODO]`. Remaining unknowns stay as explicit `[TODO: ...]`.

## Readiness gate (from the `save`/`generate` response)
Do **not** declare the SDR complete while:
- `readiness.critical_findings_unresolved > 0`, or
- `readiness.primary_conversion_proven` is `false` (when an analytics/`event_volume` provider is connected).

If the gate isn't met, say so plainly and tell the user exactly what must change
(usually a website/app fix), then offer `tracking_plan(action='refine')`.

> The reference bar is `../examples/bmk-eco-farms-sdr.md`: full funnel, verified
> primary conversion with proof params + cross-platform destinations, zero
> unresolved TODOs, honest coverage notes.
