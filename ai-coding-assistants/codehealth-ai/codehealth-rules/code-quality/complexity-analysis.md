# Complexity Analysis - Detailed Steps

## Purpose
Identify code complexity hotspots that hurt maintainability and increase bug risk.

---

## Step 1: Identify Analyzable Source Files

Based on detected tech stack, determine which files to analyze:

```bash
# Exclude generated code, vendor, node_modules, build output
find . -type f \( -name "*.ts" -o -name "*.js" -o -name "*.py" -o -name "*.java" \
  -o -name "*.go" -o -name "*.rs" -o -name "*.rb" -o -name "*.cs" \) \
  -not -path "*/node_modules/*" -not -path "*/vendor/*" \
  -not -path "*/dist/*" -not -path "*/build/*" \
  -not -path "*/.next/*" -not -path "*/__pycache__/*" \
  -not -path "*/generated/*"
```

---

## Step 2: Compute Cyclomatic Complexity

Cyclomatic complexity counts the number of independent paths through a function:
- Each `if`, `else if`, `case`, `&&`, `||`, `while`, `for`, `catch` adds 1
- Ternary operators add 1
- Null coalescing `??` adds 1

### Thresholds:
| CC Score | Rating | Action |
|----------|--------|--------|
| 1-5 | Simple | No action |
| 6-10 | Moderate | Monitor |
| 11-20 | Complex | 🟡 Refactor recommended |
| 21-50 | Very Complex | 🟠 Refactor needed |
| 50+ | Untestable | 🔴 Must refactor |

---

## Step 3: Compute Cognitive Complexity

Cognitive complexity measures how hard code is to understand:
- Each nesting level increases the "penalty" for control structures
- Breaks in linear flow (if, for, while) add 1
- Nesting adds additional penalty per depth level
- Boolean operators in conditions add complexity

### Thresholds:
| Score | Rating | Action |
|-------|--------|--------|
| 0-8 | Easy | No action |
| 9-15 | Moderate | Monitor |
| 16-25 | High | 🟡 Refactor recommended |
| 25+ | Very High | 🟠 Refactor needed |

---

## Step 4: Function/Method Length

| Lines | Rating | Action |
|-------|--------|--------|
| 1-20 | Ideal | No action |
| 21-50 | Acceptable | Monitor |
| 51-100 | Long | 🟡 Consider splitting |
| 100+ | Too Long | 🟠 Split into smaller functions |

---

## Step 5: File Length & Structure

| Lines | Rating | Action |
|-------|--------|--------|
| 1-200 | Ideal | No action |
| 201-500 | Acceptable | Monitor |
| 501-1000 | Large | 🟡 Consider splitting |
| 1000+ | Too Large | 🟠 Needs modularization |

---

## Step 6: Parameter Count

| Count | Rating | Action |
|-------|--------|--------|
| 0-3 | Ideal | No action |
| 4-5 | Acceptable | Consider options object |
| 6-7 | High | 🟡 Refactor to options/builder pattern |
| 8+ | Excessive | 🟠 Definitely refactor |

---

## Step 7: Generate Complexity Report

```markdown
## 📊 Complexity Analysis Report

### Summary
| Metric | Count | Threshold Exceeded |
|--------|-------|-------------------|
| Functions analyzed | {n} | — |
| High cyclomatic complexity (>10) | {n} | 🟡 {n} / 🟠 {n} / 🔴 {n} |
| High cognitive complexity (>15) | {n} | 🟡 {n} / 🟠 {n} |
| Long functions (>50 lines) | {n} | 🟡 {n} / 🟠 {n} |
| Large files (>500 lines) | {n} | 🟡 {n} / 🟠 {n} |
| Many parameters (>5) | {n} | 🟡 {n} / 🟠 {n} |

### Complexity Hotspots (Top 10)
| # | File | Function | CC | Cognitive | Lines | Priority |
|---|------|----------|----|-----------|----|----------|
| 1 | {file} | {func} | {cc} | {cog} | {lines} | 🔴 |
| 2 | {file} | {func} | {cc} | {cog} | {lines} | 🟠 |
| ... |

### Refactoring Suggestions
For each hotspot:
- **{function_name}** ({file}:{line})
  - Current CC: {score}, Lines: {n}
  - Suggestion: {Extract method / Introduce guard clauses / Replace conditional with polymorphism}
  - Estimated effort: {Low/Medium/High}

### Complexity Trend
If git history available, show if complexity is increasing over recent commits.
```

---

## Completion
Output to `dep-analysis-docs/analysis/complexity-report.md`
