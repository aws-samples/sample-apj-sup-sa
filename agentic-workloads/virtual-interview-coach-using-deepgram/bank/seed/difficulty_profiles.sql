-- Seed the three difficulty_profile rows (T007) — one row per tier (data-model.md).
--
-- These levers make Easy/Moderate/Difficult BEHAVIORALLY DISTINCT (FR-214 / SC-004): the row
-- for the session's difficulty is injected into the HR persona prompt at session start (T031).
-- F002 injects only the behavioral levers; scoring_strictness is stored for G3 and NOT applied
-- here (Principle II — the headline rubric stays level-independent).
--
-- Lever scales (documented so the persona-injection layer maps them consistently):
--   probing_intensity  1..5  follow-up depth per competency (how hard a vague answer is drilled)
--   curveball_rate     0..1  probability a turn injects an unexpected/stress angle
--   warmth             1..5  conversational tone (5 = warm/encouraging, 1 = neutral/clinical)
--   hint_policy        offer | minimal | none   whether the coach scaffolds toward an answer
--   domain_depth       1..5  how deep into role-specific specifics the questioning goes
--   scoring_strictness 1..5  G3 only (stored, not applied in F002)
--
-- Idempotent: ON CONFLICT keeps the seed re-runnable and lets levers be re-tuned in place.

INSERT INTO difficulty_profile
    (level, probing_intensity, curveball_rate, warmth, hint_policy, domain_depth, scoring_strictness)
VALUES
    ('easy',      2, 0.00, 5, 'offer',   2, 2),
    ('moderate',  3, 0.15, 4, 'minimal', 3, 3),
    ('difficult', 5, 0.40, 2, 'none',    5, 4)
ON CONFLICT (level) DO UPDATE SET
    probing_intensity  = EXCLUDED.probing_intensity,
    curveball_rate     = EXCLUDED.curveball_rate,
    warmth             = EXCLUDED.warmth,
    hint_policy        = EXCLUDED.hint_policy,
    domain_depth       = EXCLUDED.domain_depth,
    scoring_strictness = EXCLUDED.scoring_strictness;
