# aws-bento-deck

An [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills) that turns a
storyline into a finished, **AWS-branded BENTO presentation** — a single,
self-contained `.bento.html` file (not PowerPoint/PPTX). Fonts, AWS logos,
architecture icons, and brand gradients all ride inside the one file, so a deck
opens and presents anywhere with no external assets.

> **Bento**, not PPTX. This skill produces `.bento.html` only. If you want a
> `.pptx`, use a PowerPoint skill instead (e.g. `aws-slides` / `aws-pptx-skills`).

---

## What it does

The skill drives a disciplined three-phase workflow so a deck reads like it was
authored by a designer, not stamped from a template:

| Phase | Name | What happens |
|-------|------|--------------|
| 1 | **PLAN** | Co-authors a `<Topic>.deck.md` — one section per slide (layout, title, content slots, speaker notes). This file *is* the draft; you review and edit it. **Hard stop** for your approval before building. |
| 2 | **BUILD** | Compiles the `deck.md` against the bundled AWS template into a self-contained `<Topic>.bento.html`. The template shell (fonts, logos, 160 icons, gradients, layouts) is copied — never mutated. |
| 3 | **QA** | Mandatory content + structural + visual verification. A file that parses is not necessarily a deck that presents — every slide is rendered and inspected before delivery. |

Highlights:

- **On-brand by construction** — Amazon Ember typography, AWS color/gradient
  system, official architecture icons, and a catalog of purpose-built layouts.
- **Content-shape-driven layout** — numbers become charts, comparisons become
  side-by-sides, evolving diagrams use Bento's signature *morph* transition.
- **Never invents data** — missing stats, quotes, or customer names are marked
  `> TODO:` for you to fill, not fabricated.
- **Self-contained output** — one `.bento.html` you can email, open offline, or
  export to PDF.

## What's in this skill

```
aws-bento-deck/
├── SKILL.md                          ← Skill instructions (the agent reads this)
├── README.md                         ← This file
├── assets/
│   └── AWS_Deck_Template.template.bento.html   ← Brand template (fonts, logos, icons, layouts)
├── references/
│   ├── layouts.md                    ← Every layout id, its slots, when to use it
│   ├── deck-format.md                ← The .deck.md plan-file schema
│   ├── design-system.md              ← Type/color/gradient/icon/margin rules
│   ├── bento-schema.md               ← Bento format contract (splice, morph, media)
│   ├── data-viz.md                   ← Chart selection + charting standards
│   └── qa.md                         ← The Phase 3 QA checklist
└── scripts/
    ├── splice_deck.mjs               ← Escape + splice doc JSON into the shell + round-trip verify
    ├── validate_deck.mjs             ← Structural QA (exit non-zero on errors)
    └── render_slides.mjs             ← Visual QA — screenshots every slide
```

## Requirements

- **Node.js 18+** — the `scripts/*.mjs` helpers run on plain Node (no build step).
- **`playwright` npm package** — only for the visual-QA screenshot step
  (`render_slides.mjs`). Install it in any scratch directory with
  `npm i playwright && npx playwright install chromium`. If Playwright is not
  available, the skill falls back to the Playwright MCP browser tools for the
  same visual-QA procedure.

---

## Installation

### Claude Code

**Option A — Plugin marketplace (recommended).** This repo ships a Claude Code
plugin marketplace, so you can install the skill (and get automatic updates) with
two commands inside Claude Code:

```
/plugin marketplace add aws-samples/sample-apj-sup-sa
/plugin install aws-bento-deck@apj-sup-sa
```

See [`../../README.md`](../../README.md) for marketplace details.

**Option B — Copy into your skills directory.** Copy this folder into either
scope:

```bash
# Personal (all projects)
cp -R ai-coding-assistants/skills/aws-bento-deck ~/.claude/skills/aws-bento-deck

# Project-scoped (checked in for your team)
mkdir -p .claude/skills
cp -R ai-coding-assistants/skills/aws-bento-deck .claude/skills/aws-bento-deck
```

Restart Claude Code (or run `/doctor`) and confirm the skill is listed. It
activates automatically when you ask for a Bento deck.

### Codex

Codex CLI discovers Agent Skills from a `skills/` directory. Place this folder
where your Codex setup looks for skills:

```bash
# Personal scope
mkdir -p ~/.codex/skills
cp -R ai-coding-assistants/skills/aws-bento-deck ~/.codex/skills/aws-bento-deck

# Or project scope, checked into your repo
mkdir -p .codex/skills
cp -R ai-coding-assistants/skills/aws-bento-deck .codex/skills/aws-bento-deck
```

The `SKILL.md` frontmatter (`name` + `description`) is what Codex matches on to
decide when to load the skill. Trigger it by naming the skill or asking for an
"AWS Bento deck". Confirm your Codex version's skills path in its docs — some
builds read `AGENTS.md`/skill references from the working directory instead.

### Kiro

Kiro loads skill-style guidance from its steering/rules configuration. Vendor the
skill into your Kiro workspace and point steering at it:

```bash
mkdir -p .kiro/skills
cp -R ai-coding-assistants/skills/aws-bento-deck .kiro/skills/aws-bento-deck
```

Then add a steering rule (e.g. in `.kiro/steering/`) that tells Kiro to read
`.kiro/skills/aws-bento-deck/SKILL.md` when the user asks for an AWS Bento deck,
and to follow its three-phase (PLAN → BUILD → QA) workflow. Because the skill is
plain Markdown plus Node scripts, no Kiro-specific packaging is required — the
`SKILL.md` and `references/` files are the instructions, and the `scripts/` run
the same way from any agent host.

---

## Usage

Once installed, just ask for a deck and name the format explicitly:

```
Build an AWS Bento deck about our Q3 migration results for an executive audience.
```

or invoke it by name:

```
/aws-bento-deck  (Claude Code)
```

Trigger phrases the skill recognizes include: `aws-bento-deck`, "AWS Bento
슬라이드", "Bento로 AWS 발표자료", "make it a .bento.html". Generic requests like
"make me an AWS presentation" (no *Bento* keyword) intentionally do **not**
trigger it — those may want PPTX instead.

### What to expect

1. **Bring your source material.** The skill asks for it first — planning notes,
   an existing deck, doc/blog links, screenshots, diagrams, a demo flow. It reads
   what you give it and mines exact numbers, quotes, and product names rather than
   inventing generic copy.
2. **Answer a short round of decisions** — audience, the deck's objective, event
   name/date/presenter, language, target length, cover mood.
3. **Review the plan.** You get a `<Topic>.deck.md` and a one-line-per-slide
   summary. Edit the file directly or ask for changes. Nothing builds until you
   approve. (You can also edit `deck.md` yourself and say "rebuild".)
4. **Get the deck.** The skill compiles `<Topic>.bento.html`, runs all three QA
   stages, reports what it checked and fixed, and offers to open the file — it
   boots straight into the Bento editor.

### Running the helper scripts directly

The agent normally calls these for you, but you can run them by hand:

```bash
# Structural QA — exits non-zero on errors
node scripts/validate_deck.mjs <deck.bento.html>

# Visual QA — one PNG per slide + a contact sheet (needs playwright)
node scripts/render_slides.mjs <deck.bento.html> <qa-output-dir>

# Splice a composed doc JSON into the template shell (owns the escaping + round-trip check)
node scripts/splice_deck.mjs <template.bento.html> <doc.json> <out.bento.html>
```

---

## Notes & tips

- **The template is the source of truth.** Every build starts from
  `assets/AWS_Deck_Template.template.bento.html`; the shell is copied, so the
  bundled template is never modified.
- **Motion is optional and purposeful.** The skill keeps 1–3 signature motion
  patterns per deck and only uses motion that explains state change, sequence, or
  causality — the deck must still make its argument with motion removed.
- **QA is never skipped**, regardless of deck size. A parseable deck is not
  necessarily a presentable one.

## License

Distributed under the same license as this repository — see the root
[`LICENSE`](../../../LICENSE).
