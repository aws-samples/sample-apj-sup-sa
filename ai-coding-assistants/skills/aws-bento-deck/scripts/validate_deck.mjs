#!/usr/bin/env node
// validate_deck.mjs — structural QA for a built .bento.html (or bare doc JSON).
//
//   node validate_deck.mjs <deck.bento.html | doc.json>
//
// Exit 0 = no errors (warnings allowed). Exit 1 = errors found.
// This is Structural QA only — it cannot see rendering. Visual QA
// (screenshots of every slide) is a separate, still-mandatory step.

import { readFileSync } from 'node:fs';

const [, , inPath] = process.argv;
if (!inPath) { console.error('usage: node validate_deck.mjs <deck.bento.html | doc.json>'); process.exit(2); }

const raw = readFileSync(inPath, 'utf8');
const errors = [];
const warns = [];
const err = (m) => errors.push(m);
const warn = (m) => warns.push(m);

// --- extract doc ---
let doc, payload;
if (/^\s*[{[]/.test(raw)) {
  payload = raw;
} else {
  const m = raw.match(/<script[^>]*id="bento-doc"[^>]*>([\s\S]*?)<\/script>/);
  if (!m) { console.error('FAIL: no #bento-doc block found'); process.exit(1); }
  payload = m[1];
  if (payload.includes('</script')) err('payload contains a literal </script — the < escape was skipped');
  if (payload.includes('<')) warn(`payload contains ${(payload.match(/</g) || []).length} unescaped "<" chars — should be \\u003c-escaped`);
}
try { doc = JSON.parse(payload); }
catch (e) { console.error(`FAIL: doc JSON does not parse: ${e.message}`); process.exit(1); }

if (doc.format !== 'bento/slides') err(`doc.format = ${JSON.stringify(doc.format)}, expected "bento/slides"`);
if (doc.template === true) err('doc.template is still true');
const slides = doc.slides || [];
if (!slides.length) err('no slides');
const assets = doc.assets || {};

// canvas bounds
const W = doc.size?.w ?? 1280;
const H = doc.size?.h ?? 720;
const MARGIN = 96;
// template chrome (footer trio, logos) legitimately sits outside the content
// margin and below the 13px text minimum
const isChrome = (id = '') => /^awsf-|logo/.test(id);

// --- slide-level checks ---
const slideIds = new Set();
const elIdsBySlide = new Map();
for (const s of slides) {
  if (!s.id) { err('a slide has no id'); continue; }
  if (slideIds.has(s.id)) err(`duplicate slide id "${s.id}"`);
  slideIds.add(s.id);
  elIdsBySlide.set(s.id, new Set((s.elements || []).map((e) => e.id)));
}

for (const [i, s] of slides.entries()) {
  const tag = `slide ${i + 1} "${s.id}"`;

  if (!s.notes || !String(s.notes).trim()) err(`${tag}: no speaker notes`);
  if (/TODO|TBD|placeholder|PLACEHOLDER|xxx/i.test(s.notes || '')) warn(`${tag}: notes look like a placeholder`);

  if (s.stateOf && !slideIds.has(s.stateOf)) err(`${tag}: stateOf → "${s.stateOf}" does not exist`);

  // per-slide element ids must be unique (morph pairing breaks otherwise)
  const seen = new Set();
  for (const el of s.elements || []) {
    if (!el.id) { err(`${tag}: element with no id`); continue; }
    if (seen.has(el.id)) err(`${tag}: duplicate element id "${el.id}" on the same slide`);
    seen.add(el.id);

    // link targets
    if (el.link && !slideIds.has(el.link)) err(`${tag}: element "${el.id}" links to missing slide "${el.link}"`);

    // asset references — both `asset:`-prefixed strings and bare `asset` fields
    for (const [k, v] of Object.entries(el)) {
      if (typeof v === 'string' && v.startsWith('asset:')) {
        const key = v.slice(6);
        if (!(key in assets)) err(`${tag}: element "${el.id}" .${k} references missing asset "${key}"`);
      }
    }
    if (typeof el.asset === 'string' && !(el.asset in assets))
      err(`${tag}: element "${el.id}" .asset references missing asset "${el.asset}"`);
    if (typeof s.background === 'string' && s.background.startsWith('asset:')) {
      const key = s.background.slice(6);
      if (!(key in assets)) err(`${tag}: background references missing asset "${key}"`);
    }

    // bounds — hard error only when off-canvas. The 96px content margin is
    // checked for TEXT only (backgrounds, gradients, logos, footer chrome sit
    // outside it by design).
    if (el.x != null && el.w != null) {
      if (el.x < 0 || el.y < 0 || el.x + el.w > W || (el.y ?? 0) + (el.h ?? 0) > H)
        err(`${tag}: element "${el.id}" out of canvas (x=${el.x} y=${el.y} w=${el.w} h=${el.h})`);
      else if (el.type === 'text' && !isChrome(el.id) && el.x + el.w > W - MARGIN + 24)
        warn(`${tag}: text "${el.id}" enters the right ${MARGIN}px margin (x+w=${el.x + el.w} > ${W - MARGIN})`);
    }

    // text content sanity (footer chrome keeps the template's own micro sizes)
    if (el.type === 'text') {
      if (/TODO|TBD|Lorem ipsum/i.test(el.html || '') && !el.placeholder) warn(`${tag}: text "${el.id}" contains TODO/TBD/lorem`);
      if ((el.fontSize ?? 16) < 13 && !isChrome(el.id)) warn(`${tag}: text "${el.id}" fontSize ${el.fontSize} < 13px minimum`);
      if (/\bpage\s*\d+|^\s*\d+\s*$/.test(el.html || '') && el.id?.includes('pg'))
        warn(`${tag}: "${el.id}" looks like a hardcoded page number — use {{page:2}}`);
    }

    // chart data shape: bar/line/scatter series must be plain numbers
    if (el.type === 'chart' && el.option) {
      const seriesArr = Array.isArray(el.option.series) ? el.option.series : [el.option.series].filter(Boolean);
      for (const ser of seriesArr) {
        const t = ser?.type;
        if (!ser?.data) continue;
        if (t !== 'pie' && ser.data.some((d) => typeof d === 'object' && d !== null))
          err(`${tag}: chart "${el.id}" ${t} series has object data points — must be plain numbers (only pie takes {name,value})`);
        if (t === 'pie' && ser.data.length > 5)
          warn(`${tag}: chart "${el.id}" pie has ${ser.data.length} slices — keep composition charts to ≤5 categories`);
      }
    }
  }

  // morph transition needs shared flip keys with the previous linear slide
  if (s.transition === 'morph' && i > 0) {
    const prev = slides[i - 1];
    const prevKeys = new Set((prev.elements || []).map((e) => e.morphId || e.id));
    const shared = (s.elements || []).filter((e) => prevKeys.has(e.morphId || e.id));
    if (shared.length === 0)
      err(`${tag}: transition "morph" but shares NO element ids with previous slide "${prev.id}" — nothing will morph`);
  }
}

// state slides should be reachable via some link
const linkTargets = new Set();
for (const s of slides) for (const el of s.elements || []) if (el.link) linkTargets.add(el.link);
for (const s of slides)
  if (s.stateOf && !linkTargets.has(s.id)) warn(`state slide "${s.id}" has no inbound link — unreachable in present mode`);

// unused assets (info only — icons are cheap, big images are not)
const usedAssets = new Set();
const sweep = (v) => {
  if (typeof v === 'string' && v.startsWith('asset:')) usedAssets.add(v.slice(6));
  else if (Array.isArray(v)) v.forEach(sweep);
  else if (v && typeof v === 'object') {
    // bare `asset` fields (doc.fonts entries, icon-cell elements) skip the prefix
    if (typeof v.asset === 'string') usedAssets.add(v.asset);
    Object.values(v).forEach(sweep);
  }
};
sweep(doc.slides); sweep(doc.layouts); sweep(doc.fonts); sweep(doc.theme);
const unused = Object.keys(assets).filter((k) => !usedAssets.has(k));
const unusedBig = unused.filter((k) => (assets[k]?.length ?? JSON.stringify(assets[k] ?? '').length) > 100_000);
if (unusedBig.length) warn(`${unusedBig.length} unused assets >100KB: ${unusedBig.slice(0, 5).join(', ')}${unusedBig.length > 5 ? '…' : ''}`);

// --- report ---
const linear = slides.filter((s) => !s.stateOf).length;
console.log(`Deck: "${doc.title}" — ${linear} linear + ${slides.length - linear} state slides`);
for (const w of warns) console.log(`  WARN  ${w}`);
for (const e of errors) console.log(`  ERROR ${e}`);
console.log(errors.length ? `\n${errors.length} error(s), ${warns.length} warning(s) — NOT ready` : `\nStructural QA passed (${warns.length} warning(s)). Visual QA still required.`);
process.exit(errors.length ? 1 : 0);
