"""Auto-generated dashboard insights from compare-mode deltas.

When compare is on, surface the biggest movers as a one-line banner
("Sessions ▲11.1%, Paid ▼8.1%"). Computed purely from the merged card payloads
(no extra queries), so it's unit-testable.
"""

from __future__ import annotations


def biggest_movers(cards: list[dict], n: int = 3) -> list[str]:
    """Return up to ``n`` formatted mover strings, ranked by absolute % change.

    Reads scorecard metric deltas (``snap.metrics[*].delta_pct``) from compare
    cards. Ties broken by insertion order. Empty when no compare deltas exist.
    """
    movers: list[tuple[str, float]] = []
    for card in cards or []:
        snap = card.get("snap") or {}
        if not snap.get("compare"):
            continue
        title = card.get("title") or ""
        for m in snap.get("metrics") or []:
            dp = m.get("delta_pct")
            if isinstance(dp, (int, float)):
                label = m.get("label") or m.get("key") or title
                movers.append((label, float(dp)))
    movers.sort(key=lambda x: abs(x[1]), reverse=True)
    out: list[str] = []
    for label, dp in movers[:n]:
        arrow = "▲" if dp > 0 else ("▼" if dp < 0 else "•")
        pct = f"{abs(dp):.1f}".rstrip("0").rstrip(".")
        out.append(f"{label} {arrow}{pct}%")
    return out
