# Fluxito Skills

A portable [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
that teaches an AI agent how to operate the **Fluxito / Metrix Mind MCP** well.
The MCP gives the agent hands + facts; the skill gives it the operating manual.

It is **one hub skill** (`fluxito`) with per-feature references loaded on demand —
single install, shared MCP basics, lean context. SDR is the first feature; future
features (audits, dashboards) get their own `references/<feature>/` folder.

## Prerequisite

Connect the **Fluxito / Metrix Mind MCP** connector in your tool and select an
active project. The skill drives the MCP's `tracking_plan` tool; without the
connector it has nothing to operate.

## Install

**Claude Code** — copy the skill folder into your skills directory:
```bash
cp -r fluxito ~/.claude/skills/        # personal (all projects)
cp -r fluxito .claude/skills/          # or project-scoped
```

**Claude Desktop / claude.ai (Capabilities)** — upload the `fluxito` folder as a
Skill where your plan supports Skills.

**Other Agent-Skills-compatible tools** — point the tool at the `fluxito` folder;
it reads `SKILL.md` and loads `references/` on demand.

## Structure

```
fluxito/
├── SKILL.md                        # thin router + universal hard rules (always loaded)
├── references/
│   ├── mcp-basics.md               # connect, project, tracking_plan map, roles (shared)
│   └── sdr/                        # SDR feature
│       ├── sdr.md                  # procedure + hard rules
│       ├── derivation-method.md    # 5-step method (works for any business)
│       ├── markdown-schema.md      # the exact SDR doc contract
│       ├── quality-rubric.md       # gold-standard self-check
│       └── verticals/*.md          # optional accelerators
└── examples/
    └── sdr/bmk-eco-farms-sdr.md    # worked exemplar + anti-drift fixture
```

Why one skill instead of one-per-feature: the features share the MCP basics and
the markdown contract, install is one step, and progressive disclosure means only
the reference for the current task loads — so context stays just as lean.
