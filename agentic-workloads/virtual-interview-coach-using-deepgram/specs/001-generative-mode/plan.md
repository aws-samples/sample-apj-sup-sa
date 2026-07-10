# Implementation Plan: Generative Mode — Bank-Optional Interview Generation

**Branch**: `add-virtual-interview-coach-deepgram` (spec dir `001-generative-mode`) | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-generative-mode/spec.md`

## Summary

Make a seeded question bank OPTIONAL. Today `POST /sessions` calls
`prep.blueprint.assemble_blueprint`, which raises `RuntimeError("no approved archetypes available
for difficulty=...")` (blueprint.py:277) when the JD-ranked pool is empty; `sessions.py` turns that
into a 503. We extend the existing prep-window generation so that when the bank yields no rows at the
chosen difficulty (or an operator forces it via `GENERATIVE_MODE`), the prep window generates the
FULL plan — general/behavioral **and** domain questions — grounded in the confirmed resume + JD +
difficulty + duration, persists them as `source='generated'`, embeds them, and composes them into the
session plan. The live selection path stays a pure pgvector SELECT (Principle I), generated rows are
marked + logged (Principle VII b), and sessions are flagged `domain_coverage_reduced` so reports never
imply human-vetted provenance (Principle V / VII c). The current per-role JIT path
(`jit_generate.generate_domain_questions`) is generalized; bank-served behavior is unchanged when a
bank is present and the flag is off (Principle VII d).

## Technical Context

**Language/Version**: Python 3.13 (backend FastAPI app API).

**Primary Dependencies**: FastAPI, asyncpg, boto3 (Bedrock converse + Titan embed), pgvector. All
already in `backend/requirements.txt`; no new dependency.

**Storage**: RDS Postgres + pgvector, table `question_archetype` (reused; no schema change). Rows
written with `source='generated'`, `status='approved'`, `scoring_guidance` carrying a `jit`/
`generative` marker, embedded immediately so the live pgvector SELECT finds them.

**Testing**: pytest in `backend/.venv` (existing `backend/tests/`). DB-backed tests use the local
pgvector container on port 55432 per the existing dev loop.

**Target Platform**: ECS Fargate (backend service); the prep call runs inside `POST /sessions`.

**Project Type**: Web service (backend) — frontend untouched.

**Performance Goals**: No live-path change. Generation runs only in the prep window (already hosts a
JD-embed + optional JIT generation today). SC-001 live-latency gate is unaffected.

**Constraints**: Generation only in prep window (never on response_gap clock); idempotent persistence;
additive-only if any column were needed (none is); graceful failure (no new crash mode).

**Scale/Scope**: Demo scale (1 session at a time). Generation is a handful of Bedrock calls
(1 generate + N Titan embeds) per uncovered session — same order as today's JIT path.

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after design. Authority: `.specify/memory/constitution.md` v1.1.0.*

| Principle | Status | How this plan complies |
|---|---|---|
| I. Live Latency (NON-NEGOTIABLE) | PASS | All generation + embedding happens in the prep window inside `POST /sessions`, before media start. The live turn loop and the question-selection query are untouched and remain LLM-free. No change to the worker or the gap clock. |
| II. Vetted Bank & Human Review (NON-NEGOTIABLE) | PASS (via the sanctioned VII carve-out) | Bank-served selection stays the default and is byte-for-byte unchanged when approved rows exist and the flag is off. Serving generated questions is the explicit, bounded Principle VII relaxation, not a new violation. |
| III. Privacy & Owner-Scoping (NON-NEGOTIABLE) | PASS | No change to auth, owner-scoping, PII homes, retention, or delete fan-out. Generated questions are not PII and live in the same table. |
| IV. Personalization | PASS | Generated questions are grounded in the confirmed resume facts + JD + difficulty + duration. |
| V. Honest Reporting | PASS | Sessions on generated questions set `domain_coverage_reduced=TRUE` (reused honesty flag); generated rows are marked `source='generated'`. Reports never imply vetted provenance. |
| VI. Additive Schema | PASS | No migration. Reuses `question_archetype` columns (`source`, `scoring_guidance`) and the existing `voice_session.domain_coverage_reduced`. |
| VII. Generative Mode | PASS (this feature implements it) | Implements all four guards (a latency, b audit/log, c honesty flag, d bank-preferred + `GENERATIVE_MODE` opt-in). |

No violations → Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/001-generative-mode/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions & alternatives
├── data-model.md        # Phase 1 — entities & the (reused) provenance model
├── quickstart.md        # Phase 1 — how to exercise generative mode locally + on the deployed stack
├── contracts/
│   └── prep-generative-contract.md   # Phase 1 — the prep-window generation contract
└── tasks.md             # Phase 2 (/speckit-tasks) — NOT created by plan
```

### Source Code (repository root)

```text
backend/
├── src/
│   ├── config.py                 # ADD: generative_mode setting (GENERATIVE_MODE env)
│   ├── api/sessions.py           # CHANGE: keep the graceful 503 only when generation also yields nothing
│   └── prep/
│       ├── blueprint.py          # CHANGE: when ranked is empty OR generative forced, generate FULL plan
│       ├── jit_generate.py       # CHANGE: add generate_general_questions(); keep generate_domain_questions()
│       └── retrieval.py          # UNCHANGED (pure-DB selection; the live invariant)
└── tests/
    └── test_generative_mode.py   # ADD: empty-bank start, flag-forced, bank-preferred no-regression, graceful-fail
```

**Structure Decision**: Web-service backend, Option 2. The change is localized to `backend/src/prep/`
plus a one-line setting in `config.py` and the error-handling branch in `api/sessions.py`. The
frontend, worker, report-worker, and DB schema are untouched.

## Phase 0 — Research

See [research.md](./research.md). Key decisions:
- Reuse + generalize `jit_generate` rather than add a new module (smallest change; same audit/latency
  guarantees already proven for the domain path).
- No schema change: `source='generated'` + a `generative` marker in `scoring_guidance` already exist
  for domain JIT; general generated rows use `category='general'` with the same markers.
- Reuse `domain_coverage_reduced` as the honest "this session was not fully bank-vetted" flag rather
  than adding a new column (additive-simplest; the report and SPA already surface it).

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): the (unchanged) `question_archetype` shape and how a generated
  general row differs from a vetted one (provenance fields only).
- [contracts/prep-generative-contract.md](./contracts/prep-generative-contract.md): the prep-window
  behavior contract — inputs, the decision table (bank-present / empty / forced / generation-failed),
  outputs, and the invariants each branch preserves.
- [quickstart.md](./quickstart.md): how to reproduce locally (empty DB) and verify on the deployed
  stack (the live Easy/3-min path that currently fails).

## Phase 2 — Tasks

Generated by `/speckit-tasks` into [tasks.md](./tasks.md). Not created here.
