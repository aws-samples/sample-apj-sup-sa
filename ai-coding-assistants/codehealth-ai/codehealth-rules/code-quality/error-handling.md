# Error Handling Assessment - Detailed Steps

## Purpose
Evaluate error handling patterns for completeness, consistency, and resilience.

---

## Step 1: Detect Error Handling Coverage

### Unhandled Promise Rejections (Node.js/Browser)
```bash
# Async functions without try/catch
grep -rn "async " --include="*.{ts,js}" . | grep -v "try\|catch\|.catch"

# Promise chains without .catch()
grep -rn "\.then(" --include="*.{ts,js}" . | grep -v "\.catch\|try"
```

### Missing Error Propagation
```bash
# Empty catch blocks
grep -rn "catch.*{" -A2 --include="*.{ts,js,java,py}" . | grep -B1 "^\s*}"

# Catch that only logs (swallows error)
grep -rn "catch" -A3 --include="*.{ts,js}" . | grep "console\.\(log\|error\)" | grep -v "throw\|reject\|next("
```

---

## Step 2: Error Handling Pattern Assessment

| Pattern | Good Practice | Anti-Pattern |
|---------|--------------|-------------|
| Catch specificity | Catch specific error types | Catch all (`catch(e)` / bare `except:`) |
| Error propagation | Re-throw or translate to domain error | Swallow silently |
| Error information | Include context (what failed, input that caused it) | Generic "Something went wrong" |
| Retry logic | Exponential backoff for transient failures | No retry, immediate failure |
| Circuit breaker | Stop calling failing services | Keep hammering failing endpoint |
| Graceful degradation | Return cached/default when dependency fails | Hard crash |
| Error boundaries | Isolate failure to affected component (React) | Entire app crashes |
| Validation | Validate at boundaries (API input) | Deep exception from invalid data |

---

## Step 3: Classify Findings

| Severity | Pattern | Impact |
|----------|---------|--------|
| 🔴 Critical | Unhandled rejection that crashes process | Production outage |
| 🔴 Critical | No global error handler | Silent failures |
| 🟠 High | Swallowed errors hiding bugs | Hard to debug production issues |
| 🟠 High | Missing retry for transient failures | Unnecessary user-facing errors |
| 🟡 Medium | Inconsistent error response format | Poor DX for API consumers |
| 🟡 Medium | Missing input validation | Deep stack traces exposed |
| 🔵 Info | Error messages not user-friendly | UX improvement opportunity |
| 🔵 Info | Missing structured logging for errors | Observability gap |

---

## Step 4: Check for Global Error Handling

| Framework | Global Handler | Detection |
|-----------|---------------|-----------|
| Express | `app.use((err, req, res, next) => ...)` | Error middleware registered |
| NestJS | `@Catch()` exception filter | Global filter registered |
| FastAPI | `@app.exception_handler()` | Exception handler decorated |
| Django | `MIDDLEWARE` with process_exception | Middleware present |
| Spring Boot | `@ControllerAdvice` | Global exception handler class |
| React | `<ErrorBoundary>` | Error boundary component wrapping app |
| Next.js | `_error.tsx` or `error.tsx` | Error page present |

Flag if no global handler is found.

---

## Step 5: Generate Report

```markdown
## 🛡️ Error Handling Assessment

### Coverage Score: {score}/100

### Summary
| Category | Count | Status |
|----------|-------|--------|
| Unhandled async operations | {n} | {🔴/🟠/🟡/✅} |
| Swallowed errors | {n} | {🔴/🟠/🟡/✅} |
| Missing retry logic | {n} | {🔴/🟠/🟡/✅} |
| Global error handler | {present/missing} | {✅/🔴} |
| Input validation coverage | {%} | {🔴/🟠/🟡/✅} |
| Error boundaries (frontend) | {n}/{needed} | {✅/🟡} |

### Critical Gaps
| # | File | Issue | Risk | Recommendation |
|---|------|-------|------|---------------|
| 1 | {file}:{line} | Unhandled rejection | Process crash | Add try/catch |
| 2 | {file}:{line} | Empty catch block | Silent data corruption | Log + re-throw |

### Recommended Error Handling Architecture
{Tech-stack-specific recommendations for a robust error handling strategy}
```

---

## Completion
Output to `dep-analysis-docs/analysis/error-handling.md`
