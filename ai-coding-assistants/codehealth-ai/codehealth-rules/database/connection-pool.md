# Connection & Pool Analysis - Detailed Steps

## Purpose
Ensure database connection management is properly configured for the deployment model.

---

## Step 1: Detect Connection Configuration

Search for connection settings:

```bash
# Connection strings (DO NOT LOG VALUES — mask credentials)
grep -rn "DATABASE_URL\|DB_HOST\|POSTGRES_\|MYSQL_\|MONGO_URI" --include=".env*" .
grep -rn "connectionString\|dataSource\|pool" --include="*.{ts,js,py,java,yml,yaml}" .
```

---

## Step 2: Pool Configuration Assessment

| Setting | Recommended | Check |
|---------|-------------|-------|
| Min pool size | 2-5 (depends on workload) | Is it configured? |
| Max pool size | CPU cores × 2 + disk spindles | Is it appropriate? |
| Connection timeout | 5-10 seconds | Prevent hanging |
| Idle timeout | 30-60 seconds | Release unused |
| Max lifetime | 30 minutes | Prevent stale connections |

### By Deployment Model:

| Model | Max Pool Size Guidance | Notes |
|-------|----------------------|-------|
| Single server | CPU × 2 | One pool instance |
| Horizontally scaled | Total pool / instances | Avoid exhausting DB max_connections |
| Serverless (Lambda) | 1-5 per instance | Use connection proxy (RDS Proxy) |
| Kubernetes pods | Total pool / max replicas | Account for scaling |

---

## Step 3: Common Issues

| Issue | Detection | Fix |
|-------|-----------|-----|
| Pool too large | max > DB max_connections / app_instances | Reduce pool size |
| No timeout | Missing connectionTimeoutMillis | Add timeout |
| Connection leak | Open without close in error path | Add finally/using block |
| Pool per request | New pool created in handler | Move to module scope |
| Missing SSL | No ssl/tls config for production DB | Enable SSL |
| Hardcoded credentials | Password in source code | Use env vars / secrets manager |

---

## Step 4: Generate Recommendations

```markdown
## 🔌 Connection Pool Report

### Current Configuration
| Setting | Value | Assessment |
|---------|-------|-----------|
| Pool Size | {n} | {ok/too_large/too_small} |
| Timeout | {n}ms | {ok/missing/too_short} |
| SSL | {enabled/disabled} | {ok/risk} |

### Recommendations
| # | Change | Reason | Priority |
|---|--------|--------|----------|
| 1 | Set max pool to {n} | Current {m} exceeds safe limit | 🟠 |
| 2 | Add connection timeout | No timeout configured | 🟡 |
| 3 | Enable SSL | Unencrypted DB traffic | 🔴 |
```
