# Breaking Change Detection - Detailed Steps

## Purpose
Detect changes that would break existing API consumers.

---

## Step 1: Identify API Contract Source

| Source | Location | Priority |
|--------|----------|----------|
| OpenAPI spec | `openapi.yaml`, `swagger.json` | Highest (formal contract) |
| GraphQL schema | `schema.graphql`, SDL files | High |
| Proto files | `*.proto` | High |
| TypeScript types | Exported interface/type definitions | Medium |
| Route definitions | Controller/handler files | Medium (implicit contract) |

---

## Step 2: Compare Against Previous Version

If git history available:
```bash
# Compare current vs previous version of API spec
git diff HEAD~10..HEAD -- openapi.yaml schema.graphql *.proto
git log --oneline --all -- openapi.yaml schema.graphql *.proto | head -20
```

---

## Step 3: Breaking Change Rules

### REST API Breaking Changes
| Change | Breaking? | Severity |
|--------|-----------|----------|
| Remove endpoint | 🔴 Yes | Critical |
| Rename endpoint | 🔴 Yes | Critical |
| Change HTTP method | 🔴 Yes | Critical |
| Remove response field | 🔴 Yes | High |
| Change response field type | 🔴 Yes | High |
| Add required request field | 🔴 Yes | High |
| Change auth requirements | 🔴 Yes | Critical |
| Add optional request field | ✅ No | Safe |
| Add response field | ✅ No | Safe |
| Add new endpoint | ✅ No | Safe |

### GraphQL Breaking Changes
| Change | Breaking? | Severity |
|--------|-----------|----------|
| Remove type/field | 🔴 Yes | Critical |
| Change field type | 🔴 Yes | Critical |
| Add required argument | 🔴 Yes | High |
| Remove enum value | 🔴 Yes | High |
| Deprecate field | ✅ No | Info (warning) |
| Add optional field | ✅ No | Safe |
| Add enum value | ⚠️ Maybe | If client uses exhaustive match |

### gRPC Breaking Changes
| Change | Breaking? | Severity |
|--------|-----------|----------|
| Remove field number | 🔴 Yes | Critical (wire format) |
| Change field type | 🔴 Yes | Critical |
| Rename service/method | 🔴 Yes | Critical |
| Remove enum value | 🔴 Yes | High |
| Add new field | ✅ No | Safe (proto3) |
| Add new service | ✅ No | Safe |

---

## Step 4: Detect Versioning Strategy

| Strategy | Detection | Assessment |
|----------|-----------|-----------|
| URL versioning | `/v1/users`, `/v2/users` | Good for REST |
| Header versioning | `Accept: application/vnd.api.v2+json` | Good, less visible |
| No versioning | No version indicators | 🟠 Risk |
| Sunset headers | `Sunset:` header configured | Good practice |
| Deprecation markers | `@deprecated` in schema | Good practice |

---

## Step 5: Generate Report

```markdown
## ⚠️ Breaking Change Detection Report

### Summary
| Category | Breaking | Potentially Breaking | Safe |
|----------|----------|---------------------|------|
| Endpoint changes | {n} | {n} | {n} |
| Schema changes | {n} | {n} | {n} |
| Auth changes | {n} | {n} | {n} |

### Breaking Changes Found
| # | Change | Endpoint/Type | Impact | Mitigation |
|---|--------|--------------|--------|-----------|
| 1 | {description} | {location} | {who breaks} | {versioning/deprecation plan} |

### Versioning Status
- Current strategy: {strategy}
- Recommendation: {if no versioning, recommend one}

### Migration Support
- Deprecation notices: {present/missing}
- Migration guide: {present/missing}
- Sunset timeline: {defined/undefined}
```

---

## Completion
Output to `dep-analysis-docs/analysis/breaking-changes.md`
