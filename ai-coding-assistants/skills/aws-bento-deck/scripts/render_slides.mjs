#!/usr/bin/env node
// render_slides.mjs — screenshot every slide of a .bento.html for Visual QA.
//
//   node render_slides.mjs <deck.bento.html> <outdir>
//
// Requires the `playwright` npm package resolvable from CWD
// (`npm i playwright` in any scratch dir, or run from a project that has it).
// If playwright is NOT available, do Visual QA with the Playwright MCP
// browser tools instead: navigate to file://<deck>, click each `.ed-thumb`
// in the sidebar, screenshot `.ed-stage` — same procedure this script runs.
//
// Output: <outdir>/slide-01.png … one PNG per slide (linear AND state
// slides — state slides are part of the show too), plus contact-sheet.html
// listing them all for a one-glance review.

import { mkdirSync, writeFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const [, , deckPath, outDir] = process.argv;
if (!deckPath || !outDir) {
  console.error('usage: node render_slides.mjs <deck.bento.html> <outdir>');
  process.exit(2);
}

// resolve playwright from the CWD (not this script's dir) so any project
// with playwright installed can host the run
let chromium;
try {
  const req = createRequire(resolve(process.cwd(), 'noop.js'));
  const mod = await import(pathToFileURL(req.resolve('playwright')).href);
  chromium = (mod.default ?? mod).chromium;
  if (!chromium) throw new Error('no chromium export');
} catch {
  console.error('playwright not resolvable from CWD.');
  console.error('Either `npm i playwright` somewhere and run from there, or use the');
  console.error('Playwright MCP browser tools (navigate → click .ed-thumb → screenshot .ed-stage).');
  process.exit(3);
}

mkdirSync(outDir, { recursive: true });
// launch: bundled chromium → newest cached ms-playwright chromium → system Chrome
let browser;
try { browser = await chromium.launch(); }
catch {
  const { readdirSync: rd, existsSync } = await import('node:fs');
  const cache = `${process.env.HOME}/Library/Caches/ms-playwright`;
  const candidates = existsSync(cache)
    ? rd(cache).filter((d) => d.startsWith('chromium-')).sort().reverse()
        .map((d) => `${cache}/${d}/chrome-mac/Chromium.app/Contents/MacOS/Chromium`)
        .filter((p) => existsSync(p))
    : [];
  candidates.push('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
  let lastErr;
  for (const executablePath of candidates) {
    try { browser = await chromium.launch({ executablePath }); break; }
    catch (e) { lastErr = e; }
  }
  if (!browser) { console.error(`no usable Chromium found: ${lastErr?.message}`); process.exit(3); }
}
const page = await browser.newPage({ viewport: { width: 1728, height: 1080 } });
await page.goto('file://' + resolve(deckPath));
await page.waitForSelector('.ed-stage', { timeout: 15000 });
await page.waitForTimeout(1500); // fonts + splash

const thumbs = page.locator('.ed-sidebar .ed-thumb');
const n = await thumbs.count();
if (n === 0) { console.error('FAIL: no sidebar thumbnails found — did the editor boot?'); process.exit(1); }

for (let i = 0; i < n; i++) {
  await thumbs.nth(i).click();
  await page.waitForTimeout(400); // render + chart snapshot settle
  const file = `${outDir}/slide-${String(i + 1).padStart(2, '0')}.png`;
  await page.locator('.ed-stage').screenshot({ path: file });
  console.log(file);
}
await browser.close();

const pngs = readdirSync(outDir).filter((f) => f.endsWith('.png')).sort();
writeFileSync(
  `${outDir}/contact-sheet.html`,
  `<!doctype html><meta charset="utf-8"><title>contact sheet</title>
<body style="background:#1a1a1f;margin:16px;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;font:12px monospace;color:#aaa">
${pngs.map((f) => `<figure style="margin:0"><img src="${f}" style="width:100%;border-radius:6px"><figcaption>${f}</figcaption></figure>`).join('\n')}
</body>`
);
console.log(`${outDir}/contact-sheet.html (${n} slides)`);
