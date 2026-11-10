/*!
 * site-chatbot theme.js — CSS-custom-prop theming for the chat panel (PLAN §3.4).
 *
 * The parent embed script (widget.js) reads optional color attributes from its
 * <script> tag and forwards them to the chat iframe in the PARENT_ALIVE handshake:
 *
 *   <script src=".../widget.js" data-endpoint="..."
 *           data-accent="#7c3aed" data-bg="#141414" data-fg="#f5f5f5" ... defer></script>
 *
 * Supported attributes (each maps to the matching --cb-* custom prop in chat.css):
 *   data-accent  data-accent-weak  data-bg  data-fg
 *   data-muted   data-border       data-user-bg  data-user-fg
 *
 * This module is the allowlist + validator — the iframe applies only values that
 * pass (L3 discipline: nothing from a message reaches CSS unchecked). Values may
 * be hex, rgb()/rgba()/hsl()/hsla(), or a plain CSS color keyword; anything else
 * (url(), gradients, junk) is rejected so chat.css var() fallbacks survive.
 * Pure + dependency-free; requireable from node tests (same pattern as md.js).
 */
(function (global) {
  "use strict";

  // dataset key (data-<kebab> -> camelCase) -> CSS custom prop used by chat.css
  var PROPS = {
    accent: "--cb-accent", accentWeak: "--cb-accent-weak",
    bg: "--cb-bg", fg: "--cb-fg", muted: "--cb-muted", border: "--cb-border",
    userBg: "--cb-user-bg", userFg: "--cb-user-fg"
  };

  function validColor(v) {
    if (typeof v !== "string" || !v.length || v.length > 64) return false;
    return /^#[0-9a-fA-F]{3,8}$/.test(v) ||
           /^(rgb|rgba|hsl|hsla)\([^();]*\)$/.test(v) ||
           /^[a-zA-Z]{3,25}$/.test(v);
  }

  /* Apply {css-prop: color} to a style target (element.style / documentElement).
   * Only whitelisted props with valid values are set; returns how many were applied. */
  function apply(styleTarget, theme) {
    var n = 0;
    if (!theme || typeof theme !== "object") return n;
    for (var key in PROPS) {
      var v = theme[PROPS[key]];
      if (typeof v === "string" && validColor(v)) {
        styleTarget.setProperty(PROPS[key], v);
        n++;
      }
    }
    return n;
  }

  /* Parent-side helper: script.dataset -> {css-prop: value}. Values pass through
   * raw; validation happens on the iframe side (widget.js inlines the mapping). */
  function collect(dataset) {
    var out = {};
    for (var key in PROPS) {
      var v = dataset[key];
      if (typeof v === "string" && v) out[PROPS[key]] = v;
    }
    return out;
  }

  global.CBTheme = { PROPS: PROPS, validColor: validColor, apply: apply, collect: collect };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { PROPS: PROPS, validColor: validColor, apply: apply, collect: collect };
  }
})(typeof window !== "undefined" ? window : globalThis);
