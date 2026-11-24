# API Design Review - Detailed Steps

## Purpose
Assess API design maturity, consistency, and adherence to best practices.

---

## Step 1: Discover API Endpoints

### REST APIs
```bash
# Express/Fastify/Hono route patterns
grep -rn "app\.\(get\|post\|put\|patch\|delete\)\|router\.\(get\|post\|put\|patch\|delete\)" --include="*.{ts,js}" .

# Spring Boot controllers
grep -rn "@GetMapping\|@PostMapping\|@PutMapping\|@DeleteMapping\|@RequestMapping" --include="*.java" .

# Django views
grep -rn "path(\|url(" --include="urls.py" .

# FastAPI endpoints
grep -rn "@app\.\(get\|post\|put\|patch\|delete\)\|@router\.\(get\|post\|put\|patch\|delete\)" --include="*.py" .

# Go (gin/echo/fiber)
grep -rn "\.GET(\|\.POST(\|\.PUT(\|\.DELETE(" --include="*.go" .
```

### GraphQL
```bash
# Schema definitions
find . -name "*.graphql" -o -name "*.gql" | head -20
grep -rn "type Query\|type Mutation\|type Subscription" --include="*.{graphql,gql,ts,js}" .
```

### gRPC
```bash
find . -name "*.proto" | head -20
grep -rn "^service \|^rpc " --include="*.proto" .
```

---

## Step 2: REST API Design Assessment

### HTTP Method Correctness
| Endpoint | Expected Method | Actual | Issue |
|----------|----------------|--------|-------|
| Get resource | GET | {actual} | {ok/mismatch} |
| Create resource | POST | {actual} | {ok/mismatch} |
| Full update | PUT | {actual} | {ok/mismatch} |
| Partial update | PATCH | {actual} | {ok/mismatch} |
| Delete resource | DELETE | {actual} | {ok/mismatch} |

### URL Naming Conventions
| Rule | Check |
|------|-------|
| Plural nouns for collections | `/users` not `/user` |
| Kebab-case for multi-word | `/user-profiles` not `/userProfiles` |
| No verbs in URLs | `/users` not `/getUsers` |
| Hierarchical resources | `/users/:id/orders` not `/orders?userId=:id` |
| Consistent across API | All endpoints follow same convention |

### Response Consistency
| Check | Assessment |
|-------|-----------|
| Consistent envelope format | All responses use same wrapper structure |
| Consistent error format | Errors have code, message, details |
| Pagination format | Consistent cursor/offset across list endpoints |
| Date format | ISO 8601 everywhere |
| Null handling | Consistent (omit field vs null vs empty string) |

---

## Step 3: GraphQL Design Assessment

| Check | Issue | Severity |
|-------|-------|----------|
| God types | Type with >20 fields | 🟡 Medium |
| Missing DataLoader | Resolver makes DB call without batching | 🟠 High |
| No depth limit | Queries can nest infinitely | 🔴 Critical |
| No complexity limit | Single query can request entire graph | 🔴 Critical |
| Overfetching at schema | Fields expose internal IDs/details | 🟡 Medium |
| Missing input validation | Mutations accept any input shape | 🟠 High |
| N+1 in resolvers | Same as DB N+1 but at resolver level | 🟠 High |

---

## Step 4: API Design Maturity Score

| Level | Score | Criteria |
|-------|-------|----------|
| 1 - Ad Hoc | 1/5 | Inconsistent naming, methods, responses |
| 2 - Emerging | 2/5 | Some conventions but not enforced |
| 3 - Defined | 3/5 | Consistent patterns, documented |
| 4 - Managed | 4/5 | Versioned, contract-tested, monitored |
| 5 - Optimized | 5/5 | Full lifecycle management, evolution strategy |

---

## Step 5: Generate Report

```markdown
## 🌐 API Design Review

### API Surface
| Protocol | Endpoints | Assessment |
|----------|-----------|-----------|
| REST | {n} endpoints | Maturity: {score}/5 |
| GraphQL | {n} types, {n} queries | Maturity: {score}/5 |
| gRPC | {n} services, {n} rpcs | Maturity: {score}/5 |

### Design Issues
| # | Issue | Endpoint | Severity | Recommendation |
|---|-------|----------|----------|---------------|
| 1 | {issue} | {endpoint} | {severity} | {fix} |

### Consistency Score: {%}
(Percentage of endpoints following established conventions)
```

---

## Completion
Output to `dep-analysis-docs/analysis/api-design.md`
