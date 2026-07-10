"""Static checks against the 6 generated batch notebooks.

These tests don't execute notebooks; they parse the JSON and assert that
each per-model notebook references the right primary instance type, the
right HF repo id, and contains no customer-specific terminology that
slipped past the genericization pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NB_DIR = REPO / "notebooks"


# (package, expected_primary_instance, expected_hf_repo)
_MODELS: list[tuple[str, str, str]] = [
    ("medgemma_27b",          "g7e.2xlarge", "google/medgemma-27b-text-it"),
    ("qwen3_8b",              "g7e.2xlarge", "Qwen/Qwen3-8B"),
    ("mistral_small_3_2_24b", "g7e.2xlarge", "mistralai/Mistral-Small-3.2-24B-Instruct-2506"),
    ("qwen3_30b_a3b",         "g7e.2xlarge", "Qwen/Qwen3-30B-A3B-Instruct-2507"),
    ("gemma_4_31b",           "g7e.2xlarge", "google/gemma-4-31B-it"),
    ("llama_4_scout_17b",     "p4d.24xlarge", "meta-llama/Llama-4-Scout-17B-16E-Instruct"),
]


_FORBIDDEN_TERMS = (
    "depression", "panic", "psychiatric", "ADHD", "PTSD", "OCD",
    "schizophren", "drug extraction", "medication review",
)


def _load_notebook_text(model: str) -> str:
    path = NB_DIR / f"{model}_batch.ipynb"
    nb = json.loads(path.read_text())
    chunks: list[str] = []
    for cell in nb["cells"]:
        src = cell.get("source", [])
        if isinstance(src, list):
            chunks.append("".join(src))
        else:
            chunks.append(str(src))
    return "\n".join(chunks)


@pytest.mark.parametrize("model,primary_instance,hf_repo", _MODELS)
def test_notebook_references_correct_primary_instance(
    model: str, primary_instance: str, hf_repo: str,
) -> None:
    text = _load_notebook_text(model)
    assert primary_instance in text, (
        f"{model}_batch.ipynb missing reference to its primary instance "
        f"{primary_instance!r}"
    )
    assert hf_repo in text, (
        f"{model}_batch.ipynb missing HF repo id {hf_repo!r}"
    )


@pytest.mark.parametrize("model", [m for m, *_ in _MODELS])
def test_notebook_has_no_customer_specific_terms(model: str) -> None:
    text = _load_notebook_text(model)
    for term in _FORBIDDEN_TERMS:
        assert term.lower() not in text.lower(), (
            f"{model}_batch.ipynb contains forbidden term {term!r}"
        )


def test_notebook_for_llama_does_not_claim_g7e() -> None:
    """Specific regression: the wait-time markdown used to hard-code
    'g7e.2xlarge container' for every model, which was wrong for Llama-4-Scout.
    Make sure that string never appears in the llama notebook."""
    text = _load_notebook_text("llama_4_scout_17b")
    assert "g7e.2xlarge container" not in text
    assert "p4d.24xlarge container" in text


# Gated models include section 2.5 (HF token upsert); ungated models skip it.
_GATED = {"medgemma_27b", "llama_4_scout_17b"}


@pytest.mark.parametrize("model", [m for m, *_ in _MODELS])
def test_notebook_gating_section_consistency(model: str) -> None:
    text = _load_notebook_text(model)
    if model in _GATED:
        assert "PLACEHOLDER_PASTE_YOUR_HF_TOKEN" in text, (
            f"{model} is gated but notebook is missing the HF token placeholder"
        )
        assert "Upsert the HuggingFace token" in text
    else:
        assert "PLACEHOLDER_PASTE_YOUR_HF_TOKEN" not in text, (
            f"{model} is ungated but notebook still has the HF token placeholder cell"
        )
        assert "Upsert the HuggingFace token" not in text


@pytest.mark.parametrize("model,primary_instance,hf_repo", _MODELS)
def test_notebook_uses_correct_sample_data_domain(
    model: str, primary_instance: str, hf_repo: str,
) -> None:
    text = _load_notebook_text(model)
    # All current batch models use the travel-booking dataset.
    assert "sample-data/travel/" in text or "sample-data\" / \"travel" in text or \
           "synthesized travel-booking" in text
