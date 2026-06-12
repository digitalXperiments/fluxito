# app/services/tracking_plan/__init__.py
"""Tracking-plan service: the only module that mutates tp_* tables.

Public API is re-exported here so callers do `from app.services.tracking_plan
import create_event` etc. Later tasks append to this file."""

from .bootstrap import get_main_branch, get_or_create_plan
from .exceptions import ConflictError, NotFoundError, TrackingPlanError, ValidationError
from .taxonomy import create_category, delete_category, update_category

__all__ = [
    "ConflictError",
    "NotFoundError",
    "TrackingPlanError",
    "ValidationError",
    "create_category",
    "delete_category",
    "get_main_branch",
    "get_or_create_plan",
    "update_category",
]
