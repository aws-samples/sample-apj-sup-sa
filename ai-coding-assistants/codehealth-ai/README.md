# 🩺 CodeHealth AI

**Adaptive Codebase Analysis & Optimization Workflow for AI Coding Assistants**

CodeHealth AI is a structured, 7-phase workflow that dynamically pulls any repository, detects its tech stack, and runs comprehensive analysis across dependencies, database queries, code quality, API contracts, and infrastructure — producing a prioritized optimization roadmap.

Grounded in the [AWS AI-DLC (AI-Driven Life Cycle)](https://github.com/awslabs/aidlc-workflows) adaptive workflow methodology.

---

## What It Does

```
Phase 1: DISCOVERY          → Repo pull + Tech stack auto-detection
Phase 2: DEPENDENCY ANALYSIS→ Dep graph, version freshness, CVEs, licenses
Phase 3: DATABASE & QUERY   → Schema health, N+1 queries, SQL optimization, indexing
Phase 4: CODE QUALITY       → Complexity hotspots, anti-patterns, dead code, perf issues
Phase 5: API & CONTRACTS    → Design review, breaking changes, resilience patterns
Phase 6: INFRA & CONFIG     → Secrets scan, IaC quality, Docker, CI/CD, env hygiene
Phase 7: OPTIMIZATION       → Unified priority matrix + time-based roadmap
```

Each phase adapts to what's detected — if no database is found, Phase 3 is skipped. If no API surface exists, Phase 5 is skipped. The workflow is always non-destructive (read-only) until you explicitly approve changes in Phase 7.

---

## Features

- **Tech-stack-first detection** — identifies 15+ languages, 30+ frameworks, databases, ORMs, IaC tools, and CI/CD before any analysis runs
- **SQL query optimization** — execution plan analysis, 10 rewriting techniques, ORM-specific patterns (Prisma, Django, SQLAlchemy, Hibernate)
- **Language best practices** — comprehensive catalog for TypeScript, Python, Java, Go, Rust, Ruby, PHP
- **Approval gates** — follows AI-DLC principle of human-in-the-loop at critical decision points
- **Audit trail** — complete interaction log with ISO timestamps
- **Opt-in extensions** — supply chain security, performance budgets
- **16 injection prompts** — ready-to-use prompts for triggering specific analysis phases

---

## Setup

### Kiro IDE

Copy the steering file and rule details into your workspace:

```bash
# From your project root:
mkdir -p .kiro/steering .kiro/codehealth-rules

# Copy the steering file
cp steering/codehealth-ai.md .kiro/steering/

# Copy all rule details
cp -r codehealth-rules/* .kiro/codehealth-rules/
```

Then in Kiro chat, reference `#codehealth-ai` to trigger the workflow, or simply ask:
> "Run CodeHealth AI on this repository"

### Claude Code (claude.ai/code)

```bash
# Option 1: Copy into Claude Code's rules directory
mkdir -p .claude/rules
cp steering/codehealth-ai.md .claude/rules/
cp -r codehealth-rules .claude/

# Option 2: Add to CLAUDE.md as project knowledge
cat steering/codehealth-ai.md >> CLAUDE.md
```

Then tell Claude:
> "Follow the CodeHealth AI workflow in .claude/rules/codehealth-ai.md to analyze this repository"

### Amazon Q Developer / Other AI Assistants

The workflow is plain markdown — works with any AI assistant that can read project files:

1. Copy `steering/` and `codehealth-rules/` folders into your project
2. Point the assistant to `steering/codehealth-ai.md` as the entry point
3. Use any of the 16 injection prompts from `codehealth-rules/prompts/injection-prompts.md`

---

## Directory Structure

```
codehealth-ai/
├── README.md                         ← You are here
├── steering/
│   └── codehealth-ai.md             ← Master workflow (7-phase entry point)
└── codehealth-rules/
    ├── common/                       ← Shared (process, tech signatures, output format)
    ├── discovery/                    ← Phase 1: Repo pull & detection (6 files)
    ├── analysis/                     ← Phase 2: Dependency analysis (6 files)
    ├── database/                     ← Phase 3: Database & SQL optimization (7 files)
    ├── code-quality/                 ← Phase 4: Code quality & performance (7 files)
    ├── api-contracts/                ← Phase 5: API & interface contracts (3 files)
    ├── infra-config/                 ← Phase 6: Infrastructure & configuration (4 files)
    ├── optimization/                 ← Phase 7: Optimization & roadmap (8 files)
    ├── extensions/                   ← Opt-in extensions (4 files)
    └── prompts/                      ← 16 ready-to-use injection prompts
```

---

## Usage Examples

### Full Analysis
```
Run CodeHealth AI on https://github.com/your-org/your-repo
```

### Security-Only Quick Scan
```
Run a security-focused scan: check for exposed secrets, CVEs, and IaC misconfigurations
```

### Database Optimization Only
```
Run Phase 3 (Database & Query Optimization) — focus on N+1 queries, missing indexes, SQL injection
```

---

## Output

The workflow generates a `codehealth-docs/` directory (gitignored) with:

- **`optimization-report.md`** — Comprehensive findings with prioritized roadmap
- **`state.md`** — Phase tracking with timestamps
- **`audit.md`** — Complete interaction log

---

## Methodology

Follows [AWS AI-DLC](https://github.com/awslabs/aidlc-workflows) principles:

| Principle | Implementation |
|-----------|---------------|
| Adaptive Execution | Only phases relevant to detected tech stack run |
| Transparent Planning | Detection results shown before analysis begins |
| User Control | Skip any phase, adjust priorities, override detections |
| Approval Gates | Human confirmation at security findings, strategy selection |
| Non-Destructive | Phases 1-6 are strictly read-only |
| Complete Audit | Every interaction logged with ISO timestamps |

---

## Supported Tech Stacks

| Category | Coverage |
|----------|----------|
| Languages | TypeScript, JavaScript, Python, Java, Kotlin, Go, Rust, Ruby, PHP, C#, Swift, Dart |
| Frameworks | React, Next.js, Angular, Vue, NestJS, Express, Django, Flask, FastAPI, Spring Boot, Rails |
| Databases | PostgreSQL, MySQL, SQLite, MongoDB, Redis, DynamoDB, Elasticsearch |
| ORMs | Prisma, TypeORM, SQLAlchemy, Django ORM, Hibernate, ActiveRecord, GORM, Drizzle |
| IaC | Terraform, AWS CDK, CloudFormation, Pulumi, Serverless Framework |
| CI/CD | GitHub Actions, GitLab CI, Jenkins, CircleCI, AWS CodeBuild |

---

## License

This sample code is made available under the MIT-0 license. See the LICENSE file.
