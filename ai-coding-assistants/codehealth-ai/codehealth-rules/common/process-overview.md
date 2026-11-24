# CodeHealth AI — Process Overview

## Workflow Summary

This workflow follows the AI-DLC (AI-Driven Life Cycle) adaptive methodology
from [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows).

The core principle: **The workflow adapts to the work, not the other way around.**

## Seven-Phase Architecture

```
┌────────────────┐
│  PHASE 1       │
│  DISCOVERY     │  Repo pull, tech stack detection, DB/API/Infra detection
│  (ALWAYS)      │
└───────┬────────┘
        │
        ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│  PHASE 2       │  │  PHASE 3       │  │  PHASE 4       │  │  PHASE 5       │
│  DEPENDENCIES  │  │  DATABASE &    │  │  CODE QUALITY  │  │  API &         │
│                │  │  QUERIES       │  │  & PERFORMANCE │  │  CONTRACTS     │
│  • Dep Graph   │  │  • Schema      │  │  • Complexity  │  │  • Design      │
│  • Freshness   │  │  • N+1 Queries │  │  • Anti-Ptrns  │  │  • Breaking    │
│  • CVEs        │  │  • Indexes     │  │  • Dead Code   │  │  • Resilience  │
│  • Licenses    │  │  • Migrations  │  │  • Perf Spots  │  │  • Docs        │
│  • Weight      │  │  • Anti-Ptrns  │  │  • Errors      │  │  • Contracts   │
│  (CONDITIONAL) │  │  (CONDITIONAL) │  │  (CONDITIONAL) │  │  (CONDITIONAL) │
└───────┬────────┘  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘
        │                   │                   │                   │
        └───────────┬───────┴───────────┬───────┘                   │
                    │                   │                           │
                    ▼                   ▼                           │
          ┌────────────────┐           │                           │
          │  PHASE 6       │◄──────────┴───────────────────────────┘
          │  INFRA &       │
          │  CONFIG        │
          │  • Secrets     │
          │  • IaC Quality │
          │  • Docker      │
          │  • CI/CD       │
          │  • Env Hygiene │
          │  (CONDITIONAL) │
          └───────┬────────┘
                  │
                  ▼
          ┌────────────────┐
          │  PHASE 7       │
          │  OPTIMIZATION  │  Unified priority matrix, roadmap, action plan
          │  & ROADMAP     │
          │  (ALWAYS)      │
          └────────────────┘
```

## Phase Execution Logic

| Phase | Executes When | Skips When |
|-------|--------------|------------|
| 1. Discovery | ALWAYS | Never |
| 2. Dependencies | Package manifests found | Config-only/IaC-only repo |
| 3. Database & Query | DB driver/ORM detected | No database usage |
| 4. Code Quality | Application source present | Config-only repo |
| 5. API & Contracts | Route handlers/specs detected | No API surface |
| 6. Infra & Config | IaC/Docker/CI detected | No infrastructure files |
| 7. Optimization | ALWAYS (synthesizes findings) | Never |

## Approval Gates

Following AI-DLC principles, explicit user approval is required at:

1. **Tech Stack Confirmation** (Phase 1) — before any analysis begins
2. **Phase Plan Approval** (Phase 1) — which phases will execute
3. **Security Findings Review** (Phase 2) — after vulnerability scan
4. **Query Pattern Review** (Phase 3) — after N+1 and injection detection
5. **Secrets Scan Review** (Phase 6) — after credential detection
6. **Optimization Strategy** (Phase 7) — before generating recommendations
7. **Roadmap Approval** (Phase 7) — before any execution

## Adaptive Depth Levels

Analysis depth adapts to project complexity:

| Level | Trigger | Phases 2-6 Depth |
|-------|---------|-----------------|
| Minimal | <20 deps, single file, CLI tool | Direct deps, basic checks |
| Standard | Typical project, 20-200 deps | Full tree, all standard checks |
| Comprehensive | Enterprise/monorepo, 200+ deps | Full tree + advanced checks |

## Non-Destructive Principle

CRITICAL: Phases 1-6 are STRICTLY READ-ONLY.
- No files modified
- No packages installed
- No lock files regenerated
- No database queries executed
- Source modifications only in Phase 7 after explicit user approval

## State Management

All workflow state tracked in `codehealth-docs/state.md`:
- Current phase and stage
- Completed stages with timestamps
- Skipped stages with rationale
- Detection results (tech stack, DB, API, infra)
- User decisions and approvals
- Health scores at each checkpoint

## Audit Trail

ALL interactions logged in `codehealth-docs/audit.md`:
- User inputs (complete, never summarized)
- AI decisions and rationale
- Findings and classifications
- Approval/rejection decisions
- Timestamps in ISO 8601 format

## Output Directory Structure

```
codehealth-docs/
├── discovery/                        # Phase 1 outputs
│   ├── repo-metadata.md
│   ├── tech-stack-report.md
│   ├── manifest-inventory.md
│   ├── database-detection.md
│   ├── api-detection.md
│   └── infra-detection.md
├── analysis/                         # Phases 2-6 outputs
│   ├── dependency-graph.md           # Phase 2
│   ├── version-freshness.md          # Phase 2
│   ├── security-findings.md          # Phase 2
│   ├── license-report.md             # Phase 2
│   ├── weight-analysis.md            # Phase 2
│   ├── schema-analysis.md            # Phase 3
│   ├── query-patterns.md             # Phase 3
│   ├── index-analysis.md             # Phase 3
│   ├── data-access-patterns.md       # Phase 3
│   ├── migration-health.md           # Phase 3
│   ├── complexity-report.md          # Phase 4
│   ├── anti-patterns.md              # Phase 4
│   ├── dead-code.md                  # Phase 4
│   ├── performance-hotspots.md       # Phase 4
│   ├── error-handling.md             # Phase 4
│   ├── duplication.md                # Phase 4
│   ├── api-design.md                 # Phase 5
│   ├── breaking-changes.md           # Phase 5
│   ├── api-resilience.md             # Phase 5
│   ├── secrets-scan.md               # Phase 6
│   ├── iac-quality.md                # Phase 6
│   ├── docker-analysis.md            # Phase 6
│   └── env-config.md                 # Phase 6
├── optimization/                     # Phase 7 outputs
│   ├── strategy.md
│   ├── dependency-updates.md
│   ├── database-plan.md
│   ├── code-refactoring-plan.md
│   ├── api-improvements.md
│   ├── infra-remediation.md
│   ├── migration-paths/
│   │   └── {package-name}.md
│   └── roadmap.md
├── optimization-report.md            # Final comprehensive report
├── state.md                          # Workflow state tracking
└── audit.md                          # Complete interaction log
```
