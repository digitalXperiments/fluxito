# app/services/tracking_plan/exceptions.py
"""Typed errors raised by the tracking-plan service. Adapters (MCP/HTTP) map
these to tool errors / HTTP status codes."""


class TrackingPlanError(Exception):
    """Base class for all tracking-plan service errors."""


class NotFoundError(TrackingPlanError):
    """A referenced entity does not exist (or is on a different branch)."""


class ValidationError(TrackingPlanError):
    """A write was rejected because it is structurally invalid."""


class ConflictError(TrackingPlanError):
    """A write violates a uniqueness rule (e.g. duplicate name)."""
