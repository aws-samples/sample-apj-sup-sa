# Style Guide

**The single source of truth for colors, typography, and tokens.** Every diagram draws from this — not from hex values inlined in other reference files. If you want to change the visual skin of Schematic, change this file.

**Current skin: AWS brand** — white paper, Squid-Ink text (`#232F3E`), Smile-Orange accent, Amazon Ember typography, and the official AWS Architecture Icons ([primitive-aws-icons.md](primitive-aws-icons.md)). This replaces the shipped editorial default (white-smoke / atomic-tangerine `#eb6c36`); swap these values (or run [`onboarding.md`](onboarding.md)) and every new diagram inherits the new skin without touching any type-specific logic.

---

## Tokens

### Semantic roles

Every token is referred to by **semantic role**, not by its hex value. Type references (`type-*.md`) and SKILL.md say `accent`, not `#EC7211`.

| Role | Purpose | AWS (light) | AWS (dark) |
|---|---|---|---|
| `paper` | Page background, default node fill | `#FFFFFF` (white — official AWS diagram bg) | `#161E2D` (dark squid) |
| `paper-2` | Diagram container bg, secondary fill | `#F2F3F3` | `#232F3E` (squid ink) |
| `ink` | Primary text, primary stroke | `#232F3E` (squid ink) | `#FFFFFF` |
| `muted` | Secondary text, default arrow stroke | `#545B64` | `#B6BEC9` |
| `soft` | Sublabels, boundary labels | `#7D8998` | `#8D99A8` |
| `rule` | Hairline borders | `rgba(35,47,62,0.12)` | `rgba(255,255,255,0.12)` |
| `rule-solid` | Stronger borders, baselines | `#D5DBDB` | `rgba(255,255,255,0.25)` |
| `accent` | Focal / 1–2 max per diagram | `#EC7211` (smile orange, dark step) | `#FF9900` (smile orange) |
| `accent-tint` | Fill for accent-bordered boxes | `rgba(255,153,0,0.10)` | `rgba(255,153,0,0.14)` |
| `link` | HTTP/API calls, external arrows | `#0972D3` (Cloudscape blue) | `#539FE5` |

> **Brand palette source:** AWS brand — `squid ink #232F3E`, `smile orange #FF9900`, white. Pure `#FF9900` reads weakly as thin strokes/text on white, so light-mode `accent` uses the darker step `#EC7211` (the orange AWS itself uses on light UI); `#FF9900` is the accent on dark and the base of `accent-tint`. `muted`/`soft`/`rule-solid`/`link` come from the AWS/Cloudscape grey-blue ramp. Category identity colors (`#ED7100`, `#8C4FFF`, …) belong to the icons and zone borders — see [primitive-aws-icons.md](primitive-aws-icons.md) — and are **not** editorial accents.

> **Note:** The pre-baked example HTML files in `assets/` were built under earlier skins and don't reflect the AWS skin. New diagrams the skill produces must use the tokens above.

### Inversion rule (light → dark)

Any `rgba(35,47,62, X)` in light becomes `rgba(255,255,255, X)` in dark. Same opacities, RGB flipped. The accent brightens from `#EC7211` to pure `#FF9900` on dark paper.

### Series palette (multi-series chart types only)

For chart types that genuinely need to distinguish multiple overlapping entities (currently: **radar**), use the AWS category ramp. The "1-focal" rule still holds — `accent` is reserved for the focal series; the palette below covers the rest.

| Token | Light | Dark | Notes |
|---|---|---|---|
| `series-1` | `#8C4FFF` (purple) | `#A97FFF` | Non-focal series |
| `series-2` | `#01A88D` (teal) | `#2BC0A8` | Non-focal series |
| `series-3` | `#7AA116` (green) | `#96BE3C` | Non-focal series |
| `series-4` | `#C925D1` (magenta) | `#D95CDF` | Non-focal series |
| `series-5` | `#DD344C` (red) | `#E5626F` | Non-focal series |

Fills sit at `0.14` opacity light, `0.20` dark; strokes use the full color. **Don't backfill these tokens to non-chart types** — architecture, swimlane, etc. continue to use muted-ink variants. The series palette is opt-in for diagrams where overlapping shapes demand distinguishable color, not a license to add color elsewhere.

### Terminal skin (opt-in alternate)

A self-contained palette for the terminal-window primitive (see [primitive-terminal.md](primitive-terminal.md)) — a CLI-chrome register for dev-tool posts and technical social cards. It does not replace the default skin above and isn't affected by onboarding; it's a second, fixed skin you opt into per-diagram.

| Token | Hex | Purpose |
|---|---|---|
| `terminal-page` | `#0a0a0a` | Page background behind the window |
| `terminal-paper` | `#141414` | Window body, node fill |
| `terminal-bar` | `#1b1b1b` | Titlebar strip |
| `terminal-border` | `#2b2b2b` | Window border, hairlines |
| `terminal-ink` | `#f5f5f5` | Primary text, primary stroke |
| `terminal-muted` | `#9a9a9a` | Secondary text, sublabels, ring stroke |
| `terminal-soft` | `#5c5c5c` | Tertiary — inactive dots, spokes |
| `terminal-accent` | `#ff5a36` | The one accent — focal station, prompt sign, active dot |
| `terminal-accent-tint` | `rgba(255,90,54,0.12)` | Fill for accent-bordered boxes |

**1-accent rule still holds.** Everything that isn't `terminal-ink` or `terminal-muted`/`terminal-soft` should be `terminal-accent` — never introduce a second hue.

---

## Typography

| Role | Family | Size | Weight | Usage |
|---|---|---|---|---|
| `title` | Amazon Ember | 1.75rem | 700 | Page H1 |
| `node-name` | Amazon Ember | 12px | 600 | Human-readable labels |
| `sublabel` | Amazon Ember Mono | 9px | 400 | Port, protocol, URL, field type |
| `eyebrow` | Amazon Ember Mono | 7–8px | 500, tracked 0.18em, uppercase | Type tags, axis labels |
| `arrow-label` | Amazon Ember Mono | 8px | 400, tracked 0.06em | Arrow annotations |
| `callout` | Amazon Ember *italic* | 14px | 400 | Editorial asides only |

### Font stack

Amazon Ember is **not on Google Fonts**, but this skill **bundles it** at [`assets/fonts/`](../assets/fonts/) (woff2 webfonts + desktop TTFs, from Amazon's public typography download). Load it in this order:

1. **`local('Amazon Ember')`** — present on Amazon-managed machines, or after running `<skill-dir>/scripts/install_fonts.sh`, which installs the bundled TTFs into the OS font directory.
2. **Bundled woff2 via `@font-face`** — paste the canonical block from [`assets/fonts/README.md`](../assets/fonts/README.md) into the generated HTML, pointing `FONTS` at `<skill-dir>/assets/fonts/woff2` (relative path when the output lives near the skill; or copy the woff2 files next to the deliverable and use `./fonts/`).
3. **Fallback stack** — always declared on `font-family`; sandboxed viewers (GitHub's SVG renderer, offline tools) fall back to Helvetica/Arial.

Weight map for the bundled faces: Amazon Ember 300 Lt · 400 Rg (+italic) · 600 SBd · 700 Bd · 800 He; **Amazon Ember Mono 400/700** carries every mono role (sublabels, eyebrows, arrow labels — a 500/600 spec snaps to the nearest bundled weight, which is expected). **No font loads from an external CDN** — generated diagrams need no Google Fonts `<link>`.

```css
/* sans (names, titles, callouts) */ font-family: 'Amazon Ember', 'Helvetica Neue', Helvetica, Arial, sans-serif;
/* mono (technical content)       */ font-family: 'Amazon Ember Mono', ui-monospace, monospace;
```

Installed Ember faces: Light 300 / Regular 400 / Medium 500 / Bold 700 / Heavy 800, plus italics. If a 600 weight renders as synthesized bold in a target environment, drop node names to 500 or up to 700 — never fake-bold the Heavy face.

**Load-bearing rule:** Mono is for *technical* content (ports, commands, URLs, field types). Names, titles, and callouts go in Amazon Ember. Italic Ember is reserved for annotation callouts (see [primitive-annotation.md](primitive-annotation.md)). **Never JetBrains Mono** as a blanket "dev" font. This skin intentionally drops the editorial serif (Instrument Serif) — AWS brand typography is single-family.

---

## Stroke, radius, spacing

| Token | Value | Use |
|---|---|---|
| `stroke-thin` | `0.8` | Tag-box outlines, leaf nodes |
| `stroke-default` | `1` | Most strokes |
| `stroke-strong` | `1.2` | Emphasis strokes |
| `radius-sm` | `4` | Small tags |
| `radius-md` | `6` | Node boxes |
| `radius-lg` | `8` | Containers, rings |
| `grid` | `4` | Every coord, size, and gap is divisible by 4 (hard rule) |

---

## Node type → treatment

Semantic role combinations — reference these by name in type specs.

| Type | Fill | Stroke |
|---|---|---|
| `focal` (1–2 max) | `accent-tint` | `accent` |
| `backend` | `#ffffff` (white) | `ink` |
| `store` | `ink @ 0.05` | `muted` |
| `external` | `ink @ 0.03` | `ink @ 0.30` |
| `input` | `muted @ 0.10` | `soft` |
| `optional` | `ink @ 0.02` | `ink @ 0.20` dashed `4,3` |
| `security` | `#DD344C @ 0.05` | `#DD344C @ 0.60` dashed `4,4` — aligned to the AWS Security-Identity red |

AWS-specific node and zone treatments (service-icon nodes, VPC/subnet/account containers) are specified in [primitive-aws-icons.md](primitive-aws-icons.md); zone-border identity colors don't count against the accent budget.

---

## Customizing the skin

Three options:

1. **Run onboarding** — see [`onboarding.md`](onboarding.md). Drop a URL; the skill extracts the palette + fonts and rewrites this file.
2. **Edit by hand** — change the hex values in the tables above. Run the pre-output taste gate afterward to verify the accent still reads as "focal" against the new paper color.
3. **Brand handoff** — paste your existing design-token JSON into a new section here and map its tokens to the semantic roles above.

### Constraints (don't break these)

- **Contrast**: `ink` must hit WCAG AA on `paper`. `muted` must hit AA on `paper` for 11px+ text.
- **One accent**: pick one color for `accent`. Two accents erases the focal signal. (AWS icon/zone identity colors are exempt — they're identity marks, not accents.)
- **No rainbow palette**: if your brand ships 8 colors, pick 3 (paper, ink, accent). The rest become `muted` variants.
- **Typography**: at most three families. The AWS skin runs sans + mono (Ember + Amazon Ember Mono) — brand consistency outweighs the serif/sans contrast the editorial default used.
- **Paper**: the AWS skin uses pure white because official AWS architecture diagrams do. For non-AWS skins, prefer a warm-neutral (cream, bone, light grey) — pure white without a brand mandate turns the design sterile.
- **Dot pattern is optional, not default**: the 22×22 dot pattern is an opt-in "dotted paper" variant (good for long-form editorial hero diagrams). The default background is a clean `paper` fill, no pattern. When the pattern is enabled, it should sit at ~10% opacity of `ink` on `paper` — visible but quiet.
- **Container is clean by default**: the diagram sits directly on the page paper, no secondary container background or border. A framed variant (`paper-2` bg + `rule` border + 8px radius + padding) is available as an opt-in for card-heavy layouts, but don't reach for it by default — the extra chrome fights the figure.
