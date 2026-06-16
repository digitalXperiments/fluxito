from app.api.ask_routes import _sse_frame, router


def test_router_exposes_expected_paths():
    paths = {r.path for r in router.routes}
    assert "/ask" in paths
    assert "/api/ask/stream" in paths
    assert "/api/ask/conversations" in paths


def test_sse_frame_format():
    frame = _sse_frame({"type": "text_delta", "text": "hi"})
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    assert '"text": "hi"' in frame


def test_keys_settings_redirect_exists():
    from app.api.ask_routes import router

    paths = {r.path for r in router.routes}
    assert "/settings/ai" in paths
