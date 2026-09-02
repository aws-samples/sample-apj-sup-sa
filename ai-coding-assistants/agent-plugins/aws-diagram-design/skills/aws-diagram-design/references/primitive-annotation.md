# Annotation Callout (italic aside)

Use for editorial asides — the "italic pointer" that marks a detail without competing with the primary diagram grammar. Think marginalia: *"structure IS the index"*, *"no imports, no configuration"*.

## Grammar

```svg
<!-- 1. Italic Amazon Ember text -->
<text x="904" y="36" fill="#232F3E" font-size="14" font-style="italic"
      font-weight="400"
      font-family="'Amazon Ember', 'Helvetica Neue', Helvetica, Arial, sans-serif" text-anchor="end">no imports, no configuration</text>
<!-- 2. Dashed Bézier leader -->
<path d="M 820 44 Q 700 84 520 216" fill="none"
      stroke="rgba(35,47,62,0.40)" stroke-width="1" stroke-dasharray="4,3"/>
<!-- 3. Landing dot -->
<circle cx="520" cy="216" r="2" fill="#232F3E"/>
```

## Rules
- Italic Amazon Ember signals "editorial voice" against the diagram's upright sans/mono body. Don't substitute upright text or italic mono — the italic is load-bearing.
- Dashed path (`stroke-dasharray="4,3"`) distinguishes the callout leader from primary arrows (which are solid).
- Place callouts in margins (top-right, bottom-left). Never inside the active diagram area.
- Max 2 callouts per diagram. More becomes commentary, not signal.

## Colors

| Intent | Text | Leader |
|---|---|---|
| Neutral aside | ink `#232F3E` | `rgba(35,47,62,0.40)` |
| Focal / accent | coral `#EC7211` | `rgba(255,153,0,0.50)` |
| Tertiary (muted) | muted `#545B64` | `rgba(35,47,62,0.30)` |

## Anti-patterns
- Solid arrow leader (reads as a flow arrow).
- Upright text or italic mono — the italic is load-bearing.
- Callouts crossing primary arrows / lifelines — offset to a clear margin.
- Using a callout to label something the diagram should label directly — put the label on the element.
