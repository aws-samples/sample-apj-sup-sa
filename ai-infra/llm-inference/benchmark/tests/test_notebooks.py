"""Static checks against the 6 generated benchmark notebooks.

These tests don't execute notebooks; they parse the JSON and assert that
each per-model notebook references the right HF repo id, the right number
of experiments, and contains no customer-specific terminology.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO / "models"


# (package, nb_filename, hf_repo, expected_experiment_count)
_MODELS: list[tuple[str, str, str, int]] = [
    ("medgemma_27b",          "medgemma-27b-vllm-ec2-benchmark.ipynb",
     "google/medgemma-27b-text-it", 7),
    ("qwen3_8b",              "qwen3-8b-vllm-ec2-benchmark.ipynb",
     "Qwen/Qwen3-8B", 7),
    ("mistral_small_3_2_24b", "mistral-small-3-2-24b-vllm-ec2-benchmark.ipynb",
     "mistralai/Mistral-Small-3.2-24B-Instruct-2506", 7),
    ("qwen3_30b_a3b",         "qwen3-30b-a3b-vllm-ec2-benchmark.ipynb",
     "Qwen/Qwen3-30B-A3B-Instruct-2507", 7),
    ("gemma_4_31b",           "gemma-4-31b-vllm-ec2-benchmark.ipynb",
     "google/gemma-4-31B-it", 7),
    ("llama_4_scout_17b",     "llama-4-scout-17b-vllm-ec2-benchmark.ipynb",
     "meta-llama/Llama-4-Scout-17B-16E-Instruct", 2),
    # Additional models added later:
    ("gpt_oss_20b",           "gpt-oss-20b-vllm-ec2-benchmark.ipynb",
     "openai/gpt-oss-20b", 6),
    ("qwen3_coder_next",      "qwen3-coder-next-vllm-ec2-benchmark.ipynb",
     "Qwen/Qwen3-Coder-Next", 4),
]


_FORBIDDEN_TERMS = (
    "depression", "panic", "psychiatric", "ADHD", "PTSD", "OCD",
    "schizophren", "drug extraction", "medication review",
)


def _notebook_text(package: str, filename: str) -> str:
    path = MODELS_DIR / package / filename
    nb = json.loads(path.read_text())
    chunks: list[str] = []
    for cell in nb["cells"]:
        src = cell.get("source", [])
        if isinstance(src, list):
            chunks.append("".join(src))
        else:
            chunks.append(str(src))
    return "\n".join(chunks)


@pytest.mark.parametrize("package,filename,hf_repo,n_exp", _MODELS)
def test_notebook_references_hf_repo(
    package: str, filename: str, hf_repo: str, n_exp: int,
) -> None:
    text = _notebook_text(package, filename)
    assert hf_repo in text, (
        f"{filename} missing HF repo id {hf_repo!r}"
    )


@pytest.mark.parametrize("package,filename,hf_repo,n_exp", _MODELS)
def test_notebook_experiment_count(
    package: str, filename: str, hf_repo: str, n_exp: int,
) -> None:
    text = _notebook_text(package, filename)
    # Each generated notebook contains a markdown header per experiment of the
    # form "## Experiment N — <instance>" or "Experiment N —".
    found = sum(1 for i in range(1, 10) if f"Experiment {i}" in text)
    assert found == n_exp, (
        f"{filename} declares {found} experiments, expected {n_exp}"
    )


@pytest.mark.parametrize("package,filename,hf_repo,n_exp", _MODELS)
def test_notebook_has_no_customer_specific_terms(
    package: str, filename: str, hf_repo: str, n_exp: int,
) -> None:
    text = _notebook_text(package, filename).lower()
    for term in _FORBIDDEN_TERMS:
        assert term.lower() not in text, (
            f"{filename} contains forbidden term {term!r}"
        )
