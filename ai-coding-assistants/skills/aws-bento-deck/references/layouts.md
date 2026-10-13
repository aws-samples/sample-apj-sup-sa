# AWS Deck Template — layout catalog

Layout ids live in `doc.layouts` of the template. "Slots" are the text
elements to fill (id → what goes there). Elements not listed (bars, rules,
gradients, footer, `{{page:2}}`) are chrome — copy them unchanged.
Footer trio on content slides: `awsf-logo` / `awsf-copy` / `awsf-pg`.

## Covers (no page number; big logo bottom-right)

| layout id | mood | slots |
|---|---|---|
| `layout-aws-cover` | lavender→cyan signature (the classic) | `awst-ey` kicker · `awst-title` 78px · `awst-name` presenter · `awst-role` title/team |
| `layout-aws-cover-violet` | pale lavender + teal/blue sweep (official 2026, default) | `cv0-ey` · `cv0-title` · `cv0-name` · `cv0-role` |
| `layout-aws-cover-aurora` | cyan/green/periwinkle aurora | `cv1-*` same slots |
| `layout-aws-cover-pink` | pink bloom + magenta/lime/amber | `cv2-*` same slots |

## Structure

| layout id | use for | slots |
|---|---|---|
| `layout-aws-side-accent` | secondary title page, chapter opener with body text | `aws2-ey` kicker · `aws2-title` 60px · `aws2-body` |
| `layout-aws-section-a` | section divider (green/aqua gradient) | `aws32-ey` "SECTION NN" · `aws32-title` 72px |
| `layout-aws-section-b` | section divider (pink gradient, big number bottom) | `aws33-num` "03" mono 96px · `aws33-title` 64px |
| `layout-aws-agenda` | agenda, 5 numbered rows | `aws3-agenda` ("Agenda") · rows `aws3-num-1..5` + `aws3-item-1..5` + rules — add/remove rows keeping the pattern (30px text, 18px pad, 1px rule) |

## Content

| layout id | use for | slots |
|---|---|---|
| `layout-aws-bulleted` | 3–5 bullet takeaways | `aws8-title` 42px · pairs `aws8-bul-N` (9px fuchsia square) + `aws8-bt-N` text, 50px row pitch from y=164 |
| `layout-aws-two-col` | before/after, A vs B prose | `aws10-title` · `aws10-ey-l`/`aws10-b-l` left · `aws10-ey-r`/`aws10-b-r` right (divider `aws10-div`) |
| `layout-aws-compare-cards` | 2 option cards with bullet lists | `aws11-title` · card heads `aws11-hl-1/2` · bullets `aws11-bt-C-N` |
| `layout-aws-three-col` | 3 parallel offerings/steps | `aws12-title` · per col: chip `aws12-chip-N`+icon `aws12-icon-N`, head `aws12-h-N`, body `aws12-b-N` |
| `layout-aws-four-col` | 4-phase journey | `aws13-title` · per col: big mono num `aws13-num-N`, head `aws13-h-N`, body `aws13-b-N` |
| `layout-aws-timeline` | years/eras + question pills | `aws14-title` · years `aws14-yr-N`+`aws14-sub-N` · pills `aws14-pill/pt-C-N` (rebuild pill rows per content) |
| `layout-aws-stat-donut` | ONE headline stat + claim + source | `aws15-title` · donut svg `aws15-donut` (edit % in markup) · `aws15-claim` · `aws15-src-ey`/`aws15-src` |
| `layout-aws-dual-stat` | TWO stats side by side | `aws16-title` · per side: gradient-number svg `aws16-stat-N` (edit label in markup) · `aws16-claim-N` · `aws16-src-N` |
| `layout-aws-evolution` | 3-stage maturity spectrum with arrow | `aws17-title` · heads `aws17-h-1..3` · bodies `aws17-b-1..3` · end labels `aws17-lbl-l/r` · flow label `aws17-flow` |
| `layout-aws-service-capture` | product pitch + 2 screenshots | `aws18-title` · `aws18-sub` · bullets `aws18-bt-N` · shot rects `aws18-shot-1/2` (replace with image elements) |
| `layout-aws-code` | terminal/CLI demo | `aws34-title` · code svg `aws34-code` (edit the svg text runs; keep mono font + colors #b8e986 prompt / #f299ff flags / #acacb4 comments) |

## Media & quotes

| layout id | use for | slots |
|---|---|---|
| `layout-aws-pic-caption` | photo left 58% + caption right | photo `aws26-photo` (swap placeholder for image) · `aws26-ey`/`aws26-title`/`aws26-body` |
| `layout-aws-full-photo` | full-bleed photo + bottom-left title (ken-burns on) | `aws27-photo` · `aws27-ey` · `aws27-title` (white text, scrim included) |
| `layout-aws-hyperspace` | dark starfield statement | `aws28-ey` · `aws28-title` |
| `layout-aws-quote-std` | short quote, white bg | `aws29-quote` 44px · `aws29-attr` mono |
| `layout-aws-quote-grad` | short quote on signature gradient | `aws30-quote` · `aws30-attr` |
| `layout-aws-quote-customer` | long customer quote + logo box + blurb | `aws31-quote` 31px · `aws31-name`/`aws31-role` · `aws31-blurb` (logo placeholder `aws31-logo-box/lbl`) |

## Closers & resources

| layout id | use for | slots |
|---|---|---|
| `layout-aws-qa` | Q&A | `aws35-title` (fuchsia bar `aws35-bar`) |
| `layout-aws-thanks` | thank you + contact | `aws36-title` · `aws36-contact` |
| `layout-aws-arch-pkg-1` | 8×5 AWS service icon grid | `aws37-ey`/`aws37-title` · 40 cells: icon `aws37-ic-N` (point `asset` at `icon-<service>`) + label `aws37-il-N` |
| `layout-aws-gradients` / `-patterns` / `-neutrals` / `-spectrum` / `-display-scale` / `-type-families` / `-content-fundamentals` / `-visual-foundations` | brand-guide reference pages | usually copied as-is |

## Embedded assets you can reference

- Logos: `aws-logo-gray850` (light bg) · `aws-logo-white` (dark bg)
- Gradients: `aws-grad-signature` (dynamic hero) · `aws-grad-signature-classic`
  (side strips) · `aws-grad-smile` `aws-grad-set1/2/3` (brand) · `aws-grad-2/3`
  (sections) · `aws-cover-violet/aurora/pink`
- 160 architecture icons: `icon-<service-slug>` — slug = lowercased name,
  non-alnum → `-` (e.g. `icon-amazon-bedrock`, `icon-aws-lambda`,
  `icon-amazon-elastic-kubernetes-service`). Verify the key exists in
  `doc.assets` before use.
- Fonts (already wired via `doc.fonts`): Amazon Ember Display 400/500-600/700,
  Amazon Ember Mono 400-500.
