# Roadmap Generation - Detailed Steps

## Purpose
Produce a time-based optimization roadmap synthesizing all findings into an actionable plan.

---

## Step 1: Aggregate All Findings by Priority

Collect from all phase outputs:

| Source | P0 | P1 | P2 | P3 | P4 | P5 |
|--------|----|----|----|----|----|----|
| Phase 2: Dependencies | CVEs | Deprecated | Major behind | License | Weight | Minor |
| Phase 3: Database | SQL injection | Missing constraints | N+1, indexes | Caching | Pool tune | Schema style |
| Phase 4: Code Quality | Blocking bugs | Swallowed errors | Complexity | Dead code | Duplication | Style |
| Phase 5: API | Auth bypass | Breaking changes | Design issues | Docs gaps | Resilience | Naming |
| Phase 6: Infrastructure | Exposed secrets | Open SGs | IaC gaps | Docker | CI/CD | Config |

---

## Step 2: Generate Time-Based Roadmap

```markdown
# 🗺️ Optimization Roadmap

Generated: {timestamp}
Repository: {repo_name}
Health Score: {score}/100 → Target: {target}/100

---

## 🚨 Week 1: Emergency Actions (P0)
*Focus: Eliminate active security exposure*

- [ ] **Rotate exposed secrets** ({n} found in Phase 6)
  - Files affected: {list}
  - Migrate to: {secrets_manager}
- [ ] **Patch critical CVEs** ({n} from Phase 2)
  - Command: `{audit_fix_command}`
  - Manual fixes: {list}
- [ ] **Fix SQL injection vectors** ({n} from Phase 3)
  - Files: {list with line numbers}
  - Fix: Parameterize all queries

**Estimated effort**: {hours} hours
**Health score after**: {projected_score}/100

---

## ⚡ Weeks 2-3: Urgent Fixes (P1)
*Focus: Prevent imminent breakage and data integrity issues*

- [ ] **Replace deprecated packages** ({n} from Phase 2)
  - {package} → {replacement} (see migration guide)
- [ ] **Add missing DB constraints** ({n} from Phase 3)
  - Migration: {specific ALTER TABLE statements}
- [ ] **Fix breaking API changes** ({n} from Phase 5)
  - Strategy: {versioning / deprecation / backward-compat}
- [ ] **Fix missing error boundaries** ({n} from Phase 4)
  - Critical paths without error handling: {list}

**Estimated effort**: {days} days
**Health score after**: {projected_score}/100

---

## 📈 Sprint 2: Performance (P2)
*Focus: Measurable latency and efficiency improvements*

- [ ] **Add database indexes** ({n} from Phase 3)
  ```sql
  {index_creation_statements}
  ```
- [ ] **Fix N+1 queries** (Top 5 by request frequency)
  - {list with file:line and fix summary}
- [ ] **Optimize O(n²) hotspots** ({n} from Phase 4)
  - {list with estimated improvement}
- [ ] **Parallelize sequential I/O** ({n} from Phase 4)

**Estimated effort**: {days} days
**Expected improvement**: -{x}ms p95 latency, -{y}% DB load

---

## 🛡️ Sprint 3: Reliability (P3)
*Focus: Resilience and operational stability*

- [ ] **Add retry logic** for {n} external calls
- [ ] **Implement circuit breakers** for {services}
- [ ] **Add health check endpoints** (liveness + readiness)
- [ ] **Fix error handling gaps** ({n} swallowed errors)
- [ ] **Add caching layer** for {hot_data_patterns}
- [ ] **Tune connection pools** (see database optimization plan)

**Estimated effort**: {days} days

---

## 🧹 Quarter: Maintainability (P4)
*Focus: Reduce long-term maintenance burden*

- [ ] **Remove dead code** ({n} files, {lines} LOC)
  - Safe deletion script: `dep-analysis-docs/optimization/dead-code-removal.sh`
- [ ] **Reduce complexity hotspots** (Top 10 functions)
  - Target: All functions CC < 15
- [ ] **Major dependency upgrades** (Batch 3)
  - Migration guides: `dep-analysis-docs/optimization/migration-paths/`
- [ ] **API documentation** to {target}% coverage
- [ ] **Docker optimization** (multi-stage, non-root, size reduction)
- [ ] **IaC improvements** (modules, tagging, state management)

**Estimated effort**: {weeks} weeks

---

## 🎯 Ongoing: Optimization (P5)
*Focus: Continuous improvement practices*

- [ ] **Bundle size optimization** (target: <{n}KB)
- [ ] **Consolidate duplicate libraries** ({category}: {current} → {standard})
- [ ] **Minor dependency updates** (monthly cycle)
- [ ] **Code deduplication** ({n} extraction opportunities)
- [ ] **Schema optimization** (normalization review)
- [ ] **Set up automated dependency management** (Dependabot/Renovate)

---

## 📊 Progress Tracking

| Milestone | Target Date | Health Score Target | Key Metric |
|-----------|------------|--------------------|-----------| 
| P0 Complete | Week 1 | {score} | 0 critical vulns |
| P1 Complete | Week 3 | {score} | 0 deprecated deps |
| P2 Complete | Sprint 2 | {score} | p95 < {x}ms |
| P3 Complete | Sprint 3 | {score} | 99.9% uptime |
| P4 Complete | Quarter end | {score} | CC avg < 10 |

---

## 🔄 Sustaining Practices

After roadmap completion, maintain health with:

1. **Weekly**: Automated dependency security scan in CI
2. **Monthly**: Dependency freshness review (minor updates)
3. **Quarterly**: Major version assessment + complexity review
4. **Per-PR**: Code quality gates (complexity, coverage, bundle size)
5. **Per-Sprint**: Tech debt budget (20% of capacity)
```

---

## Step 3: Present for Final Approval

```
🗺️ Optimization Roadmap Complete

Timeline: {weeks} weeks to target health score {target}/100
Total estimated effort: {total_hours} hours across {phases} priority levels

The full roadmap is at: dep-analysis-docs/optimization/roadmap.md
Detailed plans for each area are in their respective files.

What would you like to do?
  A) Start executing Week 1 actions now
  B) Export as team-shareable document
  C) Create GitHub/Jira issues from the roadmap
  D) We're done — I'll work through it manually
```

---

## Completion
Output to `dep-analysis-docs/optimization/roadmap.md`
Final state update in `dep-analysis-docs/state.md`
Close audit log in `dep-analysis-docs/audit.md`
