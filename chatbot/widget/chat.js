/* site-chatbot iframe UI: SSE streaming render, source chips (links + "Show me"), refusals. */
(function () {
  "use strict";
  var qs = new URLSearchParams(location.search);
  var PARENT_ORIGIN = qs.get("parent") || "*";
  var PAGE_URL = qs.get("page") || "";
  var PAGE_TITLE = qs.get("ptitle") || "";
  var bridge = false; // parent embed script alive?

  var $messages = document.getElementById("messages");
  var $suggestions = document.getElementById("suggestions");
  var $form = document.getElementById("composer");
  var $input = document.getElementById("q");
  var $send = $form.querySelector("button");
  var sessionId = sessionStorage.getItem("cb-session") || "";
  var streaming = false;

  /* ------------------------------------------- transcript persistence (refresh) */
  var STORE_KEY = "cb-chat-v1";
  var MAX_STORED = 30;
  var EXPIRY_MS = 7 * 24 * 3600 * 1000;
  var history = [];            // [{role, text, sources}] — mirrors the visible chat
  var restoredBots = [];       // bot bubbles re-rendered from storage (sources pending bridge)

  function loadStore() {
    try {
      var rec = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (rec && rec.sessionId && Array.isArray(rec.messages) &&
          Date.now() - (rec.updated || 0) < EXPIRY_MS) return rec;
    } catch (e) {}
    return null;
  }
  function saveStore() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        sessionId: sessionId, messages: history.slice(-MAX_STORED), updated: Date.now()
      }));
    } catch (e) {}
  }

  /* -------------------------------------------------- parent bridge handshake */
  function postParent(msg) {
    try { window.parent.postMessage(msg, PARENT_ORIGIN); } catch (e) {}
  }
  postParent({ type: "READY" });
  window.addEventListener("message", function (ev) {
    if (PARENT_ORIGIN !== "*" && ev.origin !== PARENT_ORIGIN) return;
    if (ev.data && ev.data.type === "PARENT_ALIVE") {
      bridge = true;
      // restored messages rendered before the handshake lack Show me buttons — add them
      restoredBots.forEach(function (b) {
        if (b._sources && b.querySelector(".cb-sources")) {
          b.querySelector(".cb-sources").remove();
          renderSources(b, b._sources);
        }
      });
      restoredBots = [];
    }
    if (ev.data && ev.data.type === "PAGE" && ev.data.page) {
      PAGE_URL = ev.data.page.url || PAGE_URL;
      PAGE_TITLE = ev.data.page.title || PAGE_TITLE;
      var $pc = document.getElementById("page-ctx");
      if ($pc && PAGE_URL) {
        $pc.hidden = false;
        $pc.title = PAGE_URL;
        $pc.innerHTML = "📍 Answering with context of: <b></b>";
        $pc.querySelector("b").textContent = (PAGE_TITLE || PAGE_URL).slice(0, 60);
      }
    }
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
    history.push({ role: "user", text: question });
    saveStore();
    var bot = bubble("bot");
    var entry = { role: "bot", text: "", sources: null };
    history.push(entry);
    var cursor = el("span", "cursor");
    bot.appendChild(cursor);
    var acc = "";

    try {
      var body = { message: question, session_id: sessionId };
      if (PAGE_URL) { body.page_url = PAGE_URL; body.page_title = PAGE_TITLE; }  // page-aware
      var res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
            entry.text = acc;
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
        entry.text = ev.message;
        botEl.className = "cb-msg bot refusal";
        botEl.textContent = ev.message;
      } else if (ev.type === "citations") {
        entry.sources = ev.citations;
        renderSources(botEl, ev.citations);
      } else if (ev.type === "error") {
        entry.text = ev.message;
        var note = el("div", "cb-msg bot error", ev.message);
        $messages.appendChild(note);
        scrollDown();
      } else if (ev.type === "done") {
        saveStore();
      }
    }
  }

  /* ------------------------------------------------- restore + new conversation */
  document.getElementById("clear").addEventListener("click", function () {
    history = [];
    sessionId = "";                                   // next message starts a fresh session
    sessionStorage.removeItem("cb-session");
    try { localStorage.removeItem(STORE_KEY); } catch (e) {}
    restoredBots = [];
    $messages.innerHTML = "";
    $messages.appendChild(el("div", "cb-note",
      "Ask anything about this site — answers link to the exact page and section."));
    $input.focus();
  });

  (function restore() {
    var rec = loadStore();
    if (!rec) return;
    sessionId = rec.sessionId;
    sessionStorage.setItem("cb-session", sessionId);
    rec.messages.forEach(function (m) {
      history.push(m);
      if (m.role === "user") {
        bubble("user", m.text);
        return;
      }
      var b = bubble("bot", m.text);
      if (m.sources && m.sources.length) {
        b._sources = m.sources;
        renderSources(b, m.sources);
        if (!bridge) restoredBots.push(b);
      }
    });
    scrollDown(true);
  })();

  /* ------------------------------------------------------- page context chip */
  if (PAGE_URL) {
    var $ctx = document.getElementById("page-ctx");
    $ctx.hidden = false;
    $ctx.title = PAGE_URL;
    $ctx.innerHTML = "📍 Answering with context of: <b></b>";
    $ctx.querySelector("b").textContent = (PAGE_TITLE || PAGE_URL).slice(0, 60);
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
