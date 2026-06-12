---
name: fluxito
description: >-
  Operate the Fluxito MCP — the analytics-engineering control surface for GA4,
  Google Tag Manager, Google/Meta/TikTok/Snap ads, BigQuery/Redshift/Snowflake,
  Search Console, Adobe (Analytics/Launch/Marketo), and Amplitude. Use for:
  auditing tracking / tags / data quality and diagnosing "why aren't my
  conversions firing"; building, refreshing, or diagnosing a tracking plan
  (Solution Design Reference / SDR); querying analytics, warehouses, and ad
  performance; and deploying dashboards. Works for any vertical (ecommerce, SaaS,
  lead-gen, media, marketplace, bookings) and any connected stack.
---

# Fluxito

The operating manual for the Fluxito MCP. The **server** gives you hands, facts,
and self-describing tool schemas; this **skill** gives you the method and judgment
that a tool description can't. Load only the reference you need.

## The server describes itself — don't guess params

Fluxito's tools are designed so you (and any client) never have to guess:

- **Every tool description lists its actions** and, in `[brackets]`, which platform
  each action is valid for. Read the description first.
- **`action="describe"`** on any dispatcher returns the machine-readable spec for one
  action (`params={"action":"run_report"}`) or all of them — required + optional params,
  types, and a runnable example.
- **A missing/invalid required param returns the full spec back**:
  `{error_type, missing, required, optional, example}`. Read the error and retry — you do
  not need to remember every parameter.

So: **trust the live schema and `describe` over any params you think you remember.** Tool
descriptions and schemas are generated from a single source of truth and are accurate.

## Prerequisite (check first)

The Fluxito MCP must be connected with an **active project**. See
`references/mcp-operating-guide.md`. If tool calls fail with an auth / no-active-project
error, stop and tell the user to connect the MCP and select a project.

## Routing — read the matching reference, then follow it

| The user wants to… | Read |
|---|---|
| Operate any tool, set the project, understand the surface, batch calls | `references/mcp-operating-guide.md` |
| Audit / diagnose tracking, tags, conversions, data quality | `references/workflows/audit.md` |
| Build / create a tracking plan or SDR | `references/sdr/sdr.md` |
| Diagnose "why aren't my conversions firing?" | `references/workflows/audit.md` + `references/sdr/sdr.md` (Diagnose) |
| Refresh the SDR after connecting a new platform | `references/sdr/sdr.md` (Refresh) |
| Build / deploy a dashboard | `references/workflows/dashboards.md` |

SDR depth lives under `references/sdr/`: `sdr.md` (procedure), `derivation-method.md`
(works for any business), `markdown-schema.md` (the exact doc contract),
`quality-rubric.md` (self-check), `verticals/*.md` (optional accelerators). Worked
exemplar: `examples/sdr/`.

## Universal hard rules (every Fluxito task)

- **Read `findings` before any health conclusion.** Never call an implementation healthy
  while a `critical` finding stands.
- **Be honest about coverage.** Surface `connected_but_unsupported` sources and
  `readiness.unfilled_roles`; never imply an unscanned platform was analysed.
- **Writes touch real systems.** `*_write`, `publish_container`, dashboard deploys, and
  ad-budget changes mutate live platforms (and ad spend) immediately. Confirm before
  publishing or deleting; `tagmanager_write(action="propose_change")` is a safe dry-run.
- **Never invent facts; never force-fit a vertical.** Leave `[TODO]` markers rather than
  guessing.
- **Keep the SDR markdown contract exactly** — it is parsed back into a database.
