// app/static/js/ask.js
//
// Full-page /ask mount. The chat mechanics (SSE transport, message rendering,
// draft cards, conversation load/append) now live in the reusable FluxChat core
// (flux/core.js). This file is a thin page shell: it gathers the page's element
// IDs, hands them to FluxChat.create, and keeps the page-only surfaces — the
// history rail, the usage panel, the thread header, and the sidebar list — in
// sync through the core's callbacks.
(function () {
  "use strict";

  if (!window.FluxChat) return; // core failed to load

  var messagesEl = document.getElementById("ask-messages");
  var emptyEl = document.getElementById("ask-empty");
  var setupHint = document.getElementById("ask-setup-hint");
  var form = document.getElementById("ask-composer");
  var input = document.getElementById("ask-input");
  var sendBtn = document.getElementById("ask-send");
  var convListEl = document.getElementById("ask-conv-list");
  var newBtn = document.getElementById("ask-new");

  // Thread header (title + mono eyebrow + status pill)
  var threadHeaderEl = document.getElementById("ask-thread-header");
  var threadTitleEl = document.getElementById("ask-thread-title");
  var threadEyebrowEl = document.getElementById("ask-thread-eyebrow");
  var threadStatusEl = document.getElementById("ask-thread-status");

  // Usage rail elements
  var usageEl = document.getElementById("ask-usage");
  var usageToggle = document.getElementById("ask-usage-toggle");
  var usageOpen = document.getElementById("ask-usage-open");
  var railTools = document.getElementById("rail-tools");
  var railToolNames = document.getElementById("rail-tool-names");
  var railTokIn = document.getElementById("rail-tok-in");
  var railTokOut = document.getElementById("rail-tok-out");
  var railTokTotal = document.getElementById("rail-tok-total");
  var railCost = document.getElementById("rail-cost");
  var railModelsSection = document.getElementById("rail-models-section");
  var railModelsList = document.getElementById("rail-models-list");

  function el(tag, cls, text) {
    return window.FluxChat.el(tag, cls, text);
  }

  // ── Per-conversation usage state ──────────────────────────────────────────
  // modelBuckets: { "<provider> · <model>": { input: n, output: n } }
  // currentTurnKey: the model key for the in-flight turn (set from "conversation")
  var usageState = {
    currentTurnKey: null,
    modelBuckets: {},
    toolCounts: {},
  };

  function resetUsageState() {
    usageState.currentTurnKey = null;
    usageState.modelBuckets = {};
    usageState.toolCounts = {};
  }

  function _modelKey(provider, model) {
    if (!model) return null;
    return provider ? provider + " · " + model : model;
  }

  // ── Pricing map (USD per 1M tokens, input/output) ── labeled as estimated
  var PRICING = {
    "claude-opus-4-8": [5, 25],
    "claude-sonnet-4-6": [3, 15],
    "claude-haiku-4-5": [1, 5],
    "gpt-4o": [2.5, 10],
    "gpt-4o-mini": [0.15, 0.6],
  };

  function _modelOnly(key) {
    if (!key) return null;
    var parts = key.split(" · ");
    return parts[parts.length - 1];
  }

  function computeCost(modelKey, inputTok, outputTok) {
    var model = _modelOnly(modelKey);
    var p = PRICING[model];
    if (!p) return null;
    return (inputTok / 1e6) * p[0] + (outputTok / 1e6) * p[1];
  }

  function formatCost(dollars) {
    if (dollars === null) return "—";
    if (dollars < 0.001) return "< $0.001";
    return "$" + dollars.toFixed(4);
  }

  function fmtNum(n) {
    return n.toLocaleString();
  }

  // ── Rail rendering ────────────────────────────────────────────────────────

  function renderRail() {
    try {
      var totalIn = 0,
        totalOut = 0;
      Object.values(usageState.modelBuckets).forEach(function (b) {
        totalIn += b.input;
        totalOut += b.output;
      });
      var grandTotal = totalIn + totalOut;

      if (railTokIn) railTokIn.textContent = grandTotal > 0 ? fmtNum(totalIn) : "—";
      if (railTokOut) railTokOut.textContent = grandTotal > 0 ? fmtNum(totalOut) : "—";
      if (railTokTotal) railTokTotal.textContent = grandTotal > 0 ? fmtNum(grandTotal) : "—";

      var totalCost = 0;
      var hasUnknown = false;
      Object.keys(usageState.modelBuckets).forEach(function (key) {
        var b = usageState.modelBuckets[key];
        var c = computeCost(key, b.input, b.output);
        if (c !== null) {
          totalCost += c;
        } else {
          hasUnknown = true;
        }
      });
      if (railCost) {
        if (grandTotal === 0) {
          railCost.textContent = "—";
        } else {
          var costStr = formatCost(totalCost > 0 ? totalCost : null);
          if (hasUnknown && totalCost > 0) costStr += " (partial)";
          railCost.textContent = costStr;
        }
      }

      var totalTools = Object.values(usageState.toolCounts).reduce(function (a, b) {
        return a + b;
      }, 0);
      if (railTools) {
        railTools.textContent = totalTools > 0 ? totalTools + " total" : "—";
      }
      if (railToolNames) {
        var names = Object.keys(usageState.toolCounts);
        if (names.length > 0) {
          railToolNames.innerHTML = "";
          names.forEach(function (name) {
            var row = el("div", "ask-tool-name-row");
            var nameSpan = el("span", null, name);
            var countSpan = el("span", "ask-tool-name-count", "×" + usageState.toolCounts[name]);
            row.appendChild(nameSpan);
            row.appendChild(countSpan);
            railToolNames.appendChild(row);
          });
          railToolNames.hidden = false;
        } else {
          railToolNames.innerHTML = "";
          railToolNames.hidden = true;
        }
      }

      var keys = Object.keys(usageState.modelBuckets);
      if (railModelsSection) {
        if (keys.length > 0) {
          railModelsSection.hidden = false;
          if (railModelsList) {
            railModelsList.innerHTML = "";
            keys.forEach(function (key) {
              var b = usageState.modelBuckets[key];
              var bTotal = b.input + b.output;
              var card = el("div", "ask-model-card");

              var nameDiv = el("div", "ask-model-card-name", key);
              card.appendChild(nameDiv);

              var tbl = el("table", "ask-usage-table");

              function mkRow(label, val) {
                var tr = document.createElement("tr");
                var td1 = el("td", null, label);
                var td2 = el("td", null, val);
                tr.appendChild(td1);
                tr.appendChild(td2);
                return tr;
              }

              tbl.appendChild(mkRow("Input", bTotal > 0 ? fmtNum(b.input) : "—"));
              tbl.appendChild(mkRow("Output", bTotal > 0 ? fmtNum(b.output) : "—"));
              tbl.appendChild(mkRow("Total", bTotal > 0 ? fmtNum(bTotal) : "—"));

              var modelCost = computeCost(key, b.input, b.output);
              tbl.appendChild(mkRow("Cost", formatCost(modelCost)));

              card.appendChild(tbl);
              railModelsList.appendChild(card);
            });
          }
        } else {
          railModelsSection.hidden = true;
        }
      }
    } catch (e) {
      // Never crash
    }
  }

  function addUsageFromTokens(usageObj, modelKey) {
    if (!usageObj) return;
    var key = modelKey || usageState.currentTurnKey;
    if (!key) return;
    if (!usageState.modelBuckets[key]) {
      usageState.modelBuckets[key] = { input: 0, output: 0 };
    }
    usageState.modelBuckets[key].input += usageObj.input_tokens || usageObj.prompt_tokens || 0;
    usageState.modelBuckets[key].output += usageObj.output_tokens || usageObj.completion_tokens || 0;
    renderRail();
  }

  function addToolToUsage(toolName) {
    if (!toolName) return;
    usageState.toolCounts[toolName] = (usageState.toolCounts[toolName] || 0) + 1;
    renderRail();
  }

  // ── Rail visibility ───────────────────────────────────────────────────────

  function showRail() {
    if (usageEl) usageEl.hidden = false;
    if (usageOpen) usageOpen.hidden = true;
  }

  function hideRail() {
    if (usageEl) usageEl.hidden = true;
    if (usageOpen) usageOpen.hidden = false;
  }

  if (usageToggle) usageToggle.addEventListener("click", hideRail);
  if (usageOpen) usageOpen.addEventListener("click", showRail);

  // ── Thread header (title + mono eyebrow + status pill) ───────────────────

  function statusLabelForDraftStatus(status) {
    if (status === "published") return "PUBLISHED";
    if (status === "rejected") return "DRAFT KEPT";
    return "AWAITING APPROVAL";
  }

  function setThreadStatusFromDraft(status) {
    if (!threadStatusEl) return;
    threadStatusEl.hidden = false;
    threadStatusEl.textContent = statusLabelForDraftStatus(status);
  }

  function updateThreadHeader(opts) {
    opts = opts || {};
    if (!threadHeaderEl) return;
    if (!opts.title) {
      threadHeaderEl.hidden = true;
      return;
    }
    threadHeaderEl.hidden = false;
    if (threadTitleEl) threadTitleEl.textContent = opts.title;
    if (threadEyebrowEl) {
      var eyebrow =
        opts.provider || opts.model
          ? ((opts.provider || "") + (opts.provider && opts.model ? " · " : "") + (opts.model || "")).toUpperCase()
          : "";
      threadEyebrowEl.textContent = eyebrow;
      threadEyebrowEl.hidden = !eyebrow;
    }
    if (threadStatusEl) {
      var drafts = opts.drafts || [];
      var latestDraft = drafts.length ? drafts[drafts.length - 1] : null;
      if (latestDraft) {
        setThreadStatusFromDraft(latestDraft.status);
      } else {
        threadStatusEl.hidden = true;
      }
    }
  }

  // ── Conversation sidebar ──────────────────────────────────────────────────

  function relativeTime(iso) {
    if (!iso) return "";
    try {
      var then = new Date(iso).getTime();
      if (isNaN(then)) return "";
      var mins = Math.round((Date.now() - then) / 60000);
      if (mins < 1) return "just now";
      if (mins < 60) return mins + "m ago";
      var hrs = Math.round(mins / 60);
      if (hrs < 24) return hrs + "h ago";
      var days = Math.round(hrs / 24);
      if (days < 7) return days + "d ago";
      return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch (e) {
      return "";
    }
  }

  function makeConvItem(id, title, subtitle) {
    var li = el("li", "ask-conv-item");
    li.dataset.convId = id;
    li.appendChild(el("div", "ask-conv-item-title", title));
    if (subtitle) li.appendChild(el("div", "ask-conv-item-sub", subtitle));
    li.addEventListener("click", function () {
      chat.openConversation(id);
    });
    return li;
  }

  function loadConversations() {
    fetch("/api/ask/conversations").then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        if (!convListEl) return;
        convListEl.innerHTML = "";
        (data.conversations || []).forEach(function (c) {
          convListEl.appendChild(makeConvItem(c.id, c.title, relativeTime(c.last_message_at)));
        });
        markActiveConv(chat.getConversationId());
      });
    });
  }

  function markActiveConv(id) {
    if (!convListEl) return;
    var items = convListEl.querySelectorAll(".ask-conv-item");
    items.forEach(function (li) {
      li.classList.toggle("is-active", li.dataset.convId === String(id));
    });
  }

  // ── Provider key check ────────────────────────────────────────────────────

  function checkKeys() {
    fetch("/api/ask/keys").then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        var hasKey = (data.providers || []).length > 0;
        if (setupHint) setupHint.hidden = hasKey;
      });
    });
  }

  // ── FluxChat core wiring ──────────────────────────────────────────────────

  var chat = window.FluxChat.create({
    elements: {
      messages: messagesEl,
      empty: emptyEl,
      input: input,
      form: form,
      sendBtn: sendBtn,
    },
    options: {
      placeholders: { fresh: "Ask Flux…", reply: "Reply to Flux…" },

      onStreamEnd: function () {
        loadConversations();
      },

      onToolCall: function (name) {
        addToolToUsage(name);
      },

      onUsage: function (usageObj, meta) {
        addUsageFromTokens(usageObj, meta ? _modelKey(meta.provider, meta.model) : null);
      },

      onDraftStatus: function (status) {
        setThreadStatusFromDraft(status);
      },

      // Live "conversation" frame — every turn reports its model; new
      // conversations additionally carry a title.
      onConversation: function (info) {
        markActiveConv(info.conversationId);
        if (info.model) {
          usageState.currentTurnKey = _modelKey(info.provider, info.model);
        }
        renderRail();
        showRail();

        if (info.isNew) {
          updateThreadHeader({ title: info.title, provider: info.provider, model: info.model, drafts: [] });
          if (convListEl) {
            var existing = convListEl.querySelector('[data-conv-id="' + info.conversationId + '"]');
            if (!existing) {
              var li = makeConvItem(info.conversationId, info.title, "just now");
              li.classList.add("is-active");
              convListEl.insertBefore(li, convListEl.firstChild);
            } else {
              var existingTitle = existing.querySelector(".ask-conv-item-title");
              if (existingTitle) existingTitle.textContent = info.title;
              existing.classList.add("is-active");
            }
          }
        }
      },

      // Persisted conversation opened — reset the rail, then per-message
      // onUsage/onToolCall callbacks rebuild it during render.
      onConversationLoaded: function (data, id) {
        markActiveConv(id);
        updateThreadHeader({
          title: data.title,
          provider: data.provider,
          model: data.model,
          drafts: data.drafts,
        });
        resetUsageState();
      },

      onConversationRendered: function () {
        renderRail();
        showRail();
      },

      onReset: function () {
        markActiveConv(null);
        updateThreadHeader({});
        resetUsageState();
        renderRail();
        // Don't hide the rail on new chat — just show empty state
      },
    },
  });

  // ── New chat button ───────────────────────────────────────────────────────

  if (newBtn) {
    newBtn.addEventListener("click", function () {
      chat.newChat();
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  // Prefill from a Home composer / suggestion-chip handoff (?q=...). Prefill
  // only — never auto-send — so the user reviews before it goes out.
  (function prefillFromQuery() {
    var q = new URLSearchParams(window.location.search).get("q");
    if (q && input) {
      input.value = q;
      input.focus();
      window.history.replaceState({}, "", window.location.pathname);
    }
  })();

  // Deep-link into a specific conversation (the docked panel's "Expand" link
  // points here: /ask?conversation={id}). Additive — nothing set this before.
  (function openFromQuery() {
    var convId = new URLSearchParams(window.location.search).get("conversation");
    if (convId) {
      chat.openConversation(convId);
      window.history.replaceState({}, "", window.location.pathname);
    }
  })();

  loadConversations();
  checkKeys();
})();
