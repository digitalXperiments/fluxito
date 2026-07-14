/* Shared JS helpers for rendering dashboard cards.
   Exports `window.Fluxito` with:
     - esc(s)                          — HTML-escape
     - fmtNum(v)                       — compact number formatter (K/M/B)
     - fmtMetricValue(value, unit)     — unit-aware formatter (%, duration, currency…)
     - fmtDelta(delta_pct, direction, inverse?) — renders a colored delta pill
     - renderCard(card)                — returns innerHTML for a card
   Load this BEFORE any view-specific JS that calls these helpers.

   Moved out of app/templates/partials/card_renderer_js.html (Phase 0 extraction,
   dashboard revamp) — the CSS for these renderers stays inline in that partial;
   this file is the JS only. Referenced by live_view.html and public.html via a
   plain (non-deferred) <script src> so load/execution order matches the old
   inline-script behavior exactly.

   Uses `IS_OWNER` if defined as a global by the including template (live_view.html
   / public.html set it before this script would need it — see renderCard's
   backward-compat branch below).
*/
(function(){
  var F = window.Fluxito = window.Fluxito || {};
  var ICONS = {'ga4':'📊','meta':'📘','tiktok':'🎵','snap':'👻','google':'🎯','gtm':'🏷️','bigquery':'🗄️'};

  // Pastel Pop palette map: scheme name → CSS variable prefix
  var PP_SCHEMES = ['blue','green','amber','pink','teal','purple','red'];

  F.esc = function(s){
    return String(s==null?'':s).replace(/[&<>"']/g, function(m){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m];
    });
  };

  // Compact number formatter (1.2K, 3.4M, 1.2B)
  F.fmtNum = function(v){
    if (v===null||v===undefined||v==='') return '';
    var n = Number(String(v).replace(/,/g,''));
    if (isNaN(n)) return F.esc(v);
    var sign = n < 0 ? '-' : '';
    var a = Math.abs(n);
    if (a >= 1e9) return sign + (a/1e9).toFixed(2).replace(/\.00$/,'') + 'B';
    if (a >= 1e6) return sign + (a/1e6).toFixed(2).replace(/\.00$/,'') + 'M';
    if (a >= 1e3) return sign + (a/1e3).toFixed(1).replace(/\.0$/,'') + 'K';
    if (Number.isInteger(n)) return n.toLocaleString();
    return n.toLocaleString(undefined, {maximumFractionDigits: 2});
  };

  // Unit-aware metric formatter. Supported units:
  //   percent | pct | %         → "37%"
  //   duration_sec | seconds    → "4m 33s" or "1h 2m"
  //   duration_ms               → same, ms input
  //   currency (+ currency_code) → "$1.2K"
  //   bytes                     → "12.4 MB"
  //   (null/undefined)          → fmtNum
  F.fmtMetricValue = function(value, unit, opts){
    if (value===null||value===undefined||value==='') return '';
    var n = Number(String(value).replace(/,/g,''));
    if (isNaN(n)) return F.esc(value);
    var u = String(unit||'').toLowerCase();
    if (u === 'percent' || u === 'pct' || u === '%') {
      // If value is already scaled 0-100, show with 1 decimal; if 0-1, scale up.
      var pct = Math.abs(n) <= 1 && n !== 0 ? n*100 : n;
      return (Math.round(pct*10)/10).toString().replace(/\.0$/,'') + '%';
    }
    if (u === 'duration' || u === 'duration_sec' || u === 'seconds' || u === 'sec') {
      return formatDuration(n);
    }
    if (u === 'duration_ms' || u === 'ms' || u === 'milliseconds') {
      return formatDuration(n/1000);
    }
    if (u === 'currency' || u === '$') {
      var sym = (opts && opts.currency_code) ? opts.currency_code + ' ' : '$';
      return sym + F.fmtNum(n);
    }
    if (u === 'bytes') {
      if (n >= 1e9) return (n/1e9).toFixed(2) + ' GB';
      if (n >= 1e6) return (n/1e6).toFixed(2) + ' MB';
      if (n >= 1e3) return (n/1e3).toFixed(1) + ' KB';
      return n + ' B';
    }
    return F.fmtNum(n);
  };

  function formatDuration(sec){
    if (sec === null || sec === undefined || isNaN(sec)) return '';
    sec = Math.round(sec);
    var sign = sec < 0 ? '-' : '';
    sec = Math.abs(sec);
    var h = Math.floor(sec/3600);
    var m = Math.floor((sec%3600)/60);
    var s = sec % 60;
    if (h) return sign + h + 'h ' + m + 'm';
    if (m) return sign + m + 'm ' + s + 's';
    return sign + s + 's';
  }

  // Delta pill for KPI tiles.
  // inverse=true means "lower is better" (bounce_rate, cpc, etc.) so a negative
  // delta is shown as positive/green.
  F.fmtDelta = function(delta_pct, direction, inverse){
    if (delta_pct === null || delta_pct === undefined) return '';
    var n = Number(delta_pct);
    if (isNaN(n)) return '';
    var isPos = n > 0, isNeg = n < 0;
    var good = inverse ? isNeg : isPos;
    var bad  = inverse ? isPos : isNeg;
    var cls = good ? 'pos' : (bad ? 'neg' : 'neu');
    var arrow = n > 0 ? '▲' : (n < 0 ? '▼' : '•');
    var pct = (Math.round(Math.abs(n)*10)/10).toString().replace(/\.0$/,'');
    var suffix = direction ? ' ' + F.esc(direction) : '';
    return '<span class="delta ' + cls + '">' + arrow + ' ' + pct + '%' + suffix + '</span>';
  };

  // Column-name heuristic: is this a derived metric (rate/ratio/share/pct/
  // change)? Used to SKIP these columns in auto charts when absolute peers
  // exist, so a % doesn't get mixed with raw counts on a shared axis.
  F.isDerivativeMetric = function(colName){
    if (!colName) return false;
    var n = String(colName).toLowerCase();
    return /(^|_)(pct|rate|ratio|share|index|delta|chg|change|growth|variance|diff)($|_)/.test(n)
        || /(yoy|mom|wow|qoq|pct_?chg|chg_?pct|_pct$|_rate$|_ratio$|_share$)/.test(n);
  };

  // Narrower rule: is this specifically a *period-over-period change* metric?
  // Only THESE get rendered as green/red delta pills in table cells.
  F.isChangeMetric = function(colName){
    if (!colName) return false;
    var n = String(colName).toLowerCase();
    if (/(^|_)(yoy|mom|wow|qoq)(_|$)/.test(n)) return true;
    if (/(^|_)(delta|chg|change|growth|variance|diff)(_|$)/.test(n)) return true;
    if (/(pct_?chg|chg_?pct|pct_?change|change_?pct)/.test(n)) return true;
    return false;
  };

  // Inline delta pill for a single numeric cell
  F.fmtDeltaCell = function(value, inverse){
    if (value === null || value === undefined || value === '') return '';
    var n = Number(String(value).replace(/,/g,''));
    if (isNaN(n)) return F.esc(value);
    var isPos = n > 0, isNeg = n < 0;
    var good = inverse ? isNeg : isPos;
    var bad  = inverse ? isPos : isNeg;
    var cls = good ? 'pos' : (bad ? 'neg' : 'neu');
    var arrow = isPos ? '▲' : (isNeg ? '▼' : '•');
    var abs = Math.abs(n);
    var txt = (abs >= 100 ? Math.round(abs) : (Math.round(abs*10)/10))
                .toString().replace(/\.0$/,'') + '%';
    return '<span class="delta-pill ' + cls + '">' + arrow + ' ' + txt + '</span>';
  };

  var _MONTH_S = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // Canonical date-label formatter — mirrors app/dashboards/date_labels.py.
  // Keep the two in sync. Unrecognized input is returned unchanged.
  //   202401   -> "Jan 2024"     (GA4 yearMonth)
  //   20240105 -> "Jan 5, 2024"  (GA4 date)
  //   2024-Q1  -> "Q1 2024"      2024W03 -> "Wk 03 '24"      2024 -> "2024"
  F.formatDateLabel = function(value){
    if (value === null || value === undefined) return '';
    var s = String(value).trim();
    if (!s) return '';
    var m;
    if ((m = s.match(/^(\d{4})(\d{2})(\d{2})$/))) {
      var d = parseInt(m[3], 10);
      if (_MONTH_S[+m[2]-1] && d >= 1 && d <= 31) return _MONTH_S[+m[2]-1] + ' ' + d + ', ' + m[1];
    }
    if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/)) && _MONTH_S[+m[2]-1]) return _MONTH_S[+m[2]-1] + ' ' + (+m[3]) + ', ' + (+m[1]);
    if ((m = s.match(/^(\d{4})-?Q([1-4])$/i))) return 'Q' + m[2] + ' ' + m[1];
    if ((m = s.match(/^(\d{4})-?W(\d{1,2})$/i))) return "Wk " + (('0'+m[2]).slice(-2)) + " '" + m[1].slice(2);
    if ((m = s.match(/^(\d{4})(\d{2})$/)) && _MONTH_S[+m[2]-1]) return _MONTH_S[+m[2]-1] + ' ' + m[1];
    if ((m = s.match(/^(\d{4})-(\d{1,2})$/)) && _MONTH_S[+m[2]-1]) return _MONTH_S[+m[2]-1] + ' ' + (+m[1]);
    if (/^\d{4}$/.test(s)) return s;
    return s;
  };

  function _isDateColName(c){
    if (/^(date|day|report_date|week|week_start|week_end|period|timestamp|datetime|created_at|updated_at)$/i.test(c)) return true;
    // GA4 API dimension names (normalize: lowercase + strip underscores before matching)
    var n = String(c).toLowerCase().replace(/_/g,'');
    return /^(yearmonth|datehour|datehourminute|nthday|nthweek|nthmonth|hour|minute|dayofweek)$/.test(n);
  }
  function _isMonthColName(c){ return /^month(_num(ber)?)?$/i.test(c); }
  function _isYearColName(c){ return /^year$/i.test(c); }
  function _isRateCol(c){ var n = c.toLowerCase().replace(/_/g,''); return /rate$/.test(n) || /^ctr$|ctr$|^cvr$|cvr$/.test(n); }
  function _isDurationCol(c){ var n = c.toLowerCase().replace(/_/g,''); return /duration|timeon|timespent|dwelltime|avgtime/.test(n); }

  function numericColsOf(columns, rows){
    return columns.filter(function(c){
      return rows.slice(0,5).every(function(r){
        var v = String(r[c]==null?'':r[c]).replace(/,/g,'').trim();
        return v !== '' && !isNaN(parseFloat(v));
      });
    });
  }

  F.numericColsOf = numericColsOf;

  // ── Chart type detection fallback (for old cards without chart_type) ────
  F.detectChartType = function(snap){
    var ct = snap.card_type || 'UNKNOWN';
    if (ct === 'METRIC') return 'scorecard';
    if (ct === 'AUDIT')  return 'audit';
    if (ct === 'LIST')   return 'list';
    if (ct === 'TABLE')  return 'table';
    if (ct === 'CHART') {
      var cfg = snap.chart_config || {};
      var type = (cfg.type || '').toLowerCase();
      if (type === 'pie' || type === 'donut') return 'pie';
      if (type === 'line' || type === 'area') return 'line';
      if (type === 'bar' || type === 'hbar' || type === 'stacked_bar') return 'bar';
    }
    return 'table';
  };

  // ── Scorecard renderer (.sc-card Pastel Pop tiles) ─────────────────────

  // One Pastel Pop tile. label/value are raw (escaped here); delta/spark are
  // pre-built HTML fragments.
  function scTile(scheme, label, value, deltaHtml, sparkHtml){
    return '<div class="sc-card" style="--sc-border:var(--pp-'+scheme+'-border);--sc-bg:var(--pp-'+scheme+'-bg);--sc-accent:var(--pp-'+scheme+'-accent);--sc-label-color:var(--pp-'+scheme+'-label);">' +
      '<div class="sc-label" title="'+F.esc(label)+'">'+F.esc(label)+'</div>' +
      '<div class="sc-value">'+value+'</div>' + (deltaHtml||'') + (sparkHtml||'') + '</div>';
  }

  // Colored delta pill from a metric-like object {delta_pct, inverse, direction}.
  function scDelta(m){
    if (!m || m.delta_pct === null || m.delta_pct === undefined) return '';
    var n = Number(m.delta_pct);
    if (isNaN(n)) return '';
    var good = m.inverse ? n < 0 : n > 0;
    var bad  = m.inverse ? n > 0 : n < 0;
    var cls = good ? 'positive' : (bad ? 'negative' : 'neutral');
    var arrow = n > 0 ? '▲' : (n < 0 ? '▼' : '•');
    var pct = (Math.round(Math.abs(n)*10)/10).toString().replace(/\.0$/,'');
    return '<span class="sc-delta ' + cls + '">' + arrow + ' ' + pct + '%' + (m.direction ? ' ' + F.esc(m.direction) : '') + '</span>';
  }

  // Mini sparkline over the last ~10 values of `valCol` — an inline SVG
  // area+line trend (not a raw bar-per-point rect chart). Still plain SVG
  // (no ECharts instance per tile — that would be one instance per KPI card,
  // which doesn't scale to a dashboard full of scorecards).
  function scSparkline(rows, valCol, show){
    if (show === false) return '';
    var vals = (rows||[]).slice(-10).map(function(r){ return Number(String(r[valCol]==null?0:r[valCol]).replace(/,/g,'')); });
    if (vals.length < 2) vals = [1,1,1,1,1,1];
    var vMin = Math.min.apply(null, vals);
    var vMax = Math.max.apply(null, vals);
    var range = (vMax - vMin) || 1;
    var n = vals.length;
    // Plot in a 0..100 x 0..32 box, leaving 4px of top/bottom breathing room
    // so the line/dots never clip against the tile edge.
    var top = 4, bottom = 28;
    var pts = vals.map(function(v, i){
      var x = (i / (n - 1)) * 100;
      var y = bottom - ((v - vMin) / range) * (bottom - top);
      return [x, y];
    });
    var linePath = pts.map(function(p, i){ return (i === 0 ? 'M' : 'L') + p[0].toFixed(2) + ' ' + p[1].toFixed(2); }).join(' ');
    var areaPath = linePath + ' L ' + pts[n-1][0].toFixed(2) + ' 32 L ' + pts[0][0].toFixed(2) + ' 32 Z';
    var lastX = pts[n-1][0].toFixed(2), lastY = pts[n-1][1].toFixed(2);
    var uid = 'spk' + Math.random().toString(36).slice(2, 9);
    return '<div class="sc-sparkline"><svg viewBox="0 0 100 32" preserveAspectRatio="none" width="100%" height="100%">' +
      '<defs><linearGradient id="'+uid+'" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="var(--sc-accent,#1d4ed8)" stop-opacity="0.35"/>' +
        '<stop offset="100%" stop-color="var(--sc-accent,#1d4ed8)" stop-opacity="0"/>' +
      '</linearGradient></defs>' +
      '<path d="'+areaPath+'" fill="url(#'+uid+')" stroke="none"/>' +
      '<path d="'+linePath+'" fill="none" stroke="var(--sc-accent,#1d4ed8)" stroke-width="1.75" vector-effect="non-scaling-stroke"/>' +
      '<circle cx="'+lastX+'" cy="'+lastY+'" r="2.2" fill="var(--sc-accent,#1d4ed8)"/>' +
      '</svg></div>';
  }

  function renderScorecard(snap){
    var cfg = snap.chart_config || {};
    var scheme = PP_SCHEMES.indexOf(cfg.color_scheme) >= 0 ? cfg.color_scheme : 'blue';
    var unit = cfg.unit || snap.unit || 'number';
    var showSparkline = cfg.sparkline !== false;
    var rows = snap.rows || [];
    var metrics = snap.metrics || [];

    // Single headline metric (the common GA4 scorecard — the server collapses
    // the daily rows into metrics[0]): one big tile with a sparkline trend
    // drawn from the underlying rows.
    if (metrics.length === 1) {
      var m0 = metrics[0];
      var v0 = F.fmtMetricValue(m0.value, m0.unit || unit, {currency_code: m0.currency_code});
      return scTile(scheme, m0.label || m0.key || '', v0, scDelta(m0), scSparkline(rows, m0.key, showSparkline));
    }

    // Multiple metrics: a grid of tiles (no sparkline — too many series).
    if (metrics.length) {
      var tiles = metrics.map(function(m, i){
        var s = PP_SCHEMES[i % PP_SCHEMES.length];
        var v = F.fmtMetricValue(m.value, m.unit || unit, {currency_code: m.currency_code});
        return scTile(s, m.label || m.key || '', v, scDelta(m), '');
      });
      return '<div class="metric-kpi-grid">' + tiles.join('') + '</div>';
    }

    // Fallback: derive a value straight from rows/columns (non-GA4 tools, or a
    // scorecard the server couldn't aggregate). CRITICAL: never pick a
    // date/month/year dimension column as the value — a GA4 "date" column holds
    // 20241003, which fmtNum would render as "20.24M".
    var cols = snap.columns || [];
    var numCols = numericColsOf(cols, rows);
    var valueCols = numCols.filter(function(c){
      return !_isDateColName(c) && !_isMonthColName(c) && !_isYearColName(c);
    });
    var valCol = valueCols[0] || numCols[0];
    var deltaCol = numCols.find(function(c){ return F.isChangeMetric(c); }) || null;

    if (!rows.length || !valCol) {
      return '<p class="empty">No data to display.</p>';
    }

    var firstRow = rows[0];
    var valStr = F.fmtMetricValue(firstRow[valCol], unit);
    // Label: a non-numeric dimension's value if there is one, else the metric
    // column name itself — never the metric's own cell value.
    var labelCol = cols.find(function(c){ return numCols.indexOf(c) === -1; });
    var lbl = labelCol ? String(firstRow[labelCol] == null ? valCol : firstRow[labelCol]) : valCol;
    var deltaHtml = (deltaCol && firstRow[deltaCol] !== null && firstRow[deltaCol] !== undefined)
      ? scDelta({delta_pct: firstRow[deltaCol]}) : '';

    return scTile(scheme, lbl, valStr, deltaHtml, scSparkline(rows, valCol, showSparkline));
  }

  // ── Pie / donut renderer (emits data-chart-type for mountCharts) ────────
  function renderPieChart(snap){
    return '<div class="card-chart" data-chart="pie" data-chart-type="pie" data-card-id="'+F.esc(snap._card_id||'')+'"></div>';
  }

  // ── Bar-family renderer (bar / hbar / stacked_bar) — emits data-chart for
  // mountCharts's 'custom' dispatch, which honors chart_config.series when
  // present (legacy path) or auto-detects using `layout` as a steering hint
  // (first-class chart_type, dashboard revamp Phase 1).
  // Data shape: any rows/columns; auto-pick uses the first non-numeric
  // column as the category axis and up to 4 numeric columns as series.
  function renderBarChart(snap, forcedLayout){
    var cfg = snap.chart_config || {};
    var isHoriz = forcedLayout === 'hbar' || (cfg.orientation === 'horizontal') || (cfg.type || '').toLowerCase() === 'hbar';
    var rows = snap.rows || [];
    var layout = forcedLayout || (isHoriz ? 'hbar' : 'bar');
    if (!rows.length) return '<p class="empty">No data to chart.</p>';
    return '<div class="card-chart" data-chart="custom" data-chart-layout="'+layout+'" data-card-id="'+F.esc(snap._card_id||'')+'"></div>';
  }

  // ── Line-family renderer (line / area) ─────────────────────────────────
  // Data shape: a date-like (or ordered categorical) label column + up to 4
  // numeric series columns.
  function renderLineChart(snap, forcedLayout){
    var rows = snap.rows || [];
    if (!rows.length) return '<p class="empty">No data to chart.</p>';
    return '<div class="card-chart" data-chart="custom" data-chart-layout="'+(forcedLayout||'line')+'" data-card-id="'+F.esc(snap._card_id||'')+'"></div>';
  }

  // ── Combo renderer (bar+line, dual axis) ───────────────────────────────
  // Data shape: requires chart_config.series with each entry's `chart`
  // ("bar"|"line") and optional `axis: "right"` for the dual-axis metric;
  // without an explicit chart_config it degrades to the plain auto-bar/line
  // heuristic (still useful, just without the mixed bar+line mark types).
  function renderComboChart(snap){
    var rows = snap.rows || [];
    if (!rows.length) return '<p class="empty">No data to chart.</p>';
    return '<div class="card-chart" data-chart="custom" data-chart-layout="combo" data-card-id="'+F.esc(snap._card_id||'')+'"></div>';
  }

  // ── Generic "v2" chart renderer — scatter / heatmap / funnel / treemap /
  // radar / gauge / waterfall. See charts.js's per-builder column-shape
  // contracts; every builder there degrades to the auto bar/line chart when
  // a card's data doesn't fit its shape, matching how unknown chart_types
  // degrade to the table renderer today.
  function renderV2Chart(snap, layout){
    var rows = snap.rows || [];
    // Gauge can be driven purely by snap.metrics[0] (the common scorecard-style
    // single-value case), so it doesn't require row data the way the other
    // v2 types do.
    var hasData = rows.length || (layout === 'gauge' && (snap.metrics||[]).length);
    if (!hasData) return '<p class="empty">No data to chart.</p>';
    return '<div class="card-chart" data-chart="v2" data-chart-layout="'+F.esc(layout)+'" data-card-id="'+F.esc(snap._card_id||'')+'"></div>';
  }

  // ── Table renderer ────────────────────────────────────────────────────
  function renderTable(snap){
    var rows = snap.rows || snap.rows || [];
    var cols = snap.columns || [];
    var numericCols = numericColsOf(cols, rows);
    var labelCol = cols.find(function(c){ return numericCols.indexOf(c) === -1; }) || cols[0];
    var derivCols = cols.filter(function(c){ return numericCols.indexOf(c) !== -1 && F.isChangeMetric(c); });
    var isDeriv = function(c){ return derivCols.indexOf(c) !== -1; };
    var hasChart = numericCols.length >= 1 && rows.length >= 2 && labelCol;
    // Compare mode: these numeric columns carry __prev / __delta_pct on each row.
    var compareCols = (snap.compare && Array.isArray(snap.compare_columns)) ? snap.compare_columns : [];
    var isCmp = function(c){ return compareCols.indexOf(c) !== -1; };

    var tableHtml = '';
    if (rows.length) {
      var csvBtn = snap._card_id ? '<div style="text-align:right;margin-bottom:4px;"><button class="lv-csv-btn" data-card-id="'+F.esc(snap._card_id)+'" title="Download CSV" style="font-size:11px;padding:3px 8px;border:1px solid var(--border);border-radius:4px;background:var(--surface);cursor:pointer;">⬇ CSV</button></div>' : '';
      tableHtml = '<div class="table-wrap">' + csvBtn + '<table><thead><tr>' + cols.map(function(c){ return '<th>'+F.esc(c)+'</th>' + (isCmp(c) ? '<th style="text-align:right;">Prev</th><th style="text-align:right;">Δ%</th>' : ''); }).join('') + '</tr></thead><tbody>' +
        rows.map(function(r){
          return '<tr>' + cols.map(function(c){
            var v = r[c];
            var isNum = numericCols.indexOf(c) !== -1;
            var cellHtml;
            if (isDeriv(c)) {
              cellHtml = F.fmtDeltaCell(v);
            } else if (isNum && _isDateColName(c)) {
              cellHtml = F.esc(F.formatDateLabel(v));
            } else if (isNum && _isMonthColName(c)) {
              var mn = parseInt(v, 10);
              cellHtml = F.esc((mn >= 1 && mn <= 12) ? _MONTH_S[mn-1] : String(v == null ? '' : v));
            } else if (isNum && _isYearColName(c)) {
              cellHtml = F.esc(String(v == null ? '' : v));
            } else if (isNum && _isRateCol(c)) {
              cellHtml = F.esc(F.fmtMetricValue ? F.fmtMetricValue(v, 'percent') : v + '%');
            } else if (isNum && _isDurationCol(c)) {
              cellHtml = F.esc(F.fmtMetricValue ? F.fmtMetricValue(v, 'duration_sec') : v + 's');
            } else if (isNum) {
              cellHtml = F.fmtNum(v);
            } else {
              cellHtml = F.esc(v);
            }
            var isTrueNum = isNum && !_isDateColName(c) && !_isMonthColName(c) && !_isYearColName(c);
            var alignStyle = isTrueNum ? ' style="text-align:right;font-variant-numeric:tabular-nums;"' : '';
            var cmpTds = '';
            if (isCmp(c)) {
              var pv = r[c + '__prev'];
              var dpct = r[c + '__delta_pct'];
              cmpTds = '<td style="text-align:right;color:var(--text-subtle);font-variant-numeric:tabular-nums;">' +
                       (pv == null || pv === '' ? '—' : F.fmtNum(pv)) + '</td>' +
                       '<td style="text-align:right;">' + (dpct == null ? '—' : F.fmtDeltaCell(dpct)) + '</td>';
            }
            return '<td' + alignStyle + '>' + cellHtml + '</td>' + cmpTds;
          }).join('') + '</tr>';
        }).join('') +
        '</tbody></table>' + (snap.truncated ? '<p class="truncation-note">Showing '+rows.length+' of '+(snap.total_rows||rows.length)+'</p>' : '') + '</div>';
    } else {
      tableHtml = '<p class="empty">No rows to display.</p>';
    }

    var chartEl = (hasChart && snap._card_id) ? '<div class="card-chart" data-chart="table" data-card-id="'+F.esc(snap._card_id)+'"></div>' : '';
    return chartEl + tableHtml;
  }

  // ── Audit renderer ────────────────────────────────────────────────────
  function renderAudit(snap){
    var findings = snap.findings || [];
    var sevClass = function(s){ return ({critical:'badge-danger',error:'badge-danger',high:'badge-warning',warning:'badge-warning',warn:'badge-warning',medium:'badge-warning',low:'badge-success',info:'badge',ok:'badge-success',pass:'badge-success'})[String(s||'info').toLowerCase()] || 'badge'; };
    var scoreHtml = '';
    if (snap.score !== null && snap.score !== undefined) {
      var n = parseInt(snap.score,10)||0;
      var cls = n>=70?'badge-success':(n>=40?'badge-warning':'badge-danger');
      var lbl = n>=70?'Good':(n>=40?'Needs attention':'Critical');
      scoreHtml = '<div class="row mb-3" style="gap:10px;"><span class="audit-score">Score: '+n+'</span><span class="badge '+cls+'">'+lbl+'</span></div>' +
                  (snap._card_id ? '<div class="card-chart" data-chart="audit-gauge" data-card-id="'+F.esc(snap._card_id)+'" data-score="'+n+'" style="height:140px;"></div>' : '');
    }
    var summaryHtml = snap.summary ? '<p class="audit-summary">' + F.esc(snap.summary) + '</p>' : '';
    var findingsHtml = findings.length
      ? '<div class="findings-list">' + findings.map(function(f){ return '<div class="finding-row"><span class="finding-badge '+sevClass(f.severity)+'">'+F.esc(String(f.severity||'info').toUpperCase())+'</span><span class="finding-msg">'+F.esc(f.message||'')+(f.context?' <span class="finding-ctx">— '+F.esc(f.context)+'</span>':'')+'</span></div>'; }).join('') +
        (snap.truncated ? '<p class="truncation-note">Showing '+findings.length+' of '+(snap.total_findings||findings.length)+'</p>' : '') +
        '</div>'
      : '<p class="empty">No findings.</p>';
    return scoreHtml + summaryHtml + findingsHtml;
  }

  // ── List renderer ─────────────────────────────────────────────────────
  function renderList(snap){
    var items = snap.items || [];
    return items.length
      ? '<ul class="data-list">' + items.map(function(i){ return '<li class="list-item">'+F.esc(i)+'</li>'; }).join('') +
        (snap.truncated ? '<p class="truncation-note">Showing '+items.length+' of '+(snap.total_items||items.length)+'</p>' : '') +
        '</ul>'
      : '<p class="empty">No items.</p>';
  }

  // ── Main renderCard — chart_type dispatch ─────────────────────────────
  F.renderCard = function(card){
    var snap = card.snap || {};
    var ct = card.card_type || 'UNKNOWN';
    var icon = ICONS[card.platform] || '📈';

    // Inject card ID into snap so sub-renderers can emit data-card-id attributes
    snap._card_id = card.id;
    // Registry for client-side CSV export (keyed by card id).
    F._snaps = F._snaps || {};
    F._snaps[card.id] = snap;

    // chart_type from the new card spec takes precedence; fall back to heuristic
    var chartType = snap.chart_type || F.detectChartType(snap);

    var body = '';
    switch (chartType) {
      case 'scorecard':   body = renderScorecard(snap);            break;
      case 'bar':         body = renderBarChart(snap, 'bar');       break;
      case 'hbar':        body = renderBarChart(snap, 'hbar');      break;
      case 'stacked_bar': body = renderBarChart(snap, 'stacked_bar'); break;
      case 'line':        body = renderLineChart(snap, 'line');     break;
      case 'area':        body = renderLineChart(snap, 'area');     break;
      case 'combo':       body = renderComboChart(snap);            break;
      case 'pie':         body = renderPieChart(snap);              break;
      case 'donut':       body = renderPieChart(snap);              break;
      case 'table':       body = renderTable(snap);                 break;
      case 'audit':       body = renderAudit(snap);                 break;
      case 'list':        body = renderList(snap);                  break;
      case 'scatter':     body = renderV2Chart(snap, 'scatter');    break;
      case 'heatmap':     body = renderV2Chart(snap, 'heatmap');    break;
      case 'funnel':      body = renderV2Chart(snap, 'funnel');     break;
      case 'treemap':     body = renderV2Chart(snap, 'treemap');    break;
      case 'radar':       body = renderV2Chart(snap, 'radar');      break;
      case 'gauge':       body = renderV2Chart(snap, 'gauge');      break;
      case 'waterfall':   body = renderV2Chart(snap, 'waterfall');  break;
      default:            body = renderTable(snap);                 break;
    }

    // Keep backward-compat: also handle old ct-based paths for cards without chart_type
    // (detectChartType already maps old card_type → chart type above, so this is a safety net).
    // Non-owners see a neutral "unavailable" message — "older format" is only meaningful
    // to the owner who can redeploy (D6).
    if (!snap.chart_type && ct === 'UNKNOWN') {
      var isOwner = (typeof IS_OWNER !== 'undefined') ? IS_OWNER : false;
      body = isOwner
        ? '<p class="empty">This card uses an older format. Redeploy it to refresh.</p>'
        : '<p class="empty">Data temporarily unavailable.</p>';
    }

    // Determine card width. chart_config.size (sm|md|lg|xl) — when present —
    // maps directly onto the existing grid-span classes and wins over every
    // heuristic below. Absent a size, the heuristics (unchanged) decide.
    //   sm → kpi-tile (span 2)   md → card-third (span 4)
    //   lg → (default, span 6)  xl → card-wide (span 12)
    var sizeCfg = (snap.chart_config || {}).size;
    var SIZE_CLASS = {sm: 'kpi-tile', md: 'card-third', lg: '', xl: 'card-wide'};
    var hasExplicitSize = Object.prototype.hasOwnProperty.call(SIZE_CLASS, sizeCfg);

    var autoWide = false;
    if (snap.layout === 'wide') {
      autoWide = true;
    } else if (chartType === 'table' || ct === 'TABLE') {
      var colsW = (snap.columns || []);
      var numW = numericColsOf(colsW, snap.rows || []).length;
      autoWide = (colsW.length >= 6) || (numW >= 5);
    } else if (ct === 'CHART' || chartType === 'bar' || chartType === 'line' || chartType === 'pie') {
      var cfgW = snap.chart_config || {};
      if ((cfgW.series || []).length >= 4) autoWide = true;
    } else if (chartType === 'scorecard' || ct === 'METRIC') {
      if ((snap.metrics || []).length >= 4) autoWide = true;
    } else if (chartType === 'heatmap' || chartType === 'treemap' || chartType === 'waterfall' || chartType === 'combo') {
      // These read better with more horizontal room by default.
      autoWide = true;
    }

    var isKpiTile = (chartType === 'scorecard' && (snap.metrics||[]).length === 1);
    var cardCls = 'data-card' + (hasExplicitSize
      ? (SIZE_CLASS[sizeCfg] ? ' ' + SIZE_CLASS[sizeCfg] : '')
      : ((isKpiTile ? ' kpi-tile' : '') + (autoWide ? ' card-wide' : '')));
    return '<div class="' + cardCls + '" data-card-type="' + F.esc(ct) + '" data-card-id="' + F.esc(card.id) + '">' +
      '<div class="card-header"><div class="card-title"><span>' + icon + '</span><span title="' + F.esc(card.title) + '">' + F.esc(card.title) + '</span>' + (ct!=='UNKNOWN' ? '<span class="type-badge">'+F.esc(ct)+'</span>' : '') + '</div>' +
      '<div class="row" style="gap:8px;"><span class="card-meta">' + F.esc(String(card.platform||'').toUpperCase()) + '</span></div></div>' +
      '<div class="card-body">' + body + '</div></div>';
  };

  // ── CSV export (client-side, from the snap registry) ───────────────────
  F.exportCsv = function(cardId){
    var snap = (F._snaps || {})[cardId];
    if (!snap || !snap.rows || !snap.columns) return;
    var cols = snap.columns;
    var esc = function(v){
      var s = (v == null) ? '' : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var lines = [cols.map(esc).join(',')];
    snap.rows.forEach(function(r){
      lines.push(cols.map(function(c){
        var v = r[c];
        if (_isDateColName(c)) v = F.formatDateLabel(v);
        return esc(v);
      }).join(','));
    });
    var blob = new Blob([lines.join('\n')], {type: 'text/csv;charset=utf-8;'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = (snap.title || cardId || 'card') + '.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
  };

  // One delegated listener for CSV export + per-card retry buttons.
  if (typeof document !== 'undefined' && !F._delegatedClicks) {
    F._delegatedClicks = true;
    document.addEventListener('click', function(ev){
      var t = ev.target;
      var csv = t.closest && t.closest('.lv-csv-btn');
      if (csv) { ev.preventDefault(); F.exportCsv(csv.getAttribute('data-card-id')); return; }
      var retry = t.closest && t.closest('.lv-retry');
      if (retry) { ev.preventDefault(); if (window.__dashRetry) window.__dashRetry(); }
    });
  }
})();
