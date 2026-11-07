/* site-chatbot iframe UI: SSE streaming render, source chips (links + "Show me"), refusals. */
(function () {
  "use strict";
  var qs = new URLSearchParams(location.search);
  var PARENT_ORIGIN = qs.get("parent") || "*";
  var bridge = false; // parent embed script alive?

  var $messages = document.getElementById("messages");
  var $suggestions = document.getElementById("suggestions");
  var $form = document.getElementById("composer");
  var $input = document.getElementById("q");
  var $send = $form.querySelector("button");
  var sessionId = sessionStorage.getItem("cb-session") || "";
  var streaming = false;

  /* -------------------------------------------------- parent bridge handshake */
  function postParent(msg) {
    try { window.parent.postMessage(msg, PARENT_ORIGIN); } catch (e) {}
  }
  postParent({ type: "READY" });
  window.addEventListener("message", function (ev) {
    if (PARENT_ORIGIN !== "*" && ev.origin !== PARENT_ORIGIN) return;
    if (ev.data && ev.data.type === "PARENT_ALIVE") bridge = true;
    if (ev.data && ev.data.type === "OPENED") $input && $input.focus();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") postParent({ type: "CLOSE" });
  });

  /* ------------------------------------------------------------------ helpers */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function nearBottom() {
    return $messages.scrollHeight - $messages.scrollTop - $messages.clientHeight < 80;
  }
  function scrollDown(force) {
    if (force || nearBottom()) $messages.scrollTop = $messages.scrollHeight;
  }
  function bubble(cls, text) {
    var b = el("div", "cb-msg " + cls, text || "");
    $messages.appendChild(b);
    scrollDown(true);
    return b;
  }

  function renderSources(container, citations) {
    var wrap = el("div", "cb-sources");
    citations.forEach(function (c) {
      var row = el("div", "cb-source");
      var a = el("a", null, (c.rescued ? "≈ " : "📄 ") + (c.label || c.url));
      a.href = c.url;
      a.target = "_blank";
      a.rel = "noopener";
      row.appendChild(a);
      if (bridge) {
        var btn = el("button", "cb-showme", "Show me");
        btn.type = "button";
        btn.addEventListener("click", function () {
          postParent({ type: "GUIDE", url: c.url.split("#")[0], anchor: c.anchor || "",
                       quote: c.quote || "" });
        });
        row.appendChild(btn);
      }
      wrap.appendChild(row);
    });
    container.appendChild(wrap);
    scrollDown();
  }

  /* ------------------------------------------------------------------- send */
  $form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $input.value.trim();
    if (!q || streaming) return;
    $input.value = "";
    send(q);
  });

  async function send(question) {
    streaming = true;
    $send.disabled = true;
    bubble("user", question);
    var bot = bubble("bot");
    var cursor = el("span", "cursor");
    bot.appendChild(cursor);
    var acc = "";

    try {
      var res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, session_id: sessionId }),
      });
      if (!res.ok || !res.body) {
        var detail = res.status === 429 ? "Too many requests — please slow down." :
          "Something went wrong (" + res.status + ").";
        bot.className = "cb-msg bot error";
        bot.textContent = detail;
        return;
      }
      var reader = res.body.getReader();
      var buf = "";
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += new TextDecoder().decode(chunk.value, { stream: true });
        var idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          var raw = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 2);
          if (!raw.startsWith("data:")) continue;
          var ev;
          try { ev = JSON.parse(raw.slice(5).trim()); } catch (err) { continue; }
          handleEvent(ev, bot);
          if (ev.type === "token") {
            acc += ev.text;
            bot.textContent = acc;
            bot.appendChild(cursor);
            scrollDown();
          }
        }
      }
    } catch (err) {
      bot.className = "cb-msg bot error";
      bot.textContent = "Connection problem — please try again.";
    } finally {
      cursor.remove();
      if (!bot.textContent.trim() && !bot.querySelector(".cb-sources")) bot.remove();
      streaming = false;
      $send.disabled = false;
      $input.focus();
    }

    function handleEvent(ev, botEl) {
      if (ev.type === "meta") {
        sessionId = ev.session_id;
        sessionStorage.setItem("cb-session", sessionId);
      } else if (ev.type === "refusal") {
        botEl.className = "cb-msg bot refusal";
        botEl.textContent = ev.message;
      } else if (ev.type === "citations") {
        renderSources(botEl, ev.citations);
      } else if (ev.type === "error") {
        var note = el("div", "cb-msg bot error", ev.message);
        $messages.appendChild(note);
        scrollDown();
      }
    }
  }

  /* ------------------------------------------------------------------- info */
  fetch("/api/info").then(function (r) { return r.ok ? r.json() : null; }).then(function (info) {
    if (!info) return;
    document.title = info.site_name + " — Assistant";
    if (info.suggestions && info.suggestions.length) {
      $suggestions.hidden = false;
      info.suggestions.forEach(function (s) {
        var b = el("button", null, s);
        b.type = "button";
        b.addEventListener("click", function () {
          if (!streaming) { b.remove(); send(s); }
        });
        $suggestions.appendChild(b);
      });
    }
  }).catch(function () {});
})();
