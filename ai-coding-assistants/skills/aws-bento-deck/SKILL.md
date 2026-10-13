---
name: aws-bento-deck
description: >-
  Build AWS-branded BENTO presentations (self-contained .bento.html — NOT
  PowerPoint/PPTX) from the local AWS_Deck_Template. Three-phase workflow:
  co-author a deck plan as a DECK.md (per-slide layout choice, title,
  content slots, speaker notes), compile it into a finished self-contained
  .bento.html, then pass a mandatory 3-stage QA (content, structural,
  visual). Use ONLY when the user EXPLICITLY asks for a Bento
  deck — e.g. "aws-bento-deck", "/aws-bento-deck", "AWS Bento 슬라이드",
  "Bento로 AWS 발표자료", "bento.html로" — or wants to revise a DECK.md /
  rebuild a Bento deck from one. Do NOT trigger on generic requests like
  "AWS 슬라이드 만들어줘" or "AWS presentation" without the Bento keyword:
  those users may want PPTX (aws-pptx-skills / aws-slides) — ask which
  format if unclear. The AWS template ships inside this skill
  (assets/AWS_Deck_Template.template.bento.html).
---

# aws-bento-deck — AWS-branded Bento decks from a plan file

Turn a storyline into a finished AWS-branded `.bento.html` in three phases:

1. **PLAN** — write `<Topic>.deck.md` describing every slide (layout, title,
   content slots, notes). The user reviews/edits this file — it IS the draft.
   HARD STOP at the end: wait for user approval before building.
2. **BUILD** — compile the deck.md against the template into
   `<Topic>.bento.html` (self-contained: Amazon Ember fonts, AWS logos,
   architecture icons, brand gradients all ride inside).
3. **QA** — mandatory content + structural + visual verification. A
   parseable deck is not necessarily a presentable deck; the deck is not
   done until every slide has been rendered and inspected.

Never skip the plan for decks of 4+ slides. For quick 1–3 slide asks, you may
plan inline in the conversation, but still show the slide list before building.
QA is never skipped, regardless of deck size.

**Output-format routing**: this skill produces `.bento.html` only. If the
user says `.pptx` / PowerPoint, hand off to the PPTX skills. If they just
said "AWS 슬라이드"/"AWS presentation" with no format, ask once which format
they want before starting.

## Source of truth — what to read, when

- **Template**: `assets/AWS_Deck_Template.template.bento.html` (in THIS
  skill directory — resolve relative to this SKILL.md). Secondary copies,
  if the bundled one is ever missing/corrupt:
  `~/workspace/bento/working/aws-port-slide1.json` (same doc as editable
  JSON) or a user-provided path. If none exists, STOP and tell the user.
  The template is not just a style reference — it physically carries the
  fonts, logos, 160 icons, gradient art, and `doc.layouts`; every build
  starts from it (the shell is copied, so the bundled template is never
  modified).
- `references/layouts.md` — every layout id, its content slots, when to use
  it. Read BEFORE writing a plan (Phase 1).
- `references/deck-format.md` — the deck.md plan-file schema, including the
  required narrative-contract front matter. Read BEFORE writing a plan.
- `references/data-viz.md` — chart type selection + chart standards. Read
  when the material contains numbers to compare (Phase 1) and when
  composing chart elements (Phase 2).
- `references/design-system.md` — type/color/gradient/icon/margin/
  accessibility rules. Read BEFORE composing any `layout: custom` slide or
  restyling layout elements (Phase 2).
- `references/bento-schema.md` — Bento format gotchas: the splice contract,
  morph/state rules, media, dynamic fields. Read BEFORE Phase 2.
- `references/qa.md` — the full Phase 3 checklist. Read when entering QA.
- `scripts/` — `splice_deck.mjs` (escape + splice + round-trip verify),
  `validate_deck.mjs` (structural QA), `render_slides.mjs` (visual QA
  screenshots).

## Phase 1 — PLAN (write the deck.md)

The deck.md is the single source of truth for the build — a thin plan makes
a thin deck. Invest here: gather source material first, ask real questions,
and write slide content that could be presented as-is.

**1a. Gather source material (ALWAYS do this first).** Ask the user what
already exists before inventing anything:

> "발표 내용의 근거가 될 자료가 있나요? — 기획 .md/노트, 기존 발표자료,
> 블로그/문서 링크, 스크린샷·다이어그램 이미지, 데모 시나리오 등.
> 경로나 링크를 주시면 검토해서 플랜에 반영합니다."

Then actually READ everything they give you (files, URLs, images) and mine
it: exact numbers with sources, real quotes with attribution, product names,
diagrams worth re-drawing vs embedding, code snippets, demo flows. Prefer
the user's own claims and phrasing over generic marketing copy. When
material contradicts itself, ask — don't pick silently.

**1b. Ask for decisions you can't infer.** Be proactive; batch questions in
one round (use the AskUserQuestion tool when available). Typical decision
points:
- Audience & register (executive / builder / mixed → depth and jargon)
- **The deck's objective**: what should the audience think or DO differently
  after this talk? (becomes the plan's `objective:`/`desired_action:` —
  every slide must serve it)
- Event name, date, presenter name/title (the cover needs them verbatim)
- Language (ko/en/mixed — proper nouns stay English per brand guide)
- Target length: slide count and/or talk minutes
- Cover mood (violet default / aurora / pink / classic signature)
- What to do with gaps: if a slide needs a stat/screenshot/logo that wasn't
  provided, ask for it or mark it `> TODO:` in the plan — NEVER invent
  numbers, quotes, or customer names.
- Anything with more than one defensible option (chart vs table for the
  same data, one dense slide vs two light ones, drill-down as state slide
  vs separate slide) — present the options with a recommendation.

**1c. Structure the deck.** Read `references/layouts.md`, then map the
storyline to slides. Every deck gets one **cover** (4 variants — pick by
mood; violet sweep is the default). The other furniture slides are
CONDITIONAL — include them for a reason, not by habit:
   - **agenda** only when it helps navigation (8+ content slides or 3+
     distinct chapters)
   - **section headers** only at real narrative turns — not between every
     pair of slides
   - **Q&A** only if the talk format actually has Q&A
   - **thank-you** only when it carries contact info or a concrete CTA
   Prefer slide TITLES that state the conclusion, not the topic: "Managed
   Agents removes the infrastructure layer your team operates" beats
   "Managed Agents Architecture". Topic-label titles are fine for agenda/
   section/furniture slides only.

**1d. Choose layouts by CONTENT SHAPE**, not by order in the catalog —
layouts.md maps shapes (comparison, parallel items, big number, spectrum,
timeline, quote, photo, code, icon grid…) to layout ids. When nothing
fits, mark `layout: custom` and describe the composition; you'll hand-lay
it in Phase 2. Don't force content into a wrong layout. Variety rules:
   - Don't repeat the same content layout on consecutive slides without a
     narrative reason (a deliberate parallel series is fine; three
     `bulleted` slides in a row because it was easy is not).
   - Before accepting a text-heavy slide, check whether the content is
     secretly a diagram, process, comparison, or chart — convert if so.
   - Every slide carries either a meaningful visual encoding (chart,
     diagram, photo, icon+label structure) or a deliberate text-led
     composition (a quote, a big statement). Never add decorative icons or
     shapes that explain nothing.
   - One message per slide, one visual focal point. The headline carries
     the message.

**1e. Map content → Bento feature, not just layout** (layouts place content;
   features make it live — decide these IN the plan, as `fx:`/`transition:`
   hints):
   - numbers to compare → a **chart** element, not a text list (mark
     `layout: custom` + `chart:` data). Chart type + standards:
     `references/data-viz.md`.
   - a **headline number** → `fx: count-up` on that text
   - consecutive slides where the SAME subject visibly evolves →
     `morph-with-prev: true` (builder keeps shared element ids; Bento's
     signature move — e.g. a diagram gaining layers across slides, cover
     gradient → section strip). Morph when it explains continuity or
     change; don't manufacture shared elements just to have a morph.
   - a point worth **drilling into** on click → a **state slide** (child
     section with `state-of: <parent slide>` + a `link:` on the trigger element)
   - staggered bullet entrances → `fx: stagger`
   Motion restraint: motion must explain state change, sequence, or
   causality — or set the stage (covers/dividers MAY carry one ambient
   motion like ken-burns; not required). Pick 1–3 signature motion patterns
   per deck and reuse them consistently; avoid looping animation that
   explains nothing. Test: remove every motion — the deck must still make
   its argument. (Motion is also invisible in PDF export and to
   motion-sensitive viewers.)

**1f. Write `<Topic>.deck.md`** per `references/deck-format.md`, one section
per slide. Front matter carries the narrative contract (`objective:`,
`audience:`, `desired_action:` — required; `thesis:`/`duration_minutes:`
optional). Each slide gets: layout id, title, the layout's named slots
filled, `notes:` (speaker notes — REQUIRED, write real ones), and optional
`purpose:` / `takeaway:` / `image:` / `transition:` / `fx:` hints. Density
bar: every slot filled with final copy (not "TBD" except explicit `> TODO:`
gaps), real numbers with sources, speaker notes a presenter could actually
speak. Sanity pass before showing the user: read only the slide TITLES top
to bottom — they should tell the story and land on the objective by
themselves. A slide that serves no `objective:` gets cut or rethought. If
you can't fill a slide's slots concretely, go back to 1a/1b — not to build.

**1g. Review round (HARD STOP).** Show the user the file path and a
one-line-per-slide summary (slide · layout · title). Flag open `> TODO:`
items and the decisions you made on their behalf. WAIT for approval or
edits before building (unless they said "just build it"). The user editing
the deck.md directly and saying "rebuild" is a normal loop — Phase 2 must
compile whatever the file says, not what this conversation remembers.

## Phase 2 — BUILD (compile deck.md → .bento.html)

Read `references/bento-schema.md` first; read `references/design-system.md`
before any `layout: custom` composition. Content composition (steps 1–5) is
judgment work you do yourself; the splice and verification (step 6+) go
through `scripts/` — never hand-roll the escaping.

1. Load the template document JSON (prefer the working JSON; else extract
   the `#bento-doc` block from the template HTML — its type is
   `application/bento+json`; the escaped `\u003c` decodes back to `<` for free
   when you `JSON.parse`).
2. Start the output doc from the template: **keep** `format`, `version`,
   `size`, `theme`, `fonts`, `layouts`, and every entry in `assets` that the
   final deck references (fonts + logos + gradients always; icons/images only
   if used — prune the rest to keep the file lean; icons weigh ~3KB each,
   keeping all is acceptable when unsure). **Drop** the template's `slides`
   and `template:true`. Set `title`, `meta` (author/company/event); omit
   `docId` (the app mints one) — never reuse the template's.
3. For each deck.md slide: deep-copy the chosen layout's elements, keep
   element ids (morph lineage), fill each slot's `html` from the plan, drop
   unfilled placeholder text elements ONLY if the layout marks them
   placeholder (they auto-hide anyway). For `layout: custom`, compose
   elements directly (text/rect/svg/image/chart/table) following
   design-system.md.
4. Slide fields: `id` = kebab topic slug (stable, unique), `background` from
   the layout, `transition` from the plan (default `fade`; use `morph` when
   consecutive slides share element ids), `notes` from the plan verbatim.
5. Images from the plan (`image: <path>`): embed as data URI in `assets`
   (`img-<slug>`), reference via `"src": "asset:img-<slug>"`, `fit: cover`.
   Warn above ~2MB per image.
6. Write the composed doc JSON to a temp file and splice with the script:
   ```
   node scripts/splice_deck.mjs <template.bento.html> <doc.json> <out.bento.html>
   ```
   It owns the `<`-escaping correctness requirement (see bento-schema.md
   "splice contract"), replaces only the `#bento-doc` block content in a
   COPY of the shell, and round-trip verifies. If the script is
   unavailable, replicate exactly the contract by hand — the escape and
   the round-trip check are both non-negotiable.
7. Save as `<Topic>.bento.html` where the user asked (default ~/Documents).
   The deck is NOT done yet — Phase 3 is mandatory.

## Phase 3 — QA (mandatory; the deck is not deliverable without it)

Read `references/qa.md` and work through all three stages:

- **3a Content QA** — deck.md vs built deck: count/order/titles, no stray
  TODOs, numbers traceable, notes on every slide, objective still served.
- **3b Structural QA** — `node scripts/validate_deck.mjs <out.bento.html>`;
  fix every ERROR, consciously triage every WARN.
- **3c Visual QA** — `node scripts/render_slides.mjs <out.bento.html>
  <qa-dir>` (fallback: Playwright MCP), then actually LOOK at every slide
  PNG against the defect checklist in qa.md.

Do not deliver after JSON validation alone. Fix → re-splice → re-render
affected slides → re-inspect, until clean. Then report what was checked and
fixed, and offer to `open` the file — it boots straight into the editor.
