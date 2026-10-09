# Version Freshness Analysis - Detailed Steps

## Purpose
Determine how current each dependency is and identify staleness risks.

---

## Step 1: Query Latest Versions

For each direct dependency, determine the latest available version:

### Node.js
```bash
# Check outdated packages
npm outdated --json 2>/dev/null
# Returns: current, wanted (within semver range), latest (absolute latest)

# Or per-package:
npm view {package} version
npm view {package} time --json  # publication dates
```

### Python
```bash
# Check outdated
pip list --outdated --format=json 2>/dev/null

# Per-package:
pip index versions {package} 2>/dev/null
```

### Java (Maven)
```bash
mvn versions:display-dependency-updates 2>/dev/null
mvn versions:display-plugin-updates 2>/dev/null
```

### Java (Gradle)
```bash
./gradlew dependencyUpdates 2>/dev/null  # requires ben-manes plugin
```

### Go
```bash
go list -u -m all 2>/dev/null  # shows available updates
```

### Rust
```bash
cargo outdated 2>/dev/null  # requires cargo-outdated
```

### .NET
```bash
dotnet list package --outdated 2>/dev/null
```

### Ruby
```bash
bundle outdated 2>/dev/null
```

---

## Step 2: Classify Freshness

For each dependency, determine staleness level:

| Classification | Criteria | Icon |
|---------------|----------|------|
| Current | Within latest patch of latest minor | 🟢 |
| Stale | 1+ minor versions behind latest | 🟡 |
| Outdated | 1+ major versions behind latest | 🟠 |
| Abandoned | No updates 12+ months OR explicitly deprecated | 🔴 |

### Additional Signals for "Abandoned" Classification:
- Package explicitly marked deprecated in registry
- Repository archived on GitHub
- No commits in 24+ months
- Maintainer has publicly stated EOL
- Replaced by official successor package

---

## Step 3: Assess Maintenance Health

For critical/important dependencies, check maintenance signals:

```bash
# npm: View package metadata
npm view {package} --json 2>/dev/null
# Check: time.modified, maintainers count, repository

# GitHub API (if available):
# - Last commit date
# - Open issues count
# - Contributors count
# - Stars trend
```

### Maintenance Health Indicators:

| Signal | Healthy | Concerning | Risky |
|--------|---------|-----------|-------|
| Last publish | < 6 months | 6-12 months | > 12 months |
| Open issues | Actively triaged | Growing backlog | Abandoned |
| Contributors | Multiple | Single | None active |
| Downloads | Stable/growing | Declining rapidly | Near zero |

---

## Step 4: Version Pinning Analysis

Evaluate the project's version pinning strategy:

```markdown
### Version Pinning Assessment

| Strategy | Count | Percentage | Risk |
|----------|-------|-----------|------|
| Exact (`1.2.3`) | {n} | {%} | Low (but may miss security patches) |
| Caret (`^1.2.3`) | {n} | {%} | Medium |
| Tilde (`~1.2.3`) | {n} | {%} | Low-Medium |
| Range (`>=1.0.0`) | {n} | {%} | High |
| Latest/Star | {n} | {%} | Critical |

**Recommendation**: {recommendation based on findings}
```

---

## Step 5: Generate Freshness Report

```markdown
## 📅 Version Freshness Report

### Summary
| Status | Count | Percentage |
|--------|-------|-----------|
| 🟢 Current | {n} | {%} |
| 🟡 Stale (minor behind) | {n} | {%} |
| 🟠 Outdated (major behind) | {n} | {%} |
| 🔴 Abandoned/Deprecated | {n} | {%} |

### Freshness Score: {score}/100

### 🔴 Abandoned/Deprecated Dependencies
| Package | Current | Latest | Last Updated | Status |
|---------|---------|--------|-------------|--------|
| {pkg} | {ver} | {latest} | {date} | Deprecated: use {alt} |

### 🟠 Major Version Updates Available
| Package | Current | Latest | Major Versions Behind | Breaking Changes |
|---------|---------|--------|---------------------|-----------------|
| {pkg} | {ver} | {latest} | {n} | {summary} |

### 🟡 Minor Version Updates Available
| Package | Current | Latest | Minors Behind |
|---------|---------|--------|--------------|
| {pkg} | {ver} | {latest} | {n} |
```

---

## Step 6: Identify Update Risks

For outdated dependencies, flag potential risks:

| Risk | Description |
|------|-------------|
| Breaking API changes | Major version bump with removed/renamed APIs |
| Peer dep conflicts | Updating one may break peer requirements |
| Cascading updates | Updating A requires updating B, C, D |
| No migration path | No clear upgrade guide available |
| Platform requirements | New version requires Node 18+, Python 3.11+, etc. |

---

## Completion

Output to `dep-analysis-docs/analysis/version-freshness.md`
Proceed automatically to Security Vulnerability Scan.
