# Security Vulnerability Scan - Detailed Steps

## Purpose
Identify known vulnerabilities in all dependencies using CVE databases.

---

## Step 1: Execute Native Audit Commands

Use the package manager's built-in audit capability:

### Node.js (npm)
```bash
npm audit --json 2>/dev/null
# Returns: advisories with severity, CVE, affected versions, patched versions
```

### Node.js (yarn)
```bash
yarn audit --json 2>/dev/null
```

### Node.js (pnpm)
```bash
pnpm audit --json 2>/dev/null
```

### Python
```bash
# pip-audit (recommended)
pip-audit --format=json 2>/dev/null

# safety (alternative)
safety check --json 2>/dev/null

# If neither available, use osv-scanner
osv-scanner --lockfile=requirements.txt 2>/dev/null
```

### Java (Maven)
```bash
# OWASP Dependency Check (if plugin configured)
mvn org.owasp:dependency-check-maven:check 2>/dev/null

# Or use osv-scanner
osv-scanner --lockfile=pom.xml 2>/dev/null
```

### Go
```bash
govulncheck ./... 2>/dev/null

# Or:
osv-scanner --lockfile=go.sum 2>/dev/null
```

### Rust
```bash
cargo audit 2>/dev/null
```

### .NET
```bash
dotnet list package --vulnerable 2>/dev/null
```

### Ruby
```bash
bundle audit check 2>/dev/null
# Or: bundler-audit
```

### Universal (OSV Scanner - works for any ecosystem)
```bash
osv-scanner -r . 2>/dev/null
osv-scanner --lockfile={lockfile} 2>/dev/null
```

---

## Step 2: Cross-Reference Multiple Sources

If native audit is unavailable or incomplete, cross-reference:

1. **GitHub Advisory Database** (GHSA)
2. **National Vulnerability Database** (NVD/CVE)
3. **OSV Database** (Open Source Vulnerabilities)
4. **Snyk Vulnerability DB** (if available)

For each dependency + version pair, check if the version falls within any
known vulnerable range.

---

## Step 3: Classify Findings by Severity

| CVSS Score | Classification | Icon | Action Required |
|-----------|---------------|------|-----------------|
| 9.0 - 10.0 | Critical | 🔴 | Immediate upgrade/patch |
| 7.0 - 8.9 | High | 🟠 | Urgent, plan within days |
| 4.0 - 6.9 | Medium | 🟡 | Plan within sprint |
| 0.1 - 3.9 | Low | 🔵 | Acknowledge, low priority |

### Additional Classification Factors:
- **Known exploit exists**: Escalate by one level
- **Network-accessible**: Escalate if the dependency handles network input
- **Dev-only dependency**: De-escalate by one level (not in production)
- **Deeply transitive** (depth > 3): Note but lower priority

---

## Step 4: Generate Detailed Findings

For each vulnerability found:

```markdown
### {SEVERITY_ICON} {CVE_ID} - {Package}@{Version}

| Field | Value |
|-------|-------|
| **Package** | {package_name} |
| **Installed Version** | {current_version} |
| **Vulnerable Range** | {affected_versions} |
| **Fixed In** | {patched_version} |
| **CVSS Score** | {score} ({severity}) |
| **CWE** | {cwe_id} - {cwe_name} |
| **Exploit Available** | {yes/no} |
| **Direct/Transitive** | {direct or via: parent > child > pkg} |

**Description**: {brief description of the vulnerability}

**Impact**: {what an attacker could achieve}

**Remediation**:
- Upgrade to `{package}@{fixed_version}`
- Or: {alternative remediation if upgrade not possible}
```

---

## Step 5: Supply Chain Risk Assessment

Beyond CVEs, check for supply chain indicators:

| Risk | Check | Severity |
|------|-------|----------|
| Typosquatting | Package name similar to popular package | 🔴 |
| Maintainer compromise | Recent unexpected maintainer change | 🟠 |
| Install scripts | Package runs arbitrary code on install | 🟡 |
| Non-standard registry | Package from private/unknown registry | 🟡 |
| Minimal package | Very few files, recently published | 🟡 |
| Dependency confusion | Internal name matches public package | 🔴 |

```bash
# Check for install scripts (Node.js)
npm view {package} scripts --json 2>/dev/null | grep -E "preinstall|postinstall|install"
```

---

## Step 6: Generate Security Report

```markdown
## 🔒 Security Vulnerability Report

### Summary
| Severity | Count | In Production | Dev Only |
|----------|-------|--------------|----------|
| 🔴 Critical | {n} | {n} | {n} |
| 🟠 High | {n} | {n} | {n} |
| 🟡 Medium | {n} | {n} | {n} |
| 🔵 Low | {n} | {n} | {n} |
| **Total** | **{N}** | **{n}** | **{n}** |

### Security Score: {score}/100
(100 = no known vulnerabilities, 0 = critical unpatched vulns)

### Auto-Fixable
{n} of {N} vulnerabilities can be fixed by running:
\```bash
{audit fix command for the package manager}
\```

### Requires Manual Intervention
{n} vulnerabilities require manual upgrade or replacement.
```

---

## Step 7: Present for Approval

MANDATORY: This is an approval gate.

```
🔒 Security Scan Complete

Found: {critical} critical, {high} high, {medium} medium, {low} low vulnerabilities

{n} are auto-fixable with `{audit fix command}`
{n} require manual intervention

Would you like to:
  A) View detailed findings for all vulnerabilities
  B) View only Critical and High findings
  C) Proceed to optimization (I've reviewed the summary)
  D) Export findings to a separate security report
```

Wait for user response before proceeding to next stage.

---

## Error Handling

| Error | Resolution |
|-------|-----------|
| Audit command not available | Fall back to osv-scanner or manual CVE lookup |
| Network unavailable | Note limitation, use cached/offline data if available |
| Lock file missing | Warn that results may be incomplete without exact versions |
| Too many findings (>100) | Summarize by severity, offer detailed export |
