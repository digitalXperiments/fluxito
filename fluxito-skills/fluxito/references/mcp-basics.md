# Fluxito MCP — basics (shared)

Shared knowledge for every Fluxito feature. Keep this lean; feature methodology
lives in the feature's own reference.

## Connection + project
- The user connects the **Fluxito MCP** connector in their AI tool
  and must have an **active project** selected.
- `tracking_plan(action="list_sources")` confirms what's connected and surfaces
  `connected_but_unsupported` sources (connected platforms the scanner can't read
  yet) — never imply those were analysed.
- If tool calls fail with no active project / auth errors, stop and tell the user
  to connect the MCP and select a project.

## The `tracking_plan` dispatcher
All SDR/tracking-plan work goes through one MCP tool, `tracking_plan`, with an
`action` and `params`:

| action | purpose |
|---|---|
| `generate` | intake (no answers → 6 questions) then gather scans + `findings` + `readiness` + `markdown_skeleton` |
| `save` | persist your authored SDR markdown (validates the contract; returns `errors`/`warnings`) |
| `diagnose` | cross-referenced health diagnosis (findings + readiness) without writing |
| `refresh_sources` | re-scan, return structured deltas |
| `refine` | section-by-section refinement state machine (incl. `apply_source_delta`) |
| `get_intake` / `list_sources` | re-surface intake / list source coverage |

## Capability roles (why it's platform-agnostic)
Findings reason about roles, not products: `tag_inventory` (is it configured?),
`event_volume` (is it flowing?), `conversion_config` (is it set for activation?).
GA4/GTM/Google Ads fill these today; Adobe / Amplitude / warehouse fill the same
roles later with no change to the method. `readiness.unfilled_roles` tells you
which capabilities no connected platform provides.
