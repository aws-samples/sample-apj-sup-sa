# Dynamic Injection Prompts

These prompts are designed to be injected at each workflow stage. They can be
used programmatically or manually to trigger the full-stack analysis workflow.

Reference: 7-Phase workflow (Discovery → Dependencies → Database → Code Quality → API → Infra → Optimization)

---

## Prompt 1: Full Workflow Trigger

```
Analyze dependencies for the repository at {REPO_URL_OR_PATH}.

Follow the dependency analysis workflow in .kiro/steering/dep-analysis-workflow.md:
1. DISCOVERY: Clone/access the repo, detect the tech stack, collect all manifests
2. ANALYSIS: Build dependency graph, check version freshness, scan for vulnerabilities
3. OPTIMIZATION: Generate prioritized update recommendations with migration paths

Start with the welcome message, then proceed through each phase.
```

---

## Prompt 2: Repository Pull Only

```
Pull the repository at {REPO_URL_OR_PATH}.
Follow .kiro/dep-analysis-rule-details/discovery/repo-acquisition.md steps.
Report: branch, last commit date, file count, directory structure (depth 2),
and whether this is a monorepo.
Do NOT proceed to analysis yet.
```

---

## Prompt 3: Tech Stack Detection Only

```
Scan this repository and identify the full technology stack.
Follow .kiro/dep-analysis-rule-details/discovery/tech-stack-detection.md steps.
Use .kiro/dep-analysis-rule-details/common/tech-stack-signatures.md for detection patterns.

For each detected technology, report:
- Technology name and version (if determinable)
- Confidence level (HIGH/MEDIUM/LOW)
- Evidence (which files indicated this)

Group findings into: Languages, Frameworks, Package Managers, Infrastructure, CI/CD.
Present findings and wait for my confirmation before proceeding.
```

---

## Prompt 4: Dependency Analysis (Post-Detection)

```
The tech stack has been confirmed as: {TECH_STACK}
Package manager: {PACKAGE_MANAGER}
Manifest files: {MANIFEST_FILES}

Run the full Analysis phase:
1. Build the complete dependency tree (direct + transitive)
2. Check version freshness for all direct dependencies
3. Scan for known CVEs using available audit tools
4. Report findings grouped by severity

Follow .kiro/dep-analysis-rule-details/analysis/ rule files.
Present security findings and wait for my approval before optimization.
```

---

## Prompt 5: Security-Only Scan

```
Run a security-focused dependency analysis on this repository.
Skip freshness and weight analysis - focus exclusively on:
1. Known CVEs in all dependencies (direct + transitive)
2. Deprecated packages with known vulnerabilities
3. Supply chain risks (if extension enabled)

Follow .kiro/dep-analysis-rule-details/analysis/security-scan.md steps.
Report all findings with: CVE ID, CVSS score, affected package, fix version.
```

---

## Prompt 6: Optimization Path Generation

```
Given these analysis findings for the {TECH_STACK} project:
- {N} critical vulnerabilities requiring immediate patches
- {N} outdated dependencies (major versions behind)
- {N} deprecated packages needing replacement
- {N} license compatibility concerns

Generate an optimization plan following:
.kiro/dep-analysis-rule-details/optimization/strategy-selection.md
.kiro/dep-analysis-rule-details/optimization/update-recommendations.md

Output:
1. Priority matrix (P0-P5) with effort estimates
2. Batch update commands for {PACKAGE_MANAGER}
3. Migration guides for breaking changes
4. Consolidation opportunities if applicable
```

---

## Prompt 7: Generate Final Report

```
All analysis stages are complete. Generate the final optimization report.
Follow .kiro/dep-analysis-rule-details/optimization/report-generation.md.
Follow .kiro/dep-analysis-rule-details/common/output-format.md for formatting.

Write the report to dep-analysis-docs/optimization-report.md.
Include: executive summary, all findings by priority, update commands,
migration guides, and recommended next steps.
```

---

## Prompt 8: Monorepo Analysis

```
This is a monorepo using {MONOREPO_TOOL} with {N} workspace packages.

Run dependency analysis across ALL workspace packages:
1. Detect shared vs package-specific dependencies
2. Find version inconsistencies across packages
3. Check for circular dependencies between internal packages
4. Run security scan on the combined dependency tree
5. Identify consolidation opportunities (same dep at different versions)

Follow the full workflow but apply monorepo-specific handling at each stage.
```

---

## Prompt 9: CI Integration

```
Generate a CI pipeline configuration that runs dependency health checks.
Target CI system: {github-actions|gitlab-ci|jenkins|codebuild}

The pipeline should:
1. Run on a weekly schedule + on PRs that modify lock files
2. Audit for critical/high vulnerabilities (fail the build if found)
3. Check for outdated dependencies (warning only)
4. Generate a dependency health report as an artifact
5. Alert via {slack|email|github-issue} if critical findings

Follow .kiro/dep-analysis-rule-details/optimization/report-generation.md
for the CI integration snippet section.
```

---

## Prompt 10: Resume Workflow

```
Resume the dependency analysis workflow from where it left off.
Check dep-analysis-docs/state.md for current progress.
Load the audit log from dep-analysis-docs/audit.md for context.
Continue from the next incomplete stage.
```


---

## Prompt 11: Database & Query Analysis

```
Run Phase 3 (Database & Query Optimization) on this repository.
The tech stack has been confirmed as: {TECH_STACK}
Database: {DATABASE} via {ORM}
Migration tool: {MIGRATION_TOOL}

Execute:
1. Schema analysis — check for missing PKs, FKs, type issues, normalization
2. Query pattern detection — find N+1 queries, unbounded fetches, SQL injection
3. Index analysis — identify missing indexes for frequent query patterns
4. Migration health — check for destructive operations without rollbacks
5. Data access anti-patterns — chatty services, missing transactions, no caching

Follow .kiro/dep-analysis-rule-details/database/ rule files.
Present findings grouped by severity, with code-level fixes.
```

---

## Prompt 12: Code Quality & Performance Analysis

```
Run Phase 4 (Code Quality & Performance) on this repository.
Tech stack: {TECH_STACK}
Source directories: {SRC_DIRS}

Execute:
1. Complexity analysis — cyclomatic + cognitive for all functions, flag >10 CC
2. Anti-pattern detection — language-specific issues ({LANGUAGE} patterns)
3. Dead code detection — unused exports, unreachable code, stale feature flags
4. Performance hotspots — O(n²) loops, sequential awaits, memory issues
5. Error handling assessment — coverage, swallowed errors, missing boundaries

Follow .kiro/dep-analysis-rule-details/code-quality/ rule files.
Present top 10 hotspots with before/after code examples.
```

---

## Prompt 13: API & Interface Contracts Analysis

```
Run Phase 5 (API & Contracts) on this repository.
API type: {REST/GraphQL/gRPC}
Framework: {FRAMEWORK}
Spec file: {OPENAPI_PATH or "none"}

Execute:
1. API design review — method correctness, naming, response consistency
2. Breaking change detection — compare current vs previous spec/routes
3. Contract validation — implementation matches declared spec
4. Documentation coverage — endpoints with/without descriptions
5. Resilience check — rate limiting, circuit breakers, timeouts, retries

Follow .kiro/dep-analysis-rule-details/api-contracts/ rule files.
Present API maturity score and prioritized improvements.
```

---

## Prompt 14: Infrastructure & Configuration Analysis

```
Run Phase 6 (Infrastructure & Configuration) on this repository.
IaC framework: {TERRAFORM/CDK/CF/NONE}
Containers: {DOCKER/COMPOSE/K8S/NONE}
CI/CD: {GITHUB_ACTIONS/GITLAB/JENKINS/NONE}

Execute:
1. Secrets scan — CRITICAL: find all exposed credentials (mask values in output)
2. IaC quality — security misconfigs, overly permissive IAM, missing encryption
3. Docker analysis — base image freshness, running as root, image size
4. CI/CD review — unpinned actions, missing security steps, secrets in logs
5. Environment hygiene — .env management, config sprawl, safe defaults

Follow .kiro/dep-analysis-rule-details/infra-config/ rule files.
NEVER output actual secret values. Report by location and type only.
```

---

## Prompt 15: Full 7-Phase Workflow (Comprehensive)

```
Run the FULL codebase analysis workflow on the repository at {REPO_URL_OR_PATH}.

Follow .kiro/steering/dep-analysis-workflow.md for the complete 7-phase workflow:

Phase 1: DISCOVERY — Pull repo, detect tech stack, DB, APIs, infrastructure
Phase 2: DEPENDENCIES — Graph, freshness, CVEs, licenses, bundle weight
Phase 3: DATABASE — Schema, queries, indexes, migrations, anti-patterns
Phase 4: CODE QUALITY — Complexity, anti-patterns, dead code, performance, errors
Phase 5: API — Design review, breaking changes, contracts, resilience
Phase 6: INFRASTRUCTURE — Secrets, IaC, Docker, CI/CD, environment config
Phase 7: OPTIMIZATION — Unified priority matrix, recommendations, roadmap

Start with the welcome message. Execute all applicable phases based on what's
detected. Present approval gates at each required checkpoint.
Generate the final optimization roadmap with time-based action plan.
```

---

## Prompt 16: Targeted Quick Scan (Subset of Phases)

```
Run a quick health check on this repository focusing on:
- {SECURITY/PERFORMANCE/MAINTAINABILITY/ALL}

Skip full analysis depth. Focus only on:
- Critical CVEs and exposed secrets (always)
- {If PERFORMANCE: N+1 queries, O(n²) hotspots, bundle size}
- {If MAINTAINABILITY: Complexity hotspots, dead code, outdated deps}
- {If SECURITY: Injection vectors, auth issues, IaC misconfigs}

Generate a one-page summary with the top 10 findings and immediate actions.
Keep it concise — no full roadmap needed.
```
