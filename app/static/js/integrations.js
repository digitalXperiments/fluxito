/* integrations.js — OAuth app credentials management UI
   Vanilla JS, no framework. Relies on Fluxito.toast() from app.js and
   the CSRF auto-header injected by base.html. */

(function () {
  'use strict';

  // Brand gradient map per platform (matches --p-<platform> CSS vars)
  var BRAND = {
    google:    { bg: 'linear-gradient(135deg,#4285F4,#1a6fd8)', color: '#fff', shadow: '0 4px 20px rgba(66,133,244,0.35)' },
    meta:      { bg: 'linear-gradient(135deg,#0866FF,#0052cc)', color: '#fff', shadow: '0 4px 20px rgba(8,102,255,0.35)' },
    tiktok:    { bg: 'linear-gradient(135deg,#FE2C55,#010101)', color: '#fff', shadow: '0 4px 20px rgba(254,44,85,0.35)' },
    snap:      { bg: 'linear-gradient(135deg,#FFFC00,#f5e900)', color: '#1A1A1A', shadow: '0 4px 20px rgba(255,252,0,0.35)' },
    linkedin:  { bg: 'linear-gradient(135deg,#0A66C2,#00438a)', color: '#fff', shadow: '0 4px 20px rgba(10,102,194,0.35)' },
    pinterest: { bg: 'linear-gradient(135deg,#E60023,#ad001a)', color: '#fff', shadow: '0 4px 20px rgba(230,0,35,0.35)' },
    reddit:    { bg: 'linear-gradient(135deg,#FF4500,#cc3700)', color: '#fff', shadow: '0 4px 20px rgba(255,69,0,0.35)' },
    x:         { bg: 'linear-gradient(135deg,#111111,#555555)', color: '#fff', shadow: '0 4px 20px rgba(0,0,0,0.25)' },
    bing:      { bg: 'linear-gradient(135deg,#008373,#00614f)', color: '#fff', shadow: '0 4px 20px rgba(0,131,115,0.35)' },
  };

  var _currentPlatform = null;
  var _currentSource = null;

  // ── Refresh card state from API ─────────────────────────────────────────

  function loadIntegrations() {
    fetch('/api/integrations', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        items.forEach(function (item) { _updateCard(item); });
      })
      .catch(function (err) {
        console.warn('integrations refresh failed', err);
      });
  }

  function _updateCard(item) {
    var card = document.getElementById('intg-card-' + item.platform);
    if (!card) return;
    card.setAttribute('data-source', item.source || 'unconfigured');

    var badge = card.querySelector('.intg-status-row');
    if (badge) {
      badge.innerHTML = _statusBadge(item.source);
    }
    var idRow = card.querySelector('.intg-id-row');
    if (idRow) {
      if (item.client_id_masked) {
        idRow.innerHTML = '<span class="intg-id-label">Client ID</span><span class="intg-id-value">' +
          _esc(item.client_id_masked) + '</span>';
        idRow.style.display = '';
      } else {
        idRow.style.display = 'none';
      }
    }
    var updated = card.querySelector('.intg-updated');
    if (updated) {
      updated.textContent = (item.source === 'db' && item.updated_at)
        ? 'Updated ' + _fmtDate(item.updated_at)
        : ' ';
    }
    // Show/hide remove button
    var removeBtn = card.querySelector('.intg-remove-btn');
    if (removeBtn) {
      removeBtn.style.display = item.source === 'db' ? '' : 'none';
    } else if (item.source === 'db') {
      var actions = card.querySelector('.intg-actions');
      if (actions && !actions.querySelector('.intg-remove-btn')) {
        var btn = document.createElement('button');
        btn.className = 'btn sm intg-remove-btn';
        btn.textContent = 'Remove';
        btn.setAttribute('onclick', 'IntgUI.removeCredentials("' + item.platform + '")');
        actions.appendChild(btn);
      }
    }
  }

  function _statusBadge(source) {
    if (source === 'db') return '<span class="intg-badge intg-badge-db">Configured (DB)</span>';
    if (source === 'env') return '<span class="intg-badge intg-badge-env">Configured (env)</span>';
    return '<span class="intg-badge intg-badge-none">Not configured</span>';
  }

  // ── Modal open/close ────────────────────────────────────────────────────

  function openModal(platform) {
    _currentPlatform = platform;
    _currentSource = null;

    // Reset form
    var form = document.getElementById('intgForm');
    if (form) form.reset();
    _setTestResult(null);

    // Show google dev token field only for google
    var devWrap = document.getElementById('intgDevTokenWrap');
    if (devWrap) devWrap.style.display = platform === 'google' ? '' : 'none';

    // Apply brand style to header
    var head = document.getElementById('intgModalHead');
    var b = BRAND[platform] || { bg: 'var(--bg-2)', color: 'var(--ink)', shadow: 'none' };
    if (head) {
      head.style.background = b.bg;
      head.style.color = b.color;
      head.style.boxShadow = b.shadow;
    }

    var badgeEl = document.getElementById('intgModalBadge');
    if (badgeEl) { badgeEl.className = 'pbadge ' + platform; }

    var titleEl = document.getElementById('intgModalTitle');
    if (titleEl) titleEl.textContent = _capitalize(platform);

    // Fetch platform detail and populate redirect URIs
    fetch('/api/integrations/' + platform, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _currentSource = data.source || 'unconfigured';

        // Redirect URI list
        var uriEl = document.getElementById('intgRedirectUris');
        if (uriEl) {
          uriEl.innerHTML = (data.redirect_uris || []).map(function (u) {
            return '<div class="intg-uri-row">' +
              '<code class="intg-uri-code">' + _esc(u) + '</code>' +
              '<button type="button" class="intg-copy-btn" onclick="Fluxito.copy(' + JSON.stringify(u) + ',\'URI\')" aria-label="Copy URI">' +
              '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>' +
              '</button></div>';
          }).join('');
        }

        // Console link
        var consoleLink = document.getElementById('intgConsoleLink');
        if (consoleLink) consoleLink.href = data.dev_console_url || '#';

        // Setup guide link
        var guideLink = document.getElementById('intgGuideLink');
        var guideAnchor = document.getElementById('intgGuideAnchor');
        var slugs = data.tutorial_slugs || [];
        if (guideLink && guideAnchor && slugs.length) {
          guideAnchor.href = '/tutorials/' + slugs[0];
          guideLink.style.display = '';
        } else if (guideLink) {
          guideLink.style.display = 'none';
        }

        // Remove button in footer
        var removeBtn = document.getElementById('intgRemoveBtn');
        if (removeBtn) removeBtn.style.display = _currentSource === 'db' ? '' : 'none';
      })
      .catch(function (err) {
        Fluxito.toast('Could not load platform details', 'error');
        console.error(err);
      });

    // Show modal
    var backdrop = document.getElementById('intgModalBackdrop');
    if (backdrop) backdrop.classList.add('is-open');
    document.body.style.overflow = 'hidden';
  }

  function closeModal() {
    var backdrop = document.getElementById('intgModalBackdrop');
    if (backdrop) backdrop.classList.remove('is-open');
    document.body.style.overflow = '';
    _currentPlatform = null;
    _currentSource = null;
  }

  // ── Save ────────────────────────────────────────────────────────────────

  function handleSave(event) {
    event.preventDefault();
    if (!_currentPlatform) return;
    var form = event.target;
    var clientId = (form.elements.client_id && form.elements.client_id.value || '').trim();
    var clientSecret = (form.elements.client_secret && form.elements.client_secret.value || '').trim();
    var devToken = (form.elements.developer_token && form.elements.developer_token.value || '').trim();

    var body = { client_id: clientId, client_secret: clientSecret };
    if (_currentPlatform === 'google' && devToken) {
      body.extra = { developer_token: devToken };
    }

    var saveBtn = document.getElementById('intgSaveBtn');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

    fetch('/api/integrations/' + _currentPlatform, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'Save failed'); });
        return r.json();
      })
      .then(function () {
        Fluxito.toast(_capitalize(_currentPlatform) + ' credentials saved');
        closeModal();
        loadIntegrations();
      })
      .catch(function (err) {
        Fluxito.toast(err.message || 'Save failed', 'error');
      })
      .finally(function () {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
      });
  }

  // ── Test ────────────────────────────────────────────────────────────────

  function testCredentials() {
    if (!_currentPlatform) return;
    _setTestResult({ loading: true });

    fetch('/api/integrations/' + _currentPlatform + '/test', {
      method: 'POST',
      credentials: 'same-origin',
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _setTestResult({ ok: data.ok, issues: data.issues || [] });
      })
      .catch(function (err) {
        _setTestResult({ ok: false, issues: [err.message || 'Test request failed'] });
      });
  }

  function _setTestResult(result) {
    var el = document.getElementById('intgTestResult');
    if (!el) return;
    if (!result) { el.style.display = 'none'; el.innerHTML = ''; return; }
    el.style.display = '';
    if (result.loading) {
      el.className = 'intg-test-result intg-test-loading';
      el.textContent = 'Testing…';
      return;
    }
    el.className = 'intg-test-result ' + (result.ok ? 'intg-test-ok' : 'intg-test-fail');
    var lines = result.issues && result.issues.length
      ? result.issues.map(function (i) { return '<li>' + _esc(i) + '</li>'; }).join('')
      : '';
    el.innerHTML = (result.ok ? '&#10003; Credentials look valid' : '&#10007; Issues found') +
      (lines ? '<ul class="intg-test-issues">' + lines + '</ul>' : '');
  }

  // ── Remove ──────────────────────────────────────────────────────────────

  function removeCredentials(platform) {
    if (!window.confirm('Remove ' + _capitalize(platform) + ' credentials from the database? The env fallback will resume if set.')) return;
    _doRemove(platform, false);
  }

  function removeFromModal() {
    if (!_currentPlatform) return;
    if (!window.confirm('Remove ' + _capitalize(_currentPlatform) + ' credentials from the database? The env fallback will resume if set.')) return;
    _doRemove(_currentPlatform, true);
  }

  function _doRemove(platform, closeAfter) {
    fetch('/api/integrations/' + platform, { method: 'DELETE', credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'Remove failed'); });
        return r.json();
      })
      .then(function () {
        Fluxito.toast(_capitalize(platform) + ' credentials removed');
        if (closeAfter) closeModal();
        loadIntegrations();
      })
      .catch(function (err) {
        Fluxito.toast(err.message || 'Remove failed', 'error');
      });
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function toggleSecret() {
    var inp = document.getElementById('intgClientSecret');
    if (!inp) return;
    inp.type = inp.type === 'password' ? 'text' : 'password';
  }

  function _capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  function _esc(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function _fmtDate(iso) {
    try {
      var d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch (_) { return iso; }
  }

  // Close modal on backdrop click
  document.addEventListener('click', function (e) {
    var backdrop = document.getElementById('intgModalBackdrop');
    if (backdrop && e.target === backdrop) closeModal();
  });

  // Close on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeModal();
  });

  // Refresh on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadIntegrations);
  } else {
    loadIntegrations();
  }

  // Public API
  window.IntgUI = {
    openModal: openModal,
    closeModal: closeModal,
    handleSave: handleSave,
    testCredentials: testCredentials,
    removeCredentials: removeCredentials,
    removeFromModal: removeFromModal,
    toggleSecret: toggleSecret,
  };
})();
