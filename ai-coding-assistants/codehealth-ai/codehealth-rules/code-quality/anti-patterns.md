# Code Anti-Pattern Detection - Detailed Steps

## Purpose
Scan for language-specific anti-patterns that cause bugs, performance issues, or maintenance debt.

---

## Step 1: Universal Anti-Patterns (All Languages)

| Pattern | Detection | Severity | Fix |
|---------|-----------|----------|-----|
| Magic numbers | Numeric literals without named constants | 🔵 Info | Extract to named constant |
| Magic strings | String literals used for logic branching | 🔵 Info | Extract to enum/constant |
| Deep nesting | >4 levels of indentation | 🟡 Medium | Guard clauses, early returns |
| God function | Function doing >3 distinct things | 🟠 High | Single responsibility split |
| Primitive obsession | Using primitives instead of domain types | 🟡 Medium | Value objects / branded types |
| Feature envy | Method uses another object's data extensively | 🟡 Medium | Move method to data owner |
| Shotgun surgery | One change requires touching many files | 🟡 Medium | Better encapsulation |
| Boolean blindness | Multiple boolean params | 🟡 Medium | Use options object/enum |

---

## Step 2: JavaScript/TypeScript Anti-Patterns

| Pattern | Detection Signal | Severity |
|---------|-----------------|----------|
| `any` type abuse | >10% of annotations are `any` | 🟡 Medium |
| Type assertion abuse | Frequent `as unknown as T` | 🟡 Medium |
| Callback hell | >3 nested callbacks | 🟡 Medium |
| Unhandled promise | `async` without try/catch or .catch() | 🟠 High |
| Event listener leak | addEventListener without removeEventListener | 🟠 High |
| Blocking event loop | Heavy sync computation in async path | 🔴 Critical |
| Mutable exports | Exporting `let` or mutable objects | 🟡 Medium |
| Implicit globals | Undeclared variables (no strict mode) | 🟠 High |
| Prototype pollution | `Object.assign(target, untrustedInput)` | 🔴 Critical |
| eval() usage | Direct or indirect eval | 🔴 Critical |
| setTimeout(string) | String passed to setTimeout | 🔴 Critical |

### React-Specific
| Pattern | Detection | Severity |
|---------|-----------|----------|
| Inline arrow in JSX | `onClick={() => ...}` in render | 🟡 Medium |
| State in wrong layer | Global state for local concern | 🟡 Medium |
| Prop drilling (>3 levels) | Same prop passed through 4+ components | 🟡 Medium |
| useEffect dependency issues | Missing deps or over-inclusive deps | 🟠 High |
| Render during render | setState in render body | 🔴 Critical |
| Missing keys in lists | `.map()` without key prop | 🟠 High |

---

## Step 3: Python Anti-Patterns

| Pattern | Detection Signal | Severity |
|---------|-----------------|----------|
| Mutable default args | `def f(x=[]):` | 🟠 High |
| Bare except | `except:` without exception type | 🟠 High |
| Global state mutation | Module-level mutable dicts/lists modified | 🟡 Medium |
| Import side effects | Module does work on import | 🟡 Medium |
| Wildcard import | `from module import *` | 🟡 Medium |
| String formatting in logging | `logger.info(f"...")` without lazy eval | 🔵 Info |
| Missing `__all__` | Public module without explicit exports | 🔵 Info |
| Sync in async | `time.sleep()` in async function | 🔴 Critical |
| Open without with | `f = open()` without context manager | 🟡 Medium |
| Circular imports | Module A imports B, B imports A | 🟡 Medium |

---

## Step 4: Java Anti-Patterns

| Pattern | Detection Signal | Severity |
|---------|-----------------|----------|
| Empty catch blocks | `catch (Exception e) {}` | 🟠 High |
| Catching Exception | `catch (Exception e)` instead of specific | 🟡 Medium |
| Resource leak | Stream/Connection not in try-with-resources | 🟠 High |
| Static mutable state | `public static List<>` without synchronization | 🔴 Critical |
| God class | Class > 2000 lines or > 30 methods | 🟠 High |
| String concatenation in loop | `+=` on String inside loop | 🟡 Medium |
| Null returns | Method returns null instead of Optional/empty | 🟡 Medium |
| Checked exception abuse | Wrapping everything in RuntimeException | 🟡 Medium |

---

## Step 5: Go Anti-Patterns

| Pattern | Detection Signal | Severity |
|---------|-----------------|----------|
| Goroutine leak | `go func()` without cancellation | 🔴 Critical |
| Channel deadlock | Unbuffered channel with no guaranteed reader | 🟠 High |
| Error ignored | `result, _ := function()` | 🟠 High |
| Naked returns | Named returns used without clarity | 🔵 Info |
| Interface pollution | Interface with >5 methods | 🟡 Medium |
| Init function abuse | Complex logic in init() | 🟡 Medium |
| Package-level state | Mutable package vars without mutex | 🟠 High |

---

## Step 6: Generate Report

```markdown
## 🚫 Anti-Pattern Detection Report

### Summary
| Severity | Count |
|----------|-------|
| 🔴 Critical | {n} |
| 🟠 High | {n} |
| 🟡 Medium | {n} |
| 🔵 Info | {n} |

### Critical Anti-Patterns (Fix Immediately)
| # | Pattern | File | Line | Code Snippet | Fix |
|---|---------|------|------|-------------|-----|
| 1 | {pattern} | {file} | {line} | `{snippet}` | {fix} |

### High-Priority Anti-Patterns
| # | Pattern | File | Line | Impact |
|---|---------|------|------|--------|
| 1 | {pattern} | {file} | {line} | {impact} |

### Pattern Distribution
Shows which anti-patterns are most prevalent — indicates systemic issues.
```

---

## Completion
Output to `dep-analysis-docs/analysis/anti-patterns.md`
