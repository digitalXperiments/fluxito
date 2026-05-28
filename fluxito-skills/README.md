# Fluxito Skills

Portable [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
that teach an AI agent how to operate the **Fluxito / Metrix Mind MCP** well. Each
skill is a self-contained folder you install into your AI tool; it pairs with the
MCP connector (the MCP gives the agent hands + facts, the skill gives it the
operating manual).

## Skills

| Skill | What it does |
|---|---|
| [`fluxito-solution-design`](./fluxito-solution-design/) | Create, audit, refresh, and diagnose a Solution Design Reference (SDR / tracking plan) for **any** business — first-pass, gold-standard. |

_More to come — each Fluxito feature gets its own skill folder of the same shape._

## Prerequisite

Connect the **Fluxito / Metrix Mind MCP** connector in your tool and select an
active project. The skills drive the MCP's `tracking_plan` tool; without the
connector they have nothing to operate.

## Install

**Claude Code** — copy the skill folder into your skills directory:
```bash
# personal (all projects)
cp -r fluxito-solution-design ~/.claude/skills/
# or project-scoped
cp -r fluxito-solution-design .claude/skills/
```

**Claude Desktop / claude.ai (Capabilities)** — upload the `fluxito-solution-design`
folder as a Skill where your plan supports Skills.

**Other Agent-Skills-compatible tools** — point the tool at the skill folder; it
reads `SKILL.md` and loads `references/` on demand.

## How a skill is structured

```
fluxito-solution-design/
├── SKILL.md            # lean entry point — the canonical procedure + hard rules
├── references/         # loaded on demand (progressive disclosure)
│   ├── derivation-method.md
│   ├── markdown-schema.md
│   └── quality-rubric.md
└── examples/           # worked exemplars (also the repo's anti-drift fixtures)
    └── bmk-eco-farms-sdr.md
```
