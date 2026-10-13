# AWS Bento deck — design system

The template's own slides 27+ ARE the style guide — when unsure, open the
template and read the brand-foundation pages (Signature gradients, Patterns
& scrims, Neutrals, Brand spectrum, Display scale, Type families, Content
fundamentals, Visual foundations). The rules below summarize them. Read
this file BEFORE composing any `layout: custom` slide or restyling a
layout's elements.

## Type — two families only, exact scale

(template: Display scale / Type families pages)

- Display: `'Amazon Ember Display', 'Hanken Grotesk', …` — headlines AND
  body, regular weight, tight tracking (`letterSpacing ≈ -1%` of font size),
  line-height 1.08–1.15 for headlines.
- Mono: `'Amazon Ember Mono', 'IBM Plex Mono', …` — eyebrows, dates, tags,
  page numbers, big numerals. UPPERCASE, `letterSpacing:1.3` at 13px.
- Scale ramp: **72/xl** (hero) · **48/lg** · **30/md** (slide titles are
  30–54px) · **22/sm** · **16/body**. Never below 13px. Never add a third
  family, never fake-bold a headline (weight 400–500; 600 only for inline
  emphasis and presenter names).

## Color — neutral base + one accent

(template: Neutrals / Brand spectrum pages)

- Neutrals: Squid Ink `#161d26` (primary text), body `#2e3c4e`, meta gray
  `#909098`, hairlines `#e8e8ed`, subtle fill `#f3f3f7`, stage white.
- Accent: fuchsia-700 `#a000b8` for eyebrows, bullet squares, rules;
  indigo-600 `#4c5fd5` for feature headings. Max 1–2 accents per deck.
- The full 24-chip Brand spectrum (Violet 500 `#5724ff`, Purple 500
  `#7300e5`, Indigo 700 `#003efa`, Lime 300 `#acff2e`, Orange 300
  `#ff997a`, …) is for data viz, pattern fills, and gradient work — not for
  body text. Read the `layout-aws-spectrum` slide for the complete set.
- Stat-number gradient: `#7c5cff → #3aa0ff → #25c281` (svg text fill).

## Gradients are the hero

(template: Signature gradients page)

- Covers/closings: `aws-grad-signature` (dynamic hero) or the official 2026
  cover arts `aws-cover-violet/aurora/pink`.
- Narrow side strips & columns: `aws-grad-signature-classic` (corner glow
  reads better in a strip).
- Section dividers: `aws-grad-3` (green/aqua) · `aws-grad-2` (pink/peach).
- Brand-true swatches (dark Smile mesh + Violet/Ember/Aurora sets):
  `aws-grad-smile` `aws-grad-set1/2/3`.
- Never approximate a gradient with a flat fill or a plain 2-stop CSS-style
  `fillGradient` when one of these assets fits — reference the asset.

## Patterns & scrims

(template: Patterns & scrims page)

Decorative patterns sit on brand-spectrum fills at LOW ink opacity
(~10–16%); text over photography always gets the squid-ink bottom scrim
(gradient rect `rgba(20,31,46,0) → rgb(20,31,46)`), as in the full-photo
layout.

## Architecture icons

(template: icon-grid pages, 25–28)

160 official 48px SVGs embedded as `icon-<service-slug>` (lowercase,
non-alnum → `-`: `icon-amazon-bedrock`, `icon-aws-lambda`). Rules:
**never recolor, stretch, or redraw**; keep square aspect (52px in grids,
22–52px in diagrams); always label with the exact service name; check the
key exists in `doc.assets` before referencing. When a service's icon is
missing, use a neutral tile + label — don't substitute a different
service's icon.

## Layout mechanics

- Canvas 1280×720, side margins 96px (right-most x ≤ 1184), top 52–76px.
- Footer on content slides: reuse the layout's `awsf-*` elements; page
  number is the `{{page:2}}` dynamic field (never hardcode numbers).
  Covers, section dividers, and full-bleed media omit the page number.
- Logos: `aws-logo-gray850` on light, `aws-logo-white` on dark.
- Content pattern: eyebrow → headline → body/CTA. One idea per slide;
  the headline carries it. Body copy 1–3 lines at 1.4–1.5 line-height.
- Dashed borders mark placeholders (customer logo, screenshots) — keep
  that convention for anything awaiting a real asset.

## Accessibility

(the Bento schema has no alt-text field, so these carry the load)

- Body text targets WCAG AA contrast on its background; text over photos
  or gradients gets the scrim, always.
- Never encode meaning in color alone — pair it with a label, position, or
  shape (chart series get direct labels; status gets words, not just red/
  green).
- No text below 13px (template footer chrome is the only exception); source
  citations stay legible, not fine print.
- A chart or image whose content matters gets its key point stated in
  visible text or in `notes:` — the presenter must be able to convey the
  slide without pointing at colors.
