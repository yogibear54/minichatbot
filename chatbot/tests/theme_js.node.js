/* Node test suite for widget/theme.js — theming allowlist + color validation
 * (data-* attrs -> postMessage -> CSS custom props, PLAN §3.4). Run via:
 * node tests/theme_js.node.js  (or through pytest) */
"use strict";
const assert = require("assert");
const { PROPS, validColor, apply, collect } = require("../widget/theme.js");

// ------------------------------------------------------------------ validColor
// hex forms
assert(validColor("#fff"));
assert(validColor("#abcd"));
assert(validColor("#2563eb"));
assert(validColor("#2563ebcc"));
// color functions (no nested parens / semicolons / extra tokens)
assert(validColor("rgb(37, 99, 235)"));
assert(validColor("rgba(0,0,0,.3)"));
assert(validColor("hsl(221deg 83% 53%)"));
assert(validColor("hsla(120,50%,50%,0.5)"));
// plain keywords
assert(validColor("red"));
assert(validColor("transparent"));
// SECURITY: anything that is not a plain color is rejected -> chat.css var()
// fallbacks survive; no url()/expression/injection can cross into CSS
assert(!validColor("url(https://evil.example/x.png)"));
assert(!validColor("linear-gradient(to right, red, blue)"));
assert(!validColor("expression(alert(1))"));
assert(!validColor("red; } body { background: url(x) }"));
assert(!validColor("rgb(0,0,0); evil"));
assert(!validColor("rgb(0,0,0) extra"));
assert(!validColor("#123456789"));          // too many hex digits
assert(!validColor(""));
assert(!validColor(null));
assert(!validColor(123));
assert(!validColor("x".repeat(65)));        // length cap

// ----------------------------------------------------------------------- apply
function stub() {
  const calls = [];
  return { calls, setProperty(k, v) { calls.push([k, v]); } };
}
// whitelisted props only, valid values only; unknown keys and junk dropped
let s = stub();
const n = apply(s, {
  "--cb-accent": "#7c3aed", "--cb-bg": "#141414", "--cb-border": "rgb(1,2,3)",
  "--evil": "url(x)", "--cb-fg": "javascript:alert(1)", "--cb-bg ": "red"
});
assert.strictEqual(n, 3);
assert.deepStrictEqual(s.calls, [
  ["--cb-accent", "#7c3aed"], ["--cb-bg", "#141414"], ["--cb-border", "rgb(1,2,3)"]
]);
// prop names are case-sensitive (no smuggled near-matches)
s = stub();
assert.strictEqual(apply(s, { "--CB-BG": "red" }), 0);
assert.strictEqual(s.calls.length, 0);
// junk theme shapes apply nothing
s = stub();
assert.strictEqual(apply(s, null), 0);
assert.strictEqual(apply(s, "accent"), 0);
assert.strictEqual(apply(s, {}), 0);
assert.strictEqual(s.calls.length, 0);

// --------------------------------------------------------------------- collect
assert.deepStrictEqual(collect({
  accent: "#7c3aed", accentWeak: "#f3e8ff", bg: "#141414", fg: "#f5f5f5",
  muted: "#a3a3a3", border: "#333", userBg: "#7c3aed", userFg: "#fff"
}), {
  "--cb-accent": "#7c3aed", "--cb-accent-weak": "#f3e8ff", "--cb-bg": "#141414",
  "--cb-fg": "#f5f5f5", "--cb-muted": "#a3a3a3", "--cb-border": "#333",
  "--cb-user-bg": "#7c3aed", "--cb-user-fg": "#fff"
});
// partial attrs -> only present keys forwarded
assert.deepStrictEqual(collect({ accent: "blue", userBg: "blue" }),
  { "--cb-accent": "blue", "--cb-user-bg": "blue" });
assert.deepStrictEqual(collect({}), {});
// values pass through raw — validation is apply()'s job, not the collector's
assert.deepStrictEqual(collect({ bg: "url(x)" }), { "--cb-bg": "url(x)" });
// allowlist stays in sync with chat.css's --cb-* palette (8 props)
assert.strictEqual(Object.keys(PROPS).length, 8);

console.log("theme.js tests OK");
