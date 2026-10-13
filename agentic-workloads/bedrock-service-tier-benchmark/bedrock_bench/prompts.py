"""Deterministic, cache-aware prompt generation.

Every request is built from three parts (``docs/DESIGN.md`` section 3):

* a **document** sized to the workload (synthetic English prose drawn from a
  fixed word bank with a seeded RNG, so runs are reproducible),
* a **question** asking for a bounded answer,
* for ``cold`` requests, a random **nonce** as the very first line, so no two
  requests share a prefix and Bedrock's implicit prompt cache cannot hit.

Token targets are approximate (~0.75 words per token for English); the actual
input tokens reported by the API are recorded for every request.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

from .config import CacheMode, PromptSize

_WORDS = (
    "system latency request region model token cache tier service network queue "
    "throughput capacity scaling budget customer workload inference stream response "
    "prompt context window document summary analysis report metric percentile median "
    "benchmark compute accelerator memory bandwidth batch schedule priority flexible "
    "standard reserved quota limit retry timeout endpoint gateway runtime provider "
    "architecture design pattern review decision tradeoff cost performance reliability "
    "security encryption identity access policy audit compliance resilience recovery "
    "storage database index query partition replica snapshot backup archive lifecycle "
    "pipeline deployment release version rollback canary monitor alarm dashboard trace"
).split()

_QUESTIONS = (
    "Summarise the document above in five concise bullet points.",
    "List the five most frequent themes in the document above, one line each.",
    "Write a short paragraph describing what the document above is about.",
    "Give five short recommendations based on the document above.",
)

#: Approximate English words per token.
_WORDS_PER_TOKEN = 0.75


def _sentence(rng: random.Random) -> str:
    n = rng.randint(8, 18)
    words = [rng.choice(_WORDS) for _ in range(n)]
    return " ".join(words).capitalize() + "."


def make_document(target_tokens: int, rng: random.Random) -> str:
    """Synthetic prose of roughly ``target_tokens`` tokens."""
    target_words = max(int(target_tokens * _WORDS_PER_TOKEN), 16)
    out: list[str] = []
    count = 0
    while count < target_words:
        para = [_sentence(rng) for _ in range(rng.randint(4, 8))]
        text = " ".join(para)
        out.append(text)
        count += len(text.split())
    return "\n\n".join(out)


@dataclass(frozen=True)
class Prompt:
    document: str
    question: str
    #: True when this request must not hit any cache (cold).
    expect_cold: bool


class PromptFactory:
    """Builds prompts for one cell according to its size and cache mode.

    * ``cold``: nonce + fresh document per request.
    * ``warm_*``: one shared document per cell (no nonce) and a fresh question
      suffix per request, so only the document prefix can be served from cache.
    """

    def __init__(self, size: PromptSize, cache: CacheMode, seed: int, cell_key: str):
        self.size = size
        self.cache = cache
        self._rng = random.Random(f"{seed}:{cell_key}")  # nosec B311 - synthetic prompt text, not security-sensitive
        self._shared: str | None = None
        self._i = 0

    def next(self) -> Prompt:
        self._i += 1
        q = _QUESTIONS[self._i % len(_QUESTIONS)]
        if self.cache is CacheMode.COLD:
            # uuid4 makes the prefix unique even across runs with the same seed.
            doc = f"Request {uuid.uuid4()}\n\n" + make_document(self.size.input_tokens, self._rng)
            return Prompt(document=doc, question=q, expect_cold=True)
        if self._shared is None:
            self._shared = make_document(self.size.input_tokens, self._rng)
        # A unique suffix keeps the response from being a cached completion while the
        # document prefix stays identical for the prompt cache.
        return Prompt(
            document=self._shared,
            question=f"{q} (request {self._i})",
            expect_cold=False,
        )
