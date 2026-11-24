# Dead Code & Unused Exports - Detailed Steps

## Purpose
Identify code that is never executed, imported, or referenced — safe to remove.

---

## Step 1: Unused Exports Detection

### JavaScript/TypeScript
```bash
# Use ts-prune or knip if available
npx ts-prune 2>/dev/null
npx knip 2>/dev/null

# Manual: Find exports and check if imported anywhere
# Export patterns: export function, export const, export class, export default
grep -rn "^export " --include="*.{ts,tsx,js,jsx}" . | head -100
```

### Python
```bash
# Check for functions defined but never called
# Use vulture if available
vulture . 2>/dev/null

# Manual: find def/class and check usage
grep -rn "^def \|^class " --include="*.py" .
```

---

## Step 2: Unreachable Code Detection

Look for code after control flow terminators:

| Language | Pattern | Detection |
|----------|---------|-----------|
| JS/TS | Code after return/throw | Statements below return in same block |
| Python | Code after return/raise/sys.exit | Unreachable lines in function |
| Java | Code after return/throw/break | Dead code after terminator |
| Go | Code after return/panic | Unreachable statements |

---

## Step 3: Commented-Out Code

```bash
# Find large blocks of commented-out code (>10 consecutive comment lines that look like code)
# This indicates either unfinished work or fear of deletion

# JavaScript/TypeScript
grep -n "^\s*//" --include="*.{ts,js}" . | head -50

# Python
grep -n "^\s*#" --include="*.py" . | head -50
```

Flag blocks of >10 consecutive comment lines that contain:
- Function definitions
- Variable assignments
- Control flow statements
- Import statements

---

## Step 4: Unused Dependencies

Cross-reference with Phase 2 dependency data:

```bash
# Node.js: find packages in package.json not imported anywhere
# For each dependency in package.json:
#   Search for import/require of that package
#   If not found → unused

# Python: find packages in requirements.txt not imported
# For each package:
#   Search for import {package} or from {package}
#   If not found → unused
```

---

## Step 5: Stale Feature Flags

Look for feature flags that are always on/off:

```bash
# Common feature flag patterns
grep -rn "FEATURE_\|feature_flag\|isEnabled\|FF_" --include="*.{ts,js,py,java}" .
# Check if the flag is ever set to different values
# Flags always set to true/false are stale
```

---

## Step 6: Generate Report

```markdown
## 🗑️ Dead Code Report

### Summary
| Category | Count | LOC Removable | Effort |
|----------|-------|--------------|--------|
| Unused exports | {n} | {lines} | Low |
| Unreachable code | {n} | {lines} | Low |
| Commented-out code | {n} blocks | {lines} | Low |
| Unused dependencies | {n} | — | Low |
| Stale feature flags | {n} | {lines} | Medium |
| **Total** | **{n}** | **{lines}** | — |

### Dead Code Percentage: {%} of codebase

### Unused Exports (Safe to Remove)
| # | File | Export | Type | Last Modified |
|---|------|--------|------|---------------|
| 1 | {file} | {name} | function | {date} |

### Unused Dependencies
| # | Package | Size Impact | Command to Remove |
|---|---------|-------------|-------------------|
| 1 | {pkg} | {size} | `npm uninstall {pkg}` |

### Removal Commands
\```bash
# Remove unused dependencies
{commands}

# Files safe to delete entirely (all exports unused)
{file_list}
\```
```

---

## Completion
Output to `dep-analysis-docs/analysis/dead-code.md`
