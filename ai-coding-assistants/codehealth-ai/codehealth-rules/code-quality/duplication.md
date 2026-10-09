# Code Duplication Analysis - Detailed Steps

## Purpose
Detect duplicated code blocks that indicate extraction/abstraction opportunities.

---

## Step 1: Detect Exact Duplicates

Look for blocks of >10 lines that appear identically in multiple locations:

```bash
# If jscpd is available:
npx jscpd --min-lines 10 --reporters json src/ 2>/dev/null

# Manual approach: hash code blocks and compare
```

---

## Step 2: Detect Near-Duplicates

Same structural logic with different variable names or minor variations:
- Same control flow, different identifiers
- Same algorithm, different data types
- Copy-paste with minor modifications

---

## Step 3: Cross-Package Duplication (Monorepo)

For monorepos, check if multiple packages implement the same logic:
- Utility functions duplicated across packages
- Type definitions repeated
- Configuration parsing logic copied
- Validation rules duplicated

---

## Step 4: Generate Report

```markdown
## 📋 Code Duplication Report

### Summary
| Metric | Value |
|--------|-------|
| Duplication percentage | {%} |
| Duplicate blocks found | {n} |
| Total duplicated lines | {lines} |
| Extraction candidates | {n} |

### Top Duplication Clusters
| # | Pattern | Occurrences | Lines Each | Total Waste | Extraction Target |
|---|---------|-------------|-----------|-------------|-------------------|
| 1 | {description} | {n} files | {lines} | {total} | Shared utility |
| 2 | {description} | {n} files | {lines} | {total} | Base class |

### Recommended Extractions
| # | From Files | Extract To | Type | Effort |
|---|-----------|-----------|------|--------|
| 1 | {file1}, {file2} | `shared/utils/{name}` | Function | Low |
| 2 | {file1}, {file2} | `shared/hooks/{name}` | Hook | Medium |
```

---

## Completion
Output to `dep-analysis-docs/analysis/duplication.md`
