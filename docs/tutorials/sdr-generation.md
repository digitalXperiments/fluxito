# Generate and Refine an SDR

An SDR, or Solution Design Reference, is Fluxito's tracking plan. It documents events, parameters, user properties, destinations, consent behavior, ownership, and review status.

Use the SDR when you want the AI to design, audit, or improve analytics implementation instead of only reading reports.

## What the SDR is for

The SDR answers:

- What should be tracked?
- Where should each event fire?
- Which parameters are required?
- Which destinations receive each event?
- Which events map to conversions or KPIs?
- Who owns the tracking plan?
- What should audits compare live implementation against?

Once approved, audits can validate live GA4/GTM implementation against the SDR.

## 1. Connect an AI client

SDR generation is AI-driven through MCP.

Complete [Connect an AI Client with MCP](connect-ai-mcp.md), then confirm the active project.

## 2. Connect useful data sources

For the strongest first draft, connect:

- Google Analytics 4 for existing events and custom dimensions.
- Google Tag Manager for tags, triggers, and data layer behavior.
- Google Ads for conversion mappings.

You can still generate a blank or template-led SDR without all sources.

## 3. Open Solution Design

Go to:

```text
Knowledge -> Solution Design
```

Or open:

```text
/solution-design
```

If no SDR exists, the page shows guided prompts you can copy into your AI client.

## 4. Generate the first draft

Use one of these prompts:

```text
Generate an SDR for my project.
```

```text
Generate an SDR based on my GA4 events and GTM container from the last 90 days.
```

```text
Create a blank SDR for an ecommerce checkout flow that I can refine.
```

For a specific business type:

```text
Generate an SDR for a SaaS lead-generation site. Use live GA4 and GTM data where available, then fill gaps from a sensible SaaS template.
```

## 5. Review the generated sections

Open the SDR in Fluxito and review:

| Section | What to check |
|---|---|
| Business context | Is the model, market, and conversion flow correct? |
| User journeys | Are the main customer paths represented? |
| Event catalog | Are event names, triggers, and purposes accurate? |
| Parameters | Are required parameters present and typed correctly? |
| User properties | Are persistent traits documented? |
| Destination matrix | Are GA4, GTM, Ads, warehouse, and other destinations mapped? |
| Consent and privacy | Are consent rules and regional rules documented? |
| Ownership | Is there an owner and review cadence? |

## 6. Refine with the AI

Ask the AI to walk section by section:

```text
Refine my SDR section by section. Ask me the missing questions, propose changes, and wait for approval before applying them.
```

Useful follow-up prompts:

```text
Find TODOs and gaps in the SDR and prioritize the ones that affect reporting accuracy.
```

```text
Add required parameters for checkout events and explain why each one matters.
```

```text
Compare the SDR against what is live in GTM and GA4. Show drift and suggested fixes.
```

## 7. Approve the SDR

When required sections are complete, ask:

```text
Finalize this SDR and add a changelog note summarizing the approved tracking plan.
```

After approval, Fluxito creates a version. Audits can use that version as the expected tracking contract.

## 8. Export when needed

Use the SDR page to inspect the structured views and export the plan when stakeholders need a spreadsheet.

## Common issues

| Issue | Fix |
|---|---|
| Generated SDR is too generic | Add Business Context and give a business type hint. |
| Live sources are missing | Connect GA4, GTM, or Ads before regenerating. |
| Too many TODOs | Use the refinement flow and answer one section at a time. |
| AI wants to overwrite an existing SDR | Ask it to refine instead of regenerate, unless you want a clean draft. |
| Audits do not reference the SDR | Finalize or approve the SDR version first. |

## Recommended workflow

1. Add Business Context.
2. Add core KPIs.
3. Connect GA4 and GTM.
4. Generate SDR.
5. Refine missing sections.
6. Approve SDR.
7. Run GA4/GTM audits against the approved plan.
