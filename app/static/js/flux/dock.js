// app/static/js/flux/dock.js
//
// The docked Flux panel: a compact chat surface that rides along on every
// logged-in page (except /ask itself). It renders into the pre-existing empty
// mount #ask-fluxito-panel in base.html and drives it with the shared FluxChat
// core (flux/core.js). Page context (section + route) is sent with each turn so
// Flux knows where the user is standing, and each (project, section) pair keeps
// its own running conversation via localStorage.
(function () {
  "use strict";

  if (!window.FluxChat) return;

  // ── Visibility guard: never run on the full /ask page ──────────────────────
  var body = document.body;
  var onAskPage =
    location.pathname === "/ask" || (body && body.dataset && body.dataset.page === "ask");

  var toggleBtn = document.getElementById("fluxDockToggle");
  var panel = document.getElementById("ask-fluxito-panel");

  if (onAskPage) {
    // Hide the sidebar toggle so it doesn't dangle uselessly on /ask.
    if (toggleBtn) toggleBtn.hidden = true;
    return;
  }
  if (!panel) return;

  var FluxChat = window.FluxChat;

  var projectId = (body && body.dataset && body.dataset.projectId) || "none";
  var section = (body && body.dataset && body.dataset.section) || "";
  var storageKey = "fx-dock-conv:" + projectId + ":" + section;

  // Friendly label for the section eyebrow.
  var SECTION_LABELS = {
    home: "Home",
    implement: "Ask Flux",
    plan: "Tracking Plan",
    report: "Reporting",
    audit: "Auditing",
    context: "Context",
    settings: "Settings",
  };
  var sectionLabel = SECTION_LABELS[section] || (section ? section.charAt(0).toUpperCase() + section.slice(1) : "Workspace");

  // ── Build the panel chrome ─────────────────────────────────────────────────

  var el = FluxChat.el;

  var header = el("div", "flux-dock-header");
  var headMain = el("div", "flux-dock-head-main");
  headMain.appendChild(el("span", "flux-dock-title", "Flux"));
  headMain.appendChild(el("span", "flux-dock-section", sectionLabel));
  header.appendChild(headMain);

  var headActions = el("div", "flux-dock-head-actions");

  var newChatBtn = el("button", "flux-dock-btn flux-dock-newchat", "New chat");
  newChatBtn.type = "button";
  newChatBtn.title = "Start a new conversation";
  headActions.appendChild(newChatBtn);

  var expandLink = el("a", "flux-dock-btn flux-dock-expand", "Expand");
  expandLink.href = "/ask";
  expandLink.title = "Open in the full Ask Flux page";
  headActions.appendChild(expandLink);

  var closeBtn = el("button", "flux-dock-btn flux-dock-close");
  closeBtn.type = "button";
  closeBtn.setAttribute("aria-label", "Close Flux panel");
  closeBtn.title = "Close";
  closeBtn.textContent = "✕";
  headActions.appendChild(closeBtn);

  header.appendChild(headActions);

  var transcript = el("div", "flux-dock-transcript ask-messages");
  transcript.appendChild(el("div", "ask-messages-inner"));

  var empty = el("div", "ask-empty flux-dock-empty");
  var emptyH = el("h2", null, "Flux");
  var emptyP = el("p", null, "Ask about this " + sectionLabel.toLowerCase() + " view — analytics, tracking, dashboards or marketing data.");
  empty.appendChild(emptyH);
  empty.appendChild(emptyP);
  transcript.querySelector(".ask-messages-inner").appendChild(empty);

  var composer = el("form", "flux-dock-composer");
  var composerInner = el("div", "flux-dock-composer-inner");
  composerInner.appendChild(el("span", "flux-dock-composer-badge", "F"));
  var textarea = el("textarea", null);
  textarea.id = "flux-dock-input";
  textarea.rows = 1;
  textarea.placeholder = "Ask Flux…";
  textarea.setAttribute("data-flux-composer", "");
  composerInner.appendChild(textarea);
  var sendBtn = el("button", "flux-dock-send", "Send");
  sendBtn.type = "submit";
  composerInner.appendChild(sendBtn);
  composer.appendChild(composerInner);

  panel.appendChild(header);
  panel.appendChild(transcript);
  panel.appendChild(composer);

  // ── FluxChat core ──────────────────────────────────────────────────────────

  function persistConv(id) {
    try {
      if (id) localStorage.setItem(storageKey, id);
      else localStorage.removeItem(storageKey);
    } catch (e) {
      /* storage may be unavailable */
    }
  }

  function updateExpand(id) {
    expandLink.href = id ? "/ask?conversation=" + encodeURIComponent(id) : "/ask";
  }

  var chat = FluxChat.create({
    elements: {
      messages: transcript,
      empty: empty,
      input: textarea,
      form: composer,
      sendBtn: sendBtn,
    },
    options: {
      placeholders: { fresh: "Ask Flux…", reply: "Reply to Flux…" },
      extraBody: function () {
        return { page_context: { section: section, route: location.pathname } };
      },
      onConversation: function (info) {
        persistConv(info.conversationId);
        updateExpand(info.conversationId);
      },
      onConversationLoaded: function (data, id) {
        updateExpand(id);
      },
      onReset: function () {
        persistConv(null);
        updateExpand(null);
      },
    },
  });

  // ── Open / close ────────────────────────────────────────────────────────────

  var restored = false;

  function restoreConversation() {
    if (restored) return;
    restored = true;
    var stored = null;
    try {
      stored = localStorage.getItem(storageKey);
    } catch (e) {
      /* ignore */
    }
    if (stored) chat.openConversation(stored);
  }

  function isOpen() {
    return !panel.hidden;
  }

  function openPanel() {
    panel.hidden = false;
    panel.setAttribute("aria-hidden", "false");
    document.body.classList.add("flux-dock-open");
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", "true");
    restoreConversation();
    // Focus the composer once it's visible.
    window.requestAnimationFrame(function () {
      textarea.focus();
    });
  }

  function closePanel() {
    panel.hidden = true;
    panel.setAttribute("aria-hidden", "true");
    document.body.classList.remove("flux-dock-open");
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", "false");
  }

  function togglePanel() {
    if (isOpen()) closePanel();
    else openPanel();
  }

  if (toggleBtn) toggleBtn.addEventListener("click", togglePanel);
  closeBtn.addEventListener("click", closePanel);
  newChatBtn.addEventListener("click", function () {
    chat.newChat();
  });

  // ⌘K / Ctrl+K opens the dock and focuses its composer. app.js also handles
  // this shortcut (it focuses the first [data-flux-composer]); opening here
  // makes that focus land on a visible field instead of the hidden panel.
  document.addEventListener("keydown", function (e) {
    var isCmdK = (e.metaKey || e.ctrlKey) && e.key && e.key.toLowerCase() === "k";
    if (!isCmdK) return;
    // Only claim ⌘K when this page has no composer of its own. app.js focuses
    // the first [data-flux-composer] in the document; when that element is a
    // page composer (it appears earlier in the DOM than this dock's textarea),
    // let app.js keep focus there instead of stealing it into the dock.
    var first = document.querySelector("[data-flux-composer]");
    if (first && !panel.contains(first)) return;
    if (!isOpen()) {
      e.preventDefault();
      openPanel();
    }
  });
})();
