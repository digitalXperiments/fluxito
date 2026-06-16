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

  function csrfToken() {
    var m = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function addMessage(role) {
    if (emptyEl) emptyEl.hidden = true;
    var wrap = el("div", "ask-msg ask-msg-" + role);
    var body = el("div", "ask-msg-body");
    wrap.appendChild(body);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return body;
  }

  function addToolChip(body, name) {
    var chip = el("div", "ask-tool-chip", "Calling " + name + "…");
    body.appendChild(chip);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return chip;
  }

  function loadConversations() {
    fetch("/api/ask/conversations").then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        convListEl.innerHTML = "";
        (data.conversations || []).forEach(function (c) {
          var li = el("li", "ask-conv-item", c.title);
          li.addEventListener("click", function () { openConversation(c.id); });
          convListEl.appendChild(li);
        });
      });
    });
  }

  function openConversation(id) {
    fetch("/api/ask/conversations/" + id).then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        conversationId = id;
        messagesEl.innerHTML = "";
        (data.messages || []).forEach(function (m) {
          if (m.role === "tool") return;
          var body = addMessage(m.role === "assistant" ? "assistant" : "user");
          (m.content || []).forEach(function (b) {
            if (b.type === "text") body.appendChild(el("div", "ask-text", b.text));
            if (b.type === "tool_use") addToolChip(body, b.name);
          });
        });
      });
    });
  }

  function checkKeys() {
    fetch("/api/ask/keys").then(function (r) {
      if (!r.ok) return;
      r.json().then(function (data) {
        var hasKey = (data.providers || []).length > 0;
        if (setupHint) setupHint.hidden = hasKey;
      });
    });
  }

  if (newBtn) {
    newBtn.addEventListener("click", function () {
      conversationId = null;
      messagesEl.innerHTML = "";
      if (emptyEl) { messagesEl.appendChild(emptyEl); emptyEl.hidden = false; }
    });
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendBtn.disabled = true;

      addMessage("user").appendChild(el("div", "ask-text", text));
      var assistantBody = addMessage("assistant");
      var textNode = el("div", "ask-text", "");
      assistantBody.appendChild(textNode);

      fetch("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      }).then(function (resp) {
        if (!resp.ok) {
          resp.json().catch(function () { return {}; }).then(function (j) {
            textNode.textContent = j.message || "Error: " + resp.status;
            if (j.error === "no_key") window.location.href = "/settings?tab=ai";
            sendBtn.disabled = false;
          });
          return;
        }

        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buf = "";

        function pump() {
          reader.read().then(function (result) {
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
                if (lines[i].indexOf("data:") === 0) { dataLine = lines[i]; break; }
              }
              if (!dataLine) continue;
              var payload;
              try { payload = JSON.parse(dataLine.slice(5).trim()); } catch (err) { continue; }
              handleEvent(payload, textNode, assistantBody);
            }
            pump();
          }).catch(function () {
            sendBtn.disabled = false;
          });
        }
        pump();
      }).catch(function () {
        textNode.textContent = "Network error.";
        sendBtn.disabled = false;
      });
    });
  }

  function handleEvent(p, textNode, assistantBody) {
    if (p.type === "conversation") { conversationId = p.conversation_id; }
    else if (p.type === "text_delta") { textNode.textContent += p.text || ""; }
    else if (p.type === "tool_call_start") { addToolChip(assistantBody, p.tool_name || "tool"); }
    else if (p.type === "error") {
      assistantBody.appendChild(el("div", "ask-error", p.error || "Something went wrong."));
    }
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // init
  loadConversations();
  checkKeys();
})();
