# Code Refactoring Plan - Detailed Steps

## Purpose
Generate actionable refactoring recommendations from Phase 4 findings.

---

## Step 1: Categorize by Effort

### Quick Wins (<1 hour each)
- Dead code removal (unused exports, commented code, unreachable statements)
- Simple anti-pattern fixes (guard clauses, early returns)
- Unused dependency removal
- Magic number extraction to constants
- Missing error handling additions (wrap in try/catch)

### Sprint Work (1-3 days each)
- Complex function splitting (CC > 20 → multiple smaller functions)
- Anti-pattern resolution requiring architectural changes
- Performance hotspot optimization (algorithm improvements)
- Code deduplication (extract shared utilities)
- Error handling standardization across service

### Epics (>1 week each)
- Major module restructuring
- Design pattern introduction (e.g., adding DI, repository pattern)
- Full error handling overhaul
- Performance architecture changes (caching layer, worker threads)
- Monorepo shared package extraction

---

## Step 2: Complexity Reduction Plan

For each complexity hotspot:

```markdown
### Refactor: {function_name} in {file}
**Current**: CC={score}, {lines} lines, {params} parameters
**Target**: CC<10, <30 lines per extracted function

**Strategy**: {Extract Method / Replace Conditional with Polymorphism / Introduce Guard Clauses}

**Before:**
\```{language}
{simplified version showing structure}
\```

**After:**
\```{language}
{refactored version showing extracted functions}
\```

**Extracted Functions:**
1. `{name}` — handles {responsibility}
2. `{name}` — handles {responsibility}
3. `{name}` — handles {responsibility}
```

---

## Step 3: Performance Fix Plan

For each performance hotspot:

```markdown
### Optimize: {description} in {file}:{line}

**Issue**: {O(n²) loop / Sequential awaits / Memory accumulation}
**Impact**: {estimated improvement}

**Before:**
\```{language}
{original code}
\```

**After:**
\```{language}
{optimized code with comments}
\```

**Why This Works**: {explanation of the improvement}
**Testing**: Run {benchmark/test} to verify improvement
```

---

## Step 4: Dead Code Removal Script

```bash
#!/bin/bash
# Dead Code Removal - Safe to Execute
# Generated from Phase 4 analysis

# Step 1: Remove unused dependencies
{package_manager_uninstall_commands}

# Step 2: Delete dead files (no imports reference these)
{rm_commands_for_dead_files}

# Step 3: Run tests to verify nothing broke
{test_command}
```

---

## Step 5: Generate Refactoring Roadmap

```markdown
## 🔧 Code Refactoring Plan

### Quick Wins (This Week)
| # | Action | File | Lines Removed/Improved | Effort |
|---|--------|------|----------------------|--------|
| 1 | Remove {n} unused exports | various | {lines} | 30min |
| 2 | Delete commented code blocks | various | {lines} | 15min |
| 3 | Uninstall {n} unused deps | package.json | — | 15min |
| 4 | Add guard clauses in {func} | {file} | 10 → 7 lines | 30min |

### Sprint Backlog
| # | Action | File | Impact | Effort |
|---|--------|------|--------|--------|
| 1 | Split {god_function} | {file} | CC 35→8 | 2h |
| 2 | Fix O(n²) in {func} | {file} | -{x}ms | 1h |
| 3 | Extract shared {util} | monorepo | DRY {n} files | 4h |

### Tech Debt Epics
| # | Action | Scope | Business Value | Effort |
|---|--------|-------|---------------|--------|
| 1 | Error handling overhaul | Service-wide | Fewer silent failures | 3d |
| 2 | Introduce caching layer | Data layer | -50% DB load | 1w |
```

---

## Completion
Output to `dep-analysis-docs/optimization/code-refactoring-plan.md`
