# Tasks: Generative Mode — Bank-Optional Interview Generation

**Input**: Design documents from `specs/001-generative-mode/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/prep-generative-contract.md

**Tests**: INCLUDED — the spec defines explicit success criteria (SC-001..SC-005) and edge cases, so
test tasks are first-class here.

**Organization**: by user story (US1 P1 MVP, US2 P2, US3 P3). All work is in `backend/`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files / no dependency)
- **[Story]**: US1 / US2 / US3 / FND (foundational) / POL (polish)

---

## Phase 1: Setup

- [ ] T001 Confirm `backend/.venv` is usable and the local pgvector DB (port 55432) can be migrated
  **without** loading the bank (the empty-bank baseline this feature targets). No code change.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the config flag + the generalized generator that all stories build on.

- [ ] T002 [FND] Add `generative_mode: bool` to `Settings` in `backend/src/config.py`, read from
  `GENERATIVE_MODE` (default false; accept `1/true/yes`). Add to `Settings.load()`.
- [ ] T003 [FND] In `backend/src/prep/jit_generate.py`, add `generate_general_questions(resume_facts,
  job_title, job_description, difficulty, n)` mirroring `generate_domain_questions`: a behavioral/
  motivational generation prompt grounded in resume + JD, calibrated to difficulty, with 2-3 follow-up
  probes; persist `category='general'`, `competency='motivation_fit'` (or behavioral), `source='generated'`,
  `status='approved'`, embedded, `scoring_guidance={"jit": true, "generative": true, ...}`; idempotent
  uuid5 ids; return plan-ready row dicts. Reuse the existing `_embed`/`_to_pgvector`/persist helpers.

**Checkpoint**: config flag + general generator exist and unit-test in isolation.

---

## Phase 3: User Story 1 — Fresh deploy with no bank (P1) 🎯 MVP

**Goal**: empty bank → a full generated plan → session starts (no 503).

**Independent Test**: empty `question_archetype`, Easy/3-min `assemble_blueprint` returns a plan with
`domain_coverage_reduced=True` and no `RuntimeError`.

### Tests for US1 (write first, must fail before T006)

- [ ] T004 [P] [US1] `backend/tests/test_generative_mode.py::test_empty_bank_generates_full_plan` —
  monkeypatch the generators to return canned rows; assert empty-bank Easy/3-min yields a composed plan
  sized to the duration, `domain_coverage_reduced=True`, no exception.
- [ ] T005 [P] [US1] `...::test_generation_failure_is_graceful` — generators return `[]` → `assemble_blueprint`
  raises `RuntimeError`; assert `POST /sessions` maps it to 503 and rolls back the session (reuse the
  existing rollback path in `backend/src/api/sessions.py`).

### Implementation for US1

- [ ] T006 [US1] In `backend/src/prep/blueprint.py::assemble_blueprint`, when the JD-ranked `ranked`
  pool is empty (or generative forced — see T010), call `generate_general_questions(...)` +
  `generate_domain_questions(...)` sized via `_split_counts(num_questions)`, combine, and compose with
  `_compose_plan`. Set `domain_coverage_reduced=True`. Only raise the "could not prepare" `RuntimeError`
  if BOTH generation calls yield nothing. Log the Principle VII relaxation per session.
- [ ] T007 [US1] Update the error message + comment at `backend/src/api/sessions.py` (the `except
  RuntimeError` branch) so the honest 503 reflects that generation was attempted, not just the bank.

**Checkpoint**: empty-bank interviews start at all difficulties/durations (SC-001).

---

## Phase 4: User Story 2 — Uncovered role gets generated domain + general (P2)

**Goal**: thin/empty bank for an uncovered role still composes behavioral + role-specific questions.

**Independent Test**: uncovered-role JD + empty general pool → plan contains both generated general and
generated domain rows in a coherent order.

- [ ] T008 [P] [US2] `...::test_uncovered_role_composes_general_and_domain` — assert the composed plan
  has both `category='general'` and `category='domain'` generated rows and a warmup-first order.
- [ ] T009 [US2] Verify in `blueprint.py` that when `ranked` has SOME general bank rows but zero domain
  (today's domain-only JIT case), the general rows are still used and only domain is generated — i.e.
  generation backfills the missing category rather than replacing a present one. Adjust the compose
  inputs so generated rows merge with any present bank rows.

**Checkpoint**: US1 + US2 both pass; partial banks behave sanely.

---

## Phase 5: User Story 3 — Operator force + observability (P3)

**Goal**: `GENERATIVE_MODE=1` forces generation even when a bank exists; logs/flags identify it.

**Independent Test**: bank present + flag on → generation called, rows `source='generated'`; flag off →
bank-served, unchanged.

- [ ] T010 [P] [US3] `...::test_flag_off_bank_present_no_regression` — bank rows present, flag off →
  `generate_*` NOT called, plan identical to current behavior (assert via spy/mocks).
- [ ] T011 [P] [US3] `...::test_flag_on_forces_generation` — bank rows present, `generative_mode=True`
  → generation called; composed rows are `source='generated'`; `domain_coverage_reduced=True`.
- [ ] T012 [US3] Wire `settings.generative_mode` into the `blueprint.py` trigger (the "or forced"
  condition in T006) and ensure the per-session relaxation log line includes whether it was empty-bank
  vs operator-forced.

**Checkpoint**: all three stories pass independently.

---

## Phase 6: Polish & Verification

- [ ] T013 [P] [POL] Run the full `backend` pytest suite in `backend/.venv`; ensure no regression in
  existing prep/session tests.
- [ ] T014 [POL] Rebuild the backend image (CodeBuild path) with tag `c2`, force a new backend ECS
  deployment in us-west-2.
- [ ] T015 [POL] Live verify per `quickstart.md`: with the bank emptied (or a fresh row-less difficulty),
  drive the deployed portal Easy/3-min → confirm it advances past the prep step with no
  "no approved archetypes" error; confirm new `question_archetype` rows have `source='generated'`.
- [ ] T016 [P] [POL] Update `README.md` deploy guide: note the bank load is now OPTIONAL (generative
  fallback) and document `GENERATIVE_MODE`. Update `CLAUDE.md` active-feature pointer.

---

## Dependencies & Execution Order

- T001 (setup) → T002, T003 (foundational) → US1 (T004-T007) → US2 (T008-T009) → US3 (T010-T012) →
  Polish (T013-T016).
- T006 is the core change and depends on T002 + T003. T009 and T012 refine T006.
- Tests T004/T005/T008/T010/T011 are written before their implementation and must fail first.

## Parallel Opportunities

- T002 and T003 are different files/functions → [P].
- All test-authoring tasks marked [P] across stories can be drafted together (one new test file;
  coordinate to avoid edit collisions — write distinct test functions).
- T016 (docs) is independent of the code once behavior is final → [P].

## Implementation Strategy

MVP = Phases 1-3 (US1): an empty-bank instance can start interviews. Ship/verify that first (T015 can
be run against US1 alone), then add US2 (partial-bank correctness) and US3 (operator force).
