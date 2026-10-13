---
name: export-diagram
description: "Export a generated aws-diagram-design HTML diagram to .svg and/or .png next to the source. Use when the user asks to export, save, rasterize, convert, or download a diagram as PNG or SVG. Korean triggers: PNG로 내보내기, SVG로 저장, 다이어그램 이미지로 변환."
license: MIT
argument-hint: <html-file> [--svg-only|--png-only] [--scale=N] [--output=<path>]
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
metadata:
  version: "2.0.0"
  plugin: aws-diagram-design
---

# Export diagram → SVG / PNG

Export the diagram HTML at `$1` to `.svg` and/or `.png`, following the procedure in
[`../aws-diagram-design/references/export.md`](../aws-diagram-design/references/export.md).
Treat that reference as the source of truth — don't reimplement the logic here.

Full argument string: `$ARGUMENTS`

The reference lives in the sibling `aws-diagram-design` skill of this plugin. Locate the
installed plugin root (the directory containing `plugin.json`) and read
`skills/aws-diagram-design/references/export.md` from there — never assume the plugin is
under the current working directory.

## Defaults

- Produce **both** `.svg` and `.png` next to the source (e.g. `diagram.html` → `diagram.svg` + `diagram.png`).
- PNG renders at `device_scale_factor=2` with a transparent background.

## Flags

- `--svg-only` — emit only the SVG. Skip Playwright entirely.
- `--png-only` — emit only the PNG.
- `--scale=1` / `--scale=2` / `--scale=3` — override the PNG device scale factor. Default `2`.
- `--output=<path>` — override the output base path; the format extension is appended. Applies to both formats when both are produced.

## Required behaviour

1. **No source path provided** → ask the user which `.html` file to export. Don't guess.
2. **Source is a gallery file** (`assets/index.html`, multiple SVGs in one file) → refuse and ask which specific diagram file.
3. **Source has no `<svg>` block** → refuse and tell the user; don't write anything.
4. **PNG requested but Playwright not installed** → surface the install instruction from the reference verbatim and stop. Do **not** auto-install.
5. **PNG requested with `--scale` outside {1,2,3}** → reject; valid values are 1, 2, 3.
6. Never modify the source HTML, and never emit export files unprompted.

After producing the outputs, report the file paths and sizes back to the user.
