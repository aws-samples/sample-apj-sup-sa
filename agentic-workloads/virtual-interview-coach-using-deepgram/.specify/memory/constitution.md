<!--
SYNC IMPACT REPORT
==================
Version change: (unversioned template) → 1.1.0
Rationale: Reconstructs the InterviewCoach constitution from the project's documented
non-negotiables (CLAUDE.md, prior features F001-F008) as the 1.0.0 baseline, then adds one
new principle (VII. Generative Mode) — a principle ADDITION, so MINOR bump to 1.1.0.

Principles (baseline 1.0.0, reconstructed):
  I.   Live Latency (NON-NEGOTIABLE)
  II.  Vetted Question Bank & Human Review (NON-NEGOTIABLE)
  III. Privacy by Architecture & Owner-Scoping (NON-NEGOTIABLE)
  IV.  Personalization Grounded in Evidence
  V.   Honest, Non-Composite Reporting
  VI.  Additive Schema Evolution

Added in 1.1.0:
  VII. Generative Mode (sanctioned, bounded relaxation of II)

Modified principles: none (baseline reconstruction)
Added sections: Operational Constraints; Development Workflow & Quality Gates
Removed sections: none

Templates requiring updates:
  ✅ .specify/memory/constitution.md (this file)
  ⚠ .specify/templates/plan-template.md — Constitution Check section should reference
     Principles I-III + VII when planning generative work (pending; generic gate text retained)
  ✅ .specify/templates/spec-template.md — no mandatory-section change required
  ✅ .specify/templates/tasks-template.md — no principle-driven task-type change required

Follow-up TODOs:
  TODO(RATIFICATION_DATE): original adoption date of the pre-spec-kit constitution is not
  recorded in this branch; set to the project's first-feature date 2026-06-02 (docs/ headers).
-->

# Virtual Interview Coach Constitution

A voice-first AI mock-interview platform for undergraduates (18+). This constitution defines the
non-negotiable invariants every feature MUST uphold. Principles I-III are NON-NEGOTIABLE: a change
that violates them is rejected, not merged.

## Core Principles

### I. Live Latency (NON-NEGOTIABLE)

The live turn loop MUST stay free of large-model inference on the response_gap clock. The
SC-001 gate is the arbiter: response_gap p50 < 1000 ms and p95 < 1500 ms on a recording-ON
session, with a hard ceiling of p50 <= 1200 ms. All LLM work — JD embedding, question
generation, answer scoring, coaching synthesis — MUST run OFF the live clock: in the prep
window (after `POST /sessions`, before media start) or in an async worker. The live
question-selection step MUST be a pure database operation (pgvector SELECT), never a model call.

Rationale: sub-second turn-taking is the product. Any feature that moves inference onto the gap
clock regresses the core experience and is reverted regardless of other merits.

### II. Vetted Question Bank & Human Review (NON-NEGOTIABLE)

By default, only human-reviewed questions (`status='approved'`) reach a student. Question
selection on the live path is a pure pgvector cosine SELECT over approved, embedded rows — zero
LLM. Difficulty tiers (Easy/Moderate/Difficult) MUST be behaviorally distinct and calibrated.
Scoring MUST use a fixed, level-independent rubric whose version is pinned per session.

This principle admits exactly ONE sanctioned relaxation, defined in Principle VII (Generative
Mode). Any OTHER path that serves un-reviewed questions, or that puts model inference on the live
selection path, violates this principle.

Rationale: a student's practice and scores must rest on quality-controlled, comparable material;
the human-review gate is what makes that trustworthy.

### III. Privacy by Architecture & Owner-Scoping (NON-NEGOTIABLE)

Explicit consent gates recording. RDS and S3 (SSE-KMS, customer-managed keys) are the ONLY homes
for PII (resume, audio, transcript, scores, coaching notes). Default retention is 30 days with
automatic expiry. Every owner-scoped resource MUST return 404 (never 403) to non-owners so the
existence of another user's data never leaks (anti-IDOR). One-click hard delete MUST fan out
across every PII store with zero residual.

Rationale: trust is a precondition for honest practice; privacy is enforced by topology and
access control, not by policy promises.

### IV. Personalization Grounded in Evidence

Interview questions and feedback MUST be grounded in the student's confirmed resume facts plus
the pasted job description. The student confirms parsed resume facts before they are used.
Generated or selected questions are tailored to this evidence, the chosen difficulty, and the
chosen duration.

Rationale: generic interviews do not prepare a specific student for a specific role.

### V. Honest, Non-Composite Reporting

Every competency score MUST be anchored to a verbatim quote from the student's answer. The system
MUST NOT produce a single blended cross-tier composite score. Coverage gaps MUST be surfaced
honestly (e.g. `domain_coverage_reduced`), never silently hidden. Provenance MUST be honest:
a report MUST NOT imply human-vetted question provenance when the session ran on generated
questions.

Rationale: a coaching tool that flatters or obscures does not help the student improve.

### VI. Additive Schema Evolution

The database schema is owned by `voice-worker/src/db_migrate.py` and evolves ADDITIVELY (new
tables/columns/indexes; no destructive rewrites of in-use structures). Migrations are idempotent
and run in-VPC (one-off ECS task), since the database is private.

Rationale: additive, idempotent migrations keep every prior feature working and make deploys
safe to re-run.

### VII. Generative Mode (sanctioned, bounded relaxation of II)

The system MUST be able to run a COMPLETE interview with no seeded question bank. When the vetted
bank has no approved archetypes at the chosen difficulty (an empty/unseeded bank), OR generative
mode is explicitly enabled, the prep window MAY generate the FULL question plan — general/
behavioral AND domain questions — with Bedrock, grounded in the confirmed resume, the JD, the
difficulty, and the duration (per Principle IV). This is a DELIBERATE, BOUNDED relaxation of
Principle II's human-review default. It is sanctioned ONLY when ALL of the following hold:

- (a) **Latency preserved (Principle I).** Generation happens ONLY in the prep window, never on
  the response_gap clock. Generated questions are persisted + embedded during prep, and the live
  path still selects them with a pure pgvector SELECT.
- (b) **Auditable.** Generated rows MUST be marked `source='generated'` with a `jit`/`generative`
  marker in `scoring_guidance`, distinguishable from human-vetted rows, and the relaxation MUST be
  logged per session.
- (c) **Honest provenance (Principle V).** Sessions that ran on generated questions MUST be
  flagged (e.g. `domain_coverage_reduced` / a generative flag) so reports never imply human-vetted
  provenance.
- (d) **Bank-preferred.** When an approved bank IS present it remains the default; generative mode
  only fills genuine gaps, or is enabled deliberately via a `GENERATIVE_MODE` setting.

Rationale: a seeded bank is an operational nicety, not a hard prerequisite for a usable product. A
fresh deployment with no bank MUST still serve a personalized interview rather than fail. This
generalizes the existing per-role JIT generation (`backend/src/prep/jit_generate.py`) from
domain-only to the whole plan, while keeping the latency, audit, and honesty guarantees intact.

## Operational Constraints

- **Topology**: ECS Fargate (voice worker + backend app API + async report worker), an
  internet-facing ALB carrying only `/api/*` and `/offer`, CloudFront + S3 for the SPA, Cognito
  for auth, RDS Postgres (private) + pgvector, S3 SSE-KMS for resume/audio. WebRTC media flows
  browser<->worker directly (not through the ALB). Pipecat Cloud is FORBIDDEN (it would move PII
  off-topology and reopen latency).
- **Models**: default to the latest, most capable Claude models for reply/scoring/generation;
  Titan v2 for embeddings. All such calls are off the live clock by construction.
- **Bank-optional**: no deploy step may be a hard prerequisite for a usable interview. A bank
  load is OPTIONAL and improves quality where present (Principles II + VII).

## Development Workflow & Quality Gates

- Features estimated at 300+ lines follow the Spec-Kit flow: constitution -> specify -> plan ->
  tasks -> implement, with artifacts under `specs/NNN-feature/`.
- Each feature MUST state which principles it touches and how it preserves the NON-NEGOTIABLE
  ones (I, II, III). A feature touching the live path MUST re-prove SC-001 where relevant.
- Generated code carries no emoji. Python services keep their own venvs; voice-worker tests run
  in both of its venvs before merge.

## Governance

This constitution supersedes ad-hoc practice. Amendments MUST be made by editing this file with a
Sync Impact Report, a semantic-version bump, and propagation to dependent templates/docs.
Versioning: MAJOR for backward-incompatible principle removal/redefinition; MINOR for a new
principle or materially expanded guidance; PATCH for clarifications. Principles I, II, and III are
NON-NEGOTIABLE — relaxing them requires a MAJOR bump and an explicit, recorded rationale (Principle
VII is the one pre-approved, bounded carve-out from II and does not itself weaken I, III, or V).
Runtime development guidance lives in `CLAUDE.md`.

**Version**: 1.1.0 | **Ratified**: 2026-06-02 | **Last Amended**: 2026-06-25
