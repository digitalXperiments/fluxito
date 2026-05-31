# Add the Fluxito Skill

The Fluxito MCP gives your AI **hands and facts** — it can read your analytics, generate a Solution Design Reference (SDR), build dashboards, and run audits. The **Fluxito Skill** gives your AI the **operating manual** for those tools: the method, the document contract, and the guardrails that keep results gold-standard instead of generic.

Connecting the MCP is enough to *call* the tools. Adding the Skill is what makes the AI *use them well*.

## Why add the Skill to your AI client

Without the Skill, an AI connected to Fluxito will improvise — it may invent metrics, call a broken tracking setup "healthy," or produce an SDR that doesn't match the schema your team relies on. The Skill fixes that:

- **Right method, every time.** A repeatable 5-step derivation process for tracking plans / SDRs that works for any vertical (ecommerce, SaaS, lead-gen, media, marketplace) and any stack (GA4, Adobe, Amplitude, warehouse).
- **A strict document contract.** SDRs come out in the exact markdown schema Fluxito expects, so versioning, diffs, and deployment all work.
- **Honest health checks.** The Skill forces the AI to read server-computed `findings` before drawing conclusions — no more "looks good" while a critical issue stands.
- **Anti-drift.** A worked exemplar keeps quality consistent across sessions and models.
- **Lean context.** One hub skill with per-feature references loaded on demand, so it never bloats your context window.

## Prerequisite

1. The **Fluxito MCP** connector is added to your AI client. See [Connect an AI with MCP](/tutorials/connect-ai-mcp).
2. You have selected an **active project**. The Skill drives the MCP's `tracking_plan` tool; without a connected MCP and a project it has nothing to operate.

## Where to get it

The Skill ships in the Fluxito repository under the **`fluxito-skills/`** folder. The skill itself is the `fluxito/` directory inside it:

```text
fluxito-skills/
└── fluxito/
    ├── SKILL.md          # thin router + universal hard rules (always loaded)
    ├── references/       # mcp-basics + per-feature guides (loaded on demand)
    └── examples/         # worked exemplar / anti-drift fixture
```

## Install

**Claude Code** — copy the `fluxito` folder into your skills directory:

```bash
cp -r fluxito-skills/fluxito ~/.claude/skills/      # personal (all projects)
cp -r fluxito-skills/fluxito .claude/skills/        # or project-scoped
```

**Claude Desktop / claude.ai (Capabilities)** — upload the `fluxito` folder as a Skill wherever your plan supports Skills.

**Other Agent-Skills-compatible tools** — point the tool at the `fluxito` folder. It reads `SKILL.md` and pulls in `references/` only when a task needs them.

## Verify it works

Start a fresh chat in your AI client (with the Fluxito MCP connected and a project selected) and ask:

> "Build a Solution Design Reference for my active project."

If the Skill is installed, the AI will follow the Fluxito method: confirm the active project, read the current `tracking_plan` findings, and produce an SDR in the standard schema — rather than free-styling. If it skips those steps, re-check that the `fluxito` folder is in the right skills location and restart the client.

## What's next

- [SDR generation](/tutorials/sdr-generation) — the end-to-end SDR workflow the Skill powers.
- [Audits and Activity Log](/tutorials/audits-and-activity) — review exactly which tools the AI called.
