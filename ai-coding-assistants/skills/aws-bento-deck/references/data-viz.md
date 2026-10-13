# AWS Bento deck — data visualization rules

Read this when the plan or a slide contains numbers to compare, or before
composing/reviewing any `chart:` element.

## Chart, not text

Numbers to compare (trend, magnitude, share) belong in a **chart** element,
not a text list. In the plan, mark the slide `layout: custom` and attach
`chart:` data.

## Chart type — pick by what the data says

| the data's story | chart |
|---|---|
| change over time | line |
| magnitude across categories | bar (horizontal when labels are long) |
| composition / share of whole | donut/pie — ONLY with ≤5 slices |
| relationship of two variables | scatter |
| two series on very different scales | dual y-axis (bars left, line right) |

## Chart standards

- **Title states the chart's CONCLUSION**, not its topic: "Revenue doubled
  after migration", not "Revenue by quarter".
- Direct labels over legends when the series count allows.
- Units, period, and denominator always visible.
- Never distinguish series by color alone — label them.
- If an axis doesn't start at zero, say so on the slide.
- Every chart backing a claim cites its source on the slide or in `notes:`.
- Chart colors come from the deck palette / brand spectrum (see
  design-system.md) — not from body-text neutrals.

## Bento chart format gotchas

- Series data = **plain numbers** for bar/line/scatter (`{value,...}`
  objects coerce to 0). Only pie takes `{name, value}`.
- Options are pure JSON — template formatters like `{b}`/`{c}`/`{d}` are
  fine; functions are not.
- Per-item bar colors are unsupported — color by series.
- Dual axis: `yAxis` as an array of two, series pick via `yAxisIndex`.

## Headline numbers

A single hero stat is not a chart — use `stat-donut` / `dual-stat` layouts
with `fx: count-up`, and always pair the number with its claim and source.
