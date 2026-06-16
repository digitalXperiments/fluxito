// app/static/js/ask.js
(function () {
  "use strict";

  var messagesEl = document.getElementById("ask-messages");
  var emptyEl = document.getElementById("ask-empty");
  var setupHint = document.getElementById("ask-setup-hint");
  var form = document.getElementById("ask-composer");
  var input = document.getElementById("ask-input");
  var sendBtn = document.getElementById("ask-send");
  var convListEl = document.getElementById("ask-conv-list");
  var newBtn = document.getElementById("ask-new");

  var conversationId = null;

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

  // ── Markdown renderer (self-contained, XSS-safe) ─────────────────────────
  // Processes a practical subset: fenced code, inline code, headings, bold,
  // italic, links, unordered/ordered lists, GH-style tables, blockquotes,
  // paragraphs and line breaks.

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
    // Normalise line endings.
    var lines = src.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    var out = [];
    var i = 0;
    var n = lines.length;

    // Accumulate contiguous non-blank, non-special lines as a paragraph.
    var paraLines = [];

    function flushPara() {
      if (!paraLines.length) return;
      var text = paraLines.join("\n");
      out.push("<p>" + inlineMarkdown(text) + "</p>");
      paraLines = [];
    }

    while (i < n) {
      var line = lines[i];

      // ── Fenced code block ````...```
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
        i++; // consume closing fence
        var langAttr = lang ? ' class="language-' + lang + '"' : "";
        out.push("<pre><code" + langAttr + ">" + codeLines.join("\n") + "</code></pre>");
        continue;
      }

      // ── ATX Heading (#..######)
      var hMatch = line.match(/^(#{1,6})\s+(.*)/);
      if (hMatch) {
        flushPara();
        var level = hMatch[1].length;
        out.push("<h" + level + ">" + inlineMarkdown(hMatch[2].trim()) + "</h" + level + ">");
        i++;
        continue;
      }

      // ── Blockquote
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

      // ── Unordered list
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

      // ── Ordered list
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

      // ── GH-style table (line contains | and next line is separator |---|)
      if (line.indexOf("|") !== -1 && i + 1 < n && lines[i + 1].match(/^\|?[\s\-|:]+\|/)) {
        flushPara();
        var tblRows = [];
        // Header row
        tblRows.push(parseTableRow(line, true));
        i += 2; // skip separator
        while (i < n && lines[i].indexOf("|") !== -1) {
          tblRows.push(parseTableRow(lines[i], false));
          i++;
        }
        out.push('<div class="ask-table-wrap"><table>' + tblRows.join("") + "</table></div>");
        continue;
      }

      // ── Blank line → flush paragraph
      if (line.trim() === "") {
        flushPara();
        i++;
        continue;
      }

      // ── Regular text → accumulate into paragraph
      paraLines.push(line);
      i++;
    }

    flushPara();
    return out.join("\n");
  }

  function parseTableRow(line, isHeader) {
    // Strip leading/trailing pipes
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
    // HTML-escape first
    s = escHtml(s);

    // Inline code (must come before bold/italic to avoid mangling backticks)
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Bold **text** or __text__
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");

    // Italic *text* or _text_ (single, not double)
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    s = s.replace(/_([^_\n]+)_/g, "<em>$1</em>");

    // Links [text](url) — only http/https/relative
    s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)]*)\)/g, function (_, text, href) {
      return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + text + "</a>";
    });

    // Line breaks: two trailing spaces → <br>
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

  function updateConvTitle(id, title) {
    var items = convListEl.querySelectorAll(".ask-conv-item");
    items.forEach(function (li) {
      if (li.dataset.convId === String(id)) {
        li.textContent = title;
      }
    });
  }

  // ── Message / bubble helpers ──────────────────────────────────────────────

  function addMessage(role) {
    if (emptyEl) emptyEl.hidden = true;
    var wrap = el("div", "ask-msg ask-msg-" + role);
    var body = el("div", "ask-msg-body");
    wrap.appendChild(body);
    messagesEl.appendChild(wrap);
    scrollBottom();
    return body;
  }

  function makeToolChip(name, done) {
    var chip = el("div", "ask-tool-chip" + (done ? " is-done" : ""));
    var dot = el("span", "ask-tool-dot");
    chip.appendChild(dot);
    var label = el("span", "", (done ? "" : "Calling ") + name + (done ? " ✓" : "…"));
    chip.appendChild(label);
    return chip;
  }

  // ── Open persisted conversation ───────────────────────────────────────────

  function openConversation(id) {
    fetch("/api/ask/conversations/" + id).then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        conversationId = id;
        messagesEl.innerHTML = "";
        markActiveConv(id);

        (data.messages || []).forEach(function (m) {
          if (m.role === "tool") return;
          var body = addMessage(m.role === "assistant" ? "assistant" : "user");
          (m.content || []).forEach(function (b) {
            if (b.type === "text") {
              var mdDiv = el("div", "ask-md");
              mdDiv.innerHTML = renderMarkdown(b.text || "");
              body.appendChild(mdDiv);
            } else if (b.type === "tool_use") {
              body.appendChild(makeToolChip(b.name || "tool", true));
            }
          });
        });

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
      messagesEl.innerHTML = "";
      if (emptyEl) {
        messagesEl.appendChild(emptyEl);
        emptyEl.hidden = false;
      }
      markActiveConv(null);
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
      // Reset textarea height
      input.style.height = "auto";
      sendBtn.disabled = true;

      // Render user message
      var userBody = addMessage("user");
      var userMd = el("div", "ask-md");
      userMd.innerHTML = renderMarkdown(text);
      userBody.appendChild(userMd);

      // Prepare assistant bubble (empty until stream arrives)
      var assistantBody = addMessage("assistant");

      // Streaming state
      var currentTextEl = null;   // the .ask-md block currently being appended to
      var currentTextSrc = "";    // accumulated raw markdown source for currentTextEl
      var chipMap = {};           // tool_id → chip DOM element

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
          var errDiv = el("div", "ask-error", "Network error.");
          assistantBody.appendChild(errDiv);
          sendBtn.disabled = false;
        });

      // ── SSE event handler (closure over assistantBody + streaming state) ──

      function handleEvent(p) {
        if (p.type === "conversation") {
          conversationId = p.conversation_id;
          markActiveConv(conversationId);
          // For new conversations the title arrives in the first frame.
          if (p.title) {
            // Prepend to sidebar (list will fully refresh after stream ends via loadConversations).
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
          if (!currentTextEl) {
            currentTextEl = el("div", "ask-md");
            currentTextSrc = "";
            assistantBody.appendChild(currentTextEl);
          }
          currentTextSrc += p.text || "";
          currentTextEl.innerHTML = renderMarkdown(currentTextSrc);
        } else if (p.type === "tool_call_start") {
          // Seal off the current text block so text after the tool call
          // starts a fresh block (preserving visual order).
          currentTextEl = null;
          currentTextSrc = "";
          var chip = makeToolChip(p.tool_name || "tool", false);
          chipMap[p.tool_id] = chip;
          assistantBody.appendChild(chip);
        } else if (p.type === "tool_call_end") {
          var doneChip = chipMap[p.tool_id];
          if (doneChip) {
            doneChip.classList.add("is-done");
            var dotEl = doneChip.querySelector(".ask-tool-dot");
            var lblEl = doneChip.querySelector("span:not(.ask-tool-dot)");
            if (lblEl) {
              var toolName = (lblEl.textContent || "")
                .replace(/^Calling\s+/, "")
                .replace(/…$/, "");
              lblEl.textContent = toolName + " ✓";
            }
            if (dotEl) dotEl.remove();
          }
        } else if (p.type === "error") {
          var errDiv = el("div", "ask-error", p.error || "Something went wrong.");
          assistantBody.appendChild(errDiv);
        }
        scrollBottom();
      }
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  loadConversations();
  checkKeys();
})();
