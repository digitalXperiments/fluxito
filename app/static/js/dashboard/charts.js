/* Shared chart mounting logic for all dashboard views.
   Requires:
     - ECharts loaded: <script src="/static/vendor/echarts-5.5.0.min.js"></script>
     - window.Fluxito from static/js/dashboard/cards.js (for fmtNum/esc)
     - Cards rendered with [data-card-id] and [data-chart] elements
   Provides:
     - window.Fluxito.mountCharts(cards, gridEl?) — mounts ECharts into chart slots

   Smart features:
     - Honors card.snap.chart_config (explicit type/series/colors/axes)
     - Auto-detects date columns → forces line; categorical → bar
     - Horizontal bar when label is categorical and ≤8 rows
     - Dual y-axis when series scales diverge by >5×
     - Highlights last-period data point (current quarter / most recent date)
     - Single-series by default; multi-series only if chart_config asks or shapes align

   Moved out of app/templates/partials/card_charts.html (Phase 0 extraction,
   dashboard revamp). Referenced by live_view.html and public.html via a plain
   (non-deferred) <script src> loaded after cards.js, matching the old
   inline-script order exactly.
*/
(function(){
  var F = window.Fluxito = window.Fluxito || {};
  var _instances = {};

  var _MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // Detect if a string looks like a date/time label
  function looksLikeDate(s){
    if (s === null || s === undefined) return false;
    s = String(s).trim();
    if (!s) return false;
    // ISO-ish: 2024-01-05, 2024-01, 2024 (year range 1970–2100), 2024-Q1, FY24-Q1, FY24Q1
    if (/^\d{4}(-\d{1,2}(-\d{1,2})?)?$/.test(s)) {
      // Plain 4-digit string: must be a plausible year (1970–2100)
      if (/^\d{4}$/.test(s)) { var y4 = parseInt(s, 10); return y4 >= 1970 && y4 <= 2100; }
      return true;
    }
    if (/^\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2}/.test(s)) return true;
    if (/^(FY)?\d{2,4}[\- ]?Q[1-4]$/i.test(s)) return true;
    if (/^Q[1-4][\- ]?\d{2,4}$/i.test(s)) return true;
    if (/^\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}$/.test(s)) return true;
    // Month names — must be followed by a digit, whitespace, comma, dash, slash, or end-of-string
    // to avoid false positives on words like "market", "margin", "march_promo".
    if (/^(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?|sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?)(\s|-|\/|,|\d|$)/i.test(s)) return true;
    // YYYYMM compact integer (e.g. 202401) — GA4 yearMonth
    if (/^\d{6}$/.test(s)) {
      var ym = parseInt(s.slice(4,6), 10);
      if (parseInt(s.slice(0,4), 10) > 1900 && ym >= 1 && ym <= 12) return true;
    }
    // YYYYMMDD compact integer (e.g. 20251126)
    if (/^\d{8}$/.test(s)) {
      var y = parseInt(s.slice(0,4), 10), mo = parseInt(s.slice(4,6), 10), d = parseInt(s.slice(6,8), 10);
      if (y > 1900 && y < 2100 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31) return true;
    }
    return false;
  }

  // Date labels are formatted by the canonical window.Fluxito.formatDateLabel
  // (defined in card_renderer_js.html, mirrors app/dashboards/date_labels.py).

  // Format a month-number label (1–12) into abbreviated month name
  function fmtMonthLabel(s){
    var n = parseInt(s, 10);
    return (n >= 1 && n <= 12) ? _MONTH_SHORT[n-1] : s;
  }

  // Column name looks like a month-number dimension
  function isMonthNumCol(colName){
    return /^month(_num(ber)?)?$/i.test(colName);
  }

  // Heuristic formatter for a numeric column based on its name.
  // Removes underscores before matching so both camelCase and snake_case work.
  function getColFmt(colName) {
    var n = String(colName || '').toLowerCase().replace(/_/g, '');
    // Rate / percentage: bounce_rate, ctr, engagement_rate, conversion_rate, click_rate…
    if (/rate$/.test(n) || /^ctr$|ctr$|^cvr$|cvr$/.test(n)) {
      return function(v) { return F.fmtMetricValue ? F.fmtMetricValue(v, 'percent') : v + '%'; };
    }
    // Duration: average_session_duration, session_duration, time_on_page, avg_time…
    if (/duration|timeon|timespent|dwelltime|avgtime/.test(n)) {
      return function(v) { return F.fmtMetricValue ? F.fmtMetricValue(v, 'duration_sec') : v + 's'; };
    }
    return function(v) { return F.fmtNum ? F.fmtNum(v) : v; };
  }

  function toNum(v){
    var n = parseFloat(String(v==null?'':v).replace(/,/g,''));
    return isNaN(n) ? 0 : n;
  }

  // A "derivative metric" is a column whose values are derived from other
  // columns on the same row — typically a %-change, rate, ratio, or index.
  // These should not be charted alongside their source metrics, because:
  //   (a) mixing a % with an absolute value on the same chart is visually
  //       confusing and forces meaningless dual axes,
  //   (b) the %-series is usually small in magnitude but can be large in
  //       absolute value (e.g. -98, +82), which fools the scale-sorter.
  // Detection is purely name-based; the table still shows every column.
  function isDerivativeMetric(colName){
    if (!colName) return false;
    var n = String(colName).toLowerCase();
    // common suffixes/infixes for % change, rate, ratio, yoy/mom/wow/qoq deltas
    return /(^|_)(pct|rate|ratio|share|index|delta|chg|change|growth|variance|diff)($|_)/.test(n)
        || /(yoy|mom|wow|qoq|pct_?chg|chg_?pct|_pct$|_rate$|_ratio$|_share$)/.test(n);
  }

  // Given array of numbers, return max absolute value
  function maxAbs(arr){
    var m = 0;
    for (var i=0;i<arr.length;i++){ var a = Math.abs(arr[i]); if (a > m) m = a; }
    return m;
  }

  // Decide if two series should share a y-axis or be split (scale divergence >5×)
  function scalesDivergent(a, b){
    var ma = maxAbs(a), mb = maxAbs(b);
    if (ma === 0 || mb === 0) return false;
    var ratio = ma > mb ? (ma/mb) : (mb/ma);
    return ratio > 5;
  }

  // Read a CSS custom property off :root, with a hardcoded fallback.
  function cssVar(name, fallback){
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch(e){ return fallback; }
  }

  // Palette — Ledger. Reads the --dv-* tokens from app.css so a category
  // is the same color in charts and everywhere else. Hex fallbacks match app.css.
  // Note: the Ledger design system (site revamp #24) is light-only, so these
  // fallbacks no longer branch on isDark — the --dv-* custom properties
  // themselves are the single source of truth. isDark is still threaded
  // through (see mountCharts) in case dark mode returns.
  function palette(isDark){
    return {
      primary:   cssVar('--dv-c1', '#C4703A'),
      secondary: cssVar('--dv-c2', '#3E8A5F'),
      accent:    cssVar('--dv-c3', '#C4903A'),
      neg:       cssVar('--dv-c4', '#B4452F'),
      pink:      cssVar('--dv-c5', '#201B14'),
      cyan:      cssVar('--dv-c6', '#57503F'),
      orange:    cssVar('--dv-c7', '#A89F8D'),
      neutral:   cssVar('--dv-neutral', '#8A857C'),
      muted:     '#A89F8D',
      // Tan base for single-series bars + terracotta highlight for the most
      // recent value (Dashboard View design).
      bar:       cssVar('--dv-bar', '#E8DFCC'),
      highlight: cssVar('--dv-accent', '#C4703A'),
    };
  }

  // Canonical categorical color order — same sequence as the --dv-* tokens.
  function paletteSeq(pal){
    return [pal.primary, pal.secondary, pal.accent, pal.neg, pal.pink, pal.cyan, pal.orange];
  }

  // Exported so theme.js (and anything else, e.g. a future Ask Fluxito chart
  // preview) can build the exact same palette/theme without duplicating the
  // --dv-* CSS var reads.
  F.cssVar = cssVar;
  F.palette = palette;
  F.paletteSeq = paletteSeq;

  // A soft top-down gradient fill for area/line charts (theme item: "subtle
  // linear-gradient area fills"). Pair with areaStyle.opacity on the series
  // for the overall fade — this just supplies the color→transparent stops.
  // Falls back to a flat color if echarts.graphic isn't available.
  function gradientFill(color){
    if (typeof echarts !== 'undefined' && echarts.graphic && echarts.graphic.LinearGradient) {
      return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        {offset: 0, color: color},
        {offset: 1, color: 'rgba(255,255,255,0)'},
      ]);
    }
    return color;
  }

  // Resolve a semantic color role or hex to an actual color
  function resolveColor(val, pal){
    if (!val) return null;
    if (val.charAt && val.charAt(0) === '#') return val;
    return pal[val] || pal.primary;
  }

  // Build a series spec with highlight on the last (most recent) data point
  function withHighlightLast(data, baseColor, highlightColor){
    return data.map(function(v, i){
      var isLast = i === data.length - 1;
      return {
        value: v,
        itemStyle: isLast ? {color: highlightColor} : {color: baseColor},
      };
    });
  }

  // ── Auto-renderer: no chart_config ─────────────────────────────────────
  // forcedType (optional): 'bar' | 'hbar' | 'line' | 'area' | 'stacked_bar' —
  // lets a first-class snap.chart_type steer the auto-detected layout without
  // requiring an explicit chart_config (promoted types, dashboard-revamp P1).
  // Legacy callers (no forcedType) keep the exact heuristic behavior.
  function autoTableChart(inst, card, axisColor, gridColor, pal, forcedType){
    var snap = card.snap || {};
    var rows = snap.rows || [];
    var columns = snap.columns || [];
    if (!rows.length || !columns.length) { inst.clear(); return; }

    var numericCols = F.numericColsOf ? F.numericColsOf(columns, rows) : columns.filter(function(c){
      return rows.slice(0,5).every(function(r){ var v = String(r[c]||'').replace(/,/g,''); return v && !isNaN(parseFloat(v)); });
    });
    var labelCol = columns.find(function(c){ return numericCols.indexOf(c) === -1; }) || columns[0];
    if (!labelCol || !numericCols.length) { inst.clear(); return; }

    // Split into absolute vs derivative (pct/rate/yoy-chg) columns.
    // If we have at least one absolute series, chart ONLY those — derivatives
    // still appear in the table beneath the chart. If the card is *only*
    // derivatives (e.g. a rates-only report), fall back to charting them.
    // Always exclude the label/dimension column from chart series.
    var absoluteCols = numericCols.filter(function(c){ return !isDerivativeMetric(c) && c !== labelCol; });
    var chartCols = (absoluteCols.length ? absoluteCols : numericCols.filter(function(c){ return c !== labelCol; }));

    // Decide orientation: if label is a date → line chart. Else categorical
    // with ≤8 rows → horizontal bar. More rows → vertical bar.
    var sampleLabels = rows.slice(0, Math.min(5, rows.length)).map(function(r){ return String(r[labelCol] == null ? '' : r[labelCol]); });
    var dateLike = sampleLabels.length > 0 && sampleLabels.every(looksLikeDate) && sampleLabels[0] !== 'undefined';
    var isMonthNum = !dateLike && isMonthNumCol(labelCol);
    // A forced 'line'/'area' type draws a line even over categorical labels;
    // 'bar'/'stacked_bar' always draw bars even over date-like labels.
    var isForcedArea = forcedType === 'area';
    if (forcedType === 'line' || isForcedArea) dateLike = true;
    if (forcedType === 'bar' || forcedType === 'stacked_bar' || forcedType === 'hbar') dateLike = false;

    var take = Math.min(rows.length, 20);
    // Always sort date/month dimensions ascending so charts read left→right chronologically
    var takeRows = rows.slice(0, take);
    if (dateLike || isMonthNum) {
      takeRows = takeRows.slice().sort(function(a, b) {
        var la = String(a[labelCol] == null ? '' : a[labelCol]);
        var lb = String(b[labelCol] == null ? '' : b[labelCol]);
        if (isMonthNum) return parseInt(la, 10) - parseInt(lb, 10);
        return la < lb ? -1 : la > lb ? 1 : 0;
      });
    }
    var rawLabels = takeRows.map(function(r){ return String(r[labelCol] == null ? '' : r[labelCol]); });
    var labels = dateLike ? rawLabels.map(F.formatDateLabel) : (isMonthNum ? rawLabels.map(fmtMonthLabel) : rawLabels);
    var isHorizontal = forcedType === 'hbar' ? true
      : (forcedType === 'bar' || forcedType === 'stacked_bar') ? false
      : (!dateLike && !isMonthNum && take <= 8);

    // Pick series: prefer a single primary metric unless the columns all look
    // like sibling metrics (similar scale). Scale-divergent columns get split
    // onto dual axes; very small ones are dropped.
    var allSeries = chartCols.slice(0, 4).map(function(c){
      return {col: c, data: takeRows.map(function(r){ return toNum(r[c]); })};
    });

    // Step 1: Drop any series that is all-zero
    allSeries = allSeries.filter(function(s){ return maxAbs(s.data) > 0; });
    if (!allSeries.length) { inst.clear(); return; }

    // Step 2: Sort by scale descending so the biggest owns the left axis
    allSeries.sort(function(a,b){ return maxAbs(b.data) - maxAbs(a.data); });

    // Step 3: Assign to axes. The first series always goes on left axis.
    // Every subsequent series: if its scale does NOT diverge from the left axis
    // by more than 5× (per scalesDivergent), it stays on left.
    // Else goes on right axis (one right axis max). If a third
    // series would need a third axis, drop it with a tooltip warning.
    var leftSeries = [allSeries[0]];
    var rightSeries = [];
    for (var i=1; i<allSeries.length && leftSeries.length + rightSeries.length < 3; i++){
      var s = allSeries[i];
      if (!scalesDivergent(allSeries[0].data, s.data)) {
        leftSeries.push(s);
      } else if (!rightSeries.length) {
        rightSeries.push(s);
      } else {
        // Would need a 3rd axis — skip. (Better UX to omit than mix scales.)
      }
    }

    var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    var colors = paletteSeq(pal);
    var highlightColor = pal.highlight;

    // For line/bar charts: highlight last point by color-splitting the first series.
    // In horizontal mode, the value axis is X, so dual axes use xAxisIndex;
    // in vertical/line mode, value axis is Y, so dual axes use yAxisIndex.
    function buildSeries(group, axisIndex, useColorIdx){
      return group.map(function(s, i){
        var color = colors[(useColorIdx + i) % colors.length];
        var base = {
          name: s.col,
          data: s.data,
          emphasis: {focus:'series'},
        };
        if (isHorizontal) { base.xAxisIndex = axisIndex; }
        else { base.yAxisIndex = axisIndex; }
        if (dateLike) {
          // Line chart, highlight last point. Forced 'area' (or a card that
          // just naturally has one date-keyed series) gets a soft gradient fill.
          var wantArea = isForcedArea || group.length === 1;
          return Object.assign(base, {
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: {width: 2.5, color: color},
            itemStyle: {color: color},
            areaStyle: wantArea ? {opacity: isForcedArea ? 0.28 : 0.10, color: gradientFill(color)} : undefined,
            data: withHighlightLast(s.data, color, highlightColor),
          });
        }
        var stackName = forcedType === 'stacked_bar' ? 'total' : undefined;
        if (isHorizontal) {
          return Object.assign(base, {
            type: 'bar',
            barMaxWidth: 28,
            stack: stackName,
            itemStyle: {color: color, borderRadius: [0, 6, 6, 0]},
          });
        }
        var isSingleBar = group.length === 1 && !stackName;
        return Object.assign(base, {
          type: 'bar',
          barMaxWidth: 28,
          stack: stackName,
          itemStyle: {color: isSingleBar ? pal.bar : color, borderRadius: [6, 6, 0, 0]},
          // Single-series: tan bars with the most-recent period highlighted terracotta.
          data: isSingleBar ? withHighlightLast(s.data, pal.bar, highlightColor) : s.data,
        });
      });
    }

    var seriesAll = buildSeries(leftSeries, 0, 0).concat(buildSeries(rightSeries, 1, leftSeries.length));

    // Compare mode: overlay the comparison period per charted column. Line charts
    // get a faded dashed line; bar charts get a paired lighter bar (grouped).
    if (snap.compare && snap.compare_series) {
      var cmpColor = isDark ? '#6b7280' : '#cbd5e1';
      leftSeries.concat(rightSeries).forEach(function(s){
        var prev = snap.compare_series[s.col];
        if (!prev || !prev.length) return;
        var base = {name: s.col + ' (prev)', data: prev, emphasis: {focus: 'series'}};
        if (isHorizontal) { base.xAxisIndex = 0; } else { base.yAxisIndex = 0; }
        if (dateLike) {
          seriesAll.push(Object.assign(base, {
            type: 'line', smooth: true, symbol: 'none',
            lineStyle: {width: 2, type: 'dashed', color: cmpColor},
            itemStyle: {color: cmpColor},
          }));
        } else {
          seriesAll.push(Object.assign(base, {
            type: 'bar', barMaxWidth: 28,
            itemStyle: {color: cmpColor, borderRadius: isHorizontal ? [0,2,2,0] : [2,2,0,0]},
          }));
        }
      });
    }

    var leftFmt = getColFmt(leftSeries[0].col);
    var rightFmt = rightSeries.length ? getColFmt(rightSeries[0].col) : null;
    // Build col→formatter map for tooltip so each series value shows with its own unit
    var _fmtMap = {};
    leftSeries.concat(rightSeries).forEach(function(s){ _fmtMap[s.col] = getColFmt(s.col); });
    var tooltipFmt = function(params) {
      if (!params || !params.length) return '';
      var lines = ['<div style="font-size:12px;margin-bottom:2px;font-weight:600;">' + (F.esc ? F.esc(params[0].axisValue) : params[0].axisValue) + '</div>'];
      params.forEach(function(p) {
        var fmt = _fmtMap[p.seriesName] || (F.fmtNum || function(v){ return v; });
        lines.push(p.marker + ' ' + (F.esc ? F.esc(p.seriesName) : p.seriesName) + ': <strong>' + fmt(p.value) + '</strong>');
      });
      return lines.join('<br/>');
    };

    var yAxes = [{
      type: 'value',
      axisLabel: {color: axisColor, fontSize: 10, formatter: leftFmt},
      splitLine: {lineStyle: {color: gridColor}},
      axisLine: {show: false},
    }];
    if (rightSeries.length) {
      yAxes.push({
        type: 'value',
        position: 'right',
        axisLabel: {color: axisColor, fontSize: 10, formatter: rightFmt},
        splitLine: {show: false},
        axisLine: {show: false},
      });
    }

    var option;
    if (isHorizontal) {
      // Horizontal: value axis is X. Build dual xAxis if rightSeries present.
      var xAxes = [{
        type: 'value',
        axisLabel: {color: axisColor, fontSize: 10, formatter: leftFmt},
        splitLine: {lineStyle: {color: gridColor}},
        axisLine: {show: false},
      }];
      if (rightSeries.length) {
        xAxes.push({
          type: 'value',
          position: 'top',
          axisLabel: {color: axisColor, fontSize: 10, formatter: rightFmt},
          splitLine: {show: false},
          axisLine: {show: false},
        });
      }
      option = {
        grid: {top: rightSeries.length ? 46 : 30, right: 30, bottom: 30, left: 10, containLabel: true},
        tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: tooltipFmt},
        legend: {top: 0, left: 0, textStyle: {color: axisColor, fontSize: 11}, icon: 'roundRect'},
        yAxis: {type: 'category', data: labels, axisLabel: {color: axisColor, fontSize: 11}, axisLine: {lineStyle: {color: gridColor}}, inverse: true},
        xAxis: xAxes,
        series: seriesAll,
      };
    } else {
      option = {
        grid: {top: 30, right: rightSeries.length ? 52 : 14, bottom: 30, left: 48, containLabel: true},
        tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: tooltipFmt},
        legend: {top: 0, left: 0, textStyle: {color: axisColor, fontSize: 11}, icon: 'roundRect'},
        xAxis: {type: 'category', data: labels, axisLabel: {color: axisColor, fontSize: 10, rotate: labels.length > 8 ? 35 : 0}, axisLine: {lineStyle: {color: gridColor}}},
        yAxis: yAxes,
        series: seriesAll,
      };
    }
    inst.setOption(option, true);
  }

  // ── Explicit chart_config path ─────────────────────────────────────────
  // chart_config shape:
  //   {
  //     type: "bar" | "hbar" | "line" | "area" | "stacked_bar" | "pie" | "donut",
  //     x:    "column_name",                 // required for non-pie
  //     series: [
  //       {col: "sessions", label?: "Sessions", color?: "primary"|"#hex", axis?: "left"|"right", stack?: "total"},
  //       ...
  //     ],
  //     highlight_last?: true,              // highlight most recent data point
  //     orientation?: "horizontal",         // forces hbar
  //     smooth?: true,                      // line charts
  //     // combo only: per-series override of the mark type ("bar" | "line"),
  //     // field name matches SeriesSpec.kind in app/dashboards/chart_spec.py
  //     series: [{col, kind: "bar"|"line", ...}, ...]
  //   }
  function configuredChart(inst, card, cfg, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var rows = snap.rows || [];
    if (!rows.length || !cfg.series || !cfg.series.length) { inst.clear(); return; }

    var type = (cfg.type || 'bar').toLowerCase();
    var isPie = (type === 'pie' || type === 'donut');
    var isHorizontal = (type === 'hbar' || cfg.orientation === 'horizontal');
    var isCombo = (type === 'combo');
    var isLine = (type === 'line' || type === 'area');
    var isStacked = (type === 'stacked_bar');
    var isArea = (type === 'area');

    if (isPie) {
      var labelColP = cfg.x || (snap.columns||[])[0];
      var valueColP = cfg.series[0].col;
      var data = rows.map(function(r){ return {name: String(r[labelColP]), value: toNum(r[valueColP])}; });
      inst.setOption({
        tooltip: {trigger: 'item'},
        legend: {bottom: 0, textStyle: {color: axisColor, fontSize: 11}},
        series: [{
          type: 'pie',
          radius: type === 'donut' ? ['45%', '70%'] : '70%',
          data: data,
          label: {color: axisColor, fontSize: 11},
          itemStyle: {borderColor: 'var(--surface)', borderWidth: 2},
        }],
        color: paletteSeq(pal),
      }, true);
      return;
    }

    // Resolve x-column: honour cfg.x only if it actually exists in rows; otherwise
    // fall back to the first non-numeric column so the chart never shows "undefined".
    var _snapCols = snap.columns || [];
    var _seriesCols = cfg.series.map(function(s){ return s.col; });
    var xCol = cfg.x;
    if (!xCol || (rows.length && rows[0][xCol] === undefined && _snapCols.indexOf(xCol) === -1)) {
      // cfg.x doesn't exist — pick first column that isn't a series column
      xCol = _snapCols.find(function(c){ return _seriesCols.indexOf(c) === -1; }) || _snapCols[0];
    }
    var xSamples = rows.slice(0, Math.min(5, rows.length)).map(function(r){ return String(r[xCol] == null ? '' : r[xCol]); });
    var xDateLike = xSamples.length > 0 && xSamples.every(looksLikeDate);
    var xMonthNum = !xDateLike && isMonthNumCol(xCol);
    var sortedRows = rows;
    if (xDateLike || xMonthNum) {
      sortedRows = rows.slice().sort(function(a, b) {
        var la = String(a[xCol] == null ? '' : a[xCol]);
        var lb = String(b[xCol] == null ? '' : b[xCol]);
        if (xMonthNum) return parseInt(la, 10) - parseInt(lb, 10);
        return la < lb ? -1 : la > lb ? 1 : 0;
      });
    }
    var rawXLabels = sortedRows.map(function(r){ return String(r[xCol] == null ? '' : r[xCol]); });
    var labels = xDateLike ? rawXLabels.map(F.formatDateLabel) : (xMonthNum ? rawXLabels.map(fmtMonthLabel) : rawXLabels);
    var hasRight = cfg.series.some(function(s){ return s.axis === 'right'; });
    var highlightLast = cfg.highlight_last !== false; // default true

    // Build col→formatter map for tooltip and axes
    var _fmtMapC = {};
    cfg.series.forEach(function(s){ _fmtMapC[s.label || s.col] = getColFmt(s.col); });
    var tooltipFmtC = function(params) {
      if (!params || !params.length) return '';
      var lines = ['<div style="font-size:12px;margin-bottom:2px;font-weight:600;">' + (F.esc ? F.esc(params[0].axisValue) : params[0].axisValue) + '</div>'];
      params.forEach(function(p) {
        var fmt = _fmtMapC[p.seriesName] || (F.fmtNum || function(v){ return v; });
        lines.push(p.marker + ' ' + (F.esc ? F.esc(p.seriesName) : p.seriesName) + ': <strong>' + fmt(p.value) + '</strong>');
      });
      return lines.join('<br/>');
    };
    var leftColC = (cfg.series.find(function(s){ return s.axis !== 'right'; }) || cfg.series[0]).col;
    var rightColC = hasRight ? ((cfg.series.find(function(s){ return s.axis === 'right'; }) || {col: null}).col) : null;

    var seriesSpec = cfg.series.map(function(s, i){
      var color = resolveColor(s.color, pal) || paletteSeq(pal)[i % 7];
      var raw = sortedRows.map(function(r){ return toNum(r[s.col] === undefined ? null : r[s.col]); });
      var axisIdx = s.axis === 'right' ? 1 : 0;
      var base = {
        name: s.label || s.col,
      };
      // In horizontal mode the value axis is X, so dual axes use xAxisIndex.
      if (isHorizontal) { base.xAxisIndex = axisIdx; }
      else { base.yAxisIndex = axisIdx; }
      // Combo: each series picks its own mark type via `kind` (matches
      // SeriesSpec.kind in chart_spec.py — "bar"|"line"), defaulting to
      // 'line' for the first series and 'bar' for the rest so "revenue
      // (line) vs orders (bar)" works with zero extra config beyond
      // `type: "combo"`.
      var markType = isCombo ? (s.kind || (i === 0 ? 'line' : 'bar')) : (isLine ? 'line' : 'bar');
      if (markType === 'line') {
        var wantArea = isArea;
        return Object.assign(base, {
          type: 'line',
          smooth: cfg.smooth !== false,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: {width: 2.5, color: color},
          itemStyle: {color: color},
          areaStyle: wantArea ? {opacity: 0.20, color: gradientFill(color)} : undefined,
          data: (highlightLast && cfg.series.length === 1) ? withHighlightLast(raw, color, pal.highlight) : raw,
        });
      }
      var singleBar = (cfg.series.length === 1 && !isStacked);
      var barOpts = {
        type: 'bar',
        barMaxWidth: 28,
        itemStyle: {color: singleBar ? pal.bar : color, borderRadius: isHorizontal ? [0,6,6,0] : [6,6,0,0]},
      };
      if (isStacked) barOpts.stack = s.stack || 'total';
      var data = (highlightLast && singleBar)
        ? withHighlightLast(raw, pal.bar, pal.highlight)
        : raw;
      return Object.assign(base, barOpts, {data: data});
    });

    var yAxes = [{
      type: 'value',
      axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(leftColC)},
      splitLine: {lineStyle: {color: gridColor}},
      axisLine: {show: false},
    }];
    if (hasRight) {
      yAxes.push({
        type: 'value',
        position: 'right',
        axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(rightColC)},
        splitLine: {show: false},
        axisLine: {show: false},
      });
    }

    var option;
    if (isHorizontal) {
      // In horizontal layout, yAxes (built above for "right") are not used as Y;
      // we translate "axis:right" into dual xAxis instead.
      var xAxesH = [{
        type: 'value',
        axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(leftColC)},
        splitLine: {lineStyle: {color: gridColor}},
        axisLine: {show: false},
      }];
      if (hasRight) {
        xAxesH.push({
          type: 'value',
          position: 'top',
          axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(rightColC)},
          splitLine: {show: false},
          axisLine: {show: false},
        });
      }
      option = {
        grid: {top: hasRight ? 46 : 30, right: 30, bottom: 30, left: 10, containLabel: true},
        tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: tooltipFmtC},
        legend: {top: 0, left: 0, textStyle: {color: axisColor, fontSize: 11}, icon: 'roundRect'},
        yAxis: {type: 'category', data: labels, axisLabel: {color: axisColor, fontSize: 11}, axisLine: {lineStyle: {color: gridColor}}, inverse: true},
        xAxis: xAxesH,
        series: seriesSpec,
      };
    } else {
      option = {
        grid: {top: 30, right: hasRight ? 52 : 14, bottom: 30, left: 48, containLabel: true},
        tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: tooltipFmtC},
        legend: {top: 0, left: 0, textStyle: {color: axisColor, fontSize: 11}, icon: 'roundRect'},
        xAxis: {type: 'category', data: labels, axisLabel: {color: axisColor, fontSize: 10, rotate: labels.length > 8 ? 35 : 0}, axisLine: {lineStyle: {color: gridColor}}},
        yAxis: yAxes,
        series: seriesSpec,
      };
    }
    inst.setOption(option, true);
  }

  // ── New first-class chart types (dashboard revamp Phase 1) ──────────────
  // Each builder degrades to autoTableChart (the same fallback unknown/
  // ill-shaped cards use today) when the row/column shape doesn't fit the
  // chart's data contract, instead of rendering blank or throwing.
  //
  // Column-shape contracts — field names match the Pydantic models in
  // app/dashboards/chart_spec.py (ScatterConfig, HeatmapConfig, FunnelConfig,
  // TreemapConfig, RadarConfig, GaugeConfig, WaterfallConfig) so the backend
  // schema and this renderer agree on the wire shape of chart_config:
  //   scatter:   chart_config.x_col (numeric), .y_col (numeric), optional
  //              .size_col (numeric, bubble radius). An unmodeled .group
  //              (categorical col, extra="allow") splits into colored series.
  //              No config: first 2 numeric columns become x_col/y_col.
  //   heatmap:   chart_config.x_col (categorical), .y_col (categorical),
  //              .value_col (numeric).
  //              No config: first 2 non-numeric columns + first numeric column.
  //   funnel:    chart_config.stage_col (label), .value_col (numeric).
  //              No config: first non-numeric col + first numeric col.
  //   treemap:   chart_config.label_col (name), .value_col (numeric). Flat
  //              {name, value} pairs only in this phase — .parent_col
  //              (nested hierarchy) is schema-reserved but not yet rendered
  //              here (falls back to flat); see followups.
  //              No config: first non-numeric col + first numeric col.
  //   radar:     chart_config.label_col (entity/name — one polygon per row),
  //              .value_cols (list of metric column names, the indicators, >=3).
  //              No config: first non-numeric col as name, up to 6 remaining
  //              numeric cols as indicators (needs >=3).
  //   gauge:     chart_config.value_col (numeric), optional .min/.max
  //              (default 0/100), .target (marker — not yet drawn, see followups).
  //              No config: snap.metrics[0].value, else first numeric cell.
  //   waterfall: chart_config.label_col (step label), .delta_col (signed
  //              numeric change per step). Rows are treated as sequential
  //              deltas from a zero baseline.
  //   combo:     (via configuredChart) chart_config.series[].kind ("bar"|
  //              "line", per SeriesSpec.kind) — defaults to "line" for the
  //              first series and "bar" for the rest when omitted.

  function pickXY(cols, rows, numericCols){
    // Prefer the two numeric columns whose names don't look like an id/date.
    var candidates = numericCols.filter(function(c){ return !looksLikeDate(String((rows[0]||{})[c])) && !/^id$/i.test(c); });
    return candidates.length >= 2 ? candidates.slice(0, 2) : numericCols.slice(0, 2);
  }

  function buildScatterOption(inst, card, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    if (!rows.length) { inst.clear(); return; }
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
    var xCol = cfg.x_col, yCol = cfg.y_col;
    if (!xCol || !yCol || numericCols.indexOf(xCol) === -1 || numericCols.indexOf(yCol) === -1) {
      var xy = pickXY(cols, rows, numericCols);
      xCol = xy[0]; yCol = xy[1];
    }
    if (!xCol || !yCol) { autoTableChart(inst, card, axisColor, gridColor, pal); return; }
    var sizeCol = cfg.size_col && numericCols.indexOf(cfg.size_col) !== -1 ? cfg.size_col : null;
    // Not part of the formal ScatterConfig schema, but ChartConfigBase allows
    // extra fields — an optional categorical `group` column splits the plot
    // into multiple colored series (e.g. one color per channel).
    var groupCol = cfg.group && cols.indexOf(cfg.group) !== -1 ? cfg.group : null;
    var colors = paletteSeq(pal);
    var groups = groupCol ? Array.from(new Set(rows.map(function(r){ return String(r[groupCol]); }))).slice(0, 7) : [null];
    var maxSize = sizeCol ? Math.max.apply(null, rows.map(function(r){ return toNum(r[sizeCol]); })) || 1 : 1;
    var series = groups.map(function(g, i){
      var groupRows = g === null ? rows : rows.filter(function(r){ return String(r[groupCol]) === g; });
      return {
        name: g === null ? (yCol) : g,
        type: 'scatter',
        symbolSize: sizeCol ? function(val){ return 8 + (val[2] / maxSize) * 22; } : 10,
        itemStyle: {color: colors[i % colors.length], opacity: 0.75},
        data: groupRows.map(function(r){ return [toNum(r[xCol]), toNum(r[yCol]), sizeCol ? toNum(r[sizeCol]) : null]; }),
      };
    });
    inst.setOption({
      grid: {top: groupCol ? 40 : 20, right: 20, bottom: 36, left: 50, containLabel: true},
      tooltip: {trigger: 'item', formatter: function(p){
        var fmtX = getColFmt(xCol), fmtY = getColFmt(yCol);
        return (F.esc ? F.esc(p.seriesName) : p.seriesName) + '<br/>' + xCol + ': ' + fmtX(p.value[0]) + '<br/>' + yCol + ': ' + fmtY(p.value[1]);
      }},
      legend: groupCol ? {top: 0, left: 0, textStyle: {color: axisColor, fontSize: 11}} : undefined,
      xAxis: {type: 'value', name: xCol, nameTextStyle: {color: axisColor, fontSize: 10}, axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(xCol)}, splitLine: {lineStyle: {color: gridColor}}, axisLine: {show: false}},
      yAxis: {type: 'value', name: yCol, nameTextStyle: {color: axisColor, fontSize: 10}, axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(yCol)}, splitLine: {lineStyle: {color: gridColor}}, axisLine: {show: false}},
      series: series,
    }, true);
  }

  function buildHeatmapOption(inst, card, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    if (!rows.length) { inst.clear(); return; }
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
    var catCols = cols.filter(function(c){ return numericCols.indexOf(c) === -1; });
    var xCol = cfg.x_col && catCols.indexOf(cfg.x_col) !== -1 ? cfg.x_col : catCols[0];
    var yCol = cfg.y_col && catCols.indexOf(cfg.y_col) !== -1 && cfg.y_col !== xCol ? cfg.y_col : catCols.filter(function(c){ return c !== xCol; })[0];
    var valCol = (cfg.value_col && numericCols.indexOf(cfg.value_col) !== -1) ? cfg.value_col : numericCols[0];
    if (!xCol || !yCol || !valCol) { autoTableChart(inst, card, axisColor, gridColor, pal); return; }

    var xCats = Array.from(new Set(rows.map(function(r){ return String(r[xCol]); }))).slice(0, 40);
    var yCats = Array.from(new Set(rows.map(function(r){ return String(r[yCol]); }))).slice(0, 40);
    var data = [];
    var maxV = 0;
    rows.forEach(function(r){
      var xi = xCats.indexOf(String(r[xCol])), yi = yCats.indexOf(String(r[yCol]));
      if (xi === -1 || yi === -1) return;
      var v = toNum(r[valCol]);
      maxV = Math.max(maxV, v);
      data.push([xi, yi, v]);
    });
    var seq = paletteSeq(pal);
    inst.setOption({
      grid: {top: 20, right: 20, bottom: 60, left: 90, containLabel: true},
      tooltip: {position: 'top', formatter: function(p){
        return xCats[p.value[0]] + ' / ' + yCats[p.value[1]] + ': <strong>' + (F.fmtNum ? F.fmtNum(p.value[2]) : p.value[2]) + '</strong>';
      }},
      xAxis: {type: 'category', data: xCats, axisLabel: {color: axisColor, fontSize: 10, rotate: 35}, splitArea: {show: true}},
      yAxis: {type: 'category', data: yCats, axisLabel: {color: axisColor, fontSize: 10}, splitArea: {show: true}},
      visualMap: {
        min: 0, max: maxV || 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
        textStyle: {color: axisColor, fontSize: 10},
        inRange: {color: [gridColor, seq[0], pal.neg]},
      },
      series: [{type: 'heatmap', data: data, itemStyle: {borderColor: 'var(--surface)', borderWidth: 1}, emphasis: {itemStyle: {shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.4)'}}}],
    }, true);
  }

  function buildFunnelOption(inst, card, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    if (!rows.length) { inst.clear(); return; }
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
    var labelCol = (cfg.stage_col && cols.indexOf(cfg.stage_col) !== -1) ? cfg.stage_col : cols.find(function(c){ return numericCols.indexOf(c) === -1; });
    var valCol = (cfg.value_col && numericCols.indexOf(cfg.value_col) !== -1) ? cfg.value_col : numericCols[0];
    if (!labelCol || !valCol) { autoTableChart(inst, card, axisColor, gridColor, pal); return; }
    var data = rows.map(function(r){ return {name: String(r[labelCol] == null ? '' : r[labelCol]), value: toNum(r[valCol])}; })
      .sort(function(a, b){ return b.value - a.value; })
      .slice(0, 12);
    inst.setOption({
      tooltip: {trigger: 'item', formatter: function(p){ return p.name + ': ' + (F.fmtNum ? F.fmtNum(p.value) : p.value) + ' (' + p.percent + '%)'; }},
      legend: {bottom: 0, textStyle: {color: axisColor, fontSize: 11}},
      color: paletteSeq(pal),
      series: [{
        type: 'funnel', left: '6%', right: '6%', top: 10, bottom: 40,
        sort: 'descending', gap: 2, minSize: '10%', maxSize: '100%',
        label: {color: axisColor, fontSize: 11, formatter: '{b}: {c}'},
        itemStyle: {borderColor: 'var(--surface)', borderWidth: 1},
        data: data,
      }],
    }, true);
  }

  function buildTreemapOption(inst, card, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    if (!rows.length) { inst.clear(); return; }
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
    var labelCol = (cfg.label_col && cols.indexOf(cfg.label_col) !== -1) ? cfg.label_col : cols.find(function(c){ return numericCols.indexOf(c) === -1; });
    var valCol = (cfg.value_col && numericCols.indexOf(cfg.value_col) !== -1) ? cfg.value_col : numericCols[0];
    if (!labelCol || !valCol) { autoTableChart(inst, card, axisColor, gridColor, pal); return; }
    // NOTE: cfg.parent_col (nested hierarchy) is reserved in the schema but
    // not yet rendered here — this phase only draws a flat {name, value}
    // treemap (see followups).
    var colors = paletteSeq(pal);
    var data = rows.map(function(r, i){
      return {name: String(r[labelCol] == null ? '' : r[labelCol]), value: toNum(r[valCol]), itemStyle: {color: colors[i % colors.length]}};
    });
    inst.setOption({
      tooltip: {formatter: function(p){ return p.name + ': ' + (F.fmtNum ? F.fmtNum(p.value) : p.value); }},
      series: [{
        type: 'treemap', roam: false, nodeClick: false,
        breadcrumb: {show: false},
        label: {color: '#fff', fontSize: 11, formatter: '{b}\n{c}'},
        upperLabel: {show: false},
        itemStyle: {borderColor: 'var(--surface)', borderWidth: 2, gapWidth: 2},
        data: data,
      }],
    }, true);
  }

  function buildRadarOption(inst, card, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    if (!rows.length) { inst.clear(); return; }
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
    var nameCol = (cfg.label_col && cols.indexOf(cfg.label_col) !== -1) ? cfg.label_col : cols.find(function(c){ return numericCols.indexOf(c) === -1; });
    var metricCols = (cfg.value_cols && cfg.value_cols.length)
      ? cfg.value_cols.filter(function(c){ return numericCols.indexOf(c) !== -1; })
      : numericCols.slice(0, 6);
    if (!nameCol || metricCols.length < 3) { autoTableChart(inst, card, axisColor, gridColor, pal); return; }
    var indicator = metricCols.map(function(c){
      var max = Math.max.apply(null, rows.map(function(r){ return toNum(r[c]); })) || 1;
      return {name: c, max: max * 1.15};
    });
    var colors = paletteSeq(pal);
    var data = rows.slice(0, 7).map(function(r, i){
      return {
        name: String(r[nameCol] == null ? '' : r[nameCol]),
        value: metricCols.map(function(c){ return toNum(r[c]); }),
        itemStyle: {color: colors[i % colors.length]},
        areaStyle: {opacity: 0.12, color: colors[i % colors.length]},
      };
    });
    inst.setOption({
      tooltip: {trigger: 'item'},
      legend: {bottom: 0, textStyle: {color: axisColor, fontSize: 11}},
      radar: {
        indicator: indicator,
        axisName: {color: axisColor, fontSize: 10.5},
        splitLine: {lineStyle: {color: gridColor, type: 'dashed'}},
        splitArea: {show: false},
        axisLine: {lineStyle: {color: gridColor}},
      },
      series: [{type: 'radar', data: data}],
    }, true);
  }

  // Generalized gauge (audit score gauge, generalized to any 0..max metric).
  function buildGaugeOption(card, axisColor, gridColor, pal, opts){
    opts = opts || {};
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    var min = opts.min != null ? opts.min : (cfg.min != null ? cfg.min : 0);
    var max = opts.max != null ? opts.max : (cfg.max != null ? cfg.max : 100);
    var target = opts.target != null ? opts.target : cfg.target;
    var value = opts.value;
    if (value == null) {
      var valCol = cfg.value_col;
      if (valCol && rows.length) value = toNum(rows[0][valCol]);
      else if (snap.metrics && snap.metrics.length) value = toNum(snap.metrics[0].value);
      else {
        var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
        value = (numericCols.length && rows.length) ? toNum(rows[0][numericCols[0]]) : 0;
      }
    }
    var range = max - min || 1;
    var pct = (value - min) / range;
    var color = pct >= 0.7 ? (pal.secondary || '#10b981') : (pct >= 0.4 ? (pal.accent || '#f59e0b') : (pal.neg || '#ef4444'));
    var fmt = function(v){ return F.fmtNum ? F.fmtNum(v) : v; };
    // .target (schema-reserved) doesn't get its own needle/tick in this phase
    // — it's surfaced as a "/ target" suffix on the detail label instead of a
    // full marker, which is simple and still legible at KPI-tile sizes.
    var detailText = target != null ? fmt(value) + ' / ' + fmt(target) : undefined;
    return {
      series: [{
        type: 'gauge', startAngle: 180, endAngle: 0, min: min, max: max,
        progress: {show: true, width: 14, itemStyle: {color: color}},
        axisLine: {lineStyle: {width: 14, color: [[1, gridColor]]}},
        axisTick: {show: false}, splitLine: {show: false}, axisLabel: {show: false}, pointer: {show: false},
        detail: {
          valueAnimation: true, fontSize: target != null ? 16 : 22, fontWeight: 700,
          offsetCenter: [0, '-5%'], color: color,
          formatter: detailText != null ? function(){ return detailText; } : fmt,
        },
        data: [{value: value}],
      }],
    };
  }

  function buildWaterfallOption(inst, card, axisColor, gridColor, pal){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    if (!rows.length) { inst.clear(); return; }
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : [];
    var labelCol = (cfg.label_col && cols.indexOf(cfg.label_col) !== -1) ? cfg.label_col : cols.find(function(c){ return numericCols.indexOf(c) === -1; });
    var valCol = (cfg.delta_col && numericCols.indexOf(cfg.delta_col) !== -1) ? cfg.delta_col : numericCols[0];
    if (!labelCol || !valCol) { autoTableChart(inst, card, axisColor, gridColor, pal); return; }

    var labels = rows.map(function(r){ return String(r[labelCol] == null ? '' : r[labelCol]); });
    var deltas = rows.map(function(r){ return toNum(r[valCol]); });
    // Transparent "base" series carries the running total up to each bar so
    // the visible delta bar floats at the right height (the classic ECharts
    // stacked-bar waterfall trick).
    var running = 0;
    var base = [];
    var pos = [];
    var neg = [];
    deltas.forEach(function(d){
      if (d >= 0) { base.push(running); pos.push(d); neg.push(0); running += d; }
      else { running += d; base.push(running); pos.push(0); neg.push(-d); }
    });
    inst.setOption({
      grid: {top: 20, right: 14, bottom: 30, left: 48, containLabel: true},
      tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: function(params){
        var real = deltas[params[0].dataIndex];
        return (F.esc ? F.esc(labels[params[0].dataIndex]) : labels[params[0].dataIndex]) + ': <strong>' + (F.fmtNum ? F.fmtNum(real) : real) + '</strong>';
      }},
      xAxis: {type: 'category', data: labels, axisLabel: {color: axisColor, fontSize: 10, rotate: labels.length > 8 ? 35 : 0}, axisLine: {lineStyle: {color: gridColor}}},
      yAxis: {type: 'value', axisLabel: {color: axisColor, fontSize: 10, formatter: getColFmt(valCol)}, splitLine: {lineStyle: {color: gridColor}}, axisLine: {show: false}},
      series: [
        {name: '_base', type: 'bar', stack: 'wf', itemStyle: {color: 'transparent'}, emphasis: {itemStyle: {color: 'transparent'}}, silent: true, data: base},
        {name: 'Increase', type: 'bar', stack: 'wf', barMaxWidth: 32, itemStyle: {color: pal.secondary, borderRadius: [6,6,0,0]}, data: pos},
        {name: 'Decrease', type: 'bar', stack: 'wf', barMaxWidth: 32, itemStyle: {color: pal.neg, borderRadius: [6,6,0,0]}, data: neg},
      ],
    }, true);
  }

  // ── Pie chart builder ──────────────────────────────────────────────────
  // Builds an ECharts option for a pie/donut chart from snap data.
  // Pastel Pop multi-color palette matches the card_renderer_js.html renderPieChart.
  function buildPieOption(card, axisColor){
    var snap = card.snap || {};
    var cfg = snap.chart_config || {};
    var rows = snap.rows || [];
    var cols = snap.columns || [];
    var isDonut = cfg.donut === true || (cfg.type || '').toLowerCase() === 'donut' || snap.chart_type === 'donut';
    var showLegend = cfg.show_legend !== false;

    // Determine label column (first non-numeric or explicit x) and value column
    var numericCols = F.numericColsOf ? F.numericColsOf(cols, rows) : cols.filter(function(c){
      return rows.slice(0,5).every(function(r){ var v = String(r[c]||'').replace(/,/g,''); return v && !isNaN(parseFloat(v)); });
    });
    var labelCol = cfg.x || cols.find(function(c){ return numericCols.indexOf(c) === -1; }) || cols[0];
    var valCol = (cfg.series && cfg.series[0] && cfg.series[0].col) || numericCols[0] || cols[1] || cols[0];

    var data = rows.map(function(r){
      return {name: String(r[labelCol] == null ? '' : r[labelCol]), value: toNum(r[valCol])};
    });

    return {
      tooltip: {trigger: 'item', formatter: function(p){ return p.name + ': ' + (F.fmtNum ? F.fmtNum(p.value) : p.value) + ' (' + p.percent + '%)'; }},
      legend: showLegend ? {bottom: 0, textStyle: {color: axisColor, fontSize: 11}} : {show: false},
      color: paletteSeq(palette(document.documentElement.getAttribute('data-theme') === 'dark')),
      series: [{
        type: 'pie',
        radius: isDonut ? ['45%','70%'] : '70%',
        data: data,
        label: {color: axisColor, fontSize: 11},
        itemStyle: {borderColor: 'var(--surface)', borderWidth: 2},
        emphasis: {itemStyle: {shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)'}},
      }],
    };
  }

  F.buildPieOption = buildPieOption;
  F.buildGaugeOption = buildGaugeOption;

  // ── Entry point ────────────────────────────────────────────────────────
  F.mountCharts = function(cards, gridEl){
    Object.values(_instances).forEach(function(c){ try { c.dispose(); } catch(e){} });
    _instances = {};
    if (typeof echarts === 'undefined') {
      // Store the latest args so the poll always uses the most-recent call.
      F.mountCharts._pending = {cards: cards, gridEl: gridEl};
      if (!F.mountCharts._pollActive) {
        F.mountCharts._pollActive = true;
        var wait = function(){
          if (typeof echarts !== 'undefined') {
            F.mountCharts._pollActive = false;
            var p = F.mountCharts._pending;
            F.mountCharts._pending = null;
            F.mountCharts(p.cards, p.gridEl);
          } else { setTimeout(wait, 50); }
        };
        setTimeout(wait, 50);
      }
      return;
    }

    var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    // Curated 'fluxito' theme (theme.js) — (re)registered for the current mode
    // on every mount so a dark/light toggle (which re-mounts all charts) picks
    // up the right palette/tooltip/typography. Falls back to the built-in
    // 'dark'/light default if theme.js hasn't loaded for some reason.
    if (F.registerFluxitoTheme) F.registerFluxitoTheme(isDark);
    var theme = F.registerFluxitoTheme ? 'fluxito' : (isDark ? 'dark' : null);
    var axisColor = isDark ? '#9ca3af' : '#6b7280';
    var gridColor = cssVar('--dv-grid', isDark ? '#2A2A30' : '#ECEFF1');
    var pal = palette(isDark);
    var root = gridEl || document;

    cards.forEach(function(card){
      var els = root.querySelectorAll('[data-card-id="' + card.id + '"] [data-chart]');
      els.forEach(function(el){
        var kind = el.getAttribute('data-chart');
        var inst = echarts.init(el, theme, {renderer: 'canvas'});

        // Cross-filtering: clicking a bar/point sets the matching dimension filter
        // (no-op when the dashboard has no filter for that dimension).
        if (window.dashCrossFilter && (kind === 'custom' || kind === 'table')) {
          inst.on('click', function(card){
            return function(params){
              var snap = card.snap || {};
              var cols = snap.columns || [];
              var numeric = F.numericColsOf ? F.numericColsOf(cols, snap.rows || []) : [];
              var labelCol = cols.find(function(c){ return numeric.indexOf(c) === -1; });
              if (labelCol && params && params.name != null) {
                window.dashCrossFilter(labelCol, String(params.name));
              }
            };
          }(card));
        }

        if (kind === 'pie') {
          // New-style pie card rendered by renderPieChart in card_renderer_js.html
          inst.setOption(buildPieOption(card, axisColor), true);
        } else if (kind === 'custom') {
          // Bar/line/area/stacked_bar/hbar card — use chart_config if it has
          // explicit series (legacy path, unchanged); otherwise auto-detect,
          // steered by the first-class chart_type when present (data-chart-layout).
          var customCfg = (card.snap||{}).chart_config || {};
          if (customCfg.series && customCfg.series.length) {
            configuredChart(inst, card, customCfg, axisColor, gridColor, pal);
          } else {
            autoTableChart(inst, card, axisColor, gridColor, pal, el.getAttribute('data-chart-layout'));
          }
        } else if (kind === 'table') {
          // Legacy TABLE card with auto-chart. If it has chart_config override, honor it.
          var cfg = (card.snap||{}).chart_config;
          if (cfg && cfg.series && cfg.series.length) {
            configuredChart(inst, card, cfg, axisColor, gridColor, pal);
          } else {
            autoTableChart(inst, card, axisColor, gridColor, pal);
          }
        } else if (kind === 'audit-gauge') {
          var score = parseInt(el.getAttribute('data-score'), 10) || 0;
          inst.setOption(buildGaugeOption(card, axisColor, gridColor, pal, {min: 0, max: 100, value: score}));
        } else if (kind === 'v2') {
          // New first-class chart types (dashboard revamp Phase 1). Each
          // builder self-degrades to autoTableChart on an ill-shaped card.
          var layout = el.getAttribute('data-chart-layout');
          switch (layout) {
            case 'scatter':  buildScatterOption(inst, card, axisColor, gridColor, pal); break;
            case 'heatmap':  buildHeatmapOption(inst, card, axisColor, gridColor, pal); break;
            case 'funnel':   buildFunnelOption(inst, card, axisColor, gridColor, pal); break;
            case 'treemap':  buildTreemapOption(inst, card, axisColor, gridColor, pal); break;
            case 'radar':    buildRadarOption(inst, card, axisColor, gridColor, pal); break;
            case 'waterfall':buildWaterfallOption(inst, card, axisColor, gridColor, pal); break;
            case 'gauge':    inst.setOption(buildGaugeOption(card, axisColor, gridColor, pal)); break;
            default:         autoTableChart(inst, card, axisColor, gridColor, pal); break;
          }
        }

        _instances[card.id + ':' + (el.getAttribute('data-chart')||'')] = inst;
      });
    });

    if (!F._resizeListenerRegistered) {
      F._resizeListenerRegistered = true;
      var _resizeTimer = null;
      window.addEventListener('resize', function(){
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(function(){
          Object.values(_instances).forEach(function(c){ try { c.resize(); } catch(e){} });
        }, 150);
      });
    }
  };

  // Expose the live instance map so callers (live_view.html's deterministic
  // PDF-ready signal) can attach one-shot 'finished' listeners after a mount
  // without reaching into this module's private state.
  F.getChartInstances = function(){ return _instances; };

  // Back-compat alias for older templates that call the bare mountCharts().
  window.mountCharts = F.mountCharts;
})();
