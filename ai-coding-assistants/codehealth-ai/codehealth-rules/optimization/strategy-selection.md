# Optimization Strategy Selection - Detailed Steps

## Purpose
Determine the optimization priority order based on analysis findings.

---

## Step 1: Load Analysis Findings

Read all findings from the Analysis phase:
- `dep-analysis-docs/analysis/security-findings.md`
- `dep-analysis-docs/analysis/version-freshness.md`
- `dep-analysis-docs/analysis/dependency-graph.md`
- `dep-analysis-docs/analysis/license-report.md` (if executed)
- `dep-analysis-docs/analysis/weight-analysis.md` (if executed)

---

## Step 2: Apply Priority Matrix

| Priority | Category | Criteria | SLA |
|----------|----------|----------|-----|
| P0 | Security Critical | CVSS 9.0+, known exploit, in production path | Fix within 24h |
| P1 | Security High + Deprecated | CVSS 7.0-8.9 OR package deprecated/EOL | Fix within 1 week |
| P2 | Major Version Behind | 1+ major versions behind, accumulating tech debt | Plan within 1 sprint |
| P3 | License Risk | Incompatible license in production dependencies | Review within 2 sprints |
| P4 | Performance/Size | Heavy deps with lighter alternatives | Opportunistic |
| P5 | Minor Freshness | Minor versions behind, no security impact | Next dependency update cycle |

---

## Step 3: Calculate Overall Risk Score

```
Risk Score = (Critical_CVEs × 40) + (High_CVEs × 20) + (Deprecated × 15) +
             (Major_Behind × 10) + (License_Issues × 8) + (Medium_CVEs × 5)

Classification:
  0-20:   LOW RISK      → Healthy project, maintenance updates only
  21-50:  MEDIUM RISK   → Some attention needed, plan updates
  51-80:  HIGH RISK     → Significant issues, prioritize remediation
  81+:    CRITICAL RISK → Immediate action required, security exposure
```

---

## Step 4: Determine Optimization Approach

Based on risk score and project context:

### LOW RISK Strategy
- Run all patch updates immediately
- Schedule minor updates for next cycle
- No urgent action required
- Recommend automated dependency update tooling (Dependabot/Renovate)

### MEDIUM RISK Strategy
- Fix all security findings first (P0/P1)
- Batch remaining updates by risk level
- Recommend enabling automated security alerts
- Plan major updates for next quarter

### HIGH RISK Strategy
- Emergency patch for P0 items
- Sprint planning for P1 items
- Create migration plan for deprecated packages
- Consider dependency audit as recurring practice

### CRITICAL RISK Strategy
- Stop current work, address P0 immediately
- Assess blast radius of vulnerabilities
- Check if exploits are actively targeting these CVEs
- Emergency response: patch, deploy, monitor

---

## Step 5: Present Strategy for Approval

MANDATORY: This is an approval gate.

```markdown
## 📋 Optimization Strategy

**Overall Risk Score**: {score} ({classification})

### Proposed Priority Order:

| # | Priority | Action Items | Estimated Effort |
|---|----------|-------------|-----------------|
| 1 | P0 Security | {n} critical patches | {est} hours |
| 2 | P1 Urgent | {n} high + deprecated | {est} hours |
| 3 | P2 Planned | {n} major updates | {est} days |
| 4 | P3 License | {n} license reviews | {est} hours |
| 5 | P4 Performance | {n} replacements | {est} hours |
| 6 | P5 Freshness | {n} minor updates | {est} hours |

**Total Estimated Effort**: {total} hours/days

### Approach:
- {strategy description based on risk level}

Options:
  A) Accept strategy and generate detailed recommendations
  B) Modify priorities (specify changes)
  C) Focus only on security (P0 + P1)
  D) Generate report only (no action recommendations)
```

Wait for user selection before proceeding.

---

## Step 6: Log Decision

Record the user's strategy selection in audit.md with timestamp.
Update state.md with chosen strategy.
