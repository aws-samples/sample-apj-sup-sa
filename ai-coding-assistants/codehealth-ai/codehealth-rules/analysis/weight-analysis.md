# Dependency Weight Analysis - Detailed Steps

## Purpose
Analyze bundle size impact for frontend/browser projects and identify heavy dependencies.

---

## Step 1: Measure Install Size

### Node.js
```bash
# Total node_modules size
du -sh node_modules/ 2>/dev/null

# Per-package size
du -sh node_modules/{package}/ 2>/dev/null

# Package count
ls node_modules/ | wc -l
```

### Alternative: Use package-size tools
```bash
# If available:
npx package-phobia {package_name}
npx bundlephobia-cli {package_name}
```

---

## Step 2: Analyze Bundle Contribution

For frontend projects, estimate the impact on the final bundle:

| Package | Install Size | Bundle Size (min+gz) | Tree-Shakeable |
|---------|-------------|---------------------|----------------|
| {pkg} | {size} | {bundle_size} | {yes/no/partial} |

### Common Heavy Dependencies and Alternatives:

| Heavy Package | Size (min+gz) | Lighter Alternative | Alternative Size |
|--------------|--------------|--------------------|-----------------| 
| moment | ~72KB | dayjs | ~2KB |
| lodash (full) | ~72KB | lodash-es (tree-shake) | ~varies |
| axios | ~14KB | native fetch | 0KB |
| uuid | ~4KB | crypto.randomUUID() | 0KB |
| classnames | ~1KB | clsx | ~0.5KB |
| date-fns (full) | ~30KB | date-fns (tree-shake) | ~varies |

---

## Step 3: Detect Duplicates

Find packages installed at multiple versions:

```bash
# npm
npm ls --all 2>&1 | grep "deduped" | wc -l
npm ls --all 2>&1 | grep -v "deduped" | sort | uniq -d

# Analyze lock file for duplicates
# Look for same package name at different versions
```

---

## Step 4: Generate Weight Report

```markdown
## ⚖️ Dependency Weight Report

### Summary
| Metric | Value |
|--------|-------|
| node_modules size | {size} |
| Total packages | {count} |
| Duplicate packages | {count} |
| Estimated bundle impact | {size} |

### Heaviest Dependencies (Top 10)
| # | Package | Install Size | Bundle Contribution | Alternative |
|---|---------|-------------|--------------------| ------------|
| 1 | {pkg} | {size} | {bundle} | {alt or "—"} |

### Optimization Opportunities
| Action | Estimated Savings | Effort |
|--------|------------------|--------|
| Replace {pkg} with {alt} | -{size} | Low |
| Tree-shake {pkg} | -{size} | Medium |
| Remove unused {pkg} | -{size} | Low |
| Deduplicate {n} packages | -{size} | Low (npm dedupe) |
```

---

## Completion

Output to `dep-analysis-docs/analysis/weight-analysis.md`
