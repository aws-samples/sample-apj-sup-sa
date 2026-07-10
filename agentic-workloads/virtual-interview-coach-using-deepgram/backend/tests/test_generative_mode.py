"""Generative Mode (F009 / Constitution VII) — assemble_blueprint decision-logic tests.

These exercise the prep-window branching WITHOUT a database: retrieval, both generators, the JD
embed, and the two DB writes (create_blueprint / set_session_plan) are monkeypatched, so the test
asserts WHICH path fires and the resulting honesty flag — not pgvector behavior (that is covered by
test_session_prep.py against the live container).

Maps to specs/001-generative-mode/contracts/prep-generative-contract.md test assertions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
for p in (str(_BACKEND), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

pytestmark = pytest.mark.asyncio

JD_TITLE = "Software Engineer"
JD_TEXT = "Backend engineer: design APIs, own microservices, debug production incidents."
DIFFICULTY = "easy"
NUM_Q = 6


def _general_row(i: int) -> dict:
    return {
        "id": f"gen-general-{i}", "category": "general", "competency": "motivation_fit",
        "question_type": "warmup" if i == 0 else "behavioral", "role_family": None,
        "industry": None, "seniority": None, "difficulty": DIFFICULTY,
        "prompt_template": f"general q{i}", "follow_up_prompts": ["probe"],
        "scoring_guidance": {}, "distance": 0.0, "source": "generated",
    }


def _domain_row(i: int) -> dict:
    return {
        "id": f"gen-domain-{i}", "category": "domain", "competency": "role_specific",
        "question_type": "technical", "role_family": "software_engineering",
        "industry": None, "seniority": None, "difficulty": DIFFICULTY,
        "prompt_template": f"domain q{i}", "follow_up_prompts": ["probe"],
        "scoring_guidance": {}, "distance": 0.0, "source": "generated",
    }


def _bank_general_row(i: int) -> dict:
    row = _general_row(i)
    row["id"] = f"bank-general-{i}"
    row["source"] = "curated"
    return row


@pytest.fixture
def patched(monkeypatch):
    """Patch the blueprint's external touch points; record which generators were called."""
    from src.prep import blueprint, retrieval, jit_generate

    calls = {"general": 0, "domain": 0, "retrieve": 0, "ranked_return": None}

    # JD embed: no Bedrock — deterministic stub vector.
    monkeypatch.setattr(blueprint, "_embed_jd", lambda t, d: [0.0] * 1024)

    # DB writes: no DB — just capture.
    async def fake_create_blueprint(session_id, competencies, ordered_ids, opening_id):
        return "bp-1"

    async def fake_set_session_plan(session_id, ordered_ids, blueprint_id, reduced):
        calls["reduced_persisted"] = reduced

    monkeypatch.setattr(blueprint.db, "create_blueprint", fake_create_blueprint)
    monkeypatch.setattr(blueprint.db, "set_session_plan", fake_set_session_plan)

    # Domain-match count drives include_domain; default 0 (uncovered) unless a test overrides.
    async def fake_count_domain_matches(difficulty, *, role_family=None, industry=None):
        return calls.get("domain_match_count", 0)

    monkeypatch.setattr(retrieval, "count_domain_matches", fake_count_domain_matches)

    async def fake_retrieve_ranked(jd, difficulty, **kw):
        calls["retrieve"] += 1
        return list(calls["ranked_return"] or [])

    monkeypatch.setattr(retrieval, "retrieve_ranked", fake_retrieve_ranked)

    async def fake_gen_general(resume, title, jd, diff, n=4):
        calls["general"] += 1
        return [_general_row(i) for i in range(max(1, n))] if calls.get("general_yield", True) else []

    async def fake_gen_domain(title, jd, diff, fam, n=6):
        calls["domain"] += 1
        return [_domain_row(i) for i in range(max(1, n))] if calls.get("domain_yield", True) else []

    monkeypatch.setattr(jit_generate, "generate_general_questions", fake_gen_general)
    monkeypatch.setattr(jit_generate, "generate_domain_questions", fake_gen_domain)

    return blueprint, calls


async def test_empty_bank_generates_full_plan(patched):
    """US1/SC-001: empty bank -> general + domain generated, plan returned, flagged reduced, no raise."""
    blueprint, calls = patched
    calls["ranked_return"] = []  # empty bank

    result = await blueprint.assemble_blueprint(
        "sess-1", JD_TITLE, JD_TEXT, DIFFICULTY, num_questions=NUM_Q, resume_summary="Jordan, SWE"
    )

    assert calls["general"] == 1, "general questions must be generated when the bank has none"
    assert calls["domain"] == 1, "domain questions must be generated for the uncovered role"
    assert result["ordered_archetype_ids"], "a non-empty plan must be produced"
    assert result["domain_coverage_reduced"] is True, "generated session must be flagged (VII c)"
    assert calls["reduced_persisted"] is True


async def test_generation_failure_raises(patched):
    """Edge case / FR-010: empty bank AND both generators yield nothing -> honest RuntimeError."""
    blueprint, calls = patched
    calls["ranked_return"] = []
    calls["general_yield"] = False
    calls["domain_yield"] = False

    with pytest.raises(RuntimeError) as exc:
        await blueprint.assemble_blueprint(
            "sess-2", JD_TITLE, JD_TEXT, DIFFICULTY, num_questions=NUM_Q
        )
    assert "could not prepare" in str(exc.value).lower()


async def test_uncovered_role_composes_general_and_domain(patched):
    """US2: empty general pool + uncovered role -> plan has BOTH generated general and domain rows."""
    blueprint, calls = patched
    calls["ranked_return"] = []

    result = await blueprint.assemble_blueprint(
        "sess-3", JD_TITLE, JD_TEXT, DIFFICULTY, num_questions=NUM_Q, resume_summary="x"
    )
    ids = result["ordered_archetype_ids"]
    assert any(i.startswith("gen-general-") for i in ids), "expected generated general rows"
    assert any(i.startswith("gen-domain-") for i in ids), "expected generated domain rows"


async def test_flag_off_bank_present_no_regression(patched, monkeypatch):
    """US3/SC-004: bank serves general+domain, flag off -> NO generation, not flagged reduced."""
    blueprint, calls = patched
    # Bank returns general rows; domain matches present so include_domain path is covered.
    calls["domain_match_count"] = 3
    calls["ranked_return"] = [_bank_general_row(0), _bank_general_row(1), _domain_row_as_bank()]

    # Ensure generative_mode defaults off regardless of ambient env.
    result = await blueprint.assemble_blueprint(
        "sess-4", JD_TITLE, JD_TEXT, DIFFICULTY, num_questions=NUM_Q, generative_mode=False
    )
    assert calls["general"] == 0, "no general generation when the bank has general rows"
    assert calls["domain"] == 0, "no domain generation when the role is covered"
    assert result["domain_coverage_reduced"] is False, "pure bank plan is not flagged reduced"


async def test_flag_on_forces_generation(patched):
    """US3: generative_mode forced -> generate even though the bank could serve; rows are generated."""
    blueprint, calls = patched
    calls["domain_match_count"] = 3
    calls["ranked_return"] = [_bank_general_row(0)]  # bank could serve, but we force generation

    result = await blueprint.assemble_blueprint(
        "sess-5", JD_TITLE, JD_TEXT, DIFFICULTY, num_questions=NUM_Q,
        resume_summary="x", generative_mode=True,
    )
    assert calls["retrieve"] == 0, "forced generative mode must skip the bank retrieval entirely"
    assert calls["general"] == 1 and calls["domain"] == 1
    assert result["domain_coverage_reduced"] is True
    assert all(i.startswith("gen-") for i in result["ordered_archetype_ids"])


def _domain_row_as_bank() -> dict:
    row = _domain_row(0)
    row["id"] = "bank-domain-0"
    row["source"] = "curated"
    return row
