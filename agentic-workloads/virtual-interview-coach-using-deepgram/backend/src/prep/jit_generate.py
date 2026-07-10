"""Just-in-time domain question generation (session-prep) — for roles the vetted bank does NOT cover.

When `assemble_blueprint` finds NO approved domain archetypes for a JD's role at the chosen difficulty
(the General-only fallback would otherwise fire), this module generates a small set of JD-specific
domain questions with Bedrock, persists + embeds them, and returns them so the blueprint can compose
them into THIS session's plan.

CONSTITUTION NOTE (Principle II / FR-2c / SC-003 — read this): the vetted bank guarantees that only
HUMAN-REVIEWED ('approved') questions reach a student. This module deliberately RELAXES that guarantee
for *uncovered roles only*: the generated questions are served to the student in the same session, with
NO human review. This is a product decision to never leave a niche-role student with only generic
questions. The trade-off is explicit and logged; the rows are marked so they are auditable and
distinguishable from human-vetted ones (status='approved' so retrieval serves them, source='generated',
and a 'jit' marker in scoring_guidance for audit).

LATENCY (Principle I — unchanged): this runs ONLY in the prep window (after POST /sessions, before the
turn clock). It performs a Bedrock generation call + a Titan embedding call — both already off the
response_gap clock, exactly like _embed_jd and resume parsing. The live turn loop and the pgvector
SELECT remain LLM-free, so SC-001 is not affected.
"""

from __future__ import annotations

import json
import logging
import uuid

from ..config import settings
from .. import db

log = logging.getLogger("backend")

# Stable namespace so a (role, difficulty, index) generated question maps to the same id — re-prep of
# the same uncovered role upserts rather than duplicating (idempotent, like the bank's uuid5 scheme).
_JIT_NS = uuid.UUID("6f4c0d2e-0000-4002-a010-0000000000c1")

# Generated domain questions use the only domain competency the enum allows.
_COMPETENCY = "role_specific"
_QUESTION_TYPE = "technical"

# Allowed GENERAL competencies + question types (mirror voice-worker/src/db_migrate.py CHECK
# constraints). Generated general rows MUST use values from these sets or the INSERT fails.
_GENERAL_COMPETENCIES = (
    "teamwork", "problem_solving", "motivation_fit", "communication", "leadership", "adaptability",
)
_GENERAL_QTYPES = ("warmup", "behavioral", "situational")

_GEN_SYSTEM = (
    "You write interview questions for a vetted question bank. Given a job title and description, write "
    "{n} distinct, behavioral/situational interview questions that probe the ROLE-SPECIFIC competencies "
    "this job actually requires. Each must invite a STAR answer (a specific past situation, the "
    "candidate's task, the actions they took, and the result), be calibrated to the '{difficulty}' "
    "difficulty tier, and PROBE without leading (never hint at the answer). For each question include "
    "2-3 short follow-up probes that drill deeper WITHIN the same question's competency.\n\n"
    "Return ONLY valid JSON, no prose:\n"
    '{{ "questions": [ {{ "prompt_template": string, "follow_up_prompts": [string, ...] }}, ... ] }}'
)


def _coerce(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    data = json.loads(text)
    out = []
    for q in data.get("questions") or []:
        pt = (q.get("prompt_template") or "").strip()
        if pt:
            out.append({
                "prompt_template": pt,
                "follow_up_prompts": [str(p) for p in (q.get("follow_up_prompts") or [])][:4],
            })
    return out


def _generate_questions(job_title: str, job_description: str, difficulty: str, n: int) -> list[dict]:
    """Bedrock converse generation of n JD-specific domain questions (prep window, off the gap clock)."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    system = _GEN_SYSTEM.format(n=n, difficulty=difficulty)
    user = f"Job title: {job_title}\n\nJob description:\n{job_description[:8000]}"
    resp = client.converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": 1500, "temperature": 0.4},
    )
    return _coerce(resp["output"]["message"]["content"][0]["text"])


# General/behavioral generation (F009 / Constitution VII). Generalizes the domain generator to the
# behavioral questions that previously came ONLY from the vetted bank, so a bank-less deploy can still
# run a complete interview. The model picks a competency + question_type from the CLOSED enums; we
# validate the choice and fall back to safe defaults so the INSERT can never violate the CHECK.
_GEN_GENERAL_SYSTEM = (
    "You write GENERAL (behavioral / motivational) interview questions for a vetted bank. Using the "
    "candidate's background and the target job, write {n} distinct questions that probe transferable "
    "competencies (not role-specific technical depth). The FIRST question MUST be a gentle warmup "
    "('tell me about yourself'-style). Each later question invites a STAR answer (a specific past "
    "situation, task, actions, result), is calibrated to the '{difficulty}' tier, and PROBES without "
    "leading. Ground them in the candidate's actual background where natural, but keep them answerable. "
    "For each, include 2-3 short follow-up probes within the same competency.\n\n"
    "For each question pick:\n"
    "  competency: one of teamwork | problem_solving | motivation_fit | communication | leadership | adaptability\n"
    "  question_type: warmup (first only) | behavioral | situational\n\n"
    "Return ONLY valid JSON, no prose:\n"
    '{{ "questions": [ {{ "prompt_template": string, "competency": string, "question_type": string, '
    '"follow_up_prompts": [string, ...] }}, ... ] }}'
)


def _coerce_general(raw: str) -> list[dict]:
    """Parse general-question JSON, validating competency/question_type against the closed enums."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    data = json.loads(text)
    out = []
    for q in data.get("questions") or []:
        pt = (q.get("prompt_template") or "").strip()
        if not pt:
            continue
        comp = (q.get("competency") or "").strip().lower()
        qtype = (q.get("question_type") or "").strip().lower()
        out.append({
            "prompt_template": pt,
            "competency": comp if comp in _GENERAL_COMPETENCIES else "motivation_fit",
            "question_type": qtype if qtype in _GENERAL_QTYPES else "behavioral",
            "follow_up_prompts": [str(p) for p in (q.get("follow_up_prompts") or [])][:4],
        })
    return out


def _generate_general(resume_summary: str, job_title: str, job_description: str,
                      difficulty: str, n: int) -> list[dict]:
    """Bedrock converse generation of n general/behavioral questions (prep window, off the gap clock)."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    system = _GEN_GENERAL_SYSTEM.format(n=n, difficulty=difficulty)
    user = (
        f"Candidate background:\n{resume_summary[:4000]}\n\n"
        f"Job title: {job_title}\n\nJob description:\n{job_description[:6000]}"
    )
    resp = client.converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": 1800, "temperature": 0.5},
    )
    return _coerce_general(resp["output"]["message"]["content"][0]["text"])


def _embed(text: str) -> list[float]:
    """Titan-embed one question (same model + path as _embed_jd). Returns a 1024-dim vector."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    resp = client.invoke_model(
        modelId=settings.bedrock_embedding_model_id,
        body=json.dumps({"inputText": text[:20000], "normalize": True}),
        accept="application/json",
        contentType="application/json",
    )
    vec = json.loads(resp["body"].read())["embedding"]
    if len(vec) != 1024:
        raise ValueError(f"Titan returned {len(vec)}-dim; expected 1024")
    return vec


def _to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def generate_domain_questions(
    job_title: str,
    job_description: str,
    difficulty: str,
    role_family: str | None,
    n: int = 6,
) -> list[dict]:
    """Generate + persist + embed JD-specific domain questions and return them as plan-ready rows
    (the same dict shape retrieve_ranked yields: id, category, competency, question_type, role_family,
    difficulty, prompt_template, follow_up_prompts, distance). Returns [] on any failure (caller then
    keeps the General-only fallback — never blocks prep).

    role_family is stamped on the rows so a covered-but-empty role stays consistent; for a truly novel
    role (classify returned None) we stamp a synthesized 'jit_<slug>' family — retrieval won't match it
    by family, which is why the blueprint composes these rows DIRECTLY into the plan rather than relying
    on the approved-bank query to re-find them."""
    try:
        questions = _generate_questions(job_title, job_description, difficulty, n)
    except Exception as exc:  # noqa: BLE001 - generation failure must not block prep
        log.warning("JIT generation failed (%s); keeping General-only fallback", type(exc).__name__)
        return []
    if not questions:
        return []

    fam = role_family or "jit_" + "".join(c for c in job_title.lower() if c.isalnum())[:24]
    pool = await db.get_pool()
    rows: list[dict] = []
    for i, q in enumerate(questions):
        aid = uuid.uuid5(_JIT_NS, f"{fam}:{difficulty}:{i}:{q['prompt_template'][:60]}")
        try:
            vec = _embed(q["prompt_template"])
        except Exception as exc:  # noqa: BLE001 - skip a question we cannot embed
            log.warning("JIT embed failed for one question (%s); skipping it", type(exc).__name__)
            continue
        # Persist: source='generated', status='approved' (served this session), embedded immediately.
        # scoring_guidance carries a 'jit' marker for audit (it is NOT loaded into the live queue — G2).
        await pool.execute(
            """
            INSERT INTO question_archetype
                (id, category, competency, question_type, industry, role_family, seniority,
                 difficulty, prompt_template, follow_up_prompts, scoring_guidance,
                 embedding, embedding_model, source, status, version, active)
            VALUES
                ($1, 'domain', $2, $3, NULL, $4, NULL,
                 $5, $6, $7::jsonb, $8::jsonb,
                 $9::vector, $10, 'generated', 'approved', 1, TRUE)
            ON CONFLICT (id) DO UPDATE SET
                prompt_template = EXCLUDED.prompt_template,
                follow_up_prompts = EXCLUDED.follow_up_prompts,
                embedding = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model
            """,
            aid, _COMPETENCY, _QUESTION_TYPE, fam, difficulty,
            q["prompt_template"], json.dumps(q["follow_up_prompts"]),
            json.dumps({"jit": True, "job_title": job_title}),
            _to_pgvector(vec), settings.bedrock_embedding_model_id,
        )
        rows.append({
            "id": str(aid),
            "category": "domain",
            "competency": _COMPETENCY,
            "question_type": _QUESTION_TYPE,
            "role_family": fam,
            "industry": None,
            "seniority": None,
            "difficulty": difficulty,
            "prompt_template": q["prompt_template"],
            "follow_up_prompts": q["follow_up_prompts"],
            "scoring_guidance": {},
            "distance": 0.0,  # JD-generated — treat as closest (most job-specific)
        })
    log.info(
        "JIT generated %d domain question(s) for uncovered role '%s' (family=%s, difficulty=%s) — "
        "served WITHOUT human review (Constitution II relaxation for uncovered roles)",
        len(rows), job_title, fam, difficulty,
    )
    return rows


async def generate_general_questions(
    resume_summary: str,
    job_title: str,
    job_description: str,
    difficulty: str,
    n: int = 4,
) -> list[dict]:
    """Generate + persist + embed GENERAL/behavioral questions and return them as plan-ready rows
    (same dict shape as generate_domain_questions / retrieve_ranked). Returns [] on any failure so
    the caller can fall back without blocking prep.

    F009 / Constitution VII: this is the piece that lets a bank-LESS deploy still run a complete
    interview — previously general questions came only from the vetted bank. Runs ONLY in the prep
    window (off the response_gap clock). Rows are marked source='generated' + a 'generative' marker so
    they are auditable and distinguishable from human-vetted rows (Principle VII b)."""
    try:
        questions = _generate_general(resume_summary, job_title, job_description, difficulty, n)
    except Exception as exc:  # noqa: BLE001 - generation failure must not block prep
        log.warning("Generative general-question generation failed (%s)", type(exc).__name__)
        return []
    if not questions:
        return []

    pool = await db.get_pool()
    rows: list[dict] = []
    for i, q in enumerate(questions):
        # Idempotent across re-prep: a 'general' namespace keyed by difficulty + index + prompt prefix.
        aid = uuid.uuid5(_JIT_NS, f"general:{difficulty}:{i}:{q['prompt_template'][:60]}")
        try:
            vec = _embed(q["prompt_template"])
        except Exception as exc:  # noqa: BLE001 - skip a question we cannot embed
            log.warning("Generative general embed failed for one question (%s); skipping", type(exc).__name__)
            continue
        await pool.execute(
            """
            INSERT INTO question_archetype
                (id, category, competency, question_type, industry, role_family, seniority,
                 difficulty, prompt_template, follow_up_prompts, scoring_guidance,
                 embedding, embedding_model, source, status, version, active)
            VALUES
                ($1, 'general', $2, $3, NULL, NULL, NULL,
                 $4, $5, $6::jsonb, $7::jsonb,
                 $8::vector, $9, 'generated', 'approved', 1, TRUE)
            ON CONFLICT (id) DO UPDATE SET
                competency = EXCLUDED.competency,
                question_type = EXCLUDED.question_type,
                prompt_template = EXCLUDED.prompt_template,
                follow_up_prompts = EXCLUDED.follow_up_prompts,
                embedding = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model
            """,
            aid, q["competency"], q["question_type"], difficulty,
            q["prompt_template"], json.dumps(q["follow_up_prompts"]),
            json.dumps({"jit": True, "generative": True}),
            _to_pgvector(vec), settings.bedrock_embedding_model_id,
        )
        rows.append({
            "id": str(aid),
            "category": "general",
            "competency": q["competency"],
            "question_type": q["question_type"],
            "role_family": None,
            "industry": None,
            "seniority": None,
            "difficulty": difficulty,
            "prompt_template": q["prompt_template"],
            "follow_up_prompts": q["follow_up_prompts"],
            "scoring_guidance": {},
            "distance": 0.0,
        })
    log.info(
        "Generative mode produced %d general question(s) (difficulty=%s) — served WITHOUT human "
        "review (Constitution VII relaxation)",
        len(rows), difficulty,
    )
    return rows
