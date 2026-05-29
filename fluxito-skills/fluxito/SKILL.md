---
name: fluxito
description: >-
  Use for any task that operates the Fluxito / Metrix Mind MCP: creating,
  auditing, refreshing, or diagnosing a Solution Design Reference (SDR / tracking
  plan) for ANY business and ANY analytics stack. Triggers: "create/build a
  tracking plan or SDR", "audit my tracking", "why aren't my conversions firing",
  "refresh the SDR after connecting X". Works for any vertical (ecommerce, SaaS,
  lead-gen, media, marketplace, nonprofit, bookings) and any analytics platform
  (GA4, Adobe, Amplitude, warehouse).
---

# Fluxito

The operating manual for the Fluxito / Metrix Mind MCP. The MCP gives you hands
and facts; this skill gives you the method. Load only the reference you need.

## Prerequisite (always check first)
The Fluxito / Metrix Mind MCP must be connected with an active project. See
`references/mcp-basics.md`. If tool calls fail or no project is active, tell the
user to connect the MCP and pick a project before continuing.

## Routing — read the matching reference, then follow it

| The user wants to… | Read |
|---|---|
| Create / build a tracking plan or SDR | `references/sdr/sdr.md` |
| Diagnose "why aren't my conversions firing?" | `references/sdr/sdr.md` (Diagnose section) |
| Refresh the SDR after connecting a new platform | `references/sdr/sdr.md` (Refresh section) |
| (future features) | `references/<feature>/…` |

Everything an SDR task needs lives under `references/sdr/`:
`sdr.md` (procedure + hard rules), `derivation-method.md` (works for any business),
`markdown-schema.md` (the exact doc contract), `quality-rubric.md` (self-check),
`verticals/*.md` (optional accelerators). Worked exemplar: `examples/sdr/`.

## Universal hard rules (every Fluxito task)
- Read server-computed `findings` before any health conclusion; never call an
  implementation healthy while a `critical` finding stands.
- Be honest about coverage: surface `connected_but_unsupported` sources and
  `readiness.unfilled_roles`; never imply an unscanned platform was analysed.
- Never invent facts; never force-fit a vertical.
- Keep the SDR markdown contract exactly — it is parsed back into a database.
