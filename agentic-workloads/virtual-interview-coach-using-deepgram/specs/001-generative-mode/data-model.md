# Phase 1 Data Model: Generative Mode

No schema migration. This documents the (unchanged) entities and the provenance distinction.

## question_archetype (reused, unchanged)

One interview question. Relevant columns for this feature:

| Column | Vetted (bank) | Generated (this feature) |
|---|---|---|
| `category` | `general` or `domain` | `general` or `domain` (same values) |
| `competency` | from the bank taxonomy | `motivation_fit`/behavioral for general; `role_specific` for domain |
| `difficulty` | `easy`/`moderate`/`difficult` | same — generated at the requested tier |
| `prompt_template` | human-authored | model-generated, JD/resume-grounded |
| `follow_up_prompts` | human-authored | model-generated probes |
| `embedding` | Titan v2, 1024-dim | Titan v2, 1024-dim (embedded at prep) |
| `embedding_model` | pinned model id | same pinned model id |
| `source` | `curated` | **`generated`** ← provenance marker |
| `status` | `approved` (after human review) | `approved` (served this session — VII relaxation) |
| `scoring_guidance` | human rubric notes | carries **`{"jit": true, ...}`** marker for audit |
| `active` | `TRUE` | `TRUE` |

**Provenance rule**: a row with `source='generated'` was NOT human-reviewed. The `jit` marker in
`scoring_guidance` and the `source` value together make every generated row auditable and
distinguishable from vetted ones (Principle VII b).

**Idempotency**: generated row ids are `uuid5(namespace, "{family|general}:{difficulty}:{i}:{prompt[:60]}")`,
so re-preparing the same role + difficulty upserts in place rather than duplicating (FR-011).

## interview_blueprint (reused, unchanged)

The ordered plan for one session: `target_competencies`, `ordered_archetype_ids`,
`opening_archetype_id`. Composed identically whether rows are vetted or generated.

## voice_session (reused, unchanged)

| Column | Use in this feature |
|---|---|
| `difficulty` | the requested tier; drives generation calibration |
| `duration_minutes` | drives the generated question count |
| `domain_coverage_reduced` | set `TRUE` for a session served (wholly or partly) by generated questions — the honest "not fully bank-vetted" flag the report + SPA already consume (Principle V / VII c) |

## Live-selection invariant (unchanged)

The live path selects questions with the existing pgvector cosine query over
`status='approved' AND embedding IS NOT NULL AND active=TRUE`. Because generated rows are written with
exactly those attributes during prep, the live query finds them with **no change** — and stays
LLM-free (Principle I). This is the crux of why generation in prep does not touch live latency.
