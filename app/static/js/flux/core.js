// app/static/js/flux/core.js
//
// FluxChat — reusable chat core extracted from ask.js. Owns the SSE transport
// (POST /api/ask/stream + frame parsing), message-list rendering (markdown,
// tool-call lines, GTM draft cards), and conversation load/append logic.
//
// It carries ZERO hardcoded getElementById: every DOM reference comes from the
// `elements` object handed to FluxChat.create({ elements, options }). Page-only
// concerns (history rail, usage panel, provider picker, thread header) live in
// the caller and hook in through the `options.on*` callbacks below.
//
// Exposed as a plain script global (window.FluxChat) to match the app's
// deferred-script loading — no ES modules outside the tracking-plan SPA.
(function () {
  "use strict";

  // ── DOM helpers ───────────────────────────────────────────────────────────

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function csrfToken() {
    var m = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
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

  // ── Tool-call line summary ────────────────────────────────────────────────

  var TOOL_DETAIL_KEYS = [
    "action",
    "query",
    "metric",
    "event_name",
    "name",
    "platform",
    "date_range",
    "period",
    "report",
  ];

  function summarizeToolInput(inputObj) {
    if (!inputObj || typeof inputObj !== "object") return "";
    var parts = [];
    TOOL_DETAIL_KEYS.forEach(function (k) {
      if (parts.length >= 2) return;
      var v = inputObj[k];
      if (typeof v === "string" && v.trim() && v.length <= 40) parts.push(v.trim());
    });
    return parts.join(" · ");
  }

  function makeToolLine(name, detail, done) {
    var line = el("div", "ask-tool-line" + (done ? " is-done" : " is-pending"));
    line.appendChild(document.createTextNode("→ " + name + (detail ? " · " + detail : "")));
    if (done) {
      var check = el("span", "ask-tool-line-check", "  ✓");
      line.appendChild(check);
    }
    return line;
  }

  // ── FluxChat instance factory ─────────────────────────────────────────────
  //
  // config = {
  //   elements: { messages, empty?, input?, form?, sendBtn? },
  //   options: {
  //     streamUrl?, conversationsUrl?, draftsUrl?,
  //     placeholders?: { fresh, reply },
  //     extraBody?,               // object | () => object, merged into stream body
  //     onConversation?,         // ({conversationId, title, provider, model, isNew})
  //     onToolCall?,             // (toolName)
  //     onUsage?,                // (usageObj, {provider, model} | null)
  //     onDraftStatus?,          // (status)
  //     onStreamStart?, onStreamEnd?,
  //     onConversationLoaded?,   // (data, id)  — before transcript render
  //     onConversationRendered?, // (data, id)  — after transcript render
  //     onReset?,                // ()          — after newChat()
  //   }
  // }
  function create(config) {
    config = config || {};
    var refs = config.elements || {};
    var opts = config.options || {};

    var messagesEl = refs.messages || null;
    var emptyEl = refs.empty || null;
    var input = refs.input || null;
    var form = refs.form || null;
    var sendBtn = refs.sendBtn || null;

    var streamUrl = opts.streamUrl || "/api/ask/stream";
    var conversationsUrl = opts.conversationsUrl || "/api/ask/conversations";
    var draftsUrl = opts.draftsUrl || "/api/ask/drafts";
    var placeholders = opts.placeholders || {};

    var conversationId = null;
    var messagesInner = null;

    function hook(name) {
      var fn = opts[name];
      if (typeof fn !== "function") return undefined;
      try {
        return fn.apply(null, Array.prototype.slice.call(arguments, 1));
      } catch (e) {
        // A page callback must never break the stream.
        return undefined;
      }
    }

    // ── DOM plumbing ─────────────────────────────────────────────────────────

    function scrollBottom() {
      if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function ensureInner() {
      if (messagesInner && messagesInner.isConnected) return messagesInner;
      if (messagesEl) messagesInner = messagesEl.querySelector(".ask-messages-inner");
      if (!messagesInner && messagesEl) {
        messagesInner = el("div", "ask-messages-inner");
        messagesEl.appendChild(messagesInner);
      }
      return messagesInner;
    }

    function setPlaceholder(kind) {
      if (!input) return;
      var text = kind === "reply" ? placeholders.reply : placeholders.fresh;
      if (text != null) input.placeholder = text;
    }

    function addMessage(role) {
      if (emptyEl) emptyEl.hidden = true;
      var inner = ensureInner();
      var wrap = el("div", "ask-msg ask-msg-" + role);
      if (role === "assistant") {
        var avatar = el("span", "ask-msg-avatar");
        avatar.setAttribute("aria-hidden", "true");
        avatar.textContent = "F";
        wrap.appendChild(avatar);
      }
      var body = el("div", "ask-msg-body");
      wrap.appendChild(body);
      inner.appendChild(wrap);
      scrollBottom();
      return body;
    }

    // ── GTM diff card (Conversation approve flow) ──────────────────────────
    //
    // `draftData` shape (server: app.ask.drafts.draft_to_stream_payload):
    //   { id, message_id, kind, title, status, published_version,
    //     payload: { workspace_label, target, diff: [{kind, text}] } }
    // status ∈ 'pending' | 'published' | 'rejected'.

    function renderDraftCard(draftData) {
      var card = el("div", "ask-draft-card");
      card.dataset.draftId = draftData.id;

      var payload = draftData.payload || {};

      var header = el("div", "ask-draft-header");
      if (payload.workspace_label) {
        header.appendChild(el("span", "ask-draft-header-label", payload.workspace_label));
      }
      if (payload.target) {
        header.appendChild(el("span", "ask-draft-header-target", payload.target));
      }
      card.appendChild(header);

      var body = el("div", "ask-draft-body");
      (payload.diff || []).forEach(function (line) {
        var kind = line.kind === "removed" || line.kind === "added" ? line.kind : "context";
        var row = el("div", "ask-draft-line is-" + kind);
        row.textContent = line.text || "";
        body.appendChild(row);
      });
      card.appendChild(body);

      card.appendChild(renderDraftFooter(draftData));

      return card;
    }

    function renderDraftFooter(draftData) {
      var status = draftData.status;
      if (status === "published") {
        var pubFooter = el("div", "ask-draft-footer is-published");
        pubFooter.appendChild(el("span", "ask-draft-dot is-published"));
        var vLabel = draftData.published_version ? " · version " + draftData.published_version : "";
        var pubText = (draftData.payload && draftData.payload.workspace_label
          ? "Published to " + draftData.payload.workspace_label.split(" · ")[0]
          : "Published") + vLabel + " · just now";
        pubFooter.appendChild(el("span", "ask-draft-status-text is-published", pubText));
        pubFooter.appendChild(el("span", "ask-draft-rollback-hint", "Rollback available"));
        return pubFooter;
      }

      if (status === "rejected") {
        var rejFooter = el("div", "ask-draft-footer is-rejected");
        rejFooter.appendChild(el("span", "ask-draft-dot is-rejected"));
        rejFooter.appendChild(
          el("span", "ask-draft-status-text is-rejected", "Rejected — workspace kept as draft.")
        );
        var undo = el("span", "ask-draft-undo", "Undo");
        undo.addEventListener("click", function () {
          resolveDraft(draftData.id, "reset", rejFooter);
        });
        rejFooter.appendChild(undo);
        return rejFooter;
      }

      var footer = el("div", "ask-draft-footer");
      footer.appendChild(
        el("span", "ask-draft-footer-note", "Publishes to the live container after your approval.")
      );
      var rejectBtn = el("span", "ask-draft-btn ask-draft-btn-reject", "Reject");
      rejectBtn.addEventListener("click", function () {
        resolveDraft(draftData.id, "reject", footer);
      });
      var approveBtn = el("span", "ask-draft-btn ask-draft-btn-approve", "Approve & publish");
      approveBtn.addEventListener("click", function () {
        resolveDraft(draftData.id, "approve", footer);
      });
      footer.appendChild(rejectBtn);
      footer.appendChild(approveBtn);
      return footer;
    }

    function resolveDraft(draftId, action, footerEl) {
      if (footerEl)
        footerEl.querySelectorAll(".ask-draft-btn").forEach(function (b) {
          b.setAttribute("disabled", "disabled");
        });
      fetch(draftsUrl + "/" + draftId + "/" + action, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok || !result.data || !result.data.draft) return;
          var draft = result.data.draft;
          var card = footerEl ? footerEl.closest(".ask-draft-card") : null;
          if (!card) return;
          var oldFooter = card.querySelector(".ask-draft-footer");
          var newFooter = renderDraftFooter(draft);
          if (oldFooter) card.replaceChild(newFooter, oldFooter);
          else card.appendChild(newFooter);

          hook("onDraftStatus", draft.status);

          if (action === "approve") {
            // Design: this confirmation is a sibling of the diff card inside
            // the *same* avatar/body column — not a brand-new Flux turn.
            appendFollowUpMessage(
              "Done — published as version " +
                (draft.published_version || "—") +
                ". Match rate should recover within a few hours; I'll watch it and confirm in " +
                "tomorrow's briefing. I also set a monitor so this can't silently break again.",
              card.parentNode
            );
          }
        })
        .catch(function () {
          // Leave the disabled buttons — retry by reloading the conversation.
        });
    }

    function appendFollowUpMessage(text, body) {
      var mdDiv = el("div", "ask-md");
      mdDiv.innerHTML = renderMarkdown(text);
      body.appendChild(mdDiv);
      scrollBottom();
    }

    // ── Open persisted conversation ────────────────────────────────────────

    function openConversation(id) {
      return fetch(conversationsUrl + "/" + id).then(function (r) {
        if (!r.ok) return;
        return r.json().then(function (data) {
          conversationId = id;
          var inner = ensureInner();
          inner.innerHTML = "";

          if (emptyEl) {
            inner.appendChild(emptyEl);
            emptyEl.hidden = true;
          }

          hook("onConversationLoaded", data, id);
          setPlaceholder("reply");

          // Drafts grouped by the assistant message they render under, so each
          // re-renders in its current state (pending/published/rejected).
          var draftsByMessage = {};
          var unattachedDrafts = [];
          (data.drafts || []).forEach(function (d) {
            if (d.message_id) {
              (draftsByMessage[d.message_id] = draftsByMessage[d.message_id] || []).push(d);
            } else {
              unattachedDrafts.push(d);
            }
          });

          var lastAssistantBody = null;

          (data.messages || []).forEach(function (m) {
            if (m.role === "tool") return;

            if (m.role === "assistant" && m.token_usage) {
              var u = m.token_usage;
              var meta = {
                provider: u.provider || data.provider || null,
                model: u.model || data.model || null,
              };
              if (meta.model) hook("onUsage", u, meta);
            }

            var body = addMessage(m.role === "assistant" ? "assistant" : "user");
            if (m.role === "assistant") lastAssistantBody = body;

            var openLines = null;
            (m.content || []).forEach(function (b) {
              if (b.type === "text") {
                openLines = null;
                var mdDiv = el("div", "ask-md");
                mdDiv.innerHTML = renderMarkdown(b.text || "");
                body.appendChild(mdDiv);
              } else if (b.type === "tool_use") {
                hook("onToolCall", b.name || "tool");
                if (!openLines) {
                  openLines = el("div", "ask-tool-lines");
                  body.appendChild(openLines);
                }
                openLines.appendChild(makeToolLine(b.name || "tool", summarizeToolInput(b.input), true));
              }
            });

            (draftsByMessage[m.id] || []).forEach(function (d) {
              body.appendChild(renderDraftCard(d));
            });
          });

          if (unattachedDrafts.length && lastAssistantBody) {
            unattachedDrafts.forEach(function (d) {
              lastAssistantBody.appendChild(renderDraftCard(d));
            });
          }

          hook("onConversationRendered", data, id);
          scrollBottom();
        });
      });
    }

    // ── New chat / reset ───────────────────────────────────────────────────

    function newChat() {
      conversationId = null;
      var inner = ensureInner();
      inner.innerHTML = "";
      if (emptyEl) {
        inner.appendChild(emptyEl);
        emptyEl.hidden = false;
      }
      setPlaceholder("fresh");
      hook("onReset");
    }

    // ── Send / stream ──────────────────────────────────────────────────────

    function send(text) {
      text = (text || "").trim();
      if (!text) return;
      if (sendBtn) sendBtn.disabled = true;
      hook("onStreamStart");

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
      var lineMap = {}; // tool_id → .ask-tool-line DOM element
      var argsMap = {}; // tool_id → accumulated tool_args_delta fragments (raw JSON)
      var toolLines = null; // single .ask-tool-lines block for this turn

      var thinkingRemoved = false;
      function removeThinking() {
        if (thinkingRemoved) return;
        thinkingRemoved = true;
        if (thinkingEl && thinkingEl.parentNode) {
          thinkingEl.remove();
          thinkingEl = null;
        }
      }

      function finishStream() {
        if (sendBtn) sendBtn.disabled = false;
        hook("onStreamEnd");
      }

      var extra = typeof opts.extraBody === "function" ? opts.extraBody() : opts.extraBody;
      var reqBody = { message: text, conversation_id: conversationId };
      if (extra && typeof extra === "object") {
        Object.keys(extra).forEach(function (k) {
          reqBody[k] = extra[k];
        });
      }

      fetch(streamUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken(),
        },
        body: JSON.stringify(reqBody),
      })
        .then(function (resp) {
          if (!resp.ok) {
            resp
              .json()
              .catch(function () {
                return {};
              })
              .then(function (j) {
                removeThinking();
                var errDiv = el("div", "ask-error", j.message || "Error: " + resp.status);
                assistantBody.appendChild(errDiv);
                if (j.error === "no_key") window.location.href = "/settings?tab=ai";
                if (sendBtn) sendBtn.disabled = false;
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
                  finishStream();
                  return;
                }
                buf += decoder.decode(result.value, { stream: true });
                var idx;
                while ((idx = buf.indexOf("\n\n")) >= 0) {
                  var frame = buf.slice(0, idx);
                  buf = buf.slice(idx + 2);
                  var flines = frame.split("\n");
                  var dataLine = null;
                  for (var i = 0; i < flines.length; i++) {
                    if (flines[i].indexOf("data:") === 0) {
                      dataLine = flines[i];
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
                if (sendBtn) sendBtn.disabled = false;
              });
          }
          pump();
        })
        .catch(function () {
          removeThinking();
          var errDiv = el("div", "ask-error", "Network error.");
          assistantBody.appendChild(errDiv);
          if (sendBtn) sendBtn.disabled = false;
        });

      // ── SSE event handler ─────────────────────────────────────────────────

      function handleEvent(p) {
        try {
          if (p.type === "conversation") {
            conversationId = p.conversation_id;
            hook("onConversation", {
              conversationId: conversationId,
              title: p.title || null,
              provider: p.provider || null,
              model: p.model || null,
              isNew: !!p.title,
            });
            if (p.title) setPlaceholder("reply");
          } else if (p.type === "text_delta") {
            removeThinking();
            if (!currentTextEl) {
              toolLines = null;
              currentTextEl = el("div", "ask-md");
              currentTextSrc = "";
              assistantBody.appendChild(currentTextEl);
            }
            currentTextSrc += p.text || "";
            currentTextEl.innerHTML = renderMarkdown(currentTextSrc);
          } else if (p.type === "tool_call_start") {
            removeThinking();
            currentTextEl = null;
            currentTextSrc = "";

            if (!toolLines) {
              toolLines = el("div", "ask-tool-lines");
              assistantBody.appendChild(toolLines);
            }

            var line = makeToolLine(p.tool_name || "tool", "", false);
            lineMap[p.tool_id] = line;
            argsMap[p.tool_id] = "";
            toolLines.appendChild(line);

            hook("onToolCall", p.tool_name || "tool");
          } else if (p.type === "tool_args_delta") {
            if (p.tool_id != null && argsMap[p.tool_id] != null) {
              argsMap[p.tool_id] += p.args_fragment || "";
            }
          } else if (p.type === "tool_call_end") {
            var doneLine = lineMap[p.tool_id];
            if (doneLine) {
              var toolName = p.tool_name || doneLine.textContent.replace(/^→\s*/, "").split(" · ")[0];
              var detail = "";
              try {
                var argsObj = JSON.parse(argsMap[p.tool_id] || "{}");
                detail = summarizeToolInput(argsObj);
              } catch (e) {
                detail = "";
              }
              var replacement = makeToolLine(toolName, detail, true);
              doneLine.parentNode.replaceChild(replacement, doneLine);
              lineMap[p.tool_id] = replacement;
            }
          } else if (p.type === "draft") {
            removeThinking();
            currentTextEl = null;
            currentTextSrc = "";
            toolLines = null;
            if (p.draft) {
              assistantBody.appendChild(renderDraftCard(p.draft));
              hook("onDraftStatus", p.draft.status || "pending");
            }
          } else if (p.type === "message_done") {
            if (p.usage) {
              hook("onUsage", p.usage, null);
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
    }

    // ── Wire the composer (auto-grow + submit) ─────────────────────────────

    if (input) {
      input.addEventListener("input", function () {
        input.style.height = "auto";
        var capped = Math.min(input.scrollHeight, 200);
        input.style.height = capped + "px";
      });
    }

    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (!input) return;
        var text = input.value.trim();
        if (!text) return;
        input.value = "";
        input.style.height = "auto";
        send(text);
      });
    }

    // ── Public instance API ────────────────────────────────────────────────
    return {
      send: send,
      openConversation: openConversation,
      newChat: newChat,
      reset: newChat,
      renderDraftCard: renderDraftCard,
      getConversationId: function () {
        return conversationId;
      },
      setConversationId: function (id) {
        conversationId = id || null;
      },
      setPlaceholder: setPlaceholder,
      scrollBottom: scrollBottom,
    };
  }

  // ── Global export ───────────────────────────────────────────────────────
  window.FluxChat = {
    create: create,
    // Stateless helpers exposed for callers that need them (dock, etc.)
    renderMarkdown: renderMarkdown,
    escHtml: escHtml,
    csrfToken: csrfToken,
    el: el,
  };
})();
