"""Implementation hub services.

Bridges the structured tracking plan (source of truth for *what* should be
tracked) with the live GTM container (what is *actually* deployed) and the
Flux draft/approve pipeline (how a planned event gets shipped).

- ``build_coverage`` — the read model behind the Implement page: one row per
  planned event with per-source implementation status, live drift, and a GTM
  deployment verdict.
- ``build_deploy_proposal`` — turns a planned event into a pending FluxDraft
  (a GTM GA4-event-tag + custom-event-trigger proposal) in the exact payload
  shape the Ask propose_change flow produces, so it flows through the existing
  approve/reject endpoints.
"""

from app.services.implementation.coverage import build_coverage
from app.services.implementation.generate import build_deploy_proposal

__all__ = ["build_coverage", "build_deploy_proposal"]
