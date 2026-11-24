# Optimization Report Generation - Detailed Steps

## Purpose
Produce the final comprehensive report combining all analysis and optimization findings.

---

## Step 1: Gather All Artifacts

Load outputs from all executed stages:
- `dep-analysis-docs/discovery/repo-metadata.md`
- `dep-analysis-docs/discovery/tech-stack-report.md`
- `dep-analysis-docs/discovery/manifest-inventory.md`
- `dep-analysis-docs/analysis/dependency-graph.md`
- `dep-analysis-docs/analysis/version-freshness.md`
- `dep-analysis-docs/analysis/security-findings.md`
- `dep-analysis-docs/analysis/license-report.md` (if executed)
- `dep-analysis-docs/analysis/weight-analysis.md` (if executed)
- `dep-analysis-docs/optimization/strategy.md`
- `dep-analysis-docs/optimization/update-recommendations.md`
- `dep-analysis-docs/optimization/consolidation.md` (if executed)
- `dep-analysis-docs/optimization/migration-paths/` (if executed)

---

## Step 2: Generate Executive Summary

Compute summary metrics:

```markdown
## Executive Summary

| Metric | Value | Assessment |
|--------|-------|-----------|
| Overall Risk Score | {score}/100 | {LOW/MEDIUM/HIGH/CRITICAL} |
| Total Dependencies | {n} direct / {n} transitive | {ok/high} |
| Security Vulnerabilities | {n} total ({critical} critical) | {status} |
| Outdated Packages | {n} ({%} of total) | {status} |
| Deprecated Packages | {n} | {status} |
| License Risks | {n} | {status} |
| Estimated Remediation | {hours/days} | — |

### Health Grade: {A/B/C/D/F}

Grading Scale:
- A: 0-10 risk score, no critical findings
- B: 11-30 risk score, no critical CVEs
- C: 31-50 risk score, some attention needed
- D: 51-80 risk score, significant remediation needed
- F: 81+ risk score, critical security exposure
```

---

## Step 3: Compile Full Report

Follow the output format defined in `common/output-format.md`.

The final report at `dep-analysis-docs/optimization-report.md` must include:

1. **Header**: Repository name, date, tech stack
2. **Executive Summary**: Key metrics and health grade
3. **P0 Actions**: Critical security fixes with exact commands
4. **P1 Actions**: Urgent updates (deprecated, high CVEs)
5. **P2 Actions**: Planned major updates with migration links
6. **P3 Actions**: Opportunistic improvements (size, consolidation)
7. **Update Batches**: Copy of batch commands from update-recommendations
8. **Health Dashboard**: Visual summary of dependency states
9. **Recommendations**: Long-term practices (CI tooling, policies)
10. **Appendix**: Links to detailed reports in subdirectories

---

## Step 4: Generate Actionable Next Steps

```markdown
## Recommended Next Steps

### Immediate (This Week)
- [ ] Run Batch 1 (patches): `{command}`
- [ ] Fix {n} critical CVEs (see P0 section)
- [ ] Review {n} deprecated package replacements

### Short-Term (This Sprint)
- [ ] Execute Batch 2 (minor updates)
- [ ] Plan migration for {deprecated_pkg}
- [ ] Set up Dependabot/Renovate for automated updates

### Medium-Term (This Quarter)
- [ ] Complete Batch 3 major updates (see migration guides)
- [ ] Consolidate {category} libraries
- [ ] Add dependency audit to CI pipeline

### Ongoing Practices
- [ ] Enable automated security alerts
- [ ] Monthly dependency freshness review
- [ ] Quarterly major version assessment
- [ ] Annual license compliance audit
```

---

## Step 5: Final Presentation

Present the report to the user:

```
╔══════════════════════════════════════════════════════════════════╗
║           ✅ DEPENDENCY ANALYSIS COMPLETE                        ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Health Grade: {grade}                                           ║
║  Risk Score:   {score}/100 ({classification})                    ║
║                                                                  ║
║  Findings:                                                       ║
║    🔴 {n} Critical  🟠 {n} High  🟡 {n} Medium  🔵 {n} Low      ║
║                                                                  ║
║  Reports Generated:                                              ║
║    → dep-analysis-docs/optimization-report.md (full report)      ║
║    → dep-analysis-docs/analysis/ (detailed findings)             ║
║    → dep-analysis-docs/optimization/ (action plans)              ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  Would you like me to:                                           ║
║    A) Execute Batch 1 safe patches now                           ║
║    B) Walk through the critical findings                         ║
║    C) Generate a PR-ready branch with all safe updates           ║
║    D) We're done - I'll review the reports                       ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## Step 6: Update State (Final)

Mark all stages as complete in `dep-analysis-docs/state.md`.
Log final interaction in `dep-analysis-docs/audit.md`.

---

## Optional: CI Integration Snippet

If user wants to integrate this analysis into CI:

### GitHub Actions
```yaml
name: Dependency Health Check
on:
  schedule:
    - cron: '0 9 * * 1'  # Weekly Monday 9am
  workflow_dispatch:

jobs:
  dep-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Audit dependencies
        run: |
          npm audit --json > audit-results.json
          npm outdated --json > outdated-results.json
      - name: Check for critical issues
        run: |
          CRITICAL=$(jq '.metadata.vulnerabilities.critical' audit-results.json)
          if [ "$CRITICAL" -gt 0 ]; then
            echo "::error::Critical vulnerabilities found!"
            exit 1
          fi
```

### GitLab CI
```yaml
dependency-check:
  stage: test
  script:
    - npm audit --audit-level=critical
    - npm outdated || true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
```
