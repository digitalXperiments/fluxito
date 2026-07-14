"""
Chart schema — the formal, validated contract for ``chart_config`` per ``chart_type``.

Before this module, ``chart_config`` was an unvalidated free-form dict; the only
documentation of its shape lived in a comment block in the (now-deleted)
``card_charts.html`` partial, mirrored today in ``app/static/js/dashboard/charts.js``
(see the "Explicit chart_config path" comment there). This module makes that
contract a real, importable Pydantic v2 schema so the MCP tools, the Ask
Fluxito chat builder, and the frontend can all validate against — and generate
docs/JSON Schema from — a single source of truth.

Canonical ``chart_type`` vocabulary (19 values):
  legacy (7):  scorecard, bar, line, pie, table, audit, list
  new (12):    area, combo, stacked_bar, hbar, donut, scatter, heatmap,
               funnel, treemap, radar, gauge, waterfall

Backward compatibility is non-negotiable: cards stored before this schema
existed may carry ``chart_type='bar'`` with ``chart_config.type='stacked_bar'``
(the sub-mode used to be selected purely via ``chart_config.type``, before
these became first-class ``chart_type`` values). Every config model accepts
that legacy shape unchanged — validation here is deliberately permissive
(``extra="allow"``, all fields optional) rather than strict; it exists to
catch genuinely malformed shapes (e.g. a ``series`` entry with no ``col``),
not to police forward-compatible extra keys.

Entry points for other modules:
  validate_chart_config(chart_type, chart_config) -> (normalized_config, warnings)
  export_json_schema() -> dict[chart_type, JSON Schema]  (frontend/tool docs)
  CHART_TYPES -> frozenset of every chart_type with a model here (drift guard)
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class SeriesSpec(BaseModel):
    """One plotted series/column. Shared by every multi-series chart type
    (bar, line, area, stacked_bar, hbar, donut-adjacent bar/line combos, combo).

    ``kind`` is combo-only (bar vs line per series); every other type ignores it.
    """

    model_config = ConfigDict(extra="allow")

    col: str
    label: str | None = None
    color: str | None = None
    axis: Literal["left", "right"] | None = None
    stack: str | None = None
    kind: Literal["bar", "line"] | None = None


class ChartConfigBase(BaseModel):
    """Fields documented for every chart_type today (see charts.js's
    "Explicit chart_config path" comment and the ``dashboard_deploy_batch``
    docstring in ``dashboard_tools.py``). Every per-type config model below
    inherits these so a legacy card validates unchanged no matter which
    chart_type it was stored under.

    ``type`` is the legacy sub-mode field: old cards select area/stacked_bar/
    hbar/donut via ``chart_type='bar'|'line'|'pie'`` + ``chart_config.type=...``
    rather than a first-class chart_type. Kept optional and unvalidated against
    the outer chart_type on purpose — see module docstring.
    """

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    x: str | None = None
    series: list[SeriesSpec] | None = None
    highlight_last: bool | None = None
    orientation: Literal["horizontal", "vertical"] | None = None
    smooth: bool | None = None
    color_scheme: Literal["blue", "green", "amber", "purple", "red", "teal", "pink"] | None = None
    sparkline: bool | None = None
    unit: Literal["number", "currency", "percent", "duration"] | None = None
    stacked: bool | None = None
    donut: bool | None = None
    show_legend: bool | None = None


# ---------------------------------------------------------------------------
# Per-chart_type config models
# ---------------------------------------------------------------------------
# The 7 legacy types carry no extra fields beyond the base — they're listed
# explicitly (rather than aliased to ChartConfigBase) so each has its own
# JSON Schema entry and so future per-type fields don't leak across types.


class ScorecardConfig(ChartConfigBase):
    """Single metric highlight. ``unit``/``sparkline``/``color_scheme`` drive
    the tile; ``series``/``x`` are unused (scorecards derive their value from
    ``snap.metrics``, see ``snapshot.py``)."""


class BarConfig(ChartConfigBase):
    """Vertical bar chart. Also accepts the legacy sub-modes ``stacked_bar``/
    ``hbar`` via ``chart_config.type`` for cards stored before those became
    first-class chart_type values."""


class LineConfig(ChartConfigBase):
    """Line chart. Also accepts the legacy ``area`` sub-mode via
    ``chart_config.type`` for pre-existing cards."""


class PieConfig(ChartConfigBase):
    """Pie chart. Also accepts the legacy ``donut`` sub-mode via
    ``chart_config.donut`` / ``chart_config.type``."""


class TableConfig(ChartConfigBase):
    """Tabular data — chart_config mostly unused beyond ``unit``/formatting."""


class AuditConfig(ChartConfigBase):
    """Findings/issues list — no chart-specific fields; kept for schema parity."""


class ListConfig(ChartConfigBase):
    """Simple item list — no chart-specific fields; kept for schema parity."""


class AreaConfig(ChartConfigBase):
    """First-class area chart (filled line). Equivalent to the legacy
    ``chart_type='line'`` + ``chart_config.type='area'`` shape."""


class StackedBarConfig(ChartConfigBase):
    """First-class stacked bar chart. Equivalent to the legacy
    ``chart_type='bar'`` + ``chart_config.type='stacked_bar'`` shape.
    ``series[].stack`` groups which series share a stack (default group
    ``"total"`` when omitted, matching charts.js)."""


class HBarConfig(ChartConfigBase):
    """First-class horizontal bar chart. Equivalent to the legacy
    ``chart_type='bar'`` + ``chart_config.orientation='horizontal'`` shape."""


class DonutConfig(ChartConfigBase):
    """First-class donut chart. Equivalent to the legacy ``chart_type='pie'``
    + ``chart_config.donut=true`` shape."""


class ComboConfig(ChartConfigBase):
    """Bar+line combo, optionally dual-axis. Each entry in ``series`` picks its
    own render kind via ``kind: "bar" | "line"`` (defaults to ``"bar"`` client-
    side when omitted); dual axis is driven by the existing ``series[].axis``
    field, same as bar/line."""


class ScatterConfig(ChartConfigBase):
    """XY scatter. ``x_col``/``y_col`` name the plotted columns; ``size_col``
    is optional (bubble size)."""

    x_col: str | None = None
    y_col: str | None = None
    size_col: str | None = None


class HeatmapConfig(ChartConfigBase):
    """2D heatmap. ``x_col``/``y_col`` are the two categorical axes,
    ``value_col`` is the cell intensity metric."""

    x_col: str | None = None
    y_col: str | None = None
    value_col: str | None = None


class FunnelConfig(ChartConfigBase):
    """Funnel chart. ``stage_col`` names the stage/label column, ``value_col``
    the metric that shrinks stage over stage."""

    stage_col: str | None = None
    value_col: str | None = None


class TreemapConfig(ChartConfigBase):
    """Treemap. ``label_col``/``value_col`` size each rectangle; ``parent_col``
    is optional for a nested (multi-level) hierarchy."""

    label_col: str | None = None
    value_col: str | None = None
    parent_col: str | None = None


class RadarConfig(ChartConfigBase):
    """Radar/spider chart. ``label_col`` names each row's series name (one
    polygon per row); ``value_cols`` lists the metric columns used as the
    radar's indicators (axes)."""

    label_col: str | None = None
    value_cols: list[str] | None = None


class GaugeConfig(ChartConfigBase):
    """Single-value gauge. ``value_col`` is the metric; ``min``/``max`` bound
    the dial (default 0/100 client-side when omitted); ``target`` draws an
    optional threshold marker."""

    value_col: str | None = None
    min: float | None = None
    max: float | None = None
    target: float | None = None


class WaterfallConfig(ChartConfigBase):
    """Waterfall chart. ``label_col`` names each step, ``delta_col`` the
    (signed) change contributed by that step."""

    label_col: str | None = None
    delta_col: str | None = None


# ---------------------------------------------------------------------------
# chart_type -> config model
# ---------------------------------------------------------------------------

_CONFIG_MODELS: dict[str, type[ChartConfigBase]] = {
    "scorecard": ScorecardConfig,
    "bar": BarConfig,
    "line": LineConfig,
    "pie": PieConfig,
    "table": TableConfig,
    "audit": AuditConfig,
    "list": ListConfig,
    "area": AreaConfig,
    "combo": ComboConfig,
    "stacked_bar": StackedBarConfig,
    "hbar": HBarConfig,
    "donut": DonutConfig,
    "scatter": ScatterConfig,
    "heatmap": HeatmapConfig,
    "funnel": FunnelConfig,
    "treemap": TreemapConfig,
    "radar": RadarConfig,
    "gauge": GaugeConfig,
    "waterfall": WaterfallConfig,
}

# Exported so other modules (dashboard_tools, snapshot, and the drift-guard
# test) can check membership against exactly the types this schema knows,
# instead of re-deriving/duplicating the vocabulary.
CHART_TYPES: frozenset[str] = frozenset(_CONFIG_MODELS)


# ---------------------------------------------------------------------------
# Discriminated union: ChartSpec = {chart_type, chart_config} tagged by chart_type
# ---------------------------------------------------------------------------
# One wrapper model per chart_type, each pinning `chart_type` to a Literal so
# pydantic can discriminate on it. This is the formal "card chart spec" shape;
# validate_chart_config() below is the ergonomic (chart_type, chart_config)
# entry point most callers use, built on top of this union.


class _ScorecardSpec(BaseModel):
    chart_type: Literal["scorecard"]
    chart_config: ScorecardConfig = Field(default_factory=ScorecardConfig)


class _BarSpec(BaseModel):
    chart_type: Literal["bar"]
    chart_config: BarConfig = Field(default_factory=BarConfig)


class _LineSpec(BaseModel):
    chart_type: Literal["line"]
    chart_config: LineConfig = Field(default_factory=LineConfig)


class _PieSpec(BaseModel):
    chart_type: Literal["pie"]
    chart_config: PieConfig = Field(default_factory=PieConfig)


class _TableSpec(BaseModel):
    chart_type: Literal["table"]
    chart_config: TableConfig = Field(default_factory=TableConfig)


class _AuditSpec(BaseModel):
    chart_type: Literal["audit"]
    chart_config: AuditConfig = Field(default_factory=AuditConfig)


class _ListSpec(BaseModel):
    chart_type: Literal["list"]
    chart_config: ListConfig = Field(default_factory=ListConfig)


class _AreaSpec(BaseModel):
    chart_type: Literal["area"]
    chart_config: AreaConfig = Field(default_factory=AreaConfig)


class _ComboSpec(BaseModel):
    chart_type: Literal["combo"]
    chart_config: ComboConfig = Field(default_factory=ComboConfig)


class _StackedBarSpec(BaseModel):
    chart_type: Literal["stacked_bar"]
    chart_config: StackedBarConfig = Field(default_factory=StackedBarConfig)


class _HBarSpec(BaseModel):
    chart_type: Literal["hbar"]
    chart_config: HBarConfig = Field(default_factory=HBarConfig)


class _DonutSpec(BaseModel):
    chart_type: Literal["donut"]
    chart_config: DonutConfig = Field(default_factory=DonutConfig)


class _ScatterSpec(BaseModel):
    chart_type: Literal["scatter"]
    chart_config: ScatterConfig = Field(default_factory=ScatterConfig)


class _HeatmapSpec(BaseModel):
    chart_type: Literal["heatmap"]
    chart_config: HeatmapConfig = Field(default_factory=HeatmapConfig)


class _FunnelSpec(BaseModel):
    chart_type: Literal["funnel"]
    chart_config: FunnelConfig = Field(default_factory=FunnelConfig)


class _TreemapSpec(BaseModel):
    chart_type: Literal["treemap"]
    chart_config: TreemapConfig = Field(default_factory=TreemapConfig)


class _RadarSpec(BaseModel):
    chart_type: Literal["radar"]
    chart_config: RadarConfig = Field(default_factory=RadarConfig)


class _GaugeSpec(BaseModel):
    chart_type: Literal["gauge"]
    chart_config: GaugeConfig = Field(default_factory=GaugeConfig)


class _WaterfallSpec(BaseModel):
    chart_type: Literal["waterfall"]
    chart_config: WaterfallConfig = Field(default_factory=WaterfallConfig)


ChartSpec = Annotated[
    _ScorecardSpec
    | _BarSpec
    | _LineSpec
    | _PieSpec
    | _TableSpec
    | _AuditSpec
    | _ListSpec
    | _AreaSpec
    | _ComboSpec
    | _StackedBarSpec
    | _HBarSpec
    | _DonutSpec
    | _ScatterSpec
    | _HeatmapSpec
    | _FunnelSpec
    | _TreemapSpec
    | _RadarSpec
    | _GaugeSpec
    | _WaterfallSpec,
    Field(discriminator="chart_type"),
]

_chart_spec_adapter: TypeAdapter[ChartSpec] = TypeAdapter(ChartSpec)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_chart_spec(payload: dict) -> ChartSpec:
    """Validate a full ``{"chart_type": ..., "chart_config": {...}}`` payload
    against the discriminated union directly. Raises ``pydantic.ValidationError``
    (including for an unknown ``chart_type`` — unlike ``validate_chart_config``,
    this is the strict entry point). Used by callers that already have a
    combined spec dict (e.g. a future ``dashboard_card_preview`` tool) rather
    than separate ``chart_type``/``chart_config`` arguments.
    """
    return _chart_spec_adapter.validate_python(payload)


def validate_chart_config(chart_type: str | None, chart_config: dict | None) -> tuple[dict, list[str]]:
    """Validate ``chart_config`` against its ``chart_type``'s schema.

    Returns ``(normalized_config, warnings)``. ``normalized_config`` is a plain
    dict (safe to store back into JSONB) with unset optional fields dropped;
    unknown/extra keys are preserved (``extra="allow"``) since old cards and
    forward-compatible future keys must both survive.

    Raises ``ValueError`` only for genuinely malformed shapes (e.g. a
    ``series`` entry with no ``col``, or ``chart_config`` not being an
    object) — callers (``dashboard_tools._validate_card_specs``) aggregate
    that into their existing fail-fast error format. An *unknown* chart_type
    is reported as a warning, not a hard error, here — the tool-level
    ``VALID_CHART_TYPES`` check is what actually gates that; this function
    stays usable standalone (e.g. from a future preview tool) without
    duplicating that gate.
    """
    warnings: list[str] = []

    if chart_config is not None and not isinstance(chart_config, dict):
        raise ValueError(f"chart_config must be an object, got {type(chart_config).__name__}")
    cfg = chart_config or {}

    ct = (chart_type or "").lower()
    model_cls = _CONFIG_MODELS.get(ct)
    if model_cls is None:
        warnings.append(
            f"unknown chart_type '{chart_type}' — chart_config accepted without type-specific validation"
        )
        return dict(cfg), warnings

    try:
        parsed = model_cls.model_validate(cfg)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else None
        loc = ".".join(str(p) for p in first["loc"]) if first else ""
        msg = first["msg"] if first else str(exc)
        detail = f"{loc}: {msg}" if loc else msg
        raise ValueError(f"chart_config invalid for chart_type '{chart_type}' ({detail})") from exc

    return parsed.model_dump(exclude_none=True, mode="json"), warnings


def export_json_schema() -> dict[str, dict]:
    """Export a JSON Schema per chart_type's chart_config model, for the
    frontend chart-builder UI and MCP tool docs."""
    return {ct: model_cls.model_json_schema() for ct, model_cls in _CONFIG_MODELS.items()}
