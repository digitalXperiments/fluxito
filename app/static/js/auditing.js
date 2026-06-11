/* ==========================================================
   Fluxito — Auditing Platform JavaScript
   Handles: tab switching, Rule Book modal, custom rules CRUD,
            search/filter, and the "Run Audit in Claude" button.
   ========================================================== */

(function () {
  'use strict';

  // ── Tab switching ────────────────────────────────────────
  var tabs = document.querySelectorAll('.audit-tab');
  var panels = document.querySelectorAll('.audit-tab-panel');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.dataset.tab;
      tabs.forEach(function (t) { t.classList.remove('is-active'); });
      panels.forEach(function (p) { p.classList.remove('is-active'); });
      tab.classList.add('is-active');
      var panel = document.getElementById('tab-' + target);
      if (panel) panel.classList.add('is-active');

      if (target === 'custom') loadCustomRules();
    });
  });

  // ── Rule Book search ─────────────────────────────────────
  var rbSearch = document.getElementById('rbSearch');
  if (rbSearch) {
    rbSearch.addEventListener('input', function () {
      var q = rbSearch.value.toLowerCase().trim();
      document.querySelectorAll('.rb-card').forEach(function (card) {
        var name = (card.dataset.name || '') + ' ' + (card.dataset.platform || '');
        card.classList.toggle('is-hidden', q.length > 0 && !name.includes(q));
      });
    });
  }

  // ── Rule Book modal ──────────────────────────────────────
  var rbModal = document.getElementById('rbModal');
  var rbModalTitle = document.getElementById('rbModalTitle');
  var rbModalBody = document.getElementById('rbModalBody');
  var rbModalClose = document.getElementById('rbModalClose');

  document.querySelectorAll('.rb-view-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var platform = btn.dataset.platform;
      openRbModal(platform);
    });
  });

  function openRbModal(platform) {
    if (!rbModal) return;
    rbModal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    rbModalTitle.textContent = 'Loading…';
    rbModalBody.innerHTML = '<div class="spinner-center"><div class="spinner"></div></div>';

    fetch('/api/tag-rulebook/platforms/' + platform)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (spec) {
        renderRbModal(spec);
      })
      .catch(function (e) {
        rbModalBody.innerHTML = '<p style="padding:24px;color:var(--muted)">Failed to load rule book: ' + e + '</p>';
      });
  }

  function renderRbModal(spec) {
    rbModalTitle.textContent = spec.display_name + ' — Rule Book';

    var html = '';

    // Header info
    html += '<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:18px">';
    html += '<div><div style="font-size:11px;color:var(--muted);font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">Events</div>';
    html += '<div style="font-size:24px;font-weight:700;font-family:var(--mono);color:var(--ink)">' + (spec.event_count || 0) + '</div></div>';
    html += '<div><div style="font-size:11px;color:var(--muted);font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">Spec Version</div>';
    html += '<div style="font-size:13px;font-weight:600;font-family:var(--mono);color:var(--ink)">' + (spec.spec_version || '—') + '</div></div>';
    if (spec.docs_url) {
      html += '<div style="margin-left:auto;display:flex;align-items:center">';
      html += '<a href="' + spec.docs_url + '" target="_blank" rel="noopener" class="btn ghost sm">Official Docs ↗</a></div>';
    }
    html += '</div>';

    // Global rules
    if (spec.global_rules && spec.global_rules.length) {
      html += '<div style="margin-bottom:16px">';
      html += '<div style="font-size:11px;font-weight:700;font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:8px">Global Rules</div>';
      spec.global_rules.forEach(function (gr) {
        html += '<div style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;background:var(--bg-2);border-radius:8px;margin-bottom:6px">';
        html += '<span class="fr-sev-badge sev-badge--' + gr.severity + '">' + gr.severity + '</span>';
        html += '<div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;color:var(--ink)">' + gr.description + '</div>';
        if (gr.remediation) {
          html += '<div style="font-size:12px;color:var(--muted);margin-top:4px">' + gr.remediation + '</div>';
        }
        html += '</div></div>';
      });
      html += '</div>';
    }

    // Events
    if (spec.events && spec.events.length) {
      html += '<div style="font-size:11px;font-weight:700;font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:10px">Events (' + spec.events.length + ')</div>';
      html += '<div class="rb-modal-events">';
      spec.events.forEach(function (ev) {
        html += '<details class="rb-event-row">';
        html += '<summary class="rb-event-summary">';
        html += '<span style="font-family:var(--mono);font-weight:700;color:var(--ink)">' + ev.event_name + '</span>';
        if (ev.aliases && ev.aliases.length) {
          html += '<span style="font-size:11px;color:var(--muted)">alias: ' + ev.aliases.join(', ') + '</span>';
        }
        html += '<span class="fr-sev-badge sev-badge--' + ev.severity_if_missing_required + '" style="margin-left:8px">' + ev.severity_if_missing_required + '</span>';
        html += '<span class="rb-event-chevron"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></span>';
        html += '</summary>';

        html += '<div class="rb-event-body">';
        if (ev.notes) {
          html += '<p style="font-size:12px;color:var(--muted);margin-bottom:10px">' + ev.notes + '</p>';
        }

        var allParams = (ev.required_params || []).concat(ev.recommended_params || []);
        if (allParams.length) {
          html += '<div class="rb-param-list">';
          allParams.forEach(function (p) {
            html += '<div class="rb-param-row">';
            html += '<span style="font-weight:600;color:var(--ink)">' + p.name + '</span>';
            html += '<span style="font-size:10px;color:var(--muted)">' + (p.type || 'string') + '</span>';
            if (p.required) {
              html += '<span class="rb-param-badge required">required</span>';
            } else if (p.recommended) {
              html += '<span class="rb-param-badge recommended">recommended</span>';
            }
            if (p.allowed_values && p.allowed_values.length) {
              html += '<span style="font-size:10px;color:var(--muted);flex:1;text-align:right">one of: ' + p.allowed_values.slice(0,4).join(', ') + (p.allowed_values.length > 4 ? '…' : '') + '</span>';
            } else if (p.notes) {
              html += '<span style="font-size:10px;color:var(--muted);flex:1;text-align:right">' + p.notes.slice(0,60) + '</span>';
            }
            html += '</div>';
          });
          html += '</div>';
        } else {
          html += '<p style="font-size:12px;color:var(--muted)">No parameter spec defined.</p>';
        }
        html += '</div></details>';
      });
      html += '</div>';
    }

    rbModalBody.innerHTML = html;
  }

  if (rbModalClose) {
    rbModalClose.addEventListener('click', closeRbModal);
  }
  if (rbModal) {
    rbModal.addEventListener('click', function (e) {
      if (e.target === rbModal) closeRbModal();
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeRbModal(); closeCrModal(); }
  });

  function closeRbModal() {
    if (!rbModal) return;
    rbModal.classList.remove('is-open');
    document.body.style.overflow = '';
  }

  // ── Custom rules ─────────────────────────────────────────
  var crModal = document.getElementById('crModal');
  var crModalClose = document.getElementById('crModalClose');
  var crCancelBtn = document.getElementById('crCancelBtn');
  var crForm = document.getElementById('crForm');
  var addRuleBtn = document.getElementById('addRuleBtn');
  var customRulesList = document.getElementById('customRulesList');

  if (addRuleBtn) {
    addRuleBtn.addEventListener('click', openCrModal);
  }
  if (crModalClose) crModalClose.addEventListener('click', closeCrModal);
  if (crCancelBtn) crCancelBtn.addEventListener('click', closeCrModal);
  if (crModal) {
    crModal.addEventListener('click', function (e) {
      if (e.target === crModal) closeCrModal();
    });
  }

  function openCrModal() {
    if (!crModal) return;
    crModal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    if (crForm) crForm.reset();
  }
  function closeCrModal() {
    if (!crModal) return;
    crModal.classList.remove('is-open');
    document.body.style.overflow = '';
  }

  if (crForm) {
    crForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var data = Object.fromEntries(new FormData(crForm).entries());

      // Build required_params array from csv
      var reqCsv = (data.required_params_csv || '').trim();
      var forbCsv = (data.forbidden_params_csv || '').trim();
      delete data.required_params_csv;
      delete data.forbidden_params_csv;
      data.required_params = reqCsv ? reqCsv.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
      data.forbidden_params = forbCsv ? forbCsv.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
      data.param_assertions = [];
      data.rule_id = 'custom.' + Date.now();

      var saveBtn = document.getElementById('crSaveBtn');
      if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

      fetch('/api/custom-rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
        .then(function (r) { return r.json(); })
        .then(function (result) {
          if (result.error) throw new Error(result.message || 'Save failed');
          closeCrModal();
          loadCustomRules();
          showToast('Custom rule saved', 'success');
        })
        .catch(function (e) {
          showToast('Error: ' + e.message, 'error');
        })
        .finally(function () {
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Rule'; }
        });
    });
  }

  function loadCustomRules() {
    if (!customRulesList) return;
    fetch('/api/custom-rules')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var rules = data.rules || [];
        if (!rules.length) {
          customRulesList.innerHTML = '<div class="audit-empty" style="padding:48px 24px"><div class="audit-empty-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></div><h3>No custom rules</h3><p>Create rules to enforce business-specific requirements.</p></div>';
          return;
        }
        customRulesList.innerHTML = rules.map(function (r) {
          return '<div class="cr-row" data-rule-id="' + r.rule_id + '">' +
            '<div class="cr-row-body">' +
            '<div class="cr-row-name">' + escapeHtml(r.name) + '</div>' +
            '<div class="cr-row-meta">' +
            '<span class="cr-pill ' + (r.severity || '') + '">' + (r.severity || 'warning') + '</span>' +
            '<span class="cr-pill">' + (r.platform || '*') + '</span>' +
            (r.event && r.event !== '*' ? '<span class="cr-pill">' + escapeHtml(r.event) + '</span>' : '') +
            '</div>' +
            (r.description ? '<div style="font-size:12px;color:var(--muted);margin-top:5px">' + escapeHtml(r.description) + '</div>' : '') +
            '</div>' +
            '<button class="cr-row-delete" data-rule-id="' + r.rule_id + '" title="Delete rule" aria-label="Delete rule ' + escapeHtml(r.name) + '">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>' +
            '</button>' +
            '</div>';
        }).join('');

        // Wire delete buttons
        customRulesList.querySelectorAll('.cr-row-delete').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var rid = btn.dataset.ruleId;
            if (!confirm('Delete this rule?')) return;
            fetch('/api/custom-rules/' + encodeURIComponent(rid), { method: 'DELETE' })
              .then(function (r) { return r.json(); })
              .then(function () { loadCustomRules(); showToast('Rule deleted', 'success'); })
              .catch(function () { showToast('Delete failed', 'error'); });
          });
        });
      })
      .catch(function () {
        customRulesList.innerHTML = '<p style="padding:24px;color:var(--muted)">Failed to load custom rules.</p>';
      });
  }

  // ── "Run Audit in Claude" button ─────────────────────────
  function handleRunAudit() {
    showToast('Ask Claude to run_audit or tag_rulebook — results will appear here automatically.', 'info', 5000);
  }
  var runBtns = document.querySelectorAll('#runAuditBtn, #runAuditBtn2');
  runBtns.forEach(function (btn) {
    btn.addEventListener('click', handleRunAudit);
  });

  // ── Toast helper ─────────────────────────────────────────
  function showToast(msg, type, duration) {
    duration = duration || 3500;
    var host = document.querySelector('.toast-host');
    if (!host) return;
    var toast = document.createElement('div');
    toast.className = 'toast toast--' + (type || 'info');
    toast.textContent = msg;
    host.appendChild(toast);
    requestAnimationFrame(function () { toast.classList.add('is-visible'); });
    setTimeout(function () {
      toast.classList.remove('is-visible');
      setTimeout(function () { toast.remove(); }, 350);
    }, duration);
  }

  // ── HTML escaping ─────────────────────────────────────────
  function escapeHtml(s) {
    if (!s) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Animate score rings on load ───────────────────────────
  document.querySelectorAll('.adc-ring-fill').forEach(function (el) {
    var da = el.getAttribute('stroke-dasharray') || '';
    el.setAttribute('stroke-dasharray', '0 113.1');
    setTimeout(function () {
      el.style.transition = 'stroke-dasharray .9s cubic-bezier(.4,0,.2,1)';
      el.setAttribute('stroke-dasharray', da);
    }, 200);
  });

})();
