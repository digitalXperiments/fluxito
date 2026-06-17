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


def test_new_key_routes_registered():
    """DELETE /api/ask/keys/{provider}, POST /api/ask/keys/default, POST /api/ask/keys/test must be registered."""
    paths = {r.path for r in router.routes}
    assert "/api/ask/keys/{provider}" in paths, "DELETE route missing"
    assert "/api/ask/keys/default" in paths, "POST /default route missing"
    assert "/api/ask/keys/test" in paths, "POST /test route missing"


def test_delete_provider_route_method():
    """Verify the DELETE method is wired to /api/ask/keys/{provider}."""
    for r in router.routes:
        if r.path == "/api/ask/keys/{provider}":
            assert "DELETE" in r.methods, "Expected DELETE method on /api/ask/keys/{provider}"
            break
    else:
        raise AssertionError("/api/ask/keys/{provider} route not found")


def test_default_and_test_routes_are_post():
    """POST /api/ask/keys/default and /api/ask/keys/test must be POST."""
    post_paths = {r.path for r in router.routes if "POST" in getattr(r, "methods", set())}
    assert "/api/ask/keys/default" in post_paths
    assert "/api/ask/keys/test" in post_paths


def test_model_options_route_registered():
    paths = {r.path for r in router.routes}
    assert "/api/ask/model-options" in paths


def test_admin_models_routes_registered():
    paths = {r.path for r in router.routes}
    assert "/api/ask/admin/models" in paths


def test_admin_models_get_and_post_methods():
    for r in router.routes:
        if r.path == "/api/ask/admin/models":
            methods = getattr(r, "methods", set())
            assert "GET" in methods or "POST" in methods


def test_ai_models_settings_page_registered():
    paths = {r.path for r in router.routes}
    assert "/settings/ai-models" in paths
