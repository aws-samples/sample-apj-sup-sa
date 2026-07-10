# Contract: Prep-Window Generation (Generative Mode)

Governs `prep.blueprint.assemble_blueprint(...)` and the generation helpers in
`prep.jit_generate`. This is an internal (in-process) contract invoked from `POST /sessions`,
entirely within the prep window (before media start).

## Inputs

| Input | Source |
|---|---|
| `session_id`, `job_title`, `job_description`, `difficulty`, `num_questions` | `POST /sessions` body + duration mapping (existing) |
| confirmed resume facts | the session's user (existing precondition; consent + confirmed resume already enforced) |
| `settings.generative_mode` | `GENERATIVE_MODE` env (NEW; default off) |

## Decision table

| Bank has approved rows at difficulty? | `GENERATIVE_MODE` | Behavior |
|---|---|---|
| Yes | off | **Unchanged**: bank-served pgvector selection; domain JIT still fills an uncovered role as today. |
| Yes | on | Generate the FULL plan (general + domain) and compose it; set `domain_coverage_reduced=TRUE`; log the relaxation. |
| No (empty pool) | off | **Generate** the FULL plan (general + domain); set `domain_coverage_reduced=TRUE`; log. (This replaces today's 503.) |
| No (empty pool) | on | Same as above (generate). |
| Generation yields zero usable rows | any | Roll back the empty session; raise the honest "could not prepare interview" error → `POST /sessions` returns 503 with an updated message. No new crash mode. |

## Generation behavior (when triggered)

1. Size the plan with the existing `_split_counts(num_questions)` → `(n_general, n_tech, n_jobscope)`.
2. `generate_general_questions(resume_facts, job_title, jd, difficulty, n=n_general + 1_warmup)` —
   behavioral/motivational questions grounded in resume + JD, calibrated to difficulty, each with
   2-3 follow-up probes. Persisted `category='general'`, `source='generated'`, `status='approved'`,
   embedded, `scoring_guidance={"jit": true, ...}`.
3. `generate_domain_questions(job_title, jd, difficulty, role_family, n=n_tech + n_jobscope)` —
   existing helper, unchanged.
4. Compose with the existing `_compose_plan(generated_rows, num_questions)` so ordering/arc match
   bank-served plans; persist the blueprint; set `domain_coverage_reduced=TRUE`.

## Outputs

Same dict shape as today:
`{ blueprint_id, target_competencies, ordered_archetype_ids, opening_archetype_id, domain_coverage_reduced }`.
`blueprint_ready=true` is returned by `POST /sessions` on success.

## Invariants (MUST hold — map to Constitution)

- **I (latency)**: no generation/embedding off this prep window; the live selection query is
  unchanged and LLM-free. SC-001 unaffected.
- **II/VII**: bank-served path unchanged when rows exist and flag off; generated rows marked
  `source='generated'` + `jit` marker.
- **V/VII c**: `domain_coverage_reduced=TRUE` whenever any served question was generated.
- **VI**: no schema change.
- **FR-010/FR-011**: failure is graceful (honest 503, session rolled back); persistence is idempotent.

## Test assertions (for tasks.md / test_generative_mode.py)

- Empty bank + Easy/3-min → `assemble_blueprint` returns a plan with ≥ the duration's question count,
  `domain_coverage_reduced=True`; no `RuntimeError`.
- Empty bank + Difficult/30-min → plan sized to the longer interview.
- Bank present + flag off → identical plan to current behavior (no generation called).
- Bank present + flag on → generation called; plan rows are `source='generated'`.
- Generation returns [] → `RuntimeError` raised → `POST /sessions` 503 (session rolled back), message
  references generation attempted.
- All generated rows have `source='generated'` and a `jit` marker; live selection query (unchanged)
  returns them.
