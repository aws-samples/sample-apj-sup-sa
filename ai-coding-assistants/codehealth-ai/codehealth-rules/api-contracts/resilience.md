# Rate Limiting & Resilience - Detailed Steps

## Purpose
Check for resilience patterns protecting the API from abuse and cascading failures.

---

## Step 1: Rate Limiting Detection

### Check for rate limiting implementation:
```bash
# Express middleware
grep -rn "rateLimit\|rate-limit\|throttle\|express-rate-limit" --include="*.{ts,js}" .

# NestJS
grep -rn "@Throttle\|ThrottlerModule\|ThrottlerGuard" --include="*.ts" .

# Django
grep -rn "throttle_classes\|UserRateThrottle\|AnonRateThrottle" --include="*.py" .

# Spring Boot
grep -rn "@RateLimiter\|RateLimitBucket\|Resilience4j" --include="*.java" .

# API Gateway config
grep -rn "throttle\|rateLimit\|quota" --include="*.{yml,yaml,json}" .
```

### Assessment:
| Check | Status | Finding |
|-------|--------|---------|
| Rate limiting exists | {yes/no} | {details} |
| Per-user limits | {yes/no} | Prevents single user abuse |
| Per-IP limits | {yes/no} | Prevents anonymous abuse |
| Global limits | {yes/no} | Prevents overall overload |
| Graceful 429 responses | {yes/no} | Includes Retry-After header |

---

## Step 2: Circuit Breaker Detection

```bash
# Check for circuit breaker patterns
grep -rn "circuitBreaker\|circuit-breaker\|CircuitBreaker\|opossum\|resilience4j\|polly" \
  --include="*.{ts,js,java,py,cs}" .
```

### For each external dependency (HTTP call, DB, queue):
| External Call | Circuit Breaker | Timeout | Retry | Assessment |
|--------------|----------------|---------|-------|-----------|
| {service} | {yes/no} | {yes/no} | {yes/no} | {🟢/🟡/🔴} |

---

## Step 3: Timeout Configuration

```bash
# HTTP client timeouts
grep -rn "timeout\|connectTimeout\|readTimeout\|requestTimeout" --include="*.{ts,js,java,py}" .
```

### Check:
- Every outbound HTTP call has a timeout configured
- Database queries have statement timeouts
- External service calls have reasonable timeouts (not infinite)

---

## Step 4: Retry Logic

Check for retry patterns on transient failures:
```bash
grep -rn "retry\|retries\|maxRetries\|backoff\|exponential" --include="*.{ts,js,java,py}" .
```

### Retry Best Practices:
| Practice | Check |
|----------|-------|
| Exponential backoff | Not fixed intervals |
| Jitter | Random component to prevent thundering herd |
| Max retries | Bounded (not infinite retry) |
| Idempotency | Retried operations are safe to repeat |
| Circuit breaker integration | Stop retrying when circuit open |

---

## Step 5: Health Checks

```bash
# Health check endpoints
grep -rn "/health\|/healthz\|/ready\|/live\|/ping" --include="*.{ts,js,java,py,go}" .
```

### Required health checks:
| Type | Purpose | Status |
|------|---------|--------|
| Liveness | Process is alive | {present/missing} |
| Readiness | Can serve traffic (deps healthy) | {present/missing} |
| Deep health | All dependencies reachable | {present/missing} |

---

## Step 6: Generate Report

```markdown
## 🛡️ API Resilience Report

### Resilience Score: {score}/100

### Summary
| Pattern | Status | Coverage |
|---------|--------|----------|
| Rate Limiting | {✅/❌} | {details} |
| Circuit Breakers | {✅/❌} | {n}/{total} external calls covered |
| Timeouts | {✅/❌} | {n}/{total} calls have timeouts |
| Retry Logic | {✅/❌} | {n}/{total} transient-failure paths |
| Health Checks | {✅/❌} | {types present} |
| Graceful Shutdown | {✅/❌} | {drain connections on SIGTERM} |

### Missing Resilience Patterns
| # | Pattern | Where Needed | Impact of Absence | Effort |
|---|---------|-------------|-------------------|--------|
| 1 | Circuit Breaker | {service call} | Cascading failure | Medium |
| 2 | Timeout | {DB query} | Thread exhaustion | Low |
| 3 | Rate Limit | {public endpoint} | DDoS vulnerability | Low |

### Recommendations
{Prioritized list of resilience improvements with implementation guidance}
```

---

## Completion
Output to `dep-analysis-docs/analysis/api-resilience.md`
