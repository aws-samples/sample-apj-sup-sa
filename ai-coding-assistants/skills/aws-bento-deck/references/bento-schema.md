# AWS Bento deck — Bento format gotchas

Read this before Phase 2 composition. Deeper schema reference: the
`bento-slides` plugin skill and https://bento.page/agents.md .

## The splice contract (correctness-critical)

- Escape every `<` in the spliced JSON as `\u003c` (JSON-legal, decodes
  back to `<` on parse) so the payload can never contain a literal
  `</script>`. Never regenerate the shell — only the CONTENT of the
  `<script type="application/bento+json" id="bento-doc">` block changes.
- `scripts/splice_deck.mjs` does both correctly and round-trip verifies —
  prefer it over hand-splicing. If unavailable, replicate exactly: escape,
  splice into a COPY, re-extract, re-parse, compare slide count/title.

## Elements

- Fully specify element fields (shapes need stroke/strokeWidth; text needs
  fontFamily/align/valign/lineHeight) — missing fields render wrong.
- Text `html` allows only inline b/i/u/br/span.
- Line shapes take their color from `fill` (not `stroke`); the renderer
  draws lines horizontally across the element box (vertical = rotation).
- svg-element CSS is auto-scoped by the renderer, but keep custom svg
  minimal.

## Morph & states

- Morph pairs need SAME element ids across slides + `transition:"morph"`
  on the LATER slide. (An optional `morphId` can re-target pairing without
  changing `id`, v1.0.7+.)
- State slides: `stateOf: <parent-slide-id>` hides the slide from linear
  navigation; reach it via an element's `link: <slide-id>`; ← returns to
  the parent. Share ids with the parent for a smooth morph.

## Media & assets

- Media elements (`kind: video|audio`): embed short clips as data URI,
  link big files by URL; video autoplay needs `muted:true` and runs only
  in present mode.
- Asset references: `"asset:key"` strings in element fields, bare
  `asset: key` in font entries and icon cells. Every referenced key must
  exist in `doc.assets`.
- Images: embed as data URI under `img-<slug>`, `fit: cover`; warn above
  ~2MB per image.

## Dynamic fields

- Page numbers are `{{page:2}}` (zero-padded), never hardcoded. Also
  available: `{{pages}}` `{{title}}` `{{date}}` `{{time}}` `{{author}}`
  `{{company}}` `{{event}}` — resolved at render, raw token stays in the
  model.

## Doc-level

- Start from the template: keep `format`/`version`/`size`/`theme`/`fonts`/
  `layouts` + referenced assets; drop the template's `slides` and
  `template:true`; set `title`/`meta`; never reuse the template's `docId`
  (omit it — the app mints one).
- Slide `id` = kebab topic slug, stable and unique.

## Fallbacks

- If the bundled template (`assets/AWS_Deck_Template.template.bento.html`
  in this skill) is missing but the user still wants a deck, fall
  back to the generic bento-slides flow (fetch the shell from
  https://bento.page/releases/slides/Bento_Slides.bento.html) — but say
  the AWS branding will be absent and offer to rebuild the template first.
- Extra verification option when the bento dev server is running
  (localhost:5199): load the doc via `window.bento.loadDoc()` and
  screenshot there.
