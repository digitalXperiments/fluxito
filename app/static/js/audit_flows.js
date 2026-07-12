/* ============================================================
   Automated Test Flows — list page + vendors registry editor.
   One file serves both /audits/flows and /audits/vendors; each
   half no-ops when its root element is absent.
   ============================================================ */
(function () {
  'use strict';

  function toast(msg, kind) {
    if (window.Fluxito && window.Fluxito.toast) window.Fluxito.toast(msg, kind);
  }
  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function fmtDateTime(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d)) return '—';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }
  async function jfetch(url, opts) {
    var res = await fetch(url, opts);
    var data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok) {
      var msg = (data && (data.message || data.detail)) || ('Request failed (' + res.status + ')');
      throw new Error(msg);
    }
    return data;
  }

  /* ==========================================================
     FLOWS LIST
     ========================================================== */
  function initFlows() {
    var mount = document.getElementById('afFlowsMount');
    if (!mount) return;

    var FLOWS = (window.__AUDIT_FLOWS__ || []).slice();
    var byId = {};
    FLOWS.forEach(function (f) { byId[f.id] = f; });

    var search = '';
    var statusFilter = 'all';

    var DEVICE_ICON = {
      desktop: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
      mobile_web: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/></svg>'
    };

    function statusPill(status) {
      var s = status || 'never_run';
      var label = s === 'never_run' ? 'never run' : s;
      return '<span class="af-pill af-pill--' + esc(s) + '">' + esc(label) + '</span>';
    }

    function assertCell(f) {
      var lr = f.latest_run;
      if (!lr || lr.assertions_total == null) return '<span class="af-assert af-assert--none">—</span>';
      var n = lr.assertions_passed || 0, m = lr.assertions_total || 0;
      var cls = (m > 0 && n === m) ? 'af-assert--pass' : 'af-assert--fail';
      if (m === 0) cls = 'af-assert--none';
      return '<span class="af-assert ' + cls + '">' + n + ' / ' + m + '</span>';
    }

    function groupsCell(f) {
      var g = f.groups || [];
      if (!g.length) return '<span class="af-dim">—</span>';
      return '<span class="af-chip-row">' + g.map(function (x) {
        return '<span class="af-chip">' + esc(x) + '</span>';
      }).join('') + '</span>';
    }

    function latestRunLink(f) {
      var lr = f.latest_run;
      if (!lr || !lr.id) return '';
      return '<a class="af-iconbtn" title="Latest run" href="/audits/flows/' + esc(f.id) + '/runs/' + esc(lr.id) + '">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg></a>';
    }

    function matchesFilter(f) {
      if (statusFilter !== 'all' && (f.last_status || 'never_run') !== statusFilter) return false;
      if (search) {
        var hay = ((f.name || '') + ' ' + (f.description || '') + ' ' + (f.groups || []).join(' ')).toLowerCase();
        if (hay.indexOf(search) === -1) return false;
      }
      return true;
    }

    function render() {
      if (!FLOWS.length) {
        var tpl = document.getElementById('afEmptyTpl');
        mount.innerHTML = '';
        if (tpl) mount.appendChild(tpl.content.cloneNode(true));
        return;
      }
      var rows = FLOWS.filter(matchesFilter);
      if (!rows.length) {
        mount.innerHTML = '<div class="af-empty-hint" style="padding:24px 4px;">No flows match this filter.</div>';
        return;
      }
      var html = '<div class="af-table-wrap"><table class="af-table"><thead><tr>' +
        '<th>Flow</th><th>Device</th><th>Description</th><th>Groups</th>' +
        '<th>Last status</th><th>Assertions</th><th>Enabled</th><th>Schedule</th><th>Next run</th><th></th>' +
        '</tr></thead><tbody>';
      rows.forEach(function (f) {
        html += '<tr data-fid="' + esc(f.id) + '">' +
          '<td><a class="af-flow-name" href="/audits/flows/' + esc(f.id) + '">' + esc(f.name) + '</a></td>' +
          '<td><span class="af-device-icon" title="' + esc(f.device) + '">' + (DEVICE_ICON[f.device] || '') + '</span></td>' +
          '<td><div class="af-flow-desc">' + (f.description ? esc(f.description) : '<span class="af-dim">—</span>') + '</div></td>' +
          '<td>' + groupsCell(f) + '</td>' +
          '<td class="af-status-cell">' + statusPill(f.last_status) + '</td>' +
          '<td class="af-assert-cell">' + assertCell(f) + '</td>' +
          '<td><label class="af-switch"><input type="checkbox" class="af-toggle"' + (f.enabled ? ' checked' : '') + '><span class="af-slider"></span></label></td>' +
          '<td>' + (f.schedule_cron ? '<span class="af-cron">' + esc(f.schedule_cron) + '</span>' : '<span class="af-dim">—</span>') + '</td>' +
          '<td class="af-dim">' + esc(fmtDateTime(f.next_run_at)) + '</td>' +
          '<td><span class="af-row-actions">' +
            '<button type="button" class="af-iconbtn af-iconbtn--run af-run" title="Run now">' +
            '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button>' +
            latestRunLink(f) +
          '</span></td>' +
        '</tr>';
      });
      html += '</tbody></table></div>';
      mount.innerHTML = html;
    }

    function updateRow(f) {
      var tr = mount.querySelector('tr[data-fid="' + f.id + '"]');
      if (!tr) { render(); return; }
      tr.querySelector('.af-status-cell').innerHTML = statusPill(f.last_status);
      tr.querySelector('.af-assert-cell').innerHTML = assertCell(f);
      var actions = tr.querySelector('.af-row-actions');
      var runBtn = actions.querySelector('.af-run');
      // Rebuild the latest-run link (id may have changed).
      var existingLink = actions.querySelector('a.af-iconbtn');
      if (existingLink) existingLink.remove();
      if (f.latest_run && f.latest_run.id) {
        actions.insertAdjacentHTML('beforeend', latestRunLink(f));
      }
      runBtn.disabled = false;
    }

    // ── Toggle enable/disable ──
    mount.addEventListener('change', function (ev) {
      var input = ev.target.closest('.af-toggle');
      if (!input) return;
      var tr = input.closest('tr');
      var fid = tr.getAttribute('data-fid');
      var enabled = input.checked;
      input.disabled = true;
      jfetch('/api/audit/flows/' + fid + '/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled })
      }).then(function (flow) {
        byId[fid] = flow;
        for (var i = 0; i < FLOWS.length; i++) if (FLOWS[i].id === fid) FLOWS[i] = flow;
        input.disabled = false;
        input.checked = flow.enabled;
        // Refresh the schedule / next-run cells.
        tr.children[7].innerHTML = flow.schedule_cron ? '<span class="af-cron">' + esc(flow.schedule_cron) + '</span>' : '<span class="af-dim">—</span>';
        tr.children[8].innerHTML = esc(fmtDateTime(flow.next_run_at));
        toast(enabled ? 'Flow enabled' : 'Flow disabled');
      }).catch(function (e) {
        input.disabled = false;
        input.checked = !enabled;
        toast(e.message, 'error');
      });
    });

    // ── Run now ──
    mount.addEventListener('click', function (ev) {
      var btn = ev.target.closest('.af-run');
      if (!btn) return;
      var tr = btn.closest('tr');
      var fid = tr.getAttribute('data-fid');
      var f = byId[fid];
      var priorRunId = f && f.latest_run ? f.latest_run.id : null;
      btn.disabled = true;
      tr.querySelector('.af-status-cell').innerHTML = '<span class="af-pill af-pill--running">running</span>';
      jfetch('/api/audit/flows/' + fid + '/run', { method: 'POST' })
        .then(function () {
          toast('Run started');
          pollForRun(fid, priorRunId, btn);
        })
        .catch(function (e) {
          btn.disabled = false;
          tr.querySelector('.af-status-cell').innerHTML = statusPill(f ? f.last_status : 'never_run');
          toast(e.message, 'error');
        });
    });

    function pollForRun(fid, priorRunId, btn) {
      var tries = 0;
      var MAX = 100; // ~5 min at 3s
      var timer = setInterval(function () {
        tries++;
        jfetch('/api/audit/flows/' + fid + '/runs').then(function (data) {
          var runs = (data && data.runs) || [];
          var latest = runs[0];
          if (latest && latest.id !== priorRunId && latest.status !== 'running') {
            clearInterval(timer);
            var f = byId[fid];
            if (f) {
              f.last_status = latest.status;
              f.latest_run = {
                id: latest.id,
                status: latest.status,
                assertions_total: latest.assertions_total,
                assertions_passed: latest.assertions_passed
              };
              updateRow(f);
            }
            toast('Run ' + latest.status);
          } else if (tries >= MAX) {
            clearInterval(timer);
            if (btn) btn.disabled = false;
            toast('Run is taking longer than expected; check back shortly.', 'error');
          }
        }).catch(function () {
          if (tries >= MAX) { clearInterval(timer); if (btn) btn.disabled = false; }
        });
      }, 3000);
    }

    // ── Controls ──
    var searchEl = document.getElementById('afSearch');
    if (searchEl) searchEl.addEventListener('input', function () {
      search = this.value.trim().toLowerCase();
      render();
    });
    var chips = document.getElementById('afStatusFilter');
    if (chips) chips.addEventListener('click', function (ev) {
      var chip = ev.target.closest('.af-fchip');
      if (!chip) return;
      chips.querySelectorAll('.af-fchip').forEach(function (c) { c.classList.remove('is-active'); });
      chip.classList.add('is-active');
      statusFilter = chip.getAttribute('data-status');
      render();
    });

    render();

    // Enrich with latest_run summaries from the API.
    jfetch('/api/audit/flows').then(function (data) {
      var fresh = (data && data.flows) || [];
      FLOWS = fresh;
      byId = {};
      FLOWS.forEach(function (f) { byId[f.id] = f; });
      render();
    }).catch(function () { /* keep seeded render */ });
  }

  /* ==========================================================
     VENDORS — profile + rule book + custom rules, per vendor
     ========================================================== */
  function initVendors() {
    var root = document.getElementById('afVendors');
    if (!root) return;

    var VENDORS = (window.__VENDORS__ || []).slice();
    var CATALOG = window.__VENDOR_CATALOG__ || [];
    var PLATFORMS = [];      // rule-book summaries from /api/tag-rulebook/platforms
    var PLATFORM_INDEX = {}; // platform slug -> summary
    var RB_CACHE = {};       // platform slug -> full serialized rule book
    var CUSTOM_RULES = null; // all project custom rules (lazy)
    var current = null;      // selected vendor object, or null = new

    var els = {
      list: document.getElementById('afVList'),
      catalog: document.getElementById('afVCatalog'),
      title: document.getElementById('afVFormTitle'),
      badges: document.getElementById('afVDetailBadges'),
      tabs: document.getElementById('afVTabs'),
      name: document.getElementById('afVName'),
      slug: document.getElementById('afVSlug'),
      platform: document.getElementById('afVPlatform'),
      pattern: document.getElementById('afVPattern'),
      desc: document.getElementById('afVDesc'),
      params: document.getElementById('afVParams'),
      seed: document.getElementById('afVSeedBtn'),
      del: document.getElementById('afVDeleteBtn'),
      rbPane: document.getElementById('afVRulebook'),
      rbCount: document.getElementById('afVRbCount'),
      crCount: document.getElementById('afVCrCount'),
      crList: document.getElementById('afVCustomList')
    };

    // ── Tabs ──
    function setTab(name) {
      root.querySelectorAll('[data-vpane]').forEach(function (p) {
        p.hidden = p.getAttribute('data-vpane') !== name;
      });
      els.tabs.querySelectorAll('.af-vtab').forEach(function (t) {
        t.classList.toggle('is-active', t.getAttribute('data-vtab') === name);
      });
      if (name === 'rulebook') renderRulebookPane();
      if (name === 'custom') renderCustomPane();
    }
    els.tabs.addEventListener('click', function (ev) {
      var t = ev.target.closest('.af-vtab');
      if (t) setTab(t.getAttribute('data-vtab'));
    });

    // ── Platform helpers ──
    function vendorPlatform(v) {
      if (!v) return null;
      if (v.catalog_slug && PLATFORM_INDEX[v.catalog_slug]) return v.catalog_slug;
      if (v.slug && PLATFORM_INDEX[v.slug]) return v.slug;
      return null;
    }
    // Live value while editing (the Profile select is the source of truth).
    function selectedPlatform() { return (els.platform.value || '').trim() || null; }

    function fetchRb(platform) {
      if (RB_CACHE[platform]) return Promise.resolve(RB_CACHE[platform]);
      return jfetch('/api/tag-rulebook/platforms/' + encodeURIComponent(platform)).then(function (rb) {
        RB_CACHE[platform] = rb;
        return rb;
      });
    }

    function populatePlatformSelect() {
      var opts = ['<option value="">— none —</option>'].concat(PLATFORMS.map(function (p) {
        return '<option value="' + esc(p.platform) + '">' + esc(p.display_name) +
          ' (' + p.event_count + ' events · ' + p.global_rule_count + ' rules)</option>';
      }));
      var cur = els.platform.value;
      els.platform.innerHTML = opts.join('');
      els.platform.value = cur || '';
    }

    // ── Header badges + tab counts ──
    function renderBadges() {
      var platform = selectedPlatform();
      var bits = [];
      if (platform && PLATFORM_INDEX[platform]) {
        var p = PLATFORM_INDEX[platform];
        bits.push('<span class="af-vbadge">' + esc(p.display_name) + '</span>');
        if (p.spec_version) bits.push('<span class="af-vbadge af-vbadge-muted">' + esc(p.spec_version) + '</span>');
        els.rbCount.hidden = false;
        els.rbCount.textContent = String((p.event_count || 0) + (p.global_rule_count || 0));
      } else {
        els.rbCount.hidden = true;
      }
      els.badges.innerHTML = bits.join('');
      els.seed.hidden = !platform;
      var filtered = filteredCustomRules();
      els.crCount.hidden = filtered === null || !filtered.length;
      if (filtered && filtered.length) els.crCount.textContent = String(filtered.length);
    }

    // ── Left rail ──
    function renderList() {
      if (!VENDORS.length) {
        els.list.innerHTML = '<div class="af-empty-inline" style="padding:8px 2px;">No vendors yet.</div>';
      } else {
        els.list.innerHTML = VENDORS.map(function (v) {
          var pc = (v.params || []).length;
          var platform = vendorPlatform(v);
          var badge = platform && PLATFORM_INDEX[platform]
            ? '<span class="af-vitem-badge">' + esc(PLATFORM_INDEX[platform].display_name) + '</span>'
            : '';
          return '<button type="button" class="af-vitem' + (current && current.id === v.id ? ' is-active' : '') +
            '" data-vid="' + esc(v.id) + '">' +
            '<div class="af-vitem-name">' + esc(v.name) + badge + '</div>' +
            '<div class="af-vitem-sub">' + esc(v.url_pattern) + ' · ' + pc + ' param' + (pc === 1 ? '' : 's') + '</div>' +
            '</button>';
        }).join('');
      }
    }

    function renderCatalog() {
      els.catalog.innerHTML = CATALOG.slice(0, 40).map(function (c) {
        return '<button type="button" class="af-catalog-pick" data-slug="' + esc(c.slug) +
          '" data-name="' + esc(c.display_name) + '">' + esc(c.display_name) + '</button>';
      }).join('');
    }

    // ── Profile form ──
    function paramRow(p) {
      p = p || {};
      var srcQuery = (p.source || 'query') === 'query';
      return '<tr>' +
        '<td><input class="af-input af-input-sm p-label" value="' + esc(p.label || '') + '" placeholder="Event name"/></td>' +
        '<td><input class="af-input af-input-sm mono p-key" value="' + esc(p.key || '') + '" placeholder="en"/></td>' +
        '<td><select class="af-select af-input-sm p-source">' +
          '<option value="query"' + (srcQuery ? ' selected' : '') + '>query</option>' +
          '<option value="auto"' + (!srcQuery ? ' selected' : '') + '>auto</option>' +
        '</select></td>' +
        '<td><input class="af-input af-input-sm p-default" value="' + esc(p.default || '') + '" placeholder="—"/></td>' +
        '<td><input class="af-input af-input-sm p-hint" value="' + esc(p.hint || '') + '" placeholder="optional"/></td>' +
        '<td><button type="button" class="af-remove-x p-remove" title="Remove">&times;</button></td>' +
      '</tr>';
    }

    function renderParams(params) {
      els.params.innerHTML = (params || []).map(paramRow).join('');
    }

    function loadForm(v) {
      current = v;
      els.title.textContent = v ? ('Editing: ' + v.name) : 'New vendor';
      els.name.value = v ? v.name : '';
      els.slug.value = v ? v.slug : '';
      els.platform.value = (v && vendorPlatform(v)) || (v && v.catalog_slug) || '';
      if (els.platform.value && !PLATFORM_INDEX[els.platform.value]) els.platform.value = '';
      els.pattern.value = v ? v.url_pattern : '';
      els.desc.value = v ? (v.description || '') : '';
      renderParams(v ? v.params : []);
      els.del.hidden = !v;
      renderList();
      renderBadges();
      setTab('profile');
    }

    function collectParams() {
      var out = [];
      els.params.querySelectorAll('tr').forEach(function (tr) {
        var key = tr.querySelector('.p-key').value.trim();
        if (!key) return;
        var p = {
          label: tr.querySelector('.p-label').value.trim(),
          key: key,
          source: tr.querySelector('.p-source').value
        };
        var def = tr.querySelector('.p-default').value;
        var hint = tr.querySelector('.p-hint').value.trim();
        if (def !== '') p.default = def;
        if (hint) p.hint = hint;
        out.push(p);
      });
      return out;
    }

    function slugify(s) {
      return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    }

    // ── Rule book pane ──
    function renderRulebookPane() {
      var platform = selectedPlatform();
      if (!platform) {
        els.rbPane.innerHTML = '<div class="af-empty-inline">Link a rule book platform on the Profile tab to see its spec here.</div>';
        return;
      }
      els.rbPane.innerHTML = '<div class="af-empty-inline">Loading rule book…</div>';
      fetchRb(platform).then(function (rb) {
        if (selectedPlatform() !== platform) return; // switched away meanwhile
        var html = '';
        html += '<div class="af-rb-head">' +
          '<div><div class="af-rb-title">' + esc(rb.display_name) + '</div>' +
          '<div class="af-rb-sub">' + esc(rb.spec_version || '') +
            (rb.docs_url ? ' · <a href="' + esc(rb.docs_url) + '" target="_blank" rel="noopener">Docs ↗</a>' : '') +
          '</div></div>' +
          '<div class="af-rb-stats">' + (rb.event_count || 0) + ' events · ' + (rb.global_rules || []).length + ' global rules</div>' +
        '</div>';
        if ((rb.detection_patterns || []).length) {
          html += '<div class="af-rb-section">Detection patterns</div>' +
            '<div class="af-rb-patterns">' + rb.detection_patterns.map(function (p) {
              return '<code>' + esc(p) + '</code>';
            }).join(' ') + '</div>';
        }
        if ((rb.global_rules || []).length) {
          html += '<div class="af-rb-section">Global rules</div>';
          html += rb.global_rules.map(function (r) {
            return '<div class="af-rb-rule" data-sev="' + esc(r.severity || 'info') + '">' +
              '<div class="af-rb-rule-head"><code>' + esc(r.rule_id) + '</code>' +
              '<span class="af-rb-sev">' + esc(r.severity || '') + '</span></div>' +
              '<div class="af-rb-rule-desc">' + esc(r.description || '') + '</div>' +
              (r.remediation ? '<div class="af-rb-rule-fix">Fix: ' + esc(r.remediation) + '</div>' : '') +
            '</div>';
          }).join('');
        }
        if ((rb.events || []).length) {
          html += '<div class="af-rb-section">Event specs</div>';
          html += rb.events.map(function (ev) {
            var req = (ev.required_params || []).map(function (p) { return p.name; });
            var rec = (ev.recommended_params || []).map(function (p) { return p.name; });
            return '<details class="af-rb-event"><summary><code>' + esc(ev.event_name) + '</code>' +
              '<span class="af-rb-event-meta">' + req.length + ' required · ' + rec.length + ' recommended</span></summary>' +
              (req.length ? '<div class="af-rb-plist"><strong>Required:</strong> ' + req.map(esc).join(', ') + '</div>' : '') +
              (rec.length ? '<div class="af-rb-plist"><strong>Recommended:</strong> ' + rec.map(esc).join(', ') + '</div>' : '') +
              (ev.notes ? '<div class="af-rb-plist">' + esc(ev.notes) + '</div>' : '') +
            '</details>';
          }).join('');
        }
        els.rbPane.innerHTML = html;
      }).catch(function () {
        els.rbPane.innerHTML = '<div class="af-empty-inline">Could not load the rule book.</div>';
      });
    }

    // ── Seed profile from rule book ──
    els.seed.addEventListener('click', function () {
      var platform = selectedPlatform();
      if (!platform) return;
      fetchRb(platform).then(function (rb) {
        if (!els.pattern.value.trim() && (rb.detection_patterns || []).length) {
          els.pattern.value = rb.detection_patterns[0];
        }
        // Merge unique required params across event specs into the params table.
        var have = {};
        collectParams().forEach(function (p) { have[p.key] = true; });
        var added = 0;
        (rb.events || []).forEach(function (ev) {
          (ev.required_params || []).forEach(function (p) {
            if (have[p.name] || added >= 15) return;
            have[p.name] = true;
            added++;
            els.params.insertAdjacentHTML('beforeend', paramRow({ label: p.name, key: p.name, source: 'query', hint: p.type || '' }));
          });
        });
        toast(added ? ('Seeded ' + added + ' params from ' + rb.display_name) : 'Nothing new to seed');
      }).catch(function () { toast('Could not load the rule book', 'error'); });
    });

    // ── Custom rules pane ──
    function filteredCustomRules() {
      if (CUSTOM_RULES === null) return null;
      var platform = selectedPlatform();
      var vslug = current ? current.slug : null;
      return CUSTOM_RULES.filter(function (r) {
        return (platform && r.platform === platform) || (vslug && r.platform === vslug) || r.platform === '*';
      });
    }

    function loadCustomRules() {
      return jfetch('/api/custom-rules').then(function (data) {
        CUSTOM_RULES = data.rules || [];
        renderBadges();
      }).catch(function () { CUSTOM_RULES = []; });
    }

    function renderCustomPane() {
      var run = (CUSTOM_RULES === null) ? loadCustomRules() : Promise.resolve();
      els.crList.innerHTML = '<div class="af-empty-inline">Loading…</div>';
      run.then(function () {
        var rules = filteredCustomRules() || [];
        if (!rules.length) {
          els.crList.innerHTML = '<div class="af-empty-inline">No custom rules for this vendor yet — add one below.</div>';
          return;
        }
        els.crList.innerHTML = rules.map(function (r) {
          var req = (r.required_params || []).join(', ');
          var forb = (r.forbidden_params || []).join(', ');
          return '<div class="af-cr-row" data-sev="' + esc(r.severity || 'warning') + '">' +
            '<div class="af-cr-main">' +
              '<div class="af-cr-name">' + esc(r.name || r.rule_id) +
                '<span class="af-rb-sev">' + esc(r.severity || '') + '</span>' +
                (r.platform === '*' ? '<span class="af-vbadge af-vbadge-muted">all platforms</span>' : '') +
              '</div>' +
              '<div class="af-cr-meta mono">' + esc(r.platform) + ' · ' + esc(r.event || '*') +
                (req ? ' · requires: ' + esc(req) : '') + (forb ? ' · forbids: ' + esc(forb) : '') + '</div>' +
              (r.remediation ? '<div class="af-cr-fix">Fix: ' + esc(r.remediation) + '</div>' : '') +
            '</div>' +
            '<button type="button" class="af-remove-x af-cr-delete" data-rule-id="' + esc(r.rule_id) + '" title="Delete rule">&times;</button>' +
          '</div>';
        }).join('');
      });
    }

    els.crList.addEventListener('click', function (ev) {
      var btn = ev.target.closest('.af-cr-delete');
      if (!btn) return;
      var ruleId = btn.getAttribute('data-rule-id');
      if (!confirm('Delete this custom rule? Every audit and flow using it stops enforcing it.')) return;
      jfetch('/api/custom-rules/' + encodeURIComponent(ruleId), { method: 'DELETE' }).then(function () {
        CUSTOM_RULES = (CUSTOM_RULES || []).filter(function (r) { return r.rule_id !== ruleId; });
        renderCustomPane();
        renderBadges();
        toast('Custom rule deleted');
      }).catch(function (e) { toast(e.message, 'error'); });
    });

    document.getElementById('afCrSaveBtn').addEventListener('click', function () {
      var name = document.getElementById('afCrName').value.trim();
      if (!name) { toast('Rule name is required', 'error'); return; }
      var platform = selectedPlatform() || (current && current.slug) || '*';
      var csv = function (id) {
        return document.getElementById(id).value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      };
      var body = {
        rule_id: 'custom.' + slugify(name),
        platform: platform,
        event: document.getElementById('afCrEvent').value.trim() || '*',
        name: name,
        severity: document.getElementById('afCrSeverity').value,
        required_params: csv('afCrRequired'),
        forbidden_params: csv('afCrForbidden'),
        param_assertions: [],
        remediation: document.getElementById('afCrRemediation').value.trim() || null
      };
      jfetch('/api/custom-rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function () {
        ['afCrName', 'afCrEvent', 'afCrRequired', 'afCrForbidden', 'afCrRemediation'].forEach(function (id) {
          document.getElementById(id).value = '';
        });
        return loadCustomRules();
      }).then(function () {
        renderCustomPane();
        toast('Custom rule saved');
      }).catch(function (e) { toast(e.message, 'error'); });
    });

    // ── Save vendor ──
    document.getElementById('afVSaveBtn').addEventListener('click', function () {
      var name = els.name.value.trim();
      var vslug = els.slug.value.trim().toLowerCase();
      var pattern = els.pattern.value.trim();
      if (!name) { toast('Name is required', 'error'); return; }
      if (!vslug) { toast('Slug is required', 'error'); return; }
      if (!pattern) { toast('URL pattern is required', 'error'); return; }
      var body = {
        name: name, slug: vslug, url_pattern: pattern,
        description: els.desc.value.trim() || null,
        params: collectParams(),
        catalog_slug: selectedPlatform()
      };
      var editing = current && current.id;
      var url = editing ? '/api/audit/vendors/' + current.id : '/api/audit/vendors';
      jfetch(url, {
        method: editing ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function (v) {
        if (editing) {
          for (var i = 0; i < VENDORS.length; i++) if (VENDORS[i].id === v.id) VENDORS[i] = v;
        } else {
          VENDORS.push(v);
        }
        VENDORS.sort(function (a, b) { return (a.name || '').localeCompare(b.name || ''); });
        loadForm(v);
        toast('Vendor saved');
      }).catch(function (e) { toast(e.message, 'error'); });
    });

    // ── Delete vendor ──
    els.del.addEventListener('click', function () {
      if (!current || !current.id) return;
      if (!confirm('Delete vendor "' + current.name + '"?')) return;
      jfetch('/api/audit/vendors/' + current.id, { method: 'DELETE' }).then(function () {
        VENDORS = VENDORS.filter(function (v) { return v.id !== current.id; });
        loadForm(null);
        toast('Vendor deleted');
      }).catch(function (e) { toast(e.message, 'error'); });
    });

    // ── Add new ──
    document.getElementById('afVAddBtn').addEventListener('click', function () { loadForm(null); });

    // ── Add param row ──
    document.getElementById('afVAddParam').addEventListener('click', function () {
      els.params.insertAdjacentHTML('beforeend', paramRow({}));
    });
    els.params.addEventListener('click', function (ev) {
      var rm = ev.target.closest('.p-remove');
      if (rm) rm.closest('tr').remove();
    });

    // ── Select existing vendor ──
    els.list.addEventListener('click', function (ev) {
      var item = ev.target.closest('.af-vitem');
      if (!item) return;
      var vid = item.getAttribute('data-vid');
      var v = VENDORS.filter(function (x) { return x.id === vid; })[0];
      if (v) loadForm(v);
    });

    // ── Catalog quick-pick: link + seed from the rule book when one matches ──
    els.catalog.addEventListener('click', function (ev) {
      var pick = ev.target.closest('.af-catalog-pick');
      if (!pick) return;
      loadForm(null);
      var cslug = pick.getAttribute('data-slug');
      els.name.value = pick.getAttribute('data-name');
      els.slug.value = slugify(cslug);
      if (PLATFORM_INDEX[cslug]) {
        els.platform.value = cslug;
        renderBadges();
        els.seed.click(); // pre-fill url_pattern + params from the rule book
      } else {
        els.pattern.focus();
      }
    });

    // ── Platform select change ──
    els.platform.addEventListener('change', function () { renderBadges(); });

    // ── Boot: load rule-book platform list, then render ──
    jfetch('/api/tag-rulebook/platforms').then(function (data) {
      PLATFORMS = data.platforms || [];
      PLATFORMS.forEach(function (p) { PLATFORM_INDEX[p.platform] = p; });
      populatePlatformSelect();
      renderList();
      loadForm(current || VENDORS[0] || null);
    }).catch(function () { /* keep the basic editor working without rule books */ });

    loadCustomRules();
    renderList();
    renderCatalog();
    loadForm(VENDORS[0] || null);
  }

  function boot() { initFlows(); initVendors(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
