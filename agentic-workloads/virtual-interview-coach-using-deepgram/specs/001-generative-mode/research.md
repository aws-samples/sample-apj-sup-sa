# Phase 0 Research: Generative Mode

## Decision 1 — Generalize `jit_generate`, do not add a new generator module

**Decision**: Add `generate_general_questions(...)` alongside the existing
`generate_domain_questions(...)` in `backend/src/prep/jit_generate.py`, sharing the same
generate→persist→embed→return-plan-rows machinery.

**Rationale**: The domain JIT path already implements every guard Principle VII requires (prep-window
only, `source='generated'`, `scoring_guidance` marker, per-session log, idempotent uuid5 keys, graceful
return-[] on failure). Generating general questions is the same operation with a different prompt and
`category='general'`. Reusing it minimizes new code and inherits the proven latency/audit behavior.

**Alternatives considered**:
- *New `generative.py` module*: more code, duplicate the persist/embed/idempotency logic, two paths to
  keep in sync. Rejected.
- *Generate at the worker / live path*: violates Principle I. Rejected outright.

## Decision 2 — No schema migration

**Decision**: Reuse `question_archetype` as-is. Generated general rows are written with
`category='general'`, `source='generated'`, `status='approved'`, and a `{"jit": true}` /
`{"generative": true}` marker in `scoring_guidance`. The session honesty flag reuses
`voice_session.domain_coverage_reduced`.

**Rationale**: Principle VI prefers additive change; the cleanest additive change is *none*. The
columns needed for provenance and the honesty flag already exist (domain JIT uses them). Adding a
dedicated `generative` column is possible but unnecessary for the success criteria and would be a
migration to own.

**Alternatives considered**:
- *New `voice_session.generative_mode` boolean column*: clearer semantics, but a migration for a flag
  whose meaning `domain_coverage_reduced` already conveys ("not fully bank-vetted"). Deferred; can be
  added additively later if the report needs to distinguish "reduced domain coverage" from "fully
  generated".

## Decision 3 — Trigger conditions

**Decision**: Generate the full plan when **either**:
1. the JD-ranked approved pool is empty at the chosen difficulty (today's 503 case), OR
2. `settings.generative_mode` is true (operator `GENERATIVE_MODE=1`), regardless of bank contents.

When approved rows exist and the flag is off, behavior is byte-for-byte unchanged (Principle VII d).

**Rationale**: (1) delivers the MVP (US1: fresh deploy works). (2) delivers US3 (operator force) and
makes the path testable on an instance that has a bank.

## Decision 4 — Question mix when fully generative

**Decision**: Reuse the existing `_split_counts` mix (40% general / 40% technical / 20% job-scope) and
the `_compose_plan` ordering (warmup → behavioral → technical → job-scope). When generating, request
`n_general (+1 warmup)` general questions and `n_tech + n_jobscope` domain questions sized to the
duration, then compose with the same function so the interview arc is identical to a bank-served one.

**Rationale**: keeps one composition code path; the only difference between bank and generative is the
SOURCE of the rows, not how they are ordered or sized.

## Decision 5 — Graceful failure

**Decision**: If generation yields zero usable rows (model error or empty output for both general and
domain), prep returns the same honest failure as today (rolled-back session + a clear message), but the
message is updated to reflect that generation (not just the bank) was attempted. Instances WITH a
seeded bank never reach the generative branch unless forced, so they cannot regress.

**Rationale**: FR-010 — no new crash mode; honest surfacing (Principle V).

## Open questions

None blocking. A future enhancement (out of scope here) could add a distinct `generative` session flag
and surface "practice questions were AI-generated" copy in the SPA; today `domain_coverage_reduced`
already drives an honest coverage note.
