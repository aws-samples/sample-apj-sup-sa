#!/usr/bin/env node
// splice_deck.mjs — splice a bento document JSON into a template shell.
//
//   node splice_deck.mjs <template.bento.html> <doc.json> <out.bento.html>
//
// Owns the two things that MUST NOT be done by hand:
//   1. every `<` in the payload is escaped as < (so the payload can
//      never contain a literal `</script>` and terminate the data block)
//   2. after writing, the output is re-extracted, re-parsed, and compared
//      (slide count + title) — a splice that doesn't round-trip fails loudly.
//
// Never regenerates the shell: only the CONTENT of the
// <script type="application/bento+json" id="bento-doc"> block is replaced.

import { readFileSync, writeFileSync } from 'node:fs';

const [, , templatePath, docPath, outPath] = process.argv;
if (!templatePath || !docPath || !outPath) {
  console.error('usage: node splice_deck.mjs <template.bento.html> <doc.json> <out.bento.html>');
  process.exit(2);
}

const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exit(1); };

const BLOCK_RE = /(<script[^>]*id="bento-doc"[^>]*>)([\s\S]*?)(<\/script>)/;

// --- load + validate the doc JSON ---
let doc;
try { doc = JSON.parse(readFileSync(docPath, 'utf8')); }
catch (e) { fail(`doc JSON does not parse: ${e.message}`); }
if (doc.format !== 'bento/slides') fail(`doc.format is ${JSON.stringify(doc.format)}, expected "bento/slides"`);
if (!Array.isArray(doc.slides) || doc.slides.length === 0) fail('doc.slides missing or empty');
if (doc.template === true) fail('doc.template is still true — drop it for a built deck');

// --- serialize with the < escape ---
const payload = JSON.stringify(doc).replace(/</g, '\\u003c');
if (payload.includes('</script')) fail('payload still contains a literal </script after escaping (should be impossible)');

// --- splice into a copy of the shell ---
const shell = readFileSync(templatePath, 'utf8');
if (!BLOCK_RE.test(shell)) fail('template has no <script id="bento-doc"> block');
const out = shell.replace(BLOCK_RE, (_, open, _old, close) => open + payload + close);
writeFileSync(outPath, out);

// --- round-trip verification ---
const written = readFileSync(outPath, 'utf8');
const m = written.match(BLOCK_RE);
if (!m) fail('written file lost the #bento-doc block');
let roundTrip;
try { roundTrip = JSON.parse(m[2]); }
catch (e) { fail(`re-extracted payload does not parse: ${e.message}`); }
if (roundTrip.slides.length !== doc.slides.length)
  fail(`slide count changed in round-trip: ${doc.slides.length} → ${roundTrip.slides.length}`);
if (roundTrip.title !== doc.title)
  fail(`title changed in round-trip: ${JSON.stringify(doc.title)} → ${JSON.stringify(roundTrip.title)}`);

const linear = roundTrip.slides.filter((s) => !s.stateOf).length;
const states = roundTrip.slides.length - linear;
console.log(`OK: ${outPath}`);
console.log(`    title: ${roundTrip.title}`);
console.log(`    slides: ${linear} linear + ${states} state = ${roundTrip.slides.length}`);
console.log(`    size: ${(written.length / 1024 / 1024).toFixed(2)} MB`);
