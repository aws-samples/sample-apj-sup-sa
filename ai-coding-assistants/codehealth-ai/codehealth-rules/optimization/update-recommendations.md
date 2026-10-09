# Update Recommendations - Detailed Steps

## Purpose
Generate specific, actionable update commands grouped into safe execution batches.

---

## Step 1: Generate Per-Dependency Recommendations

For each dependency requiring action, produce:

```markdown
### {package_name}: {current_version} → {target_version}

| Field | Value |
|-------|-------|
| Priority | {P0-P5} |
| Update Type | {patch/minor/major/replacement} |
| Breaking Changes | {yes/no - summary} |
| Migration Guide | {URL or "none available"} |
| Automated Fix | {yes/no - codemod name if yes} |
| Cascading Updates | {list of deps that also need updating} |
| Test Coverage | {existing tests cover this area: yes/no/partial} |
| Effort Estimate | {Low: <1h / Medium: 1-4h / High: 4h+} |
```

---

## Step 2: Group into Execution Batches

### Batch 1: Safe Patches (ZERO RISK)
Criteria: Patch version increments only, no breaking changes

```bash
# Node.js (npm)
npm update {pkg1} {pkg2} {pkg3}

# Node.js (specific versions)
npm install {pkg1}@{patch_ver} {pkg2}@{patch_ver}

# Python
pip install --upgrade {pkg1}=={patch_ver} {pkg2}=={patch_ver}

# Or update requirements.txt and run:
pip install -r requirements.txt --upgrade

# Go
go get {pkg1}@v{patch_ver}
go mod tidy

# Rust
cargo update -p {pkg1}
```

**Expected outcome**: All tests pass, no code changes needed.
**Rollback**: Revert lock file changes.

---

### Batch 2: Minor Updates (LOW RISK - Test Required)
Criteria: Minor version increments, new features but backward-compatible

```bash
# Node.js
npm install {pkg1}@{minor_ver} {pkg2}@{minor_ver}

# Python
pip install {pkg1}~={minor_ver}

# Java (Maven)
# Update version in pom.xml, then:
mvn clean verify

# Java (Gradle)
# Update version in build.gradle, then:
./gradlew build
```

**Expected outcome**: Tests pass, possible deprecation warnings.
**Action after**: Run full test suite, review deprecation warnings.
**Rollback**: Revert manifest + lock file.

---

### Batch 3: Major Updates (BREAKING - Migration Required)
Criteria: Major version bumps with known breaking changes

For each major update, provide:

```markdown
#### Updating {package} from v{major_old} to v{major_new}

**Breaking Changes:**
1. {change description}
2. {change description}

**Migration Steps:**
1. Update version: `npm install {package}@{new_ver}`
2. Apply codemod: `npx {codemod_name}` (if available)
3. Manual changes required:
   - Replace `{old_api}` with `{new_api}`
   - Update config: `{config_change}`
4. Run tests: `npm test`
5. Fix failing tests

**Files Likely Affected:**
- {file_pattern_1}
- {file_pattern_2}

**Estimated Effort**: {hours}
```

---

### Batch 4: Replacements (DEPRECATED → ALTERNATIVE)
Criteria: Package is deprecated, needs replacement with different package

```markdown
#### Replacing {old_package} with {new_package}

**Why**: {old_package} is deprecated/unmaintained since {date}.
**Official Successor**: {new_package} (or "Community consensus: {pkg}")

**Migration Steps:**
1. Install new: `npm install {new_package}`
2. Update imports:
   - Old: `import { X } from '{old_package}'`
   - New: `import { X } from '{new_package}'`
3. API differences:
   - {old_api} → {new_api}
4. Remove old: `npm uninstall {old_package}`
5. Run tests

**Effort**: {estimate}
```

---

## Step 3: Generate Combined Update Script

Produce a single script the user can execute:

```bash
#!/bin/bash
# Dependency Update Script
# Generated: {timestamp}
# Repository: {repo_name}
# Strategy: {chosen_strategy}

set -e

echo "=== Batch 1: Safe Patches ==="
{batch_1_commands}

echo "Running tests after Batch 1..."
{test_command}

echo "=== Batch 2: Minor Updates ==="
{batch_2_commands}

echo "Running tests after Batch 2..."
{test_command}

echo "=== Complete ==="
echo "Batch 3 (major) and Batch 4 (replacements) require manual migration."
echo "See dep-analysis-docs/optimization/migration-paths/ for guides."
```

---

## Step 4: Present Recommendations

```markdown
## 📦 Update Recommendations

### Batch Summary
| Batch | Updates | Risk | Auto-Fixable | Effort |
|-------|---------|------|-------------|--------|
| 1: Patches | {n} | Zero | ✅ Yes | {est} |
| 2: Minor | {n} | Low | ✅ Yes (test after) | {est} |
| 3: Major | {n} | Medium-High | ❌ Manual | {est} |
| 4: Replace | {n} | Medium | ❌ Manual | {est} |

### Quick Actions
Would you like me to:
  A) Execute Batch 1 (patches only - safe, reversible)
  B) Execute Batch 1 + 2 (patches + minor - low risk)
  C) Generate the full update script (you run manually)
  D) Generate migration guides for Batch 3 + 4 only
  E) Just save the report (no execution)
```

---

## Step 5: Automated Tooling Recommendations

Suggest tools to prevent future staleness:

| Tool | Platform | Purpose |
|------|----------|---------|
| Dependabot | GitHub | Automated version bump PRs |
| Renovate | GitHub/GitLab/Bitbucket | Highly configurable auto-updates |
| npm-check-updates | Node.js | CLI for checking updates |
| pip-audit | Python | Security audit in CI |
| cargo-audit | Rust | Security audit in CI |
| OWASP Dependency-Check | Java/.NET | Enterprise vuln scanning |

Recommend adding to CI pipeline if not already present.
