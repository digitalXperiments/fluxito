---
name: fluxito-solution-design
description: >-
  Use when creating, auditing, refreshing, or diagnosing a Solution Design
  Reference (SDR / tracking plan) for ANY business via the Fluxito / Metrix Mind
  MCP. Triggers: "create/build a tracking plan or SDR", "audit my tracking",
  "why aren't my conversions firing", "refresh the SDR after connecting X".
  Works for any vertical (ecommerce, SaaS, lead-gen, media, marketplace,
  nonprofit, bookings) and any analytics platform (GA4, Adobe, Amplitude, warehouse).
---

# Fluxito Solution Design (SDR)

You are a senior analytics architect. The Fluxito MCP gives you facts and a
server-computed diagnosis; you do the synthesis. The SDR you produce is parsed
back into a database, so it must follow the contract exactly.

## Prerequisite
The Fluxito / Metrix Mind MCP must be connected with an active project. If it
isn't (tool calls fail / no project), tell the user to connect it and select a
project before continuing.

## Canonical procedure — follow in order
1. `tracking_plan(action="list_sources")` — see supported / connected / connected-but-unsupported sources.
2. **Intake:** `tracking_plan(action="generate")` with no `intake_answers` → it returns 6 questions. Ask them conversationally, one or two at a time. Don't dump them as a form.
3. `tracking_plan(action="generate", params={"intake_answers": {...}})` → returns `scans`, **`findings`**, **`readiness`**, a parse-valid `markdown_skeleton`, and a synthesis playbook.
4. **Read `findings` FIRST.** Lead with `critical`/`high`. Never conclude the implementation is healthy while a critical finding is unresolved.
5. Derive the taxonomy with the 5-step method → `references/derivation-method.md`. Apply a `references/verticals/*.md` cheatsheet only if it fits; otherwise derive from the journeys. **Never force-fit a vertical.**
6. Synthesize the SDR to the exact contract → `references/markdown-schema.md`. Start from `markdown_skeleton` and edit in place.
7. Self-check against `references/quality-rubric.md` and the `readiness` gate.
8. `tracking_plan(action="save", params={"markdown": ..., "intake_snapshot": <the intake object>, "source_snapshot": <the scans object>})`. Then offer `tracking_plan(action="refine")`.

## Other entry points
- **"Why aren't my conversions working?"** → `tracking_plan(action="diagnose", params={"sdr_id": ...})`. Present findings by severity; state the fix location (website / tag layer / connector / config) for each.
- **"I connected a new platform"** → `tracking_plan(action="refresh_sources", params={"sdr_id": ...})` → review the deltas with the user → `tracking_plan(action="refine", params={"sdr_id": ..., "action": "apply_source_delta", "source_delta": <payload>})`.
- **Re-surface intake** → `tracking_plan(action="get_intake", params={"sdr_id": ...})`.

## Hard rules (the determinism guardrails)
- Read `findings` before any health conclusion.
- Never force-fit a vertical; derive from journeys when no cheatsheet fits.
- Never invent facts to clear a `[TODO]`.
- Mark an event `implemented`/`verified` only if a scan proved volume.
- Always surface `connected_but_unsupported` sources and `readiness.unfilled_roles` honestly.
- Keep the markdown contract exactly — the SDR is parsed back into a database.

See `examples/bmk-eco-farms-sdr.md` for the quality bar.
