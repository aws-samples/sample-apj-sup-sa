# Export to PNG / SVG

Convert a generated diagram HTML file into a portable `.svg` and/or `.png` next to it. **Manual only — never run unprompted.**

## Trigger

Load this file when:

- The user invokes the plugin's `export-diagram` skill (`/aws-diagram-design:export-diagram <html-file>` in Claude Code, `/export-diagram` in Kiro CLI — defined in `skills/export-diagram/SKILL.md`).
- The user asks in natural language to export, save, rasterize, convert, or download a diagram in `.svg` or `.png` form. Typical phrasings:
  - "export this as PNG"
  - "save as SVG"
  - "give me a PNG of that diagram"
  - "rasterize it"
  - "convert to png and svg"

The skill is a thin wrapper that delegates here — both paths run the same procedure below.

## Scope

Both formats are **diagram-only** — just the `<svg>` node. Editorial wrappers (header, summary cards, footer in `-full` variants) are intentionally dropped: the export deliverable is the diagram itself, suitable for Figma, slides, social cards, or blog images.

The SVG-only export keeps the source `<title>` and `<desc>` with the diagram. Their per-diagram and per-variant prefixed IDs are what make multiple exported SVGs safe to inline in the same page without one figure resolving to another figure's accessible name.

If the user explicitly asks for "a screenshot of the whole page including the cards", that's a different request — fall back to a normal full-page screenshot via the user's OS or browser.

## SVG export procedure

1. Read the source HTML file.
2. Extract the **first** `<svg ...>...</svg>` block. Use a multiline regex anchored on `<svg` and `</svg>`. Most generated diagrams have only one SVG; if there are multiple, the first is the diagram (gallery files are an exception — see *Edge cases*).
3. Make it standalone:
   - Ensure the opening tag has `xmlns="http://www.w3.org/2000/svg"`. Add it if missing.
   - Ensure a `viewBox` is present. The skill's templates always include one; warn the user if absent rather than guessing.
   - Preserve `role="img"`, `aria-labelledby`, and the first-child `<title>` / `<desc>` exactly as authored.
   - Inject the Amazon Ember / Amazon Ember Mono `@font-face` block (canonical form in [`assets/fonts/README.md`](../assets/fonts/README.md)) into a `<style>` so the SVG renders with correct typography where the font files are reachable. Point the `url()` sources at a path that resolves **from the SVG's own location** (e.g. `../../skills/aws-diagram-design/assets/fonts/woff2` for an SVG inside this repo), and always keep the `local()` sources first — a machine with the fonts installed needs no file access at all. If the SVG will travel alone, either copy the woff2 files next to it, drop the `url()` sources and rely on `local()` + the fallback stack, or recommend the PNG export. No external font CDN is referenced.
     ```svg
     <defs>
       <style>
         @font-face { font-family: 'Amazon Ember'; font-weight: 400; src: local('Amazon Ember'), url('FONTS/AmazonEmber_W_Rg.woff2') format('woff2'); }
         /* …remaining faces from assets/fonts/README.md… */
       </style>
     </defs>
     ```
     If the SVG already contains a `<defs>` block (or a `<style>` inside it), **merge** into it — don't add a second `<defs>`. Any CSS value containing `&` must be XML-escaped as `&amp;` — a standalone `.svg` is parsed as strict XML.
4. Prepend `<?xml version="1.0" encoding="UTF-8"?>\n` so the file is well-formed XML.
5. Write to `<basename>.svg` next to the source (e.g. `example-architecture.html` → `example-architecture.svg`). Honour an explicit output path if the user provides one.

### Caveat to surface to the user

Tools that don't load linked fonts at import time (offline Illustrator, some Figma import paths, older SVG viewers) will substitute typography. The SVG renders correctly in any modern browser. For pixel-perfect portability, recommend the PNG export.

## PNG export procedure

Render **the original HTML** (not the extracted SVG) and screenshot only the `<svg>` element's bounding box. This keeps font loading reliable (already wired in the source HTML) while satisfying the "diagram only" rule. The PNG always has a **transparent background** (`omit_background=True`) so it can be placed on any slide or doc colour without a white halo. For motion-enabled HTML, append `?motion=static`, await `document.fonts.ready`, and assert the motion root has `data-frame="static"` before capture; never export at an arbitrary wall-clock delay.

### Detection

Before running anything, verify Playwright is installed:

```
python -c "import playwright" 2>NUL || python -c "import playwright"
```

If the import fails, surface this exact instruction to the user and stop:

> Playwright isn't installed. To enable PNG export, run:
> ```
> pip install playwright
> playwright install chromium
> ```
> Then ask me to export again.

Don't auto-install. The user asked for one feature, not a system change.

### Rasterize

Write the snippet below to a temp file and run it with `python <tmp.py> <src.html> <out.png>`:

```python
from playwright.sync_api import sync_playwright
import sys, pathlib

src, out = sys.argv[1], sys.argv[2]
scale = int(sys.argv[3]) if len(sys.argv) > 3 else 2

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(device_scale_factor=scale)
    page.goto(f"file://{pathlib.Path(src).resolve()}")
    page.wait_for_load_state("networkidle")
    page.locator("svg").first.screenshot(path=out, omit_background=True)
    browser.close()
```

Default `device_scale_factor=2` for crisp output. Accept `1` for compact assets or `3` for print/retina hero use, passed as a third CLI arg.

### Output naming

`example-architecture.html` → `example-architecture.png`, written next to the source. Honour explicit user-provided paths.

## Sizing the export

The PNG's pixel dimensions are the SVG's `viewBox` × `device_scale_factor`. So the size decision was already made when the diagram was drawn — see [`output-spec.md` §2](output-spec.md) for the presets. Export only picks the multiplier.

| Destination | Scale | Result from a 1280×720 `viewBox` |
|---|---|---|
| Docs, README, wiki | 2 | 2560×1440 |
| Slide deck (projected) | 2 | 2560×1440 |
| Print / PDF handout | 3 | 3840×2160 |
| Inline thumbnail, email | 1 | 1280×720 |

### Hitting an exact pixel size

When the user needs specific dimensions (an OG card at exactly 1200×630, a slide image at 1920×1080), compute the scale factor instead of guessing — Playwright accepts fractional values:

```
scale = target_width / viewBox_width
```

A 960-wide `viewBox` at a 1200px target is `scale=1.25`. Two rules:

- **Never scale below 1** to hit a small target — that soft-focuses the type. Redraw at a smaller preset instead.
- **Never scale past 4** — beyond that you're upscaling a layout that was designed for a smaller canvas; redraw at `slide-16x9` or a print preset.

If the target aspect ratio doesn't match the `viewBox` aspect ratio, say so and offer to redraw at the matching preset. Padding or cropping a finished diagram to fit a frame is not an export operation — it breaks the 40px safe margin.

## Edge cases

- **Source is `assets/index.html`** (the gallery, multiple SVGs in one file): refuse the export and ask the user which specific diagram file they meant. Don't guess.
- **No `<svg>` block found**: the source isn't a diagram file. Tell the user; don't write anything.
- **Surrounding HTML matters to the user**: they want cards/header in the image. Tell them this skill exports diagrams only, and recommend a browser-based full-page screenshot (or a separate PDF print).
- **Source is missing fonts at runtime**: Playwright will substitute, the screenshot will look off. Check that the source HTML carries the Amazon Ember / Ember Mono `@font-face` block (with resolvable `url()` paths) or that the fonts are OS-installed (`<skill-dir>/scripts/install_fonts.sh`). If neither holds, fix the source rather than working around it in export.

## What this command never does

- Modifies the source HTML.
- Adds export buttons or `<script>` tags. Static diagrams remain script-free; an already motion-enabled source may retain the scoped controller from [`animation.md`](animation.md), but export never injects another controller.
- Auto-emits `.svg` or `.png` alongside HTML generation. Manual on every call.
- Embeds an HTML wrapper (cards, headers) into the SVG via `foreignObject`. Too fragile across renderers.
