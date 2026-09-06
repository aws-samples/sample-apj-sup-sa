# Performance Budget Extension - Full Rules

## Enforcement Rules

When this extension is enabled, the following checks are MANDATORY during the
Dependency Weight Analysis stage:

### Rule 1: Total Bundle Budget
- Default budget: 200KB (minified + gzipped) for the full dependency bundle
- Flag VIOLATION if total exceeds budget
- Configurable via `dep-analysis-docs/config.md` if present

### Rule 2: Individual Package Limits
- Flag any single package contributing > 50KB to the bundle
- Must provide lighter alternative recommendation for flagged packages

### Rule 3: Tree-Shaking Score
- Analyze if dependencies export ESM (tree-shakeable) or CJS only
- Flag CJS-only packages > 10KB that have ESM alternatives
- Score: % of deps that are tree-shakeable

### Rule 4: Duplicate Detection
- Flag any package installed at 2+ different versions
- Calculate wasted bytes from duplication
- Must provide deduplication strategy

### Rule 5: Import Analysis
- For JavaScript/TypeScript: analyze actual imports vs full package
- Flag cases where <20% of a package's exports are used
- Recommend sub-path imports or lighter alternatives

## Non-Compliance
Findings are BLOCKING for Critical (budget exceeded by >50%).
Findings are WARNING for Medium (budget exceeded by <50%).
