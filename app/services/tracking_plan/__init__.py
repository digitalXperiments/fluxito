# app/services/tracking_plan/__init__.py
"""Tracking-plan service: the only module that mutates tp_* tables.

Public API is re-exported here so callers do `from app.services.tracking_plan
import create_event` etc. Later tasks append to this file."""

from .bootstrap import get_main_branch, get_or_create_plan
from .branches import (
    abandon_branch,
    create_branch,
    diff_branches,
    get_branch,
    list_branches,
    merge_branch,
    set_review_status,
)
from .events import (
    create_event,
    delete_event,
    remove_event_destination,
    set_event_destination,
    set_event_sources,
    update_event,
)
from .exceptions import ConflictError, NotFoundError, TrackingPlanError, ValidationError
from .exports import plan_to_markdown, plan_to_xlsx
from .metrics import create_metric, delete_metric, update_metric
from .properties import (
    attach_property,
    create_property,
    delete_property,
    detach_property,
    update_property,
)
from .publish import latest_snapshot_for_project, publish_branch
from .routing import (
    connect_source_destination,
    create_destination,
    create_source,
    delete_destination,
    delete_source,
    disconnect_source_destination,
    update_destination,
    update_source,
)
from .serializer import plan_to_dict
from .taxonomy import create_category, delete_category, update_category
from .validation import validate_plan

__all__ = [
    "ConflictError",
    "NotFoundError",
    "TrackingPlanError",
    "ValidationError",
    "abandon_branch",
    "attach_property",
    "connect_source_destination",
    "create_branch",
    "create_category",
    "create_destination",
    "create_event",
    "create_metric",
    "create_property",
    "create_source",
    "delete_category",
    "delete_destination",
    "delete_event",
    "delete_metric",
    "delete_property",
    "delete_source",
    "detach_property",
    "diff_branches",
    "disconnect_source_destination",
    "get_branch",
    "get_main_branch",
    "get_or_create_plan",
    "latest_snapshot_for_project",
    "list_branches",
    "merge_branch",
    "plan_to_dict",
    "plan_to_markdown",
    "plan_to_xlsx",
    "publish_branch",
    "remove_event_destination",
    "set_event_destination",
    "set_event_sources",
    "set_review_status",
    "update_category",
    "update_destination",
    "update_event",
    "update_metric",
    "update_property",
    "update_source",
    "validate_plan",
]
