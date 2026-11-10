/* Node test suite for widget/md.js — run via: node tests/md_js.node.js  (or through pytest) */
"use strict";
const assert = require("assert");
const { mdToHtml } = require("../widget/md.js");

// basics
assert.strictEqual(mdToHtml("Hello **world**"), "<p>Hello <strong>world</strong></p>");
assert.strictEqual(mdToHtml("plain text"), "<p>plain text</p>");

// emphasis
assert.strictEqual(mdToHtml("this is *italic* text"), "<p>this is <em>italic</em> text</p>");
assert.strictEqual(mdToHtml("snake_case_stays"), "<p>snake_case_stays</p>");       // no false italic

// inline + fenced code
assert.strictEqual(mdToHtml("use `extract()` here"), "<p>use <code>extract()</code> here</p>");
assert(mdToHtml("```\npip install trafilatura\n```").includes("<pre><code>pip install trafilatura</code></pre>"));

// lists
assert(mdToHtml("- one\n- two").includes("<ul><li>one</li><li>two</li></ul>"));
assert(mdToHtml("1. first\n2. second").includes("<ol><li>first</li><li>second</li></ol>"));

// blockquote + headings
assert(mdToHtml("> quoted line").includes("<blockquote>quoted line</blockquote>"));
assert(mdToHtml("## Some Section").includes('class="cb-md-h"') && mdToHtml("## Some Section").includes("Some Section"));

// multiline composition (a typical bot answer)
const doc = mdToHtml("Intro **bold**.\n\n- a\n- b\n\n> note\n\n```\ncode line\n```");
assert(doc.includes("<strong>bold</strong>") && doc.includes("<ul>") && doc.includes("<blockquote>") && doc.includes("<pre>"));

// SECURITY: raw HTML / injection must be escaped, never rendered
assert(!mdToHtml('<script>alert(1)</script>').includes("<script>"));
assert(mdToHtml('<script>alert(1)</script>').includes("&lt;script&gt;"));
assert(!mdToHtml('<img src=x onerror="alert(1)">').includes("<img"));
assert(!mdToHtml('[click](javascript:alert(1))').includes("<a "));   // links never auto-created
assert(mdToHtml('style="position:fixed"').includes("&quot;"));        // attributes escaped

// citations-style JSON never renders as HTML
assert(mdToHtml('[{"url": "https://x"}]').includes("&quot;url&quot;"));

console.log("md.js tests OK");

// markdown links collapse to label text (L3: no <a> from model output)
assert.strictEqual(mdToHtml("[Learn more →](/contact)"), "<p>Learn more →</p>");
assert(!mdToHtml("[click](javascript:alert(1))").includes("<a"));
assert(!mdToHtml("[click](javascript:alert(1))").includes("javascript"));
console.log("link-collapse tests OK");
