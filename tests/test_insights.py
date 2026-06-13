from app.dashboards.insights import biggest_movers


def _card(title, metrics, compare=True):
    return {"title": title, "snap": {"compare": compare, "metrics": metrics}}


def test_ranks_by_absolute_delta():
    cards = [
        _card("Sessions", [{"label": "Sessions", "delta_pct": 11.1}]),
        _card("Direct", [{"label": "Direct", "delta_pct": -62.7}]),
        _card("Paid", [{"label": "Paid", "delta_pct": -8.1}]),
    ]
    out = biggest_movers(cards, n=2)
    assert out == ["Direct ▼62.7%", "Sessions ▲11.1%"]


def test_skips_non_compare_cards():
    cards = [_card("X", [{"label": "X", "delta_pct": 50}], compare=False)]
    assert biggest_movers(cards) == []


def test_ignores_missing_deltas():
    cards = [_card("Y", [{"label": "Y", "delta_pct": None}, {"label": "Z", "delta_pct": 5}])]
    assert biggest_movers(cards) == ["Z ▲5%"]


def test_empty_when_no_cards():
    assert biggest_movers([]) == []


def test_caps_at_n():
    cards = [_card(f"M{i}", [{"label": f"M{i}", "delta_pct": i}]) for i in range(1, 10)]
    assert len(biggest_movers(cards, n=3)) == 3
