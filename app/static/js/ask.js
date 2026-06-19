// app/static/js/ask.js
(function () {
  "use strict";

  var messagesEl = document.getElementById("ask-messages");
  var messagesInner = null; // set after first use — lives inside ask-messages
  var emptyEl = document.getElementById("ask-empty");
  var setupHint = document.getElementById("ask-setup-hint");
  var form = document.getElementById("ask-composer");
  var input = document.getElementById("ask-input");
  var sendBtn = document.getElementById("ask-send");
  var convListEl = document.getElementById("ask-conv-list");
  var newBtn = document.getElementById("ask-new");

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

  var conversationId = null;

  // ── Per-conversation usage state ──────────────────────────────────────────
  // modelBuckets: { "<provider> · <model>": { input: n, output: n } }
  // currentTurnKey: the model key for the in-flight turn (set from "conversation" frame)
  var usageState = {
    currentTurnKey: null,  // "<provider> · <model>" for the live turn
    modelBuckets: {},      // per-model token totals
    toolCounts: {},        // { tool_name: count }
  };

  function resetUsageState() {
    usageState.currentTurnKey = null;
    usageState.modelBuckets = {};
    usageState.toolCounts = {};
  }

  function _modelKey(provider, model) {
    if (!model) return null;
    return provider ? (provider + " · " + model) : model;
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
    // Extract just the model part from "<provider> · <model>" for pricing lookup
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
      // Grand total tokens across all models
      var totalIn = 0, totalOut = 0;
      Object.values(usageState.modelBuckets).forEach(function (b) {
        totalIn += b.input;
        totalOut += b.output;
      });
      var grandTotal = totalIn + totalOut;

      if (railTokIn) railTokIn.textContent = grandTotal > 0 ? fmtNum(totalIn) : "—";
      if (railTokOut) railTokOut.textContent = grandTotal > 0 ? fmtNum(totalOut) : "—";
      if (railTokTotal) railTokTotal.textContent = grandTotal > 0 ? fmtNum(grandTotal) : "—";

      // Grand total estimated cost (sum known models; note partial if any unknown)
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

      // Tool calls
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
            var row = document.createElement("div");
            row.className = "ask-tool-name-row";
            var nameSpan = document.createElement("span");
            nameSpan.textContent = name;
            var countSpan = document.createElement("span");
            countSpan.className = "ask-tool-name-count";
            countSpan.textContent = "×" + usageState.toolCounts[name];
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

      // Per-model breakdown
      var keys = Object.keys(usageState.modelBuckets);
      if (railModelsSection) {
        if (keys.length > 0) {
          railModelsSection.hidden = false;
          if (railModelsList) {
            railModelsList.innerHTML = "";
            keys.forEach(function (key) {
              var b = usageState.modelBuckets[key];
              var bTotal = b.input + b.output;
              var card = document.createElement("div");
              card.className = "ask-model-card";

              var nameDiv = document.createElement("div");
              nameDiv.className = "ask-model-card-name";
              nameDiv.textContent = key;
              card.appendChild(nameDiv);

              var tbl = document.createElement("table");
              tbl.className = "ask-usage-table";

              function mkRow(label, val) {
                var tr = document.createElement("tr");
                var td1 = document.createElement("td");
                td1.textContent = label;
                var td2 = document.createElement("td");
                td2.textContent = val;
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
      // Never crash the stream
    }
  }

  function addUsageFromTokens(usageObj, modelKey) {
    if (!usageObj) return;
    var key = modelKey || usageState.currentTurnKey;
    if (!key) return;
    if (!usageState.modelBuckets[key]) {
      usageState.modelBuckets[key] = { input: 0, output: 0 };
    }
    usageState.modelBuckets[key].input +=
      (usageObj.input_tokens || usageObj.prompt_tokens || 0);
    usageState.modelBuckets[key].output +=
      (usageObj.output_tokens || usageObj.completion_tokens || 0);
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

  if (usageToggle) {
    usageToggle.addEventListener("click", hideRail);
  }
  if (usageOpen) {
    usageOpen.addEventListener("click", showRail);
  }

  // ── CSRF ──────────────────────────────────────────────────────────────────

  function csrfToken() {
    var m = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  // ── DOM helpers ───────────────────────────────────────────────────────────

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function scrollBottom() {
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function getOrCreateInner() {
    if (!messagesInner) {
      messagesInner = messagesEl.querySelector(".ask-messages-inner");
    }
    return messagesInner;
  }

  // ── Markdown renderer (self-contained, XSS-safe) ─────────────────────────

  function escHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderMarkdown(src) {
    try {
      return _renderMarkdown(src);
    } catch (e) {
      return "<pre>" + escHtml(src) + "</pre>";
    }
  }

  function _renderMarkdown(src) {
    var lines = src.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    var out = [];
    var i = 0;
    var n = lines.length;
    var paraLines = [];

    function flushPara() {
      if (!paraLines.length) return;
      var text = paraLines.join("\n");
      out.push("<p>" + inlineMarkdown(text) + "</p>");
      paraLines = [];
    }

    while (i < n) {
      var line = lines[i];

      var fenceMatch = line.match(/^(`{3,}|~{3,})(.*)/);
      if (fenceMatch) {
        flushPara();
        var fence = fenceMatch[1];
        var lang = escHtml((fenceMatch[2] || "").trim());
        var codeLines = [];
        i++;
        while (i < n && lines[i].indexOf(fence) !== 0) {
          codeLines.push(escHtml(lines[i]));
          i++;
        }
        i++;
        var langAttr = lang ? ' class="language-' + lang + '"' : "";
        out.push("<pre><code" + langAttr + ">" + codeLines.join("\n") + "</code></pre>");
        continue;
      }

      var hMatch = line.match(/^(#{1,6})\s+(.*)/);
      if (hMatch) {
        flushPara();
        var level = hMatch[1].length;
        out.push("<h" + level + ">" + inlineMarkdown(hMatch[2].trim()) + "</h" + level + ">");
        i++;
        continue;
      }

      if (line.match(/^>\s?/)) {
        flushPara();
        var bqLines = [];
        while (i < n && lines[i].match(/^>\s?/)) {
          bqLines.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + _renderMarkdown(bqLines.join("\n")) + "</blockquote>");
        continue;
      }

      if (line.match(/^[-*]\s+/)) {
        flushPara();
        var ulItems = [];
        while (i < n && lines[i].match(/^[-*]\s+/)) {
          ulItems.push("<li>" + inlineMarkdown(lines[i].replace(/^[-*]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ul>" + ulItems.join("") + "</ul>");
        continue;
      }

      if (line.match(/^\d+\.\s+/)) {
        flushPara();
        var olItems = [];
        while (i < n && lines[i].match(/^\d+\.\s+/)) {
          olItems.push("<li>" + inlineMarkdown(lines[i].replace(/^\d+\.\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ol>" + olItems.join("") + "</ol>");
        continue;
      }

      if (line.indexOf("|") !== -1 && i + 1 < n && lines[i + 1].match(/^\|?[\s\-|:]+\|/)) {
        flushPara();
        var tblRows = [];
        tblRows.push(parseTableRow(line, true));
        i += 2;
        while (i < n && lines[i].indexOf("|") !== -1) {
          tblRows.push(parseTableRow(lines[i], false));
          i++;
        }
        out.push('<div class="ask-table-wrap"><table>' + tblRows.join("") + "</table></div>");
        continue;
      }

      if (line.trim() === "") {
        flushPara();
        i++;
        continue;
      }

      paraLines.push(line);
      i++;
    }

    flushPara();
    return out.join("\n");
  }

  function parseTableRow(line, isHeader) {
    var stripped = line.replace(/^\|/, "").replace(/\|$/, "");
    var cells = stripped.split("|");
    var tag = isHeader ? "th" : "td";
    var cellsHtml = cells
      .map(function (c) {
        return "<" + tag + ">" + inlineMarkdown(c.trim()) + "</" + tag + ">";
      })
      .join("");
    return "<tr>" + cellsHtml + "</tr>";
  }

  function inlineMarkdown(s) {
    s = escHtml(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    s = s.replace(/_([^_\n]+)_/g, "<em>$1</em>");
    s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)]*)\)/g, function (_, text, href) {
      return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + text + "</a>";
    });
    s = s.replace(/  \n/g, "<br>");
    return s;
  }

  // ── Conversation sidebar ──────────────────────────────────────────────────

  function loadConversations() {
    fetch("/api/ask/conversations").then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        convListEl.innerHTML = "";
        (data.conversations || []).forEach(function (c) {
          var li = el("li", "ask-conv-item", c.title);
          li.dataset.convId = c.id;
          li.addEventListener("click", function () {
            openConversation(c.id);
          });
          convListEl.appendChild(li);
        });
      });
    });
  }

  function markActiveConv(id) {
    var items = convListEl.querySelectorAll(".ask-conv-item");
    items.forEach(function (li) {
      li.classList.toggle("is-active", li.dataset.convId === String(id));
    });
  }

  // ── Message helpers ───────────────────────────────────────────────────────

  function ensureInner() {
    var inner = getOrCreateInner();
    if (!inner) {
      // Shouldn't happen since HTML has it, but be safe
      inner = el("div", "ask-messages-inner");
      messagesEl.appendChild(inner);
      messagesInner = inner;
    }
    return inner;
  }

  function addMessage(role) {
    if (emptyEl) emptyEl.hidden = true;
    var inner = ensureInner();
    var wrap = el("div", "ask-msg ask-msg-" + role);
    var body = el("div", "ask-msg-body");
    wrap.appendChild(body);
    inner.appendChild(wrap);
    scrollBottom();
    return body;
  }

  // ── Tool chip factory (inline, pill style) ────────────────────────────────

  function makeToolChip(name, done) {
    var chip = el("div", "ask-tool-chip" + (done ? " is-done" : ""));
    if (!done) {
      var dot = el("span", "ask-tool-dot");
      chip.appendChild(dot);
    }
    var label = el("span", "", done ? "✓ " + name : "● " + name);
    chip.appendChild(label);
    return chip;
  }

  // ── Collapse chips into a summary line on turn completion ─────────────────

  function collapseChipsToSummary(chipsRow, toolNames) {
    if (!chipsRow || !toolNames || toolNames.length === 0) return;

    // Build summary counts
    var counts = {};
    toolNames.forEach(function (name) {
      counts[name] = (counts[name] || 0) + 1;
    });
    var total = toolNames.length;

    // Replace chips row content with summary toggle
    chipsRow.innerHTML = "";
    chipsRow.classList.remove("ask-chips-row");
    chipsRow.className = "ask-tools-summary";

    var icon = el("span", "", "🔧");
    var label = el("span", "", total + " tool call" + (total !== 1 ? "s" : ""));
    var caret = el("span", "ask-tools-summary-caret", "⌄");

    chipsRow.appendChild(icon);
    chipsRow.appendChild(label);
    chipsRow.appendChild(caret);

    // Build detail row (hidden by default)
    var detail = el("div", "ask-tools-detail");
    detail.hidden = true;
    Object.keys(counts).forEach(function (name) {
      var chip = el("div", "ask-tool-chip-summary", "✓ " + name + (counts[name] > 1 ? " ×" + counts[name] : ""));
      detail.appendChild(chip);
    });

    // Insert detail after summary
    chipsRow.parentNode.insertBefore(detail, chipsRow.nextSibling);

    // Toggle expand/collapse
    chipsRow.addEventListener("click", function () {
      var open = !detail.hidden;
      detail.hidden = open;
      chipsRow.classList.toggle("is-open", !open);
    });
  }

  // ── Open persisted conversation ───────────────────────────────────────────

  function openConversation(id) {
    fetch("/api/ask/conversations/" + id).then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        conversationId = id;
        var inner = ensureInner();
        inner.innerHTML = "";

        // Re-attach empty state (hidden)
        if (emptyEl) {
          inner.appendChild(emptyEl);
          emptyEl.hidden = true;
        }

        markActiveConv(id);

        // Reset and rebuild usage state from persisted data
        resetUsageState();

        (data.messages || []).forEach(function (m) {
          if (m.role === "tool") return;

          // Accumulate token usage from persisted assistant messages — grouped
          // by the model/provider stored alongside the usage in the DB.
          if (m.role === "assistant" && m.token_usage) {
            var u = m.token_usage;
            // Prefer the model/provider annotated on the stored usage; fall
            // back to the conversation-level model if the row predates Fix 3.
            var msgModel = u.model || data.model || null;
            var msgProvider = u.provider || data.provider || null;
            var key = _modelKey(msgProvider, msgModel);
            if (key) {
              addUsageFromTokens(u, key);
            }
          }

          var body = addMessage(m.role === "assistant" ? "assistant" : "user");
          // Render blocks in order, keeping tool-call chips inline (consecutive
          // tool calls group into one chips row) — mirrors the live stream.
          var openChips = null;
          (m.content || []).forEach(function (b) {
            if (b.type === "text") {
              openChips = null;
              var mdDiv = el("div", "ask-md");
              mdDiv.innerHTML = renderMarkdown(b.text || "");
              body.appendChild(mdDiv);
            } else if (b.type === "tool_use") {
              addToolToUsage(b.name || "tool");
              if (!openChips) {
                openChips = el("div", "ask-chips-row");
                body.appendChild(openChips);
              }
              openChips.appendChild(makeToolChip(b.name || "tool", true));
            }
          });
        });

        renderRail();
        showRail();
        scrollBottom();
      });
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

  // ── New chat button ───────────────────────────────────────────────────────

  if (newBtn) {
    newBtn.addEventListener("click", function () {
      conversationId = null;
      var inner = ensureInner();
      inner.innerHTML = "";
      if (emptyEl) {
        inner.appendChild(emptyEl);
        emptyEl.hidden = false;
      }
      markActiveConv(null);
      resetUsageState();
      renderRail();
      // Don't hide the rail on new chat — just show empty state
    });
  }

  // ── Auto-grow textarea ────────────────────────────────────────────────────

  if (input) {
    input.addEventListener("input", function () {
      input.style.height = "auto";
      var capped = Math.min(input.scrollHeight, 200);
      input.style.height = capped + "px";
    });
  }

  // ── Send / stream ─────────────────────────────────────────────────────────

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      input.style.height = "auto";
      sendBtn.disabled = true;

      // Render user message
      var userBody = addMessage("user");
      var userMd = el("div", "ask-md");
      userMd.innerHTML = renderMarkdown(text);
      userBody.appendChild(userMd);

      // Prepare assistant turn container
      var assistantBody = addMessage("assistant");
      var thinkingEl = el("div", "ask-thinking");
      for (var di = 0; di < 3; di++) {
        thinkingEl.appendChild(el("span", "ask-thinking-dot"));
      }
      assistantBody.appendChild(thinkingEl);

      // Streaming state per turn
      var currentTextEl = null;
      var currentTextSrc = "";
      var chipMap = {};     // tool_id → chip DOM element
      var chipsRow = null;  // single .ask-chips-row for this turn
      var turnToolNames = []; // ordered list for collapse

      fetch("/api/ask/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken(),
        },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      })
        .then(function (resp) {
          if (!resp.ok) {
            resp
              .json()
              .catch(function () {
                return {};
              })
              .then(function (j) {
                if (thinkingEl && thinkingEl.parentNode) {
                  thinkingEl.remove();
                  thinkingEl = null;
                }
                var errDiv = el("div", "ask-error", j.message || "Error: " + resp.status);
                assistantBody.appendChild(errDiv);
                if (j.error === "no_key") window.location.href = "/settings?tab=ai";
                sendBtn.disabled = false;
              });
            return;
          }

          var reader = resp.body.getReader();
          var decoder = new TextDecoder();
          var buf = "";

          function pump() {
            reader
              .read()
              .then(function (result) {
                if (result.done) {
                  removeThinking();
                  sendBtn.disabled = false;
                  loadConversations();
                  return;
                }
                buf += decoder.decode(result.value, { stream: true });
                var idx;
                while ((idx = buf.indexOf("\n\n")) >= 0) {
                  var frame = buf.slice(0, idx);
                  buf = buf.slice(idx + 2);
                  var lines = frame.split("\n");
                  var dataLine = null;
                  for (var i = 0; i < lines.length; i++) {
                    if (lines[i].indexOf("data:") === 0) {
                      dataLine = lines[i];
                      break;
                    }
                  }
                  if (!dataLine) continue;
                  var payload;
                  try {
                    payload = JSON.parse(dataLine.slice(5).trim());
                  } catch (err) {
                    continue;
                  }
                  handleEvent(payload);
                }
                pump();
              })
              .catch(function () {
                sendBtn.disabled = false;
              });
          }
          pump();
        })
        .catch(function () {
          removeThinking();
          var errDiv = el("div", "ask-error", "Network error.");
          assistantBody.appendChild(errDiv);
          sendBtn.disabled = false;
        });

      // ── Thinking indicator ────────────────────────────────────────────────

      var thinkingRemoved = false;
      function removeThinking() {
        if (thinkingRemoved) return;
        thinkingRemoved = true;
        if (thinkingEl && thinkingEl.parentNode) {
          thinkingEl.remove();
          thinkingEl = null;
        }
      }

      // ── SSE event handler ─────────────────────────────────────────────────

      function handleEvent(p) {
        try {
          if (p.type === "conversation") {
            conversationId = p.conversation_id;
            markActiveConv(conversationId);

            // Record which model/provider this turn uses so message_done
            // can credit the right bucket.
            if (p.model) {
              usageState.currentTurnKey = _modelKey(p.provider || null, p.model);
            }
            renderRail();
            showRail();

            // New conversations: prepend to sidebar
            if (p.title) {
              var existing = convListEl.querySelector('[data-conv-id="' + conversationId + '"]');
              if (!existing) {
                var li = el("li", "ask-conv-item is-active", p.title);
                li.dataset.convId = conversationId;
                li.addEventListener("click", function () {
                  openConversation(conversationId);
                });
                convListEl.insertBefore(li, convListEl.firstChild);
              } else {
                existing.textContent = p.title;
                existing.classList.add("is-active");
              }
            }
          } else if (p.type === "text_delta") {
            removeThinking();
            // Seal any open chips row so text and chips don't interleave visually
            if (!currentTextEl) {
              currentTextEl = el("div", "ask-md");
              currentTextSrc = "";
              assistantBody.appendChild(currentTextEl);
            }
            currentTextSrc += p.text || "";
            currentTextEl.innerHTML = renderMarkdown(currentTextSrc);
          } else if (p.type === "tool_call_start") {
            removeThinking();
            // Start a new text block after tool chips
            currentTextEl = null;
            currentTextSrc = "";

            // Lazily create chips row for this turn
            if (!chipsRow) {
              chipsRow = el("div", "ask-chips-row");
              assistantBody.appendChild(chipsRow);
            }

            var chip = makeToolChip(p.tool_name || "tool", false);
            chipMap[p.tool_id] = chip;
            chipsRow.appendChild(chip);

            // Track for rail + collapse
            turnToolNames.push(p.tool_name || "tool");
            addToolToUsage(p.tool_name || "tool");
          } else if (p.type === "tool_call_end") {
            var doneChip = chipMap[p.tool_id];
            if (doneChip) {
              doneChip.classList.add("is-done");
              var dotEl = doneChip.querySelector(".ask-tool-dot");
              if (dotEl) dotEl.remove();
              var lblEl = doneChip.querySelector("span");
              if (lblEl) {
                // Extract name from "● name" → "✓ name"
                var nameText = (lblEl.textContent || "").replace(/^[●✓]\s*/, "");
                lblEl.textContent = "✓ " + nameText;
              }
            }
          } else if (p.type === "message_done") {
            // Keep the tool-call chips inline in the thread (they show the context
            // of when each call was made). The right rail carries the aggregate.
            // Accumulate token usage from the terminal message_done.
            if (p.usage) {
              addUsageFromTokens(p.usage);
            }
          } else if (p.type === "error") {
            removeThinking();
            var errDiv = el("div", "ask-error", p.error || "Something went wrong.");
            assistantBody.appendChild(errDiv);
          }
        } catch (err) {
          // Never crash the stream
        }
        scrollBottom();
      }
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  loadConversations();
  checkKeys();
})();
