# app/services/tracking_plan/drift/__init__.py
"""Tracking-plan drift engine — live-vs-plan reconciliation.

Computes the OBSERVED state of a plan's events from live analytics:
  * Tier 1 (GA4 Data API)  — per-event volume + firing → drifted / broken / unplanned.
  * Tier 2 (BigQuery export) — per-parameter fill-rate + unplanned parameters.

Results persist to ``tp_event_drift`` / ``tp_param_observation`` (keyed by event
NAME) and are read back by the serializer. Tier 2 degrades to nothing when the
project has no BigQuery GA4-export configured — the UI then shows honest plan
data with no fabricated coverage numbers.
"""

from .service import compute_drift, run_drift_computation

__all__ = ["compute_drift", "run_drift_computation"]
