/* Minimal, safe-by-construction GFM-subset markdown -> HTML for the chat window.
 *
 * Supported (the chat-standard subset): **bold**, *italic*, _italic_, `inline code`,
 * ``` fenced code blocks, -/* /1. lists, > blockquotes, # headings (rendered as
 * emphasized lines), hard line breaks.
 *
 * Security model (L3 in PLAN §3.3): model output is untrusted. ALL input is HTML-escaped
 * FIRST, and only whitelisted tags are ever emitted — raw HTML in model output is shown
 * as text, never rendered. Links/images are intentionally not auto-created: navigation
 * happens exclusively through validated citation chips.
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* inline-level transforms on already-escaped text */
  function inline(s) {
    // corpus citation markers are internal — never display them
    s = s.replace(/\[\s*src:[^\]]*\]/gi, "");
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) {          // inline code first (protects it)
      codes.push("<code>" + c + "</code>");
      return "\x00" + (codes.length - 1) + "\x00";
    });
    // markdown links collapse to their label text — navigation happens exclusively
    // through validated citation chips, so no <a> elements are ever created here
    s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    s = s.replace(/\x00(\d+)\x00/g, function (_, i) { return codes[+i]; });
    return s;
  }

  function mdToHtml(md) {
    var lines = escapeHtml(String(md == null ? "" : md)).split("\n");
    var out = [];
    var list = null;        // "ul" | "ol" | null
    var code = null;        // array of fenced-code lines | null
    var quote = [];         // blockquote lines

    function closeList() {
      if (list) { out.push("</" + list + ">"); list = null; }
    }
    function flushQuote() {
      if (quote.length) {
        out.push("<blockquote>" + inline(quote.join("\n")) + "</blockquote>");
        quote = [];
      }
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var fence = line.match(/^\s*```(\w*)\s*$/);

      if (code !== null) {                                  // inside a fenced block
        if (fence) { out.push("<pre><code>" + code.join("\n") + "</code></pre>"); code = null; }
        else code.push(line);
        continue;
      }
      if (fence) { closeList(); flushQuote(); code = []; continue; }

      var qm = line.match(/^\s*&gt;\s?(.*)$/);              // "> " (escaped)
      if (qm) { closeList(); quote.push(qm[1]); continue; }
      flushQuote();

      var hm = line.match(/^\s*#{1,6}\s+(.*)$/);
      if (hm) { closeList(); out.push('<p class="cb-md-h">' + inline(hm[1]) + "</p>"); continue; }

      var ul = line.match(/^\s*[-*+]\s+(.*)$/);
      var ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        var want = ul ? "ul" : "ol";
        if (list !== want) { closeList(); list = want; out.push("<" + want + ">"); }
        out.push("<li>" + inline((ul || ol)[1]) + "</li>");
        continue;
      }
      closeList();
      if (!line.trim()) continue;
      out.push("<p>" + inline(line) + "</p>");
    }
    if (code !== null) out.push("<pre><code>" + code.join("\n") + "</code></pre>");
    closeList();
    flushQuote();
    return out.join("");
  }

  global.mdToHtml = mdToHtml;
  if (typeof module !== "undefined" && module.exports) module.exports = { mdToHtml: mdToHtml };
})(typeof window !== "undefined" ? window : globalThis);
