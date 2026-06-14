# tests/services/tracking_plan/test_common.py
import uuid

from app.services.tracking_plan.common import _UNSET, apply_fields, coerce_uuid
from app.services.tracking_plan.exceptions import NotFoundError


def test_coerce_uuid_accepts_str_and_uuid():
    u = uuid.uuid4()
    assert coerce_uuid(u) == u
    assert coerce_uuid(str(u)) == u


def test_apply_fields_skips_unset_and_unknown():
    class Box:
        a = 1
        b = 2

    box = Box()
    apply_fields(box, {"a": 10, "b": _UNSET, "c": 99}, allowed={"a", "b"})
    assert box.a == 10  # set
    assert box.b == 2  # _UNSET -> untouched
    assert not hasattr(box, "c")  # not in allowed -> untouched


def test_notfound_is_tracking_plan_error():
    from app.services.tracking_plan.exceptions import TrackingPlanError

    assert issubclass(NotFoundError, TrackingPlanError)
