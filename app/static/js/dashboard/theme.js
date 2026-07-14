/* Curated ECharts theme — the "not Excel" visual pass.
   Requires:
     - ECharts loaded before this file (vendor/echarts-5.5.0.min.js)
     - Loaded after charts.js so it can reuse window.Fluxito.palette /
       paletteSeq / cssVar (falls back to local copies if charts.js hasn't
       run yet, so load order is not load-bearing).
   Provides:
     - window.Fluxito.registerFluxitoTheme(isDark) — (re)registers the
       'fluxito' ECharts theme for the given mode via echarts.registerTheme.
       Call this before echarts.init(el, 'fluxito', ...) any time the mode
       may have changed (mountCharts calls it on every mount, so a dark-mode
       toggle that re-mounts charts picks up the new palette automatically).

   Design: the --dv-c1..c7 categorical palette (same tokens the rest of the
   app uses) as series colors, soft dashed gridlines, no axis ticks, a dark
   translucent tooltip with per-series colored markers, and typography that
   matches the app font. Per-chart-type detail (rounded bar caps, gradient
   area fills, gauge colors, …) lives in the option builders in charts.js —
   this theme only carries the cross-cutting look.
*/
(function () {
  var F = (window.Fluxito = window.Fluxito || {});

  function cssVar(name, fallback) {
    if (F.cssVar) return F.cssVar(name, fallback);
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function palette(isDark) {
    if (F.palette) return F.palette(isDark);
    // Ledger palette (light-only) — kept in sync with charts.js's palette().
    return {
      primary: cssVar('--dv-c1', '#C4703A'),
      secondary: cssVar('--dv-c2', '#3E8A5F'),
      accent: cssVar('--dv-c3', '#C4903A'),
      neg: cssVar('--dv-c4', '#B4452F'),
      pink: cssVar('--dv-c5', '#201B14'),
      cyan: cssVar('--dv-c6', '#57503F'),
      orange: cssVar('--dv-c7', '#A89F8D'),
      neutral: cssVar('--dv-neutral', '#8A857C'),
      muted: '#A89F8D',
      bar: cssVar('--dv-bar', '#E8DFCC'),
      highlight: cssVar('--dv-accent', '#C4703A'),
    };
  }

  function paletteSeq(pal) {
    if (F.paletteSeq) return F.paletteSeq(pal);
    return [pal.primary, pal.secondary, pal.accent, pal.neg, pal.pink, pal.cyan, pal.orange];
  }

  // Build a full ECharts theme object for the given mode.
  function buildTheme(isDark) {
    var pal = palette(isDark);
    var seq = paletteSeq(pal);
    var axisColor = isDark ? '#9ca3af' : '#6b7280';
    var gridColor = cssVar('--dv-grid', isDark ? '#2A2A30' : '#ECEFF1');
    var fontFamily = cssVar('--font-sans', "'Inter Tight','Inter',system-ui,sans-serif");

    var axisCommon = {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: axisColor, fontSize: 10.5, fontFamily: fontFamily },
      splitLine: { lineStyle: { color: gridColor, type: 'dashed', opacity: 0.7 } },
    };

    return {
      color: seq,
      backgroundColor: 'transparent',
      textStyle: { fontFamily: fontFamily },
      title: { textStyle: { color: isDark ? '#e5e7eb' : '#111827', fontFamily: fontFamily } },
      categoryAxis: Object.assign({}, axisCommon, { splitLine: { show: false } }),
      valueAxis: axisCommon,
      // Radar/other "log"-style axes fall back to the same treatment.
      logAxis: axisCommon,
      timeAxis: axisCommon,
      line: { smooth: true, symbol: 'circle', symbolSize: 6 },
      bar: { barMaxWidth: 32 },
      legend: {
        textStyle: { color: axisColor, fontSize: 11, fontFamily: fontFamily },
        icon: 'roundRect',
      },
      tooltip: {
        backgroundColor: isDark ? 'rgba(24,24,29,0.94)' : 'rgba(17,24,39,0.92)',
        borderWidth: 0,
        padding: [8, 12],
        textStyle: { color: '#f3f4f6', fontSize: 12, fontFamily: fontFamily },
        extraCssText: 'box-shadow: 0 6px 20px rgba(0,0,0,0.25); border-radius: 8px;',
      },
      graph: { color: seq },
      // Applies to funnel/pie/treemap/gauge labels that don't hit an axis.
      textStyle_default: { color: axisColor },
    };
  }

  // (Re)register the 'fluxito' theme for the given mode. Safe to call
  // repeatedly — echarts.registerTheme just overwrites the prior definition.
  // Existing chart *instances* keep whatever theme they were init'd with;
  // callers that need a mode-correct repaint must dispose + re-init (which
  // is exactly what Fluxito.mountCharts already does on every call).
  F.registerFluxitoTheme = function (isDark) {
    if (typeof echarts === 'undefined' || !echarts.registerTheme) return;
    echarts.registerTheme('fluxito', buildTheme(isDark));
  };
})();
