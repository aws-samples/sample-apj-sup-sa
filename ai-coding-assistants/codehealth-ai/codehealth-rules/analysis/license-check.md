# License Compliance Check - Detailed Steps

## Purpose
Identify license types for all dependencies and flag compatibility issues.

---

## Step 1: Extract License Information

### Node.js
```bash
# Using license-checker (if available)
npx license-checker --json 2>/dev/null

# Manual: check package.json "license" field
npm view {package} license 2>/dev/null
```

### Python
```bash
# Using pip-licenses (if available)
pip-licenses --format=json 2>/dev/null

# Manual:
pip show {package} 2>/dev/null | grep "License"
```

### Java (Maven)
```bash
mvn license:third-party-report 2>/dev/null
# Or check pom.xml <licenses> sections
```

### Go
```bash
# Check go.sum modules against known license databases
go-licenses check ./... 2>/dev/null
```

### Rust
```bash
cargo license 2>/dev/null  # requires cargo-license
```

---

## Step 2: Classify Licenses

| Category | Licenses | Risk for Proprietary |
|----------|----------|---------------------|
| Permissive | MIT, BSD-2, BSD-3, Apache-2.0, ISC, Unlicense, 0BSD | ✅ Low |
| Weak Copyleft | LGPL-2.1, LGPL-3.0, MPL-2.0, EPL-2.0 | 🟡 Medium (linking rules) |
| Strong Copyleft | GPL-2.0, GPL-3.0, AGPL-3.0 | 🔴 High (viral) |
| Network Copyleft | AGPL-3.0 | 🔴 Critical (SaaS trigger) |
| Custom/Unknown | Non-standard, "SEE LICENSE" | 🟡 Review Required |
| No License | No license file or declaration | 🟠 Risky (all rights reserved) |

---

## Step 3: Check Compatibility

Based on project's own license, check compatibility:

| Project License | Can Use GPL? | Can Use AGPL? | Can Use LGPL? |
|----------------|-------------|--------------|---------------|
| MIT | ⚠️ Output becomes GPL | ❌ No (SaaS) | ✅ Dynamic link OK |
| Apache-2.0 | ⚠️ Complex | ❌ No | ✅ Dynamic link OK |
| GPL-3.0 | ✅ Yes | ⚠️ Network trigger | ✅ Yes |
| Proprietary | ❌ No | ❌ No | ✅ Dynamic link OK |

---

## Step 4: Generate License Report

```markdown
## 📄 License Compliance Report

### Summary
| License Type | Count | Risk Level |
|-------------|-------|-----------|
| Permissive (MIT, BSD, Apache) | {n} | ✅ |
| Weak Copyleft (LGPL, MPL) | {n} | 🟡 |
| Strong Copyleft (GPL) | {n} | 🔴 |
| Network Copyleft (AGPL) | {n} | 🔴 |
| Unknown/Custom | {n} | 🟡 |
| No License | {n} | 🟠 |

### ⚠️ Compatibility Issues
| Package | License | Issue | Recommendation |
|---------|---------|-------|---------------|
| {pkg} | GPL-3.0 | Incompatible with proprietary | Find alternative |
| {pkg} | AGPL-3.0 | SaaS distribution trigger | Find alternative |
| {pkg} | Unknown | Cannot verify compliance | Contact author |
```

---

## Step 5: Present Findings

Report findings and ask if user needs deeper analysis on any flagged packages.
