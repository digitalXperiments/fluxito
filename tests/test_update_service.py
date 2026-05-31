import pytest

from app.settings_service import RUNTIME_SETTING_BY_KEY


def test_update_checks_setting_registered():
    assert "update_checks_enabled" in RUNTIME_SETTING_BY_KEY
    spec = RUNTIME_SETTING_BY_KEY["update_checks_enabled"]
    assert spec.value_type == "bool"


from app.services.update_service import is_newer, parse_semver


def test_parse_semver_strips_prefix_and_suffix():
    assert parse_semver("v1.2.3") == (1, 2, 3)
    assert parse_semver("1.2.3+local") == (1, 2, 3)
    assert parse_semver("1.2.3-rc1") == (1, 2, 3)
    assert parse_semver("1.0") == (1, 0, 0)


def test_is_newer():
    assert is_newer("1.0.3", "1.0.2") is True
    assert is_newer("1.1.0", "1.0.9") is True
    assert is_newer("1.0.2", "1.0.2") is False
    assert is_newer("1.0.1", "1.0.2") is False
    assert is_newer("1.0.2", "1.0.2+local") is False
