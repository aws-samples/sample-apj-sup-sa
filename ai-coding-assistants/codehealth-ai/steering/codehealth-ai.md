<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

---
inclusion: manual
---

# PRIORITY: CodeHealth AI — Adaptive Codebase Analysis & Optimization

# When user requests dependency analysis, code optimization, security review, or project health assessment, ALWAYS follow this workflow FIRST

## Adaptive Workflow Principle

This workflow dynamically adapts to the target repository and tech stack.
The AI model intelligently assesses what phases are needed based on:

1. Repository source (local, GitHub, GitLab, Bitbucket, or other remote)
2. Detected tech stack (languages, frameworks, package managers, databases, infrastructure)
3. Scope of analysis requested (dependencies, security, performance, database, code quality, all)
4. Risk tolerance and optimization goals
5. Project type (API service, web app, data pipeline, library, monolith, microservices)

---

## MANDATORY: Rule Details Loading

CRITICAL: When performing any phase, you MUST read and use relevant content from rule
detail files located at:

- `.kiro/codehealth-rules/` (primary location)

Common Rules: ALWAYS load common rules at workflow start:

- Load `common/process-overview.md` for workflow overview
- Load `common/tech-stack-signatures.md` for tech stack detection patterns
- Load `common/output-format.md` for report formatting rules

---

## MANDATORY: Welcome Message

When starting the workflow, display:

```
╔══════════════════════════════════════════════════════════════════════╗
║        🩺 CodeHealth AI — Adaptive Codebase Analysis                ║
║              AI-Driven Lifecycle Workflow v2.0                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  Phase 1: DISCOVERY          → Repo pull + Tech stack detection    ║
║  Phase 2: DEPENDENCY ANALYSIS→ Graph, freshness, CVEs, licenses    ║
║  Phase 3: DATABASE & QUERY   → Schema, query patterns, indexing    ║
║  Phase 4: CODE QUALITY       → Anti-patterns, complexity, dead code║
║  Phase 5: API & CONTRACTS    → Interface health, breaking changes  ║
║  Phase 6: INFRA & CONFIG     → IaC drift, secrets, env hygiene    ║
║  Phase 7: OPTIMIZATION       → Unified recommendations + roadmap   ║
╠══════════════════════════════════════════════════════════════════════╣
║  Grounded in: AWS AI-DLC Adaptive Workflow Principles              ║
║  Source: github.com/awslabs/aidlc-workflows                        ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

# Phase 1: DISCOVERY

Purpose: Dynamically pull repository and identify the full technology stack
Focus: Determine WHAT the project uses across all dimensions

Stages:

- Repository Acquisition (ALWAYS)
- Tech Stack Detection (ALWAYS)
- Dependency Manifest Collection (ALWAYS)
- Database Detection (ALWAYS — may yield "none detected")
- API Surface Detection (ALWAYS — may yield "none detected")
- Infrastructure Detection (ALWAYS — may yield "none detected")
- Workspace State Initialization (ALWAYS)

---

## Repository Acquisition (ALWAYS EXECUTE)

1. MANDATORY: Log the user's repository input in state file
2. Load all steps from `discovery/repo-acquisition.md`
3. Execute repository acquisition:
   - If local path → validate directory, check for git
   - If remote URL → clone to workspace
   - If current workspace → use CWD
   - Detect monorepo structure
4. MANDATORY: Log repository metadata (size, commit count, branch, last activity)
5. Present findings and proceed automatically

---

## Tech Stack Detection (ALWAYS EXECUTE)

1. MANDATORY: Load `common/tech-stack-signatures.md`
2. Load all steps from `discovery/tech-stack-detection.md`
3. Execute multi-signal detection covering:
   - Languages & frameworks
   - Package managers
   - Database systems (Postgres, MySQL, MongoDB, Redis, DynamoDB, etc.)
   - ORM/query builders (Prisma, TypeORM, SQLAlchemy, Hibernate, ActiveRecord, etc.)
   - API frameworks & protocols (REST, GraphQL, gRPC, WebSocket)
   - Infrastructure tools (Terraform, CDK, Docker, K8s)
4. Generate comprehensive Tech Stack Report with confidence scores
5. Wait for Explicit Approval: Present detected stack, ask user to confirm or correct
6. MANDATORY: Log confirmed tech stack in state file

---

## Dependency Manifest Collection (ALWAYS EXECUTE)

1. Load all steps from `discovery/manifest-collection.md`
2. Collect ALL dependency manifests for confirmed tech stack
3. For monorepos: recursively collect from all workspace packages
4. Generate manifest inventory with counts
5. Proceed automatically

---

## Database Detection (ALWAYS EXECUTE)

1. Load all steps from `discovery/database-detection.md`
2. Detect database usage through:
   - Connection strings in config files (masked in output)
   - ORM/driver packages in dependencies
   - Migration files (Alembic, Flyway, Knex, Prisma, ActiveRecord)
   - Schema definition files (SQL, Prisma schema, Mongoose models)
   - Docker compose service definitions (postgres, mysql, redis, mongo)
3. Classify: Relational | Document | Key-Value | Graph | Time-Series | Search
4. If none detected: mark as "No database layer" and skip Phase 3

---

## API Surface Detection (ALWAYS EXECUTE)

1. Load all steps from `discovery/api-detection.md`
2. Detect API surface through:
   - Route definitions (Express routes, Spring controllers, Django views)
   - OpenAPI/Swagger specs
   - GraphQL schemas (*.graphql, type definitions)
   - gRPC proto files
   - API Gateway configurations (SAM, serverless.yml)
3. If none detected: mark as "No API surface" and skip Phase 5

---

## Infrastructure Detection (ALWAYS EXECUTE)

1. Load all steps from `discovery/infra-detection.md`
2. Detect infrastructure through:
   - IaC files (Terraform, CDK, CloudFormation, Pulumi)
   - Dockerfile and docker-compose configurations
   - Kubernetes manifests and Helm charts
   - CI/CD pipeline definitions
   - Environment configuration files (.env patterns, config maps)
3. If none detected: mark as "No IaC layer" and skip Phase 6

---

## Workspace State Initialization (ALWAYS EXECUTE)

1. Create `codehealth-docs/state.md` with repo metadata, tech stack, manifest inventory
2. Create `codehealth-docs/audit.md` for logging all interactions
3. Present phase plan: which of Phases 2-6 will execute based on detections
4. Wait for Explicit Approval of the phase plan
5. Proceed to first applicable phase

---

# Phase 2: DEPENDENCY ANALYSIS

Purpose: Deep dependency analysis, vulnerability scanning, and risk assessment
Focus: Understand the dependency landscape and identify problems

Execute IF: Dependencies detected (almost always)
Skip IF: No package manifests found (e.g., pure IaC repo with no application code)

Stages:

- Dependency Graph Construction (ALWAYS)
- Version Freshness Analysis (ALWAYS)
- Security Vulnerability Scan (ALWAYS)
- License Compliance Check (CONDITIONAL)
- Dependency Weight Analysis (CONDITIONAL)
- Circular Dependency Detection (CONDITIONAL)

---

## Dependency Graph Construction (ALWAYS EXECUTE)

1. Load all steps from `analysis/dependency-graph.md`
2. Build full dependency tree (direct + transitive + dev + peer)
3. Calculate metrics (total count, max depth, unique maintainers, fan-out)
4. Generate dependency graph visualization
5. Present summary and proceed

---

## Version Freshness Analysis (ALWAYS EXECUTE)

1. Load all steps from `analysis/version-freshness.md`
2. For each direct dependency: current vs latest, age, pin status, maintenance
3. Classify: 🟢 Current | 🟡 Stale | 🟠 Outdated | 🔴 Abandoned
4. Generate version freshness report
5. Present findings and proceed

---

## Security Vulnerability Scan (ALWAYS EXECUTE)

1. Load all steps from `analysis/security-scan.md`
2. Cross-reference CVE databases (NVD, GitHub Advisory, OSV)
3. Classify: 🔴 Critical | 🟠 High | 🟡 Medium | 🔵 Low
4. For each: CVE ID, description, fix version, remediation path
5. Wait for Explicit Approval before continuing
6. MANDATORY: Log in audit

---

## License Compliance Check (CONDITIONAL)

Execute IF: enterprise/production project OR user requests license analysis
Skip IF: internal tooling OR user opts out

---

## Dependency Weight Analysis (CONDITIONAL)

Execute IF: frontend/browser project OR bundle size concern OR performance optimization
Skip IF: backend-only service

---

## Circular Dependency Detection (CONDITIONAL)

Execute IF: monorepo OR internal packages OR complex graph (>100 direct deps)

---

# Phase 3: DATABASE & QUERY OPTIMIZATION

Purpose: Analyze database schema design, query patterns, and data access efficiency
Focus: Identify N+1 queries, missing indexes, schema issues, and query performance bottlenecks

Execute IF: Database layer detected in Discovery phase
Skip IF: No database usage detected

Stages:

- Schema Analysis (ALWAYS when DB detected)
- Query Pattern Detection (ALWAYS when DB detected)
- SQL Query Optimization (ALWAYS when DB detected)
- Index Analysis (CONDITIONAL)
- Migration Health Check (CONDITIONAL)
- Connection & Pool Analysis (CONDITIONAL)
- Data Access Anti-Pattern Detection (ALWAYS when DB detected)

---

## Schema Analysis (ALWAYS EXECUTE when DB detected)

1. Load all steps from `database/schema-analysis.md`
2. Analyze database schema through:
   - ORM model definitions (Prisma schema, SQLAlchemy models, Hibernate entities)
   - Migration files (extract current schema state)
   - Raw SQL schema files if present
3. Check for:
   - Missing primary keys
   - Missing foreign key constraints (relational only)
   - Overly wide tables (>30 columns)
   - Inappropriate data types (VARCHAR(255) for everything, TEXT for short fields)
   - Missing NOT NULL constraints on required fields
   - Lack of normalization (repeated data across tables)
   - Over-normalization (excessive JOINs required for basic queries)
4. Generate schema health report
5. Present findings and proceed

---

## Query Pattern Detection (ALWAYS EXECUTE when DB detected)

1. Load all steps from `database/query-patterns.md`
2. Scan codebase for query patterns:

   **ORM-based queries:**
   - Eager vs lazy loading configuration
   - N+1 query patterns (loops with individual DB calls)
   - Unbounded queries (SELECT * without LIMIT)
   - Missing WHERE clauses on large tables
   - Excessive JOIN depth (>4 tables)
   - SELECT * instead of specific columns

   **Raw SQL queries:**
   - String concatenation (SQL injection risk → cross-ref with security)
   - Missing parameterized queries
   - Complex subqueries that could be CTEs
   - DISTINCT used to mask duplicate joins
   - ORDER BY on non-indexed columns

   **NoSQL patterns (MongoDB, DynamoDB):**
   - Collection scans without proper query filters
   - Missing compound indexes for common access patterns
   - Oversized documents
   - Unbounded array growth in documents
   - Single-table design anti-patterns (DynamoDB)

3. Classify findings:
   - 🔴 Critical: SQL injection, unbounded queries on large tables, N+1 in hot paths
   - 🟠 High: Missing indexes on filtered columns, lazy loading in loops
   - 🟡 Medium: SELECT *, over-fetching, non-optimal JOIN order
   - 🔵 Info: Style improvements, query readability
4. Present findings
5. Wait for Explicit Approval before continuing

---

## SQL Query Optimization (ALWAYS EXECUTE when DB detected)

1. Load all steps from `database/sql-optimization.md`
2. For each expensive/problematic query identified in Query Pattern Detection:
   - Analyze execution plan (EXPLAIN ANALYZE)
   - Apply rewriting techniques:
     - Correlated subqueries → JOINs
     - IN (subquery) → EXISTS
     - OR conditions → UNION ALL
     - Self-joins → Window functions
     - Functions on columns → sargable rewrites
   - Optimize pagination (cursor-based over OFFSET)
   - Recommend batch operations for bulk work
   - Suggest materialized views for expensive aggregations
   - Apply ORM-specific optimization patterns
   - Provide database-engine-specific tuning (PostgreSQL/MySQL/SQLite)
3. Generate before/after query comparisons with expected improvement
4. Present findings and proceed

---

## Index Analysis (CONDITIONAL)

Execute IF: Relational database detected AND query patterns found
Skip IF: NoSQL-only OR no query issues found

1. Load all steps from `database/index-analysis.md`
2. Analyze indexing strategy:
   - Columns used in WHERE, JOIN, ORDER BY that lack indexes
   - Composite index opportunities (multi-column filters)
   - Unused indexes (defined but never hit by queries)
   - Index bloat (too many indexes slowing writes)
   - Covering index opportunities
   - Partial index candidates (filtered queries on subset of data)
3. Generate index recommendations with CREATE INDEX statements
4. Estimate impact: query improvement factor vs write overhead

---

## Migration Health Check (CONDITIONAL)

Execute IF: Migration files detected (Alembic, Flyway, Knex, Prisma Migrate, etc.)
Skip IF: No migration framework detected

1. Load all steps from `database/migration-health.md`
2. Analyze migration history:
   - Pending migrations not yet applied
   - Destructive migrations without rollback (DROP TABLE, DROP COLUMN)
   - Data migrations mixed with schema migrations
   - Migration ordering conflicts
   - Large data migrations that could lock tables
3. Flag risks and suggest safe migration patterns

---

## Connection & Pool Analysis (CONDITIONAL)

Execute IF: Database connection configuration found
Skip IF: Serverless function with managed connections (e.g., RDS Proxy, PlanetScale)

1. Load all steps from `database/connection-pool.md`
2. Analyze connection management:
   - Pool size configuration vs expected concurrency
   - Connection timeout settings
   - Missing connection retry logic
   - Leaked connections (opened but not closed/returned)
   - Multiple pools to same database (waste)
3. Recommend optimal pool configuration based on deployment model

---

## Data Access Anti-Pattern Detection (ALWAYS EXECUTE when DB detected)

1. Load all steps from `database/anti-patterns.md`
2. Detect common anti-patterns:

   | Anti-Pattern | Detection Signal | Impact |
   |---|---|---|
   | N+1 Queries | Loop containing DB call | Performance: O(n) calls |
   | God Query | Single query >20 JOINs | Unmaintainable, slow |
   | Chatty Service | >5 DB calls per request | Latency accumulation |
   | Missing Pagination | find() without limit/offset | Memory + timeout |
   | Read-Your-Writes | Write then immediate read, no await | Race condition |
   | Fat Models | Business logic in DB models | Testability |
   | Soft Delete Everywhere | is_deleted checks on every query | Complexity + perf |
   | No Caching Layer | Same query repeated per request | Unnecessary DB load |

3. For each finding, provide:
   - Location (file + line range)
   - Severity and performance impact
   - Recommended fix with code example
4. Generate data access report

---

# Phase 4: CODE QUALITY & PERFORMANCE

Purpose: Analyze code for anti-patterns, complexity hotspots, dead code, and performance issues
Focus: Identify maintainability and runtime performance problems beyond dependencies

Execute IF: Application source code present (almost always)
Skip IF: Config-only repo (pure IaC with no app code)

Stages:

- Complexity Analysis (ALWAYS)
- Anti-Pattern Detection (ALWAYS)
- Dead Code & Unused Exports (ALWAYS)
- Performance Hotspot Detection (CONDITIONAL)
- Error Handling Assessment (ALWAYS)
- Code Duplication Analysis (CONDITIONAL)

---

## Complexity Analysis (ALWAYS EXECUTE)

1. Load all steps from `code-quality/complexity-analysis.md`
2. Compute complexity metrics:
   - **Cyclomatic Complexity**: Functions with CC > 10 (warning) or > 20 (critical)
   - **Cognitive Complexity**: Nesting depth, boolean expression complexity
   - **Function Length**: Functions > 50 lines (warning), > 100 lines (critical)
   - **File Length**: Files > 500 lines (warning), > 1000 lines (critical)
   - **Parameter Count**: Functions with > 5 parameters
   - **Class Size**: Classes with > 20 methods or > 10 fields
3. Identify the "complexity hotspots" (top 10 most complex functions)
4. Generate complexity report with refactoring suggestions
5. Present findings and proceed

---

## Anti-Pattern Detection (ALWAYS EXECUTE)

1. Load all steps from `code-quality/anti-patterns.md`
2. Load `code-quality/language-best-practices.md` for comprehensive reference
3. Scan for tech-stack-specific anti-patterns covering:
   - Type safety and idiomatic patterns
   - Async/concurrency issues
   - Performance pitfalls
   - Security violations
   - Framework-specific anti-patterns
   - Language-specific best practices (TS, Python, Java, Go, Rust, Ruby, PHP)
4. Score adherence to language best practices (percentage compliance)
5. Classify by impact (🔴 Critical → 🔵 Info)
6. Present findings

---

## Dead Code & Unused Exports (ALWAYS EXECUTE)

1. Load all steps from `code-quality/dead-code.md`
2. Detect unused code:
   - Exported functions/classes never imported elsewhere
   - Unreachable code after return/throw/break
   - Commented-out code blocks (>10 lines)
   - Unused variables and imports (beyond lint warnings)
   - Feature flags that are always true/false (stale toggles)
   - Test files without assertions (empty tests)
   - Unused dependencies (installed but never imported)
3. Calculate dead code percentage
4. Prioritize by size impact (bytes removable)
5. Generate removal recommendations

---

## Performance Hotspot Detection (CONDITIONAL)

Execute IF: Application handles requests/traffic OR user requests perf analysis
Skip IF: Library/utility project with no runtime component

1. Load all steps from `code-quality/performance-hotspots.md`
2. Identify performance risks:

   **Algorithmic:**
   - O(n²) or worse loops (nested iterations over collections)
   - Redundant computations (same expensive calc repeated in loop)
   - Unoptimized recursion (no memoization, no tail-call)
   - Large array/object cloning where mutation would suffice

   **I/O Bound:**
   - Sequential await where parallel is possible (`Promise.all`)
   - Synchronous file reads in request handlers
   - Missing caching for expensive operations
   - Database queries inside loops (overlaps with Phase 3)

   **Memory:**
   - Unbounded array growth (accumulating without limit)
   - Large object retention in closures
   - Streaming opportunities missed (loading full file into memory)
   - Missing pagination for large data sets

   **Frontend-Specific:**
   - Layout thrashing (read-write-read DOM cycles)
   - Large re-renders (unnecessary component updates)
   - Unoptimized images/assets in bundle
   - Missing code splitting (single mega-bundle)
   - Main thread blocking (heavy computation without Web Worker)

3. Estimate performance impact (latency/memory savings potential)
4. Generate optimization recommendations with code examples

---

## Error Handling Assessment (ALWAYS EXECUTE)

1. Load all steps from `code-quality/error-handling.md`
2. Analyze error handling patterns:
   - Missing error handling (unhandled promise rejections, uncaught exceptions)
   - Swallowed errors (empty catch blocks, errors logged but not propagated)
   - Inconsistent error types (string throws vs Error objects)
   - Missing retry logic for transient failures (network, DB)
   - No graceful degradation patterns
   - Error boundary coverage (React)
   - Missing input validation at service boundaries
3. Classify by blast radius:
   - 🔴 Critical: Unhandled rejection that crashes process, no error boundary
   - 🟠 High: Swallowed errors hiding production issues
   - 🟡 Medium: Inconsistent patterns, missing retries
   - 🔵 Info: Style improvements

---

## Code Duplication Analysis (CONDITIONAL)

Execute IF: Codebase > 10,000 lines OR monorepo with shared logic potential
Skip IF: Small project (<10 files)

1. Load all steps from `code-quality/duplication.md`
2. Detect code duplication:
   - Exact duplicates (copy-paste blocks > 10 lines)
   - Near-duplicates (same logic, different variable names)
   - Repeated patterns across files (extraction candidates)
   - Cross-package duplication in monorepos
3. Calculate duplication percentage
4. Suggest extraction/abstraction opportunities
5. Estimate effort to deduplicate

---

# Phase 5: API & INTERFACE CONTRACTS

Purpose: Analyze API design health, backward compatibility, and contract adherence
Focus: Ensure APIs are well-designed, documented, versioned, and safe to evolve

Execute IF: API surface detected in Discovery phase
Skip IF: No API surface (library, CLI tool, worker process)

Stages:

- API Design Review (ALWAYS when API detected)
- Breaking Change Detection (ALWAYS when API detected)
- Contract Validation (CONDITIONAL)
- API Documentation Coverage (CONDITIONAL)
- Rate Limiting & Resilience (CONDITIONAL)

---

## API Design Review (ALWAYS EXECUTE when API detected)

1. Load all steps from `api-contracts/design-review.md`
2. Analyze API design patterns:

   **REST APIs:**
   - HTTP method correctness (GET for reads, POST for creates, etc.)
   - Consistent resource naming (plural nouns, kebab-case)
   - Proper status code usage (not everything returns 200)
   - Pagination patterns for list endpoints
   - Consistent error response format
   - HATEOAS / hypermedia links (if applicable)
   - Query parameter conventions

   **GraphQL:**
   - Schema design (avoid God types, proper type composition)
   - N+1 resolver patterns (missing DataLoader)
   - Unbounded queries (no depth/complexity limits)
   - Missing input validation on mutations
   - Over-fetching at schema level

   **gRPC:**
   - Proto file organization (one service per file convention)
   - Proper use of oneof, repeated, map types
   - Stream vs unary method selection
   - Field numbering gaps (breaking backward compat)

3. Score API design maturity (1-5 scale)
4. Present findings

---

## Breaking Change Detection (ALWAYS EXECUTE when API detected)

1. Load all steps from `api-contracts/breaking-changes.md`
2. Compare current API surface against:
   - OpenAPI spec (if versioned/committed)
   - Previous schema versions (git history)
   - Published client SDK contracts
3. Detect breaking changes:
   - Removed endpoints/fields
   - Changed response types
   - Required field additions to request bodies
   - Changed authentication requirements
   - Renamed or moved endpoints without redirects
4. Classify: 🔴 Breaking (clients will fail) | 🟡 Potentially breaking | 🔵 Safe

---

## Contract Validation (CONDITIONAL)

Execute IF: OpenAPI spec, GraphQL schema, or Proto files exist
Skip IF: No formal contract definition

1. Load all steps from `api-contracts/contract-validation.md`
2. Validate contracts:
   - Implementation matches declared spec (all routes implemented)
   - Response types match schema definitions
   - Required fields are actually enforced
   - Examples in spec are valid
3. Flag spec drift (implementation diverged from contract)

---

## API Documentation Coverage (CONDITIONAL)

Execute IF: Public API OR team-shared API
Skip IF: Internal-only endpoints

1. Load all steps from `api-contracts/documentation.md`
2. Check documentation completeness:
   - Every endpoint has description
   - Request/response examples present
   - Error responses documented
   - Authentication requirements documented
   - Rate limits documented
3. Calculate documentation coverage percentage

---

## Rate Limiting & Resilience (CONDITIONAL)

Execute IF: Production API handling external traffic
Skip IF: Internal service OR development-only

1. Load all steps from `api-contracts/resilience.md`
2. Check for:
   - Rate limiting implementation (per-user, per-IP)
   - Circuit breaker patterns for downstream calls
   - Timeout configuration for external calls
   - Retry logic with exponential backoff
   - Bulkhead isolation (one slow client doesn't affect others)
   - Health check endpoints
3. Recommend missing resilience patterns

---

# Phase 6: INFRASTRUCTURE & CONFIGURATION

Purpose: Analyze IaC, configuration hygiene, secrets management, and deployment safety
Focus: Identify configuration drift, exposed secrets, and infrastructure risks

Execute IF: Infrastructure files detected in Discovery phase
Skip IF: No IaC, no Dockerfiles, no CI/CD configuration

Stages:

- Secrets & Credentials Scan (ALWAYS)
- IaC Quality Analysis (CONDITIONAL)
- Docker & Container Analysis (CONDITIONAL)
- CI/CD Pipeline Review (CONDITIONAL)
- Environment Configuration Hygiene (ALWAYS)

---

## Secrets & Credentials Scan (ALWAYS EXECUTE)

1. Load all steps from `infra-config/secrets-scan.md`
2. Scan entire repository for exposed secrets:
   - API keys (AWS, GCP, Azure, Stripe, Twilio, SendGrid)
   - Database connection strings with credentials
   - Private keys (RSA, SSH, TLS certificates)
   - JWT secrets and signing keys
   - OAuth client secrets
   - Webhook secrets
   - .env files committed to git
3. Check for proper secrets management:
   - .gitignore covers .env, *.pem, *.key files
   - Secrets referenced via environment variables, not hardcoded
   - Secrets manager integration (AWS Secrets Manager, Vault, etc.)
4. Classify:
   - 🔴 Critical: Exposed production secrets in git history
   - 🟠 High: Hardcoded credentials in config (even if .gitignored)
   - 🟡 Medium: Missing .gitignore patterns for sensitive files
   - 🔵 Info: Recommendations for secrets rotation
5. MANDATORY: NEVER output actual secret values in reports
6. Wait for Explicit Approval (security gate)

---

## IaC Quality Analysis (CONDITIONAL)

Execute IF: Terraform, CDK, CloudFormation, or Pulumi files detected
Skip IF: No IaC

1. Load all steps from `infra-config/iac-quality.md`
2. Analyze IaC quality:

   **Terraform:**
   - Missing state locking configuration
   - Hardcoded values that should be variables
   - Missing resource tagging
   - Overly permissive IAM policies (wildcard *)
   - Missing encryption at rest/in transit
   - Security group rules allowing 0.0.0.0/0
   - Missing lifecycle policies

   **CDK/CloudFormation:**
   - Missing removal policies on stateful resources
   - Hardcoded account IDs or regions
   - Missing parameter validation
   - Stack dependencies and circular references
   - Missing outputs for cross-stack references

   **General IaC:**
   - No module/construct reuse (copy-paste infrastructure)
   - Missing environment separation (dev/staging/prod)
   - Drift risk (manual changes possible without guardrails)

3. Generate IaC quality report with remediation

---

## Docker & Container Analysis (CONDITIONAL)

Execute IF: Dockerfile or docker-compose.yml detected
Skip IF: No container files

1. Load all steps from `infra-config/docker-analysis.md`
2. Analyze Dockerfile quality:
   - Base image freshness (outdated or deprecated images)
   - Running as root (security risk)
   - Multi-stage builds missing (large images)
   - Secrets in build args or ENV
   - Missing health checks
   - Layer ordering efficiency (cache optimization)
   - Unnecessary packages installed
   - Missing .dockerignore
3. Analyze docker-compose:
   - Missing restart policies
   - Hardcoded ports that conflict
   - Missing resource limits (memory, CPU)
   - Volume mount security
4. Generate container optimization report

---

## CI/CD Pipeline Review (CONDITIONAL)

Execute IF: CI/CD configuration detected (GitHub Actions, GitLab CI, etc.)
Skip IF: No CI/CD

1. Load all steps from `infra-config/cicd-review.md`
2. Analyze pipeline quality:
   - Missing security scanning steps
   - Unpinned action/image versions (supply chain risk)
   - Secrets exposed in logs
   - Missing cache steps (slow builds)
   - No deployment gates / approval steps
   - Missing test steps before deploy
   - Overly permissive permissions (write-all)
3. Recommend pipeline improvements

---

## Environment Configuration Hygiene (ALWAYS EXECUTE)

1. Load all steps from `infra-config/env-hygiene.md`
2. Analyze environment configuration:
   - .env.example exists and is up-to-date
   - All environment variables documented
   - Defaults are safe (no production URLs in dev defaults)
   - Type validation on config values (not just string passing)
   - Missing environment-specific overrides
   - Config sprawl (too many places to configure)
3. Recommend configuration consolidation

---

# Phase 7: OPTIMIZATION & ROADMAP

Purpose: Generate a unified optimization plan across all findings from Phases 2-6
Focus: Prioritized, actionable recommendations with effort estimates and a roadmap

Stages:

- Cross-Phase Priority Synthesis (ALWAYS)
- Dependency Update Recommendations (CONDITIONAL — from Phase 2)
- Database Optimization Plan (CONDITIONAL — from Phase 3)
- Code Refactoring Plan (CONDITIONAL — from Phase 4)
- API Improvement Plan (CONDITIONAL — from Phase 5)
- Infrastructure Remediation (CONDITIONAL — from Phase 6)
- Unified Optimization Report (ALWAYS)
- Roadmap Generation (ALWAYS)

---

## Cross-Phase Priority Synthesis (ALWAYS EXECUTE)

1. Load all steps from `optimization/strategy-selection.md`
2. Merge findings from ALL executed phases into unified priority matrix:

   | Priority | Category | Source Phase | Examples |
   |----------|----------|-------------|---------|
   | P0 | Security Critical | Phase 2, 6 | Exposed secrets, Critical CVEs, SQL injection |
   | P1 | Data Integrity | Phase 3, 5 | Missing constraints, breaking API changes |
   | P2 | Performance | Phase 3, 4 | N+1 queries, O(n²) loops, missing indexes |
   | P3 | Reliability | Phase 4, 5, 6 | Missing error handling, no retries, no health checks |
   | P4 | Maintainability | Phase 2, 4 | High complexity, dead code, outdated deps |
   | P5 | Optimization | Phase 2, 3, 4 | Bundle size, query tuning, consolidation |

3. Calculate composite project health score (0-100)
4. Present strategy for approval
5. Wait for Explicit Approval

---

## Dependency Update Recommendations (FROM Phase 2)

1. Load all steps from `optimization/update-recommendations.md`
2. Generate batched update plan:
   - Batch 1: Patch updates (zero risk)
   - Batch 2: Minor updates (low risk)
   - Batch 3: Major updates (breaking changes)
   - Batch 4: Replacements (deprecated → alternative)
3. Include package manager commands

---

## Database Optimization Plan (FROM Phase 3)

1. Load all steps from `optimization/database-optimization.md`
2. Generate database optimization actions:
   - Index creation statements (with impact estimates)
   - Query rewrites (before/after with explanation)
   - Schema migration recommendations
   - Caching layer introduction strategy
   - Connection pool tuning parameters
3. Prioritize by: query frequency × latency improvement potential

---

## Code Refactoring Plan (FROM Phase 4)

1. Load all steps from `optimization/code-refactoring.md`
2. Generate refactoring plan:
   - Complexity reduction targets (function splitting, extract method)
   - Anti-pattern fixes with code examples
   - Dead code removal (files/functions safe to delete)
   - Performance optimizations with before/after
   - Error handling improvements
3. Group by effort: Quick wins (<1h) | Sprint work (1-3 days) | Epics (>1 week)

---

## API Improvement Plan (FROM Phase 5)

1. Load all steps from `optimization/api-improvements.md`
2. Generate API improvement actions:
   - Breaking change mitigation (versioning strategy)
   - Design pattern fixes
   - Documentation gaps to fill
   - Resilience patterns to implement
3. Include migration/versioning strategy for breaking changes

---

## Infrastructure Remediation (FROM Phase 6)

1. Load all steps from `optimization/infra-remediation.md`
2. Generate infrastructure fixes:
   - Secrets rotation + migration to secrets manager
   - IaC fixes (with exact code changes)
   - Docker optimizations (reduced image sizes)
   - CI/CD pipeline hardening
   - Configuration hygiene improvements

---

## Unified Optimization Report (ALWAYS EXECUTE)

1. Load all steps from `optimization/report-generation.md`
2. Generate comprehensive report at `codehealth-docs/optimization-report.md`
3. Include all findings, grouped by priority and source phase
4. Present final report to user

---

## Roadmap Generation (ALWAYS EXECUTE)

1. Load all steps from `optimization/roadmap.md`
2. Generate time-based optimization roadmap:

```markdown
## Optimization Roadmap

### Week 1: Emergency (P0)
- [ ] Rotate exposed secrets
- [ ] Patch critical CVEs
- [ ] Fix SQL injection vectors

### Week 2-3: Urgent (P1)
- [ ] Add missing DB constraints
- [ ] Fix breaking API changes (or version)
- [ ] Replace deprecated packages

### Sprint 2: Performance (P2)
- [ ] Add missing database indexes
- [ ] Fix N+1 queries (top 5 by frequency)
- [ ] Optimize O(n²) hotspots

### Sprint 3: Reliability (P3)
- [ ] Add error handling to uncovered paths
- [ ] Implement retry logic for external calls
- [ ] Add health check endpoints

### Quarter: Maintainability (P4-P5)
- [ ] Reduce complexity in hotspot functions
- [ ] Remove dead code ({n} files, {n} LOC)
- [ ] Major dependency upgrades
- [ ] Bundle size optimization
```

3. Present roadmap for final approval

---

## Key Principles (Grounded in AI-DLC)

- **Adaptive Execution**: Only execute phases/stages relevant to the detected tech stack
- **Transparent Planning**: After Discovery, show which phases will execute and why
- **User Control**: User can skip any phase, adjust priorities, override detections
- **Progress Tracking**: Update state.md after each stage
- **Complete Audit Trail**: Log ALL interactions and findings in audit.md
- **Non-Destructive**: NEVER modify source code during analysis (Phases 1-6 are READ-ONLY)
- **Tech-Stack-First**: Always identify the tech stack BEFORE any analysis
- **Unified View**: Phase 7 synthesizes all findings into one coherent roadmap
- **Security First**: Secrets and CVEs always get P0 priority regardless of user preference
