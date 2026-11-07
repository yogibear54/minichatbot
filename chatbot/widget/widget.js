/*!
 * site-chatbot widget.js — single embed script, two jobs:
 *   1) mounts the launcher button + chat iframe
 *   2) acts as the parent-page "guide agent": receives GUIDE postMessages and
 *      scrolls to + highlights the cited content (CSS Custom Highlight API,
 *      <mark> fallback, Text-Fragment links for cross-page).
 *
 * Usage on any site:
 *   <script src="https://bot-host.example/widget.js" data-endpoint="https://bot-host.example" defer></script>
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) return;
  var ENDPOINT = (script.dataset.endpoint || new URL(script.src).origin).replace(/\/$/, "");
  var ORIGIN = new URL(ENDPOINT, location.href).origin;
  var PENDING_KEY = "cb-pending-guide";
  var open = false, iframe, launcher, styleEl, hlRegistered = false;
  var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------- inject styles */
  styleEl = document.createElement("style");
  styleEl.textContent =
    "::highlight(cb-guide){background-color:#ffe066;color:inherit}" +
    ".cb-guide-mark{background:#ffe066;color:inherit;border-radius:3px}" +
    "@keyframes cb-pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,214,10,.0)}50%{box-shadow:0 0 0 5px rgba(255,214,10,.45)}}" +
    ".cb-guide-mark.cb-pulse,.cb-guide-range.cb-pulse{animation:cb-pulse 1.1s ease-in-out 2}" +
    ".cb-launcher{position:fixed;right:24px;bottom:24px;width:56px;height:56px;border-radius:50%;" +
    "border:none;cursor:pointer;z-index:2147483000;box-shadow:0 6px 24px rgba(0,0,0,.25);" +
    "background:#111;color:#fff;display:flex;align-items:center;justify-content:center;padding:0}" +
    ".cb-launcher:hover{transform:translateY(-1px)}" +
    ".cb-frame{position:fixed;right:24px;bottom:92px;width:min(380px,calc(100vw - 32px));" +
    "height:min(600px,calc(100vh - 140px));border:none;border-radius:16px;z-index:2147483001;" +
    "box-shadow:0 12px 48px rgba(0,0,0,.3);opacity:0;transform:translateY(12px);" +
    "pointer-events:none;transition:opacity .18s ease,transform .18s ease;background:#fff}" +
    ".cb-frame.open{opacity:1;transform:none;pointer-events:auto}" +
    "@media (prefers-reduced-motion:reduce){.cb-frame{transition:none}.cb-launcher{transition:none}}";
  document.head.appendChild(styleEl);

  /* ------------------------------------------------------ launcher + iframe */
  launcher = document.createElement("button");
  launcher.className = "cb-launcher";
  launcher.type = "button";
  launcher.setAttribute("aria-label", "Open chat assistant");
  launcher.innerHTML =
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  launcher.addEventListener("click", toggle);

  iframe = document.createElement("iframe");
  iframe.className = "cb-frame";
  iframe.title = "Chat assistant";
  iframe.src = ORIGIN + "/chat?parent=" + encodeURIComponent(location.origin);
  iframe.setAttribute("allow", "clipboard-write");

  function mount() {
    document.body.appendChild(iframe);
    document.body.appendChild(launcher);
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);

  function toggle() {
    open = !open;
    iframe.classList.toggle("open", open);
    launcher.setAttribute("aria-label", open ? "Close chat assistant" : "Open chat assistant");
    if (open) {
      try { iframe.contentWindow.postMessage({ type: "OPENED" }, ORIGIN); } catch (e) {}
      try { iframe.contentWindow.focus(); } catch (e) {}
    }
  }

  window.addEventListener("message", function (ev) {
    if (ev.origin !== ORIGIN || !ev.data) return;
    var d = ev.data;
    if (d.type === "GUIDE") handleGuide(d);
    else if (d.type === "READY") { try { ev.source.postMessage({ type: "PARENT_ALIVE" }, ORIGIN); } catch (e) {} }
    else if (d.type === "CLOSE") if (open) toggle();
  });

  /* ----------------------------------------------------------- guide agent */
  function samePage(url) {
    var strip = function (u) { var p = new URL(u, location.href); return p.origin + p.pathname + p.search; };
    return strip(url) === strip(location.href);
  }

  function sttfHref(d) {
    var u = new URL(d.url, location.href);
    u.hash = d.anchor ? d.anchor.replace(/^#/, "") : "";
    if (d.quote) {
      var text = d.quote.replace(/\s+/g, " ").trim().slice(0, 300);
      // Scroll-To-Text-Fragment: '-' and "'" are directive syntax -> percent-encode
      // (commas and double quotes are already encoded by encodeURIComponent)
      var enc = encodeURIComponent(text).replace(/-/g, "%2D").replace(/'/g, "%27");
      u.href = u.href + "#:~:text=" + enc;
    }
    return u.href;
  }

  function handleGuide(d) {
    if (!d || !d.url) return;
    if (!samePage(d.url)) {
      try { sessionStorage.setItem(PENDING_KEY, JSON.stringify(d)); } catch (e) {}
      location.href = sttfHref(d);           // browser natively scrolls (+highlights via STTF)
      return;
    }
    runHighlight(d.anchor, d.quote);
  }

  function runHighlight(anchor, quote) {
    clearGuide();
    var scrolled = false;
    if (anchor) {
      var el = document.getElementById(anchor.replace(/^#/, "")) ||
               document.querySelector('[name="' + anchor.replace(/^#/, "") + '"]');
      if (el) {
        el.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
        scrolled = true;
      }
    }
    if (quote) {
      var ranges = findRanges(document.body, quote);
      if (!ranges.length) ranges = findRanges(document.body, firstWords(quote, 8));
      if (!ranges.length) ranges = findBlockRanges(document.body, quote);
      if (!ranges.length) ranges = findBlockRanges(document.body, firstWords(quote, 8));
      if (ranges.length) {
        var sc = ranges[0].startContainer.nodeType === 1 ? ranges[0].startContainer
                                                         : ranges[0].startContainer.parentElement;
        if (sc) sc.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
        if (window.Highlight && CSS.highlights) {
          var hl = new window.Highlight(...ranges);
          CSS.highlights.set("cb-guide", hl);
          hlRegistered = true;
        } else {
          wrapRanges(ranges);                    // <mark> fallback
        }
      }
    }
    clearTimeout(runHighlight._t);
    runHighlight._t = setTimeout(clearGuide, 6000);
  }

  function firstWords(s, n) { return s.replace(/\s+/g, " ").trim().split(" ").slice(0, n).join(" "); }

  /* Block-level matcher: compare per-paragraph innerText (browser-normalized, so text
   * split across GSAP/animation spans still matches). Highlights the whole block —
   * also the better "guide the eye" UX for long quotes. */
  function findBlockRanges(rootEl, needle) {
    needle = needle.replace(/\s+/g, " ").trim();
    if (!needle) return [];
    var blocks = rootEl.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, td, th, blockquote, figcaption, dd, dt");
    var out = [];
    for (var i = 0; i < blocks.length && out.length < 20; i++) {
      var t = (blocks[i].innerText || "").replace(/\s+/g, " ").trim();
      if (t && t.indexOf(needle) !== -1) {
        var r = document.createRange();
        r.selectNodeContents(blocks[i]);
        out.push(r);
      }
    }
    return out;
  }

  /* Locate a (whitespace-normalized) quote in the live DOM, returning Ranges. */
  function findRanges(rootEl, needle) {
    needle = needle.replace(/\s+/g, " ").trim();
    if (!needle) return [];
    var walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var t = n.nodeValue;
        return t && t.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var nodes = [], norm = "", map = [], lastWasSpace = true;
    var n;
    while ((n = walker.nextNode())) {
      var v = n.nodeValue;
      for (var i = 0; i < v.length; i++) {
        var ch = v[i];
        if (/\s/.test(ch)) {
          if (!lastWasSpace) { norm += " "; map.push([nodes.length, i]); lastWasSpace = true; }
        } else {
          norm += ch; map.push([nodes.length, i]); lastWasSpace = false;
        }
      }
      nodes.push(n);
    }
    // map entries only exist for kept chars; pad missing (we skipped leading ws of each node start)
    var at = norm.indexOf(needle);
    if (at < 0) return [];
    var ranges = [], count = 0;
    while (at >= 0 && count < 20) {
      var s = map[at], e = map[at + needle.length - 1];
      if (!s || !e) break;
      var r = document.createRange();
      try {
        r.setStart(nodes[s[0]], s[1]);
        r.setEnd(nodes[e[0]], e[1] + 1);
        ranges.push(r);
      } catch (err) { break; }
      at = norm.indexOf(needle, at + needle.length);
      count++;
    }
    return ranges;
  }

  function wrapRanges(ranges) {
    ranges.forEach(function (r) {
      try {
        var mark = document.createElement("mark");
        mark.className = "cb-guide-mark cb-pulse";
        r.surroundContents(mark);
      } catch (e) { /* range spans node boundaries -> skip this one */ }
    });
  }

  function clearGuide() {
    if (hlRegistered && CSS.highlights) { CSS.highlights.delete("cb-guide"); hlRegistered = false; }
    document.querySelectorAll(".cb-guide-mark").forEach(function (m) {
      var p = m.parentNode;
      m.replaceWith.apply(m, Array.prototype.slice.call(m.childNodes));
      p && p.normalize && p.normalize();
    });
  }

  /* After a cross-page guide jump: run the highlight refinement on arrival. */
  try {
    var pending = sessionStorage.getItem(PENDING_KEY);
    if (pending) {
      sessionStorage.removeItem(PENDING_KEY);
      var d = JSON.parse(pending);
      if (samePage(d.url)) {
        setTimeout(function () { runHighlight(d.anchor, d.quote); }, 400);
      }
    }
  } catch (e) {}
})();
