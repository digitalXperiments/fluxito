import importlib
import os

import app._version as version_mod


def _reload():
    return importlib.reload(version_mod)


def test_baked_app_version_takes_precedence(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.0.7")
    mod = _reload()
    assert mod.get_version() == "1.0.7"


def test_falls_back_to_track_with_local_suffix(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    mod = _reload()
    v = mod.get_version()
    assert v.endswith("+local")
    # Track is MAJOR.MINOR, fallback adds a .0 patch -> "1.0.0+local"
    assert v.count(".") == 2


def test_blank_app_version_uses_fallback(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "   ")
    mod = _reload()
    assert mod.get_version().endswith("+local")
