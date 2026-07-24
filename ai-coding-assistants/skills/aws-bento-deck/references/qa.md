# AWS Bento deck — Phase 3 QA (mandatory)

Do not deliver the deck after JSON validation alone. Render every linear
and state slide, inspect the output for visible defects, fix all defects,
and re-render the affected slides. A parseable deck is not necessarily a
presentable deck. QA is never skipped, regardless of deck size.

## 3a. Content QA — against the deck.md (read both, compare)

- [ ] slide count and order match the plan; titles match
- [ ] no lingering `TODO`/`TBD`/placeholder copy outside deliberate
      dashed-border placeholders the user approved
- [ ] every number/quote/customer name traces to source material — nothing
      invented at build time
- [ ] speaker notes present on EVERY slide, verbatim from the plan
- [ ] the deck still answers the plan's `objective:`

## 3b. Structural QA — scripted

```
node scripts/validate_deck.mjs <out.bento.html>
```

Checks: JSON parses, no literal `</script`/unescaped `<` in the payload (must be `\u003c`),
`template:true` dropped, duplicate slide/element ids, `asset:` and bare
`asset` references exist, `stateOf`/`link` targets exist, morph slides
actually share element ids with their predecessor, chart series data are
plain numbers (pie ≤5 slices), elements inside the canvas, text inside the
96px margin, ≥13px text, notes present, hardcoded page numbers.

Fix every ERROR; read every WARN and either fix it or consciously accept
it (template chrome warnings are pre-accepted).

## 3c. Visual QA — scripted render + your eyes

```
node scripts/render_slides.mjs <out.bento.html> <qa-dir>
```

Needs the `playwright` npm package resolvable from CWD. Fallback:
Playwright MCP — navigate to `file://<deck>`, click each `.ed-thumb` in
the sidebar, screenshot `.ed-stage`. Same coverage either way: every
linear AND state slide.

Then actually LOOK at every PNG / the contact sheet and check each slide
for:

- text truncation, unintended wrapping, orphan words in headlines
- element overlap; content colliding with footer chrome
- 96px side-margin violations by content
- ragged alignment, uneven gaps between siblings
- low contrast (body text must be comfortably readable on its background —
  aim for WCAG AA; never rely on color alone to distinguish meaning)
- leftover placeholder frames that should have real assets
- lopsided composition (one half empty, the other crowded)
- same layout repeating consecutively without narrative intent

## The loop

Fix defects in the doc JSON, re-splice (`splice_deck.mjs`), re-render THE
AFFECTED SLIDES, and re-inspect. Loop until clean. Only then hand over:
report what was checked and what was fixed, and offer to `open` the file —
it boots straight into the editor.
