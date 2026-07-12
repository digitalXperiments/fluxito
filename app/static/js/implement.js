/* Fluxito — Implement hub
 * Fetches the plan-vs-GTM coverage read model + pending drafts, renders the
 * coverage table, and wires Deploy / Refresh-drift / draft approve-reject.
 * Plain IIFE (no modules), consistent with the other page scripts.
 */
(function () {
  'use strict';

  var toast = (window.Fluxito && window.Fluxito.toast) || function (m) { console.log(m); };

  var IMPL_STATUS = {
    verified: { cls: 'good', label: 'Verified' },
    implemented: { cls: 'good', label: 'Implemented' },
    planned: { cls: 'muted', label: 'Planned' },
    deprecated: { cls: 'muted', label: 'Deprecated' },
  };

  var GTM_STATUS = {
    deployed: { cls: 'good', label: 'Deployed' },
    not_found: { cls: 'bad', label: 'Not found' },
    no_connection: { cls: 'muted', label: 'No GTM' },
  };

  var DRIFT_STATUS = {
    verified: { cls: 'good', label: 'Verified' },
    in_plan: { cls: 'good', label: 'In plan' },
    drifted: { cls: 'warn', label: 'Drifted' },
    broken: { cls: 'bad', label: 'Broken' },
    unplanned: { cls: 'warn', label: 'Unplanned' },
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function chip(cls, label) {
    return '<span class="impl-chip impl-chip--' + cls + '">' + esc(label) + '</span>';
  }

  function fmtNum(n) {
    if (n == null) return '—';
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
    return String(n);
  }

  function fmtPct(p) {
    if (p == null) return '—';
    return Math.round(p) + '%';
  }

  // ---- Coverage ----------------------------------------------------------

  function renderStats(summary) {
    var strip = document.getElementById('implStatStrip');
    if (!strip || !summary) return;
    Object.keys(summary).forEach(function (k) {
      var el = strip.querySelector('[data-stat="' + k + '"]');
      if (el) el.textContent = summary[k];
    });
  }

  function renderGtmBadge(gtm) {
    var badge = document.getElementById('implGtmBadge');
    if (!badge) return;
    if (!gtm || !gtm.connected) {
      badge.hidden = false;
      badge.className = 'impl-gtm-badge impl-gtm-badge--off';
      badge.textContent = 'GTM not connected';
      return;
    }
    badge.hidden = false;
    badge.className = 'impl-gtm-badge';
    var label = gtm.public_id || gtm.container_name || 'GTM container';
    badge.innerHTML = '<span class="impl-gtm-dot"></span>' + esc(label) +
      (gtm.error ? ' <span class="impl-gtm-warn">· read failed</span>' : '');
  }

  function sourcesCell(sources) {
    if (!sources || !sources.length) return '<span class="impl-muted">—</span>';
    return sources.map(function (s) {
      var meta = IMPL_STATUS[s.implementation_status] || { cls: 'muted', label: s.implementation_status };
      return '<span class="impl-src" title="' + esc(s.name) + ': ' + esc(meta.label) + '">' +
        chip(meta.cls, s.name) + '</span>';
    }).join('');
  }

  function driftCell(drift) {
    if (!drift || !drift.status) return '<span class="impl-muted">—</span>';
    var meta = DRIFT_STATUS[drift.status] || { cls: 'muted', label: drift.status };
    return chip(meta.cls, meta.label);
  }

  function rowHtml(r) {
    var gtmMeta = GTM_STATUS[r.gtm] || { cls: 'muted', label: r.gtm };
    var canDeploy = r.gtm === 'not_found';
    var actions =
      (canDeploy
        ? '<button class="btn primary xs impl-deploy-btn" data-event-id="' + esc(r.event_id) +
          '" data-event-name="' + esc(r.name) + '">Deploy</button> '
        : '') +
      '<button class="btn ghost xs impl-code-btn" data-event-id="' + esc(r.event_id) + '">Get code</button>';
    var vol = r.drift ? fmtNum(r.drift.volume_7d) : '—';
    var cov = r.drift ? fmtPct(r.drift.param_coverage_pct) : '—';
    return '<tr>' +
      '<td class="impl-ev-name"><span class="impl-ev-mono">' + esc(r.name) + '</span></td>' +
      '<td>' + (r.category ? esc(r.category) : '<span class="impl-muted">—</span>') + '</td>' +
      '<td class="impl-sources">' + sourcesCell(r.sources) + '</td>' +
      '<td>' + driftCell(r.drift) + '</td>' +
      '<td class="ta-r">' + vol + '</td>' +
      '<td class="ta-r">' + cov + '</td>' +
      '<td>' + chip(gtmMeta.cls, gtmMeta.label) + '</td>' +
      '<td class="ta-r">' + actions + '</td>' +
      '</tr>';
  }

  // Rows keyed by event_id — the snippet modal reads name + properties here.
  var ROWS_BY_ID = {};

  function renderCoverage(data) {
    var body = document.getElementById('coverageBody');
    var noPlan = document.getElementById('emptyNoPlan');
    var table = document.getElementById('coverageTable');
    if (!body) return;

    renderStats(data.summary);
    renderGtmBadge(data.gtm);
    renderUnplanned(data.unplanned_in_gtm);

    // "Connect your tag manager" card: plan exists but no GTM connection.
    var connectCard = document.getElementById('implConnectCard');
    if (connectCard) {
      connectCard.hidden = !(data.flags && data.flags.has_plan && !(data.gtm && data.gtm.connected));
    }

    if (!data.flags || !data.flags.has_plan) {
      if (table) table.hidden = true;
      if (noPlan) noPlan.hidden = false;
      return;
    }
    if (table) table.hidden = false;
    if (noPlan) noPlan.hidden = true;

    if (!data.rows || !data.rows.length) {
      body.innerHTML = '<tr><td colspan="8" class="impl-loading">No events in the plan yet.</td></tr>';
      return;
    }
    ROWS_BY_ID = {};
    data.rows.forEach(function (r) { ROWS_BY_ID[r.event_id] = r; });
    body.innerHTML = data.rows.map(rowHtml).join('');
    wireDeployButtons();
    wireCodeButtons();
  }

  function renderUnplanned(list) {
    var section = document.getElementById('unplannedSection');
    var ul = document.getElementById('unplannedList');
    var count = document.getElementById('unplannedCount');
    if (!section || !ul) return;
    if (!list || !list.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    if (count) count.textContent = list.length;
    ul.innerHTML = list.map(function (u) {
      return '<li class="impl-unplanned-item">' +
        '<span class="impl-ev-mono">' + esc(u.event_name) + '</span>' +
        '<span class="impl-unplanned-src">via ' + esc(u.source) +
        (u.label ? ' · ' + esc(u.label) : '') + '</span></li>';
    }).join('');
  }

  function loadCoverage() {
    var body = document.getElementById('coverageBody');
    if (body) body.innerHTML = '<tr><td colspan="8" class="impl-loading">Loading coverage…</td></tr>';
    return fetch('/api/implement/coverage', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(renderCoverage)
      .catch(function () {
        if (body) body.innerHTML = '<tr><td colspan="8" class="impl-loading">Failed to load coverage.</td></tr>';
      });
  }

  // ---- Deploy ------------------------------------------------------------

  function wireDeployButtons() {
    var btns = document.querySelectorAll('.impl-deploy-btn');
    Array.prototype.forEach.call(btns, function (btn) {
      btn.addEventListener('click', function () {
        var eventId = btn.getAttribute('data-event-id');
        var eventName = btn.getAttribute('data-event-name');
        btn.disabled = true;
        btn.textContent = 'Staging…';
        fetch('/api/implement/deploy-proposal', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ event_id: eventId }),
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (res) {
            if (!res.ok || res.d.error) {
              toast((res.d && res.d.message) || 'Could not create proposal', 'error');
              btn.disabled = false;
              btn.textContent = 'Deploy';
              return;
            }
            toast('Draft staged for ' + eventName);
            btn.textContent = 'Staged';
            loadDrafts();
          })
          .catch(function () {
            toast('Network error', 'error');
            btn.disabled = false;
            btn.textContent = 'Deploy';
          });
      });
    });
  }

  // ---- "Get code" snippet modal -------------------------------------------
  // Generates copy-paste implementation snippets from the plan's event shape
  // (name + typed properties with examples) — the no-tag-manager path.

  var codeModal = { eventId: null, fmt: 'datalayer' };

  function exampleLiteral(p) {
    var ex = p.example;
    if (ex != null && ex !== '') {
      if (p.data_type === 'number') {
        var n = Number(ex);
        if (!isNaN(n)) return String(n);
      }
      if (p.data_type === 'boolean') return String(ex) === 'false' ? 'false' : 'true';
      if (p.is_list || p.data_type === 'array' || p.data_type === 'object') {
        try { JSON.parse(ex); return String(ex); } catch (e) { /* fall through */ }
      }
      return JSON.stringify(String(ex));
    }
    if (p.is_list || p.data_type === 'array') return '[]';
    switch (p.data_type) {
      case 'number': return '0';
      case 'boolean': return 'true';
      case 'object': return '{}';
      default: return JSON.stringify('<' + p.name + '>');
    }
  }

  function paramLines(props, indent) {
    return (props || []).map(function (p) {
      var comment = ' // ' + (p.data_type || 'string') + (p.is_list ? '[]' : '') + (p.required ? ', required' : '');
      return indent + JSON.stringify(p.name) + ': ' + exampleLiteral(p) + ',' + comment;
    });
  }

  function buildSnippet(r, fmt) {
    var props = r.properties || [];
    if (fmt === 'gtag') {
      var lines = ['gtag("event", ' + JSON.stringify(r.name) + (props.length ? ', {' : ');')];
      if (props.length) {
        lines = lines.concat(paramLines(props, '  '));
        lines.push('});');
      }
      return lines.join('\n');
    }
    // Default: GTM dataLayer push
    var out = [
      'window.dataLayer = window.dataLayer || [];',
      'dataLayer.push({',
      '  event: ' + JSON.stringify(r.name) + ',',
    ];
    if (props.length) out = out.concat(paramLines(props, '  '));
    else out.push('  // no parameters defined in the plan yet');
    out.push('});');
    return out.join('\n');
  }

  var CODE_HINTS = {
    datalayer: 'Fire this where the user action happens. Requires the GTM container snippet on the page — a custom-event trigger on "' ,
    gtag: 'Requires the GA4 gtag.js snippet (your Measurement ID) on the page.',
  };

  function renderCodeModal() {
    var r = ROWS_BY_ID[codeModal.eventId];
    if (!r) return;
    var block = document.getElementById('implCodeBlock');
    var title = document.getElementById('implCodeTitle');
    var hint = document.getElementById('implCodeHint');
    if (title) title.textContent = r.name;
    if (block) block.textContent = buildSnippet(r, codeModal.fmt);
    if (hint) {
      hint.textContent = codeModal.fmt === 'gtag'
        ? CODE_HINTS.gtag
        : CODE_HINTS.datalayer + r.name + '" picks it up in GTM.';
    }
    Array.prototype.forEach.call(document.querySelectorAll('#implCodeTabs .impl-code-tab'), function (t) {
      t.classList.toggle('is-active', t.getAttribute('data-fmt') === codeModal.fmt);
    });
  }

  function openCodeModal(eventId) {
    codeModal.eventId = eventId;
    var modal = document.getElementById('implCodeModal');
    if (!modal) return;
    renderCodeModal();
    modal.hidden = false;
  }

  function closeCodeModal() {
    var modal = document.getElementById('implCodeModal');
    if (modal) modal.hidden = true;
  }

  function wireCodeButtons() {
    Array.prototype.forEach.call(document.querySelectorAll('.impl-code-btn'), function (btn) {
      btn.addEventListener('click', function () { openCodeModal(btn.getAttribute('data-event-id')); });
    });
  }

  function wireCodeModal() {
    var modal = document.getElementById('implCodeModal');
    if (!modal) return;
    var close = document.getElementById('implCodeClose');
    var copy = document.getElementById('implCodeCopy');
    if (close) close.addEventListener('click', closeCodeModal);
    modal.addEventListener('click', function (e) { if (e.target === modal) closeCodeModal(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeCodeModal(); });
    Array.prototype.forEach.call(modal.querySelectorAll('.impl-code-tab'), function (tab) {
      tab.addEventListener('click', function () {
        codeModal.fmt = tab.getAttribute('data-fmt');
        renderCodeModal();
      });
    });
    if (copy) {
      copy.addEventListener('click', function () {
        var block = document.getElementById('implCodeBlock');
        if (!block) return;
        navigator.clipboard.writeText(block.textContent).then(function () {
          copy.textContent = 'Copied ✓';
          setTimeout(function () { copy.textContent = 'Copy snippet'; }, 1600);
        }, function () { toast('Copy failed — select the code manually', 'error'); });
      });
    }
  }

  // ---- Drafts ------------------------------------------------------------

  function draftHtml(d) {
    var payload = d.payload || {};
    var diff = (payload.diff || []).map(function (line) {
      return '<div class="impl-diff-line impl-diff-line--' + esc(line.kind || 'context') + '">' +
        esc(line.text) + '</div>';
    }).join('');
    return '<div class="impl-draft" data-draft-id="' + esc(d.id) + '">' +
      '<div class="impl-draft-head">' +
        '<div class="impl-draft-title">' + esc(d.title) + '</div>' +
        '<a class="impl-draft-link" href="' + esc(d.conversation_url) + '">Open in Flux ›</a>' +
      '</div>' +
      (payload.workspace_label ? '<div class="impl-draft-meta">' + esc(payload.workspace_label) + '</div>' : '') +
      (diff ? '<div class="impl-draft-diff">' + diff + '</div>' : '') +
      '<div class="impl-draft-actions">' +
        '<button class="btn primary xs impl-draft-approve" data-id="' + esc(d.id) + '">Approve &amp; publish</button>' +
        '<button class="btn ghost xs impl-draft-reject" data-id="' + esc(d.id) + '">Reject</button>' +
      '</div>' +
    '</div>';
  }

  function renderDrafts(data) {
    var body = document.getElementById('draftsBody');
    if (!body) return;
    var drafts = (data && data.drafts) || [];
    if (!drafts.length) {
      body.innerHTML = '<div class="impl-drafts-empty">No pending drafts. Deploy an event above to stage a change.</div>';
      return;
    }
    body.innerHTML = drafts.map(draftHtml).join('');
    wireDraftActions();
  }

  function wireDraftActions() {
    Array.prototype.forEach.call(document.querySelectorAll('.impl-draft-approve'), function (btn) {
      btn.addEventListener('click', function () { resolveDraft(btn.getAttribute('data-id'), 'approve', btn); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('.impl-draft-reject'), function (btn) {
      btn.addEventListener('click', function () { resolveDraft(btn.getAttribute('data-id'), 'reject', btn); });
    });
  }

  function resolveDraft(id, verb, btn) {
    var card = btn.closest('.impl-draft');
    if (card) card.classList.add('is-busy');
    Array.prototype.forEach.call(card ? card.querySelectorAll('button') : [], function (b) { b.disabled = true; });
    fetch('/api/ask/drafts/' + id + '/' + verb, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); })
      .then(function (res) {
        if (!res.ok || res.d.error) {
          var msg = (res.d && res.d.message) ||
            (res.status === 403 ? 'You cannot publish GTM changes in this project.' : 'Action failed');
          toast(msg, 'error');
          if (card) card.classList.remove('is-busy');
          Array.prototype.forEach.call(card ? card.querySelectorAll('button') : [], function (b) { b.disabled = false; });
          return;
        }
        toast(verb === 'approve' ? 'Published' : 'Draft rejected');
        loadDrafts();
        if (verb === 'approve') loadCoverage();
      })
      .catch(function () {
        toast('Network error', 'error');
        if (card) card.classList.remove('is-busy');
      });
  }

  function loadDrafts() {
    return fetch('/api/implement/drafts', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(renderDrafts)
      .catch(function () {});
  }

  // ---- Refresh drift -----------------------------------------------------

  function wireRefreshDrift() {
    var btn = document.getElementById('refreshDriftBtn');
    if (!btn) return;
    var label = btn.querySelector('.rd-label');
    btn.addEventListener('click', function () {
      btn.disabled = true;
      btn.classList.add('is-loading');
      if (label) label.textContent = 'Refreshing…';
      fetch('/api/implement/refresh-drift', { method: 'POST', credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok || res.d.error) {
            toast((res.d && res.d.message) || 'Drift refresh failed', 'error');
          } else {
            var skipped = res.d.drift && res.d.drift.skipped;
            toast(skipped ? 'Drift skipped: ' + skipped : 'Drift refreshed');
            return loadCoverage();
          }
        })
        .catch(function () { toast('Network error', 'error'); })
        .finally(function () {
          btn.disabled = false;
          btn.classList.remove('is-loading');
          if (label) label.textContent = 'Refresh drift';
        });
    });
  }

  // ---- Init --------------------------------------------------------------

  document.addEventListener('DOMContentLoaded', function () {
    loadCoverage();
    loadDrafts();
    wireRefreshDrift();
    wireCodeModal();
  });
})();
