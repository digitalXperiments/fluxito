import pytest

from app.settings_service import RUNTIME_SETTING_BY_KEY


def test_update_checks_setting_registered():
    assert "update_checks_enabled" in RUNTIME_SETTING_BY_KEY
    spec = RUNTIME_SETTING_BY_KEY["update_checks_enabled"]
    assert spec.value_type == "bool"
