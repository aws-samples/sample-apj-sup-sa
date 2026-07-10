"""Session-prep blueprint assembly (T016) — contracts/session-prep-contract.md.

Runs in the prep window (after POST /sessions, before the turn clock). Steps:
  1. Embed the JD once (Titan) — the ONLY LLM call in selection's vicinity, and it is NOT the
     "selection" step; it never recurs per turn (R7).
  2. Classify the JD's role_family deterministically (keyword map, NO LLM) to drive the domain filter.
  3. retrieval.retrieve_ranked(...) — pure DB + pgvector cosine rank (zero LLM, SC-003).
  4. Derive target_competencies (4-6) + ordered_archetype_ids + opening_archetype_id from the ranked
     plan, persist interview_blueprint, and link it onto voice_session.

Niche-role fallback (FR-222): if no approved DOMAIN archetypes match the JD's role, the plan falls back
to General competencies only and sets voice_session.domain_coverage_reduced = TRUE — honest, not silent.

Everything here is off the response_gap clock by construction (it completes before media start).
"""

from __future__ import annotations

import logging

from ..config import settings
from .. import db
from . import retrieval
from . import jit_generate

log = logging.getLogger("backend")

# Plan sizing: enough archetypes to cover 4-6 competencies plus room for the live advance/follow-up
# walk, kept small so prep stays well under the ~2s target (SC-003).
_PLAN_SIZE = 12
_MAX_COMPETENCIES = 6

# Category mix for a composed interview (item 1): of the N main questions, this fraction is general
# (behavioral/personal), technical (domain), and job-scope (the domain rows closest to the JD). An
# opening warmup is always added on top. Job-scope is NOT a separate bank tag — it is the highest
# JD-ranked domain rows (closest by cosine), so 'technical' draws the remaining domain rows. Ratios
# are applied with largest-remainder rounding so the counts always sum to N.
_MIX_GENERAL = 0.40
_MIX_TECHNICAL = 0.40
_MIX_JOBSCOPE = 0.20


def _split_counts(total: int) -> tuple[int, int, int]:
    """Split `total` main questions into (general, technical, job-scope) by the mix, summing to total.

    Largest-remainder method so rounding never loses or gains a question. With a tiny total the
    general bucket is favored (a short interview should still open with behavioral questions)."""
    if total <= 0:
        return (0, 0, 0)
    raw = [total * _MIX_GENERAL, total * _MIX_TECHNICAL, total * _MIX_JOBSCOPE]
    floors = [int(x) for x in raw]
    remainder = total - sum(floors)
    # Distribute the leftover to the largest fractional parts (general first on ties — it is listed
    # first), so a short interview leans behavioral rather than technical.
    order = sorted(range(3), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in range(remainder):
        floors[order[i]] += 1
    return (floors[0], floors[1], floors[2])

# Deterministic role_family classifier (NO LLM). Maps JD keywords to the bank's role_family values.
# First family with a keyword hit wins; ties broken by hit count. Keys MUST match the taxonomy's
# role_family values (bank/seed/taxonomy.json) so the generated+embedded domain rows are reachable —
# a family the bank has but the classifier cannot route to is invisible at selection. Extend together.
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "software_engineering": (
        "software engineer", "backend", "front-end", "frontend", "full stack", "full-stack",
        "developer", "programming", "api", "microservice", "devops", "sre", "python", "java",
        "typescript", "golang", "kubernetes", "distributed systems",
    ),
    "data_analytics": (
        "data analyst", "data analytics", "data scientist", "data science", "analytics",
        "sql", "dashboard", "bi ", "business intelligence", "etl", "tableau", "power bi",
        "machine learning", "statistics", "reporting",
    ),
    "product_management": (
        "product manager", "product management", "product owner", "roadmap", "prioritization",
        "prioritisation", "stakeholder", "user research", "product strategy", "go-to-market",
        "backlog", "product lifecycle", "feature prioritization", "kpi", "user stories",
    ),
    "finance": (
        "financial analyst", "finance", "accounting", "accountant", "fp&a", "audit", "auditor",
        "controller", "treasury", "budgeting", "forecasting", "variance analysis", "gaap",
        "financial reporting", "reconciliation", "valuation",
    ),
    "sales": (
        "sales", "account executive", "business development", "sales development", "sdr", "bdr",
        "quota", "pipeline", "prospecting", "lead generation", "closing deals", "crm",
        "account management", "objection handling", "revenue target",
    ),
}


def classify_role_family(job_title: str, job_description: str) -> str | None:
    """Best-effort role_family from the JD (deterministic keyword match, no LLM).

    Returns None when nothing matches — the caller then runs the General-only fallback (FR-222).
    """
    haystack = f"{job_title}\n{job_description}".lower()
    best: tuple[int, str] | None = None
    for family, keywords in _ROLE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in haystack)
        if hits and (best is None or hits > best[0]):
            best = (hits, family)
    return best[1] if best else None


def _embed_jd(job_title: str, job_description: str) -> list[float]:
    """Embed the JD once with Titan (prep window). Falls back to a deterministic synthetic vector
    when Bedrock is unavailable (local/dev) so the rest of prep is still exercisable — the synthetic
    path is non-semantic but keeps the pgvector plumbing working off the gap clock."""
    text = f"{job_title}\n\n{job_description}".strip()
    try:
        import boto3
        import json as _json

        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        resp = client.invoke_model(
            modelId=settings.bedrock_embedding_model_id,
            body=_json.dumps({"inputText": text[:20000], "normalize": True}),
            accept="application/json",
            contentType="application/json",
        )
        vec = _json.loads(resp["body"].read())["embedding"]
        if len(vec) != 1024:
            raise ValueError(f"Titan returned {len(vec)}-dim; expected 1024")
        return vec
    except Exception as exc:  # noqa: BLE001 - degrade to synthetic so prep still completes locally
        log.warning("JD Titan embedding unavailable (%s); using synthetic prep embedding", type(exc).__name__)
        return _synthetic_embed(text)


def _synthetic_embed(text: str, dim: int = 1024) -> list[float]:
    """Deterministic normalized 1024-dim pseudo-vector (mirrors bank.embeddings.synthetic_embedding;
    inlined to avoid a cross-subtree import in the deployed backend image)."""
    import hashlib
    import math
    import struct

    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{text}:{counter}".encode("utf-8")).digest()
        for i in range(0, len(digest), 4):
            (val,) = struct.unpack(">I", digest[i : i + 4])
            out.append((val / 0xFFFFFFFF) * 2.0 - 1.0)
            if len(out) >= dim:
                break
        counter += 1
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


def _derive_competencies(plan: list[dict]) -> list[str]:
    """The 4-6 target competencies covered by the ranked plan, in first-seen (rank) order."""
    seen: list[str] = []
    for row in plan:
        c = row["competency"]
        if c not in seen:
            seen.append(c)
        if len(seen) >= _MAX_COMPETENCIES:
            break
    return seen


def _choose_opening(plan: list[dict]) -> str | None:
    """Pick the opening archetype: prefer a warmup/motivation_fit easy general question for a
    natural start; else the top-ranked row."""
    for row in plan:
        if row["question_type"] == "warmup" or row["competency"] == "motivation_fit":
            return row["id"]
    return plan[0]["id"] if plan else None


def _compose_plan(ranked: list[dict], num_questions: int) -> list[dict]:
    """Compose the ordered interview plan from the JD-ranked pool by the category mix (item 1).

    `ranked` is JD-closest-first (cosine). We split into general (category='general') and domain
    (category='domain') pools, then build N questions as: an opening warmup (if available), then the
    general/technical/job-scope counts from _split_counts. Job-scope = the domain rows CLOSEST to the
    JD (front of the domain pool, already rank-ordered); technical = the remaining domain rows. The
    final order interleaves a natural arc: warmup -> general/behavioral -> technical -> job-scope.

    Degrades gracefully: if a pool is short, the shortfall is backfilled from the other pool so the
    interview still reaches num_questions where the bank allows (never invents rows)."""
    general = [r for r in ranked if r.get("category") == "general"]
    domain = [r for r in ranked if r.get("category") == "domain"]

    # Opening warmup: prefer a general warmup question; it sits outside the mix counts.
    warmup = None
    for i, r in enumerate(general):
        if r.get("question_type") == "warmup":
            warmup = general.pop(i)
            break

    body_n = max(0, num_questions - (1 if warmup else 0))
    n_general, n_tech, n_jobscope = _split_counts(body_n)

    # job-scope = closest-to-JD domain rows; technical = the rest of the domain pool.
    jobscope = domain[:n_jobscope]
    technical = domain[n_jobscope : n_jobscope + n_tech]
    chosen_general = general[:n_general]

    # Backfill shortfalls across pools so a thin bank still fills the interview (no invented rows).
    chosen = chosen_general + technical + jobscope
    if len(chosen) < body_n:
        used = {id(r) for r in chosen}
        leftovers = [r for r in (general + domain) if id(r) not in used]
        chosen += leftovers[: body_n - len(chosen)]

    # Dedup by archetype id, preserve order; warmup first, then the natural behavioral->technical arc.
    ordered = ([warmup] if warmup else []) + chosen
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in ordered:
        if r["id"] not in seen:
            seen.add(r["id"])
            deduped.append(r)
    return deduped[:num_questions]


async def assemble_blueprint(
    session_id: str,
    job_title: str,
    job_description: str,
    difficulty: str,
    num_questions: int = _PLAN_SIZE,
    *,
    resume_summary: str = "",
    generative_mode: bool | None = None,
) -> dict:
    """Assemble + persist the JD-ranked plan for this session. Pure DB selection (zero LLM on the
    selection path); the only model touch is the one-time JD embedding above (and, in Generative
    Mode, prep-window question generation — also off the response_gap clock). Returns the contract
    output (blueprint_id, target_competencies, ordered_archetype_ids, opening_archetype_id,
    domain_coverage_reduced).

    `num_questions` (from the chosen interview duration) sizes the plan; the rows are composed across
    the general/technical/job-scope mix (item 1) by _compose_plan.

    Generative Mode (Constitution VII): when the vetted bank has no approved GENERAL rows at this
    difficulty (empty/unseeded bank) OR `generative_mode` is forced, generate the general/behavioral
    questions with Bedrock during prep instead of failing. Domain generation already happens for
    uncovered roles. Generated rows are marked source='generated' and the session is flagged
    domain_coverage_reduced so reporting never implies human-vetted provenance."""
    if generative_mode is None:
        generative_mode = settings.generative_mode
    jd_embedding = _embed_jd(job_title, job_description)
    role_family = classify_role_family(job_title, job_description)

    # Niche-role fallback: include domain rows only if the role is both classified AND present in the
    # bank at this difficulty. Otherwise drop to General-only and flag it honestly (FR-222).
    domain_matches = 0
    if role_family is not None:
        domain_matches = await retrieval.count_domain_matches(difficulty, role_family=role_family)
    include_domain = domain_matches > 0
    domain_coverage_reduced = not include_domain

    # Retrieve a pool at least as large as the interview so the mix has rows to compose from; cap so
    # prep stays fast. The query is JD-ranked (closest first), which is what makes job-scope selection
    # (closest domain rows) free. When generative_mode is forced we skip the bank entirely so the plan
    # is fully generated (operator override — Constitution VII d / US3).
    pool_size = max(_PLAN_SIZE, num_questions * 2)
    ranked = (
        []
        if generative_mode
        else await retrieval.retrieve_ranked(
            jd_embedding,
            difficulty,
            role_family=role_family,
            include_domain=include_domain,
            limit=pool_size,
        )
    )
    have_general = any(r.get("category") == "general" for r in ranked)
    # Tracks whether ANY composed question was generated (vs human-vetted bank). If so the session is
    # flagged domain_coverage_reduced so reporting never implies human-vetted provenance (Const. VII c).
    used_generated = False

    # Generative Mode (Constitution VII): if the bank has no GENERAL rows at this difficulty (empty
    # bank), or generation is forced, generate behavioral/general questions in the prep window and
    # compose them directly. This is what lets a bank-LESS deploy still run a complete interview.
    # Off the response_gap clock; generated rows are marked + the session flagged (VII a/b/c).
    if not have_general or generative_mode:
        gen_general = await jit_generate.generate_general_questions(
            resume_summary, job_title, job_description, difficulty, n=max(2, num_questions // 2)
        )
        if gen_general:
            ranked = ranked + gen_general
            used_generated = True

    # On-the-fly domain generation: when the vetted bank has NO approved domain questions for this role
    # at this difficulty (or generation is forced), generate JD-specific ones at prep and compose them
    # directly into the plan (off the response_gap clock). Deliberately serves un-human-reviewed
    # questions for uncovered roles / bank-less deploys — see jit_generate.py's constitution note.
    if domain_coverage_reduced or generative_mode:
        jit_rows = await jit_generate.generate_domain_questions(
            job_title, job_description, difficulty, role_family, n=max(2, num_questions // 2)
        )
        if jit_rows:
            ranked = ranked + jit_rows
            used_generated = True

    # Any generated row means the plan is not fully human-vetted -> honest coverage flag (Const. V/VII c).
    if used_generated:
        domain_coverage_reduced = True

    if not ranked:
        # Neither the bank nor generation could produce any questions — surface it honestly. Setup
        # created the session but it cannot be served; the caller rolls it back and returns 503.
        raise RuntimeError(
            f"could not prepare an interview for difficulty={difficulty!r}: "
            "no approved bank questions and question generation produced nothing"
        )

    plan = _compose_plan(ranked, num_questions)

    ordered_ids = [row["id"] for row in plan]
    competencies = _derive_competencies(plan)
    opening_id = _choose_opening(plan)

    blueprint_id = await db.create_blueprint(session_id, competencies, ordered_ids, opening_id)
    await db.set_session_plan(session_id, ordered_ids, blueprint_id, domain_coverage_reduced)

    if used_generated:
        # Audit the Constitution VII relaxation per session: which mode, and what we served.
        log.info(
            "session-prep: served GENERATED questions (Constitution VII relaxation) — "
            "reason=%s, role_family=%s, difficulty=%s, session=%s",
            "operator-forced" if generative_mode else "empty-bank-fallback",
            role_family,
            difficulty,
            session_id,
        )
    elif domain_coverage_reduced:
        log.info(
            "session-prep: niche-role fallback fired (role_family=%s, difficulty=%s) -> General-only plan",
            role_family,
            difficulty,
        )
    return {
        "blueprint_id": blueprint_id,
        "target_competencies": competencies,
        "ordered_archetype_ids": ordered_ids,
        "opening_archetype_id": opening_id,
        "domain_coverage_reduced": domain_coverage_reduced,
    }
