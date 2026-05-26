/* Fluxito — shared client JS */
(function () {
  'use strict';

  // ---------- Theme (Auto / Light / Dark, persisted in localStorage) ----------
  const root = document.documentElement;
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const THEME_KEY = 'theme';
  const VALID_MODES = ['auto', 'light', 'dark'];

  function getThemeMode() {
    let stored = null;
    try { stored = localStorage.getItem(THEME_KEY); } catch (e) {}
    return VALID_MODES.indexOf(stored) >= 0 ? stored : 'auto';
  }
  function resolveTheme(mode) {
    if (mode === 'light' || mode === 'dark') return mode;
    return mq.matches ? 'dark' : 'light';
  }
  function applyTheme() {
    const mode = getThemeMode();
    root.setAttribute('data-theme', resolveTheme(mode));
    root.setAttribute('data-theme-mode', mode);
    syncThemeUi(mode);
  }
  function syncThemeUi(mode) {
    const label = mode.charAt(0).toUpperCase() + mode.slice(1);
    document.querySelectorAll('[data-theme-label]').forEach((el) => { el.textContent = label; });
    document.querySelectorAll('[data-theme-button]').forEach((btn) => {
      const a = btn.querySelector('.theme-icon-auto');
      const l = btn.querySelector('.theme-icon-light');
      const d = btn.querySelector('.theme-icon-dark');
      if (a) a.style.display = mode === 'auto' ? '' : 'none';
      if (l) l.style.display = mode === 'light' ? '' : 'none';
      if (d) d.style.display = mode === 'dark' ? '' : 'none';
    });
  }
  function setThemeMode(mode) {
    if (VALID_MODES.indexOf(mode) < 0) mode = 'auto';
    try { localStorage.setItem(THEME_KEY, mode); } catch (e) {}
    applyTheme();
  }
  function cycleThemeMode() {
    const cur = getThemeMode();
    const next = VALID_MODES[(VALID_MODES.indexOf(cur) + 1) % VALID_MODES.length];
    setThemeMode(next);
  }
  // Re-resolve when the OS preference changes — but only if the user is on auto.
  mq.addEventListener('change', () => {
    if (getThemeMode() === 'auto') applyTheme();
  });
  // Cross-tab sync — if another tab changes the mode, reflect it here.
  window.addEventListener('storage', (e) => {
    if (e.key === THEME_KEY) applyTheme();
  });
  applyTheme();

  window.Fluxito = window.Fluxito || {};
  window.Fluxito.getThemeMode = getThemeMode;
  window.Fluxito.setThemeMode = setThemeMode;
  window.Fluxito.cycleThemeMode = cycleThemeMode;

  // ---------- Mobile nav ----------
  function initNav() {
    const toggle = document.querySelector('.nav-toggle');
    const links = document.querySelector('.nav-links');
    if (!toggle || !links) return;
    toggle.addEventListener('click', () => {
      links.classList.toggle('is-open');
      const expanded = links.classList.contains('is-open');
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
    document.addEventListener('click', (e) => {
      if (!links.contains(e.target) && !toggle.contains(e.target)) {
        links.classList.remove('is-open');
      }
    });
  }

  // ---------- Toasts (improved — top-right, with icon, longer duration) ----------
  var _svgCheck = '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  var _svgX = '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';

  function toast(msg, kind) {
    let host = document.querySelector('.toast-host');
    if (!host) {
      host = document.createElement('div');
      host.className = 'toast-host';
      document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = 'toast ' + (kind === 'error' ? 'is-error' : 'is-success');
    el.innerHTML = (kind === 'error' ? _svgX : _svgCheck) + '<span>' + _escHtml(msg) + '</span>';
    host.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .25s, transform .25s';
      el.style.opacity = '0';
      el.style.transform = 'translateX(24px)';
      setTimeout(() => el.remove(), 250);
    }, 4000);
  }

  function _escHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
  window.Fluxito.toast = toast;

  // ---------- Copy to clipboard ----------
  window.Fluxito.copy = async function (text, label) {
    try {
      await navigator.clipboard.writeText(text);
      toast((label || 'Copied') + ' to clipboard');
    } catch (e) {
      toast('Copy failed', 'error');
    }
  };

  // ---------- Kebab (three-dot) menu ----------
  window.Fluxito.toggleKebab = function (e) {
    e.preventDefault();
    e.stopPropagation();
    var menu = e.currentTarget.nextElementSibling;
    var isOpen = menu.classList.contains('is-open');
    // close any other open menus first
    document.querySelectorAll('.kebab-menu.is-open').forEach(function (m) { m.classList.remove('is-open'); });
    if (!isOpen) menu.classList.add('is-open');
  };
  // close menus on outside click
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.kebab-wrap')) {
      document.querySelectorAll('.kebab-menu.is-open').forEach(function (m) { m.classList.remove('is-open'); });
    }
  });

  // ---------- Confirm delete ----------
  window.Fluxito.confirmDelete = async function (url, msg, onOk) {
    if (!window.confirm(msg || 'Are you sure you want to delete this?')) return;
    try {
      const res = await fetch(url, { method: 'DELETE', credentials: 'same-origin' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || 'Delete failed', 'error');
        return;
      }
      toast('Deleted');
      if (typeof onOk === 'function') onOk();
      else setTimeout(() => window.location.reload(), 400);
    } catch (e) {
      toast('Network error', 'error');
    }
  };

  // ---------- Time ago ----------
  window.Fluxito.timeAgo = function (iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    if (s < 604800) return Math.floor(s / 86400) + 'd ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  };

  // ---------- Auto refresh dashboards ----------
  window.Fluxito.autoRefresh = function (config) {
    const intervalMs = (config.intervalSec || 60) * 1000;
    const endpoint = config.endpoint;
    const onData = config.onData;
    if (!endpoint || !onData) return;

    async function tick() {
      try {
        const res = await fetch(endpoint, { credentials: 'same-origin' });
        if (res.ok) {
          const data = await res.json();
          onData(data);
        }
      } catch (e) { /* silent */ }
    }
    const id = setInterval(tick, intervalMs);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) clearInterval(id);
    });
    return id;
  };

  // ---------- Notifications ----------
  var _notifCategoryIcons = {
    connection: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    dashboard: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
    billing: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>',
    system: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
  };

  function initNotifications() {
    var bell = document.getElementById('notifBell');
    var dropdown = document.getElementById('notifDropdown');
    var badge = document.getElementById('notifBadge');
    var list = document.getElementById('notifList');
    var markAllBtn = document.getElementById('notifMarkAll');
    if (!bell) return;

    // Toggle dropdown
    bell.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = dropdown.classList.contains('is-open');
      dropdown.classList.toggle('is-open');
      if (!isOpen) loadNotifications();
    });

    // Close on outside click
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.notif-wrap')) {
        dropdown.classList.remove('is-open');
      }
    });

    // Mark all read
    markAllBtn.addEventListener('click', async function () {
      try {
        await fetch('/api/notifications/read-all', { method: 'POST', credentials: 'same-origin' });
        document.querySelectorAll('.notif-item.is-unread').forEach(function (el) {
          el.classList.remove('is-unread');
          var dot = el.querySelector('.notif-item-dot');
          if (dot) dot.remove();
        });
        _updateBadge(0);
        toast('All notifications marked as read');
      } catch (e) { /* silent */ }
    });

    // Load notifications
    async function loadNotifications() {
      try {
        var res = await fetch('/api/notifications?limit=20', { credentials: 'same-origin' });
        if (!res.ok) return;
        var data = await res.json();
        _updateBadge(data.unread_count);
        _renderNotifications(data.notifications);
      } catch (e) { /* silent */ }
    }

    function _renderNotifications(notifs) {
      if (!notifs || notifs.length === 0) {
        list.innerHTML = '<div class="notif-empty">No notifications yet</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < notifs.length; i++) {
        var n = notifs[i];
        var icon = _notifCategoryIcons[n.category] || _notifCategoryIcons.system;
        var cls = n.is_read ? '' : ' is-unread';
        var dot = n.is_read ? '' : '<div class="notif-item-dot"></div>';
        html += '<div class="notif-item' + cls + '" data-id="' + n.id + '"'
             + (n.action_url ? ' data-url="' + _escHtml(n.action_url) + '"' : '') + '>'
             + '<div class="notif-item-icon is-' + n.severity + '">' + icon + '</div>'
             + '<div class="notif-item-body">'
             + '<div class="notif-item-title">' + _escHtml(n.title) + '</div>'
             + '<div class="notif-item-msg">' + _escHtml(n.message) + '</div>'
             + '<div class="notif-item-time">' + Fluxito.timeAgo(n.created_at) + '</div>'
             + '</div>' + dot + '</div>';
      }
      list.innerHTML = html;

      // Click handlers for individual notifications
      list.querySelectorAll('.notif-item').forEach(function (el) {
        el.addEventListener('click', async function () {
          var id = el.getAttribute('data-id');
          var url = el.getAttribute('data-url');
          if (el.classList.contains('is-unread')) {
            el.classList.remove('is-unread');
            var d = el.querySelector('.notif-item-dot');
            if (d) d.remove();
            try {
              await fetch('/api/notifications/' + id + '/read', { method: 'POST', credentials: 'same-origin' });
              var cur = parseInt(badge.textContent) || 0;
              _updateBadge(Math.max(0, cur - 1));
            } catch (e) { /* silent */ }
          }
          if (url) window.location.href = url;
        });
      });
    }

    function _updateBadge(count) {
      if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }

    // Initial count check
    (async function () {
      try {
        var res = await fetch('/api/notifications/count', { credentials: 'same-origin' });
        if (res.ok) {
          var data = await res.json();
          _updateBadge(data.unread_count);
        }
      } catch (e) { /* silent */ }
    })();

    // Poll for new notifications every 60s
    setInterval(async function () {
      if (document.hidden) return;
      try {
        var res = await fetch('/api/notifications/count', { credentials: 'same-origin' });
        if (res.ok) {
          var data = await res.json();
          _updateBadge(data.unread_count);
        }
      } catch (e) { /* silent */ }
    }, 60000);
  }

  // ---------- Avatar dropdown ----------
  function initAvatarDropdown() {
    var toggle = document.getElementById('avatarToggle');
    var dropdown = document.getElementById('avatarDropdown');
    if (!toggle || !dropdown) return;

    toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      // Close notification dropdown if open
      var notifDd = document.getElementById('notifDropdown');
      if (notifDd) notifDd.classList.remove('is-open');
      dropdown.classList.toggle('is-open');
    });

    document.addEventListener('click', function (e) {
      if (!e.target.closest('.avatar-wrap')) {
        dropdown.classList.remove('is-open');
      }
    });
  }

  // ---------- Theme toggle button (in user dropdown) ----------
  function initThemeToggle() {
    const btn = document.getElementById('themeMenuToggle');
    if (!btn) return;
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      cycleThemeMode();
    });
  }

  // ---------- init ----------
  function init() {
    initNav();
    initNotifications();
    initAvatarDropdown();
    initThemeToggle();
    // Sync the UI now in case the menu was rendered after applyTheme() ran.
    syncThemeUi(getThemeMode());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
