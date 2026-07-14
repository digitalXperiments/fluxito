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

  var renderMarkdown = window.FluxChat.renderMarkdown;
  var csrfToken = window.FluxChat.csrfToken;

  // Builder (chat-based dashboard builder) context banner
  var builderBanner = document.getElementById("ask-builder-banner");
  var builderBannerText = document.getElementById("ask-builder-banner-text");
  var builderBannerClose = document.getElementById("ask-builder-banner-close");

  // ── Builder context (?builder=1&dashboard=<slug>) ─────────────────────────
  // Wiring choice: ask_routes.py's /api/ask/stream body has no dedicated
  // "context" field (out of this file's scope to add one), so the least
  // invasive way to hand the model the builder's target dashboard is to
  // prepend a short context line to the *outgoing* message text itself —
  // once, on the first message of a fresh conversation — while the chat
  // bubble the user sees keeps showing their original, unmodified text.
  var builderCtx = (function () {
    var params = new URLSearchParams(window.location.search);
    return {
      enabled: params.get("builder") === "1",
      slug: params.get("dashboard") || null,
      contextSent: false,
    };
  })();

  function initBuilderBanner() {
    if (!builderCtx.enabled || !builderBanner) return;
    if (builderBannerText) {
      builderBannerText.textContent = builderCtx.slug
        ? "Building dashboard: " + builderCtx.slug
        : "Dashboard builder";
    }
    builderBanner.hidden = false;
  }

  if (builderBannerClose) {
    builderBannerClose.addEventListener("click", function () {
      builderBanner.hidden = true;
    });
  }

  // ── Embedded (drawer/iframe) compact layout ───────────────────────────────
  function initEmbeddedMode() {
    var embedded = false;
    try {
      embedded = window.parent !== window;
    } catch (e) {
      embedded = true;
    }
    if (embedded) {
      document.documentElement.classList.add("fx-ask-embedded");
    }
    return embedded;
  }

  var isEmbedded = initEmbeddedMode();

  // ── Card-preview chart bookkeeping ─────────────────────────────────────────
  // Fluxito.mountCharts() disposes *every* previously mounted chart instance
  // and remounts whatever [data-card-id] elements it can find under the given
  // root (exactly like live_view.html's renderAll → mountCharts each refresh).
  // So every time a new card_preview block is rendered we remount the full set
  // of preview cards seen so far in this conversation, not just the new one —
  // otherwise earlier previews in the same thread would go blank.
  var previewRegistry = {}; // block.id -> {card, block, wrapEl, footerEl}
  var previewOrder = []; // ordered list of block.id, oldest first

  function resetPreviewState() {
    previewRegistry = {};
    previewOrder = [];
  }

  function remountAllPreviewCharts() {
    if (!window.Fluxito || !Fluxito.mountCharts) return;
    var cards = previewOrder
      .map(function (id) {
        var entry = previewRegistry[id];
        return entry ? entry.card : null;
      })
      .filter(Boolean);
    if (!cards.length) return;
    try {
      Fluxito.mountCharts(cards, document);
    } catch (e) {
      // Never crash the stream on a chart-mount failure
    }
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

  // ── Confirm-action (add/discard a proposed card) ───────────────────────────
  //
  // NOTE: message-list rendering (text/tool-call/draft blocks), the messages
  // inner container, and per-turn scrolling now live in FluxChat (flux/core.js)
  // — `chat`, created below. This page only supplies the two Ask-side display
  // blocks core.js doesn't know how to render (card_preview / choices) via the
  // `onDisplayBlock` hook, plus their action handlers (confirm-action, the
  // dashboard picker, discard).

  function confirmAction(blockId, action, dashboardSlug) {
    var body = { conversation_id: chat.getConversationId(), block_id: blockId, action: action };
    if (dashboardSlug) body.dashboard_slug = dashboardSlug;
    return fetch("/api/ask/confirm-action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken(),
      },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r
        .json()
        .catch(function () {
          return {};
        })
        .then(function (j) {
          return { ok: r.ok, status: r.status, data: j };
        });
    });
  }

  // ── Card preview block (propose_card → CardPreviewBlock) ───────────────────

  function buildCardDom(cardObj) {
    var holder = document.createElement("div");
    try {
      holder.innerHTML =
        window.Fluxito && Fluxito.renderCard ? Fluxito.renderCard(cardObj) : "";
    } catch (e) {
      holder.innerHTML = "";
    }
    return (
      holder.firstElementChild ||
      el("div", "data-card", "This card could not be rendered.")
    );
  }

  function renderPreviewFooter(entry) {
    var footer = entry.footerEl;
    footer.innerHTML = "";
    var state = entry.block.state || "proposed";

    if (state === "added") {
      footer.appendChild(el("span", "ask-card-preview-added", "✓ Added"));
      if (entry.block.dashboard_slug) {
        var link = document.createElement("a");
        link.href = "/live-dashboards/" + encodeURIComponent(entry.block.dashboard_slug);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.className = "ask-card-preview-link";
        link.textContent = "View dashboard →";
        footer.appendChild(link);
      }
      return;
    }

    if (state === "discarded") {
      footer.appendChild(el("span", "ask-card-preview-discarded", "Discarded"));
      return;
    }

    // proposed
    var addBtn = el("button", "ask-btn ask-btn-primary", "Add to dashboard");
    addBtn.type = "button";
    addBtn.addEventListener("click", function () {
      if (entry.block.dashboard_slug) {
        doAddCard(entry, null);
      } else {
        showDashboardPicker(entry);
      }
    });

    var adjustBtn = el("button", "ask-btn", "Adjust");
    adjustBtn.type = "button";
    adjustBtn.addEventListener("click", function () {
      if (!input) return;
      input.value = "Adjust this card: ";
      input.focus();
      input.dispatchEvent(new Event("input"));
    });

    var discardBtn = el("button", "ask-btn ask-btn-danger", "Discard");
    discardBtn.type = "button";
    discardBtn.addEventListener("click", function () {
      onDiscardCard(entry);
    });

    footer.appendChild(addBtn);
    footer.appendChild(adjustBtn);
    footer.appendChild(discardBtn);
  }

  function showDashboardPicker(entry) {
    var footer = entry.footerEl;
    footer.innerHTML = "";
    var row = el("div", "ask-card-picker");

    var slugInput = document.createElement("input");
    slugInput.type = "text";
    slugInput.className = "ask-card-picker-input";
    slugInput.placeholder = "existing dashboard slug…";

    var addExistingBtn = el("button", "ask-btn ask-btn-primary", "Add");
    addExistingBtn.type = "button";
    addExistingBtn.addEventListener("click", function () {
      var slug = (slugInput.value || "").trim();
      if (!slug) {
        slugInput.focus();
        return;
      }
      doAddCard(entry, slug);
    });

    var newDashBtn = el("button", "ask-btn", "New dashboard");
    newDashBtn.type = "button";
    newDashBtn.addEventListener("click", function () {
      doAddCard(entry, "__new__");
    });

    var cancelBtn = el("button", "ask-btn ask-btn-ghost", "Cancel");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", function () {
      renderPreviewFooter(entry);
    });

    row.appendChild(slugInput);
    row.appendChild(addExistingBtn);
    row.appendChild(newDashBtn);
    row.appendChild(cancelBtn);
    footer.appendChild(row);
    slugInput.focus();
  }

  function showCardActionError(entry, message) {
    var footer = entry.footerEl;
    footer.innerHTML = "";
    footer.appendChild(el("span", "ask-card-preview-error", message || "Something went wrong."));
    var retryBtn = el("button", "ask-btn", "Back");
    retryBtn.type = "button";
    retryBtn.addEventListener("click", function () {
      renderPreviewFooter(entry);
    });
    footer.appendChild(retryBtn);
  }

  function doAddCard(entry, dashboardSlugOverride) {
    var footer = entry.footerEl;
    footer.innerHTML = "";
    footer.appendChild(el("span", "ask-card-preview-pending", "Adding…"));
    confirmAction(entry.block.id, "add", dashboardSlugOverride)
      .then(function (res) {
        if (res.ok && res.data && res.data.status === "added") {
          entry.block.state = "added";
          entry.block.dashboard_slug = res.data.dashboard_slug || entry.block.dashboard_slug;
          renderPreviewFooter(entry);
          if (isEmbedded) {
            try {
              window.parent.postMessage(
                {
                  type: "fluxito:card-added",
                  dashboardSlug: entry.block.dashboard_slug,
                  cardKey: res.data.card_key,
                },
                window.location.origin
              );
            } catch (e) {
              // Never crash the UI on a cross-frame postMessage failure
            }
          }
        } else {
          showCardActionError(entry, res.data && res.data.error);
        }
      })
      .catch(function () {
        showCardActionError(entry, "Network error.");
      });
  }

  function onDiscardCard(entry) {
    var footer = entry.footerEl;
    footer.innerHTML = "";
    footer.appendChild(el("span", "ask-card-preview-pending", "Discarding…"));
    confirmAction(entry.block.id, "discard")
      .then(function (res) {
        if (res.ok) entry.block.state = "discarded";
        renderPreviewFooter(entry);
      })
      .catch(function () {
        renderPreviewFooter(entry);
      });
  }

  function renderCardPreviewBlock(body, block) {
    var wrap = el("div", "ask-card-preview");
    var cardObj = {
      id: block.id,
      title: (block.card && block.card.title) || "Untitled card",
      platform: (block.card && block.card.platform) || "",
      card_type: (block.snap && block.snap.card_type) || undefined,
      snap: block.snap || {},
    };
    wrap.appendChild(buildCardDom(cardObj));

    if (block.warnings && block.warnings.length) {
      var warn = el("ul", "ask-card-preview-warnings");
      block.warnings.forEach(function (w) {
        warn.appendChild(el("li", "", w));
      });
      wrap.appendChild(warn);
    }

    var footer = el("div", "ask-card-preview-footer");
    wrap.appendChild(footer);

    var entry = { card: cardObj, block: block, wrapEl: wrap, footerEl: footer };
    previewRegistry[block.id] = entry;
    if (previewOrder.indexOf(block.id) === -1) previewOrder.push(block.id);

    body.appendChild(wrap);
    renderPreviewFooter(entry);
    remountAllPreviewCharts();
    return wrap;
  }

  // ── Choices block (ask_choices → ChoicesBlock) ──────────────────────────────

  function renderChoicesBlock(body, block, forceDisabled) {
    var wrap = el("div", "ask-choices");
    wrap.appendChild(el("div", "ask-choices-question", block.question || ""));

    var chipsRow = el("div", "ask-choices-chips");
    wrap.appendChild(chipsRow);

    var selected = {}; // value -> label, multi mode only

    function disableAll() {
      Array.prototype.forEach.call(chipsRow.querySelectorAll("button"), function (b) {
        b.disabled = true;
      });
      wrap.classList.add("is-answered");
    }

    (block.options || []).forEach(function (opt) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ask-choice-chip";
      chip.textContent = opt.label || opt.value || "";
      chip.addEventListener("click", function () {
        if (wrap.classList.contains("is-answered")) return;
        if (block.multi) {
          chip.classList.toggle("is-selected");
          if (chip.classList.contains("is-selected")) {
            selected[opt.value] = opt.label || opt.value;
          } else {
            delete selected[opt.value];
          }
        } else {
          disableAll();
          sendTurn(opt.value != null ? String(opt.value) : opt.label || "");
        }
      });
      chipsRow.appendChild(chip);
    });

    if (block.multi) {
      var sendChip = document.createElement("button");
      sendChip.type = "button";
      sendChip.className = "ask-choice-chip ask-choice-send";
      sendChip.textContent = "Send";
      sendChip.addEventListener("click", function () {
        var labels = Object.keys(selected).map(function (k) {
          return selected[k];
        });
        if (!labels.length) return;
        disableAll();
        sendTurn(labels.join(", "));
      });
      chipsRow.appendChild(sendChip);
    }

    if (forceDisabled) disableAll();

    body.appendChild(wrap);
    return wrap;
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

      // Builder context (?builder=1&dashboard=<slug>): prepend a hidden
      // context line to the *outgoing* message only, once, on the first
      // message of a fresh conversation — see the builderCtx comment above
      // for why (no dedicated context field on the /api/ask/stream body).
      // The rendered chat bubble always shows the user's original,
      // unmodified text (core.js keeps that separate from the API payload).
      beforeSend: function (text) {
        if (builderCtx.enabled && builderCtx.slug && chat.getConversationId() === null && !builderCtx.contextSent) {
          builderCtx.contextSent = true;
          return (
            'Context: the user is using the chat-based dashboard builder for dashboard "' +
            builderCtx.slug +
            '". If you propose a card via propose_card, set dashboard_slug to "' +
            builderCtx.slug +
            '" so it can be added to that dashboard directly.\n\n' +
            text
          );
        }
        return text;
      },

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

      // core.js knows how to render text/tool_use/draft blocks but not the
      // Ask-side display blocks the dashboard builder adds (card_preview,
      // choices) — it hands those to us verbatim, live or on history replay.
      // `isLast` disables re-answering an already-superseded choices prompt.
      onDisplayBlock: function (body, block, isLast) {
        if (block.type === "card_preview") {
          renderCardPreviewBlock(body, block);
        } else if (block.type === "choices") {
          renderChoicesBlock(body, block, !isLast);
        }
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
        resetPreviewState();
      },

      onConversationRendered: function () {
        renderRail();
        showRail();
      },

      onReset: function () {
        markActiveConv(null);
        updateThreadHeader({});
        resetUsageState();
        resetPreviewState();
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

  // Composer auto-grow, form submit, and the SSE receive loop are all owned
  // by the `chat` FluxChat instance created above (it was handed `input` and
  // `form` in `elements`) — including card_preview / choices rendering via
  // the `onDisplayBlock` hook and the builder-context injection via
  // `beforeSend`, both wired into that `options` object.

  // ── Choice chips send through the same path as the composer ───────────────
  // (renderChoicesBlock below calls this; declared here, after `chat` exists,
  // but hoisted so the forward reference from render is safe.)
  function sendTurn(text) {
    if (!text) return;
    chat.send(text);
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

  initBuilderBanner();
  loadConversations();
  checkKeys();
})();
