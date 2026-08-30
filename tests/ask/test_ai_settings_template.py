from pathlib import Path

from jinja2 import Environment

AI_SETTINGS_TEMPLATE = Path("app/templates/settings/ai.html")


def test_ai_settings_template_syntax_valid():
    source = AI_SETTINGS_TEMPLATE.read_text()
    Environment().parse(source)


def test_ai_settings_has_hero_banner():
    source = AI_SETTINGS_TEMPLATE.read_text()
    assert 'id="ai-hero-card"' in source
    assert 'id="ai-hero-provider-model"' in source
    assert 'id="ai-hero-test-btn"' in source
    assert 'id="ai-hero-config-btn"' in source


def test_ai_settings_has_category_and_capability_filters():
    source = AI_SETTINGS_TEMPLATE.read_text()
    assert 'data-category="all"' in source
    assert 'data-category="cloud"' in source
    assert 'data-category="local"' in source
    assert 'id="ai-cap-filters"' in source
    assert 'data-cap="reasoning"' in source
    assert 'data-cap="vision"' in source
    assert 'data-cap="code"' in source


def test_ai_settings_has_dual_mode_editor_and_freeform_input():
    source = AI_SETTINGS_TEMPLATE.read_text()
    assert 'id="ai-mode-catalog-btn"' in source
    assert 'id="ai-mode-custom-btn"' in source
    assert 'id="ai-editor-model-custom"' in source
    assert 'id="ai-modal-backdrop"' in source
    assert 'id="ai-test-modal"' in source
