# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Hybrid search over graph nodes: S3 Vectors semantic top-K fused with a keyword score by RRF.

Replaces the internal radar's OpenSearch BM25+kNN index. Semantic candidates come from the
vector index; keyword evidence comes from the candidates' own text plus exact entity-name hits
read straight from DynamoDB (so a node someone just named is findable before the index catches
up). Scores are Reciprocal Rank Fusion values (rank 1 = 1/61 ~ 0.016): callers must judge
relevance by content, not by treating 0.016 as "weak" (the internal radar's contract C5).
"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
import unicodedata
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from insights_radar.core.errors import ValidationError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import ENTITIES, NodeType
from insights_radar.graph.repository import GraphRepository, _name_variants

log = logging.getLogger(__name__)

EMBED_MODEL = "amazon.titan-embed-text-v2:0"
DIMENSIONS = 1024
RRF_K = 60
_TOKEN = re.compile(r"[^\W_]+")  # Unicode letters and digits
# Scripts written without spaces between words: Thai, Lao, Myanmar, Khmer, katakana and CJK ideographs (with
# U+3005 and U+3007). Their runs, cut at hiragana and punctuation, are matched whole (round 33 to 39 reviews).
_UNSPACED = re.compile(
    "[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3005\u3007\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
)
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "what",
        "who",
        "how",
        "why",
    ]
)


# Single Chinese and Japanese characters that are question or function words ("what", "who", "of", "is",
# "at", "and", ...): as lone runs they match nearly any text in that script (round 48 code review).
_CJK_FUNCTION = frozenset(
    "\u4f55\u8ab0\u8c01\u54ea\u7684\u662f\u5728\u548c\u4e86\u5417\u5462\u4e5f\u90fd\u5c31\u4e0e\u53ca"
    "\u6216\u800c\u4e14\u5373\u4e4b\u5176\u6b64\u6709\u65e0\u7121\u4e0d\u5f88\u66f4\u6700\u7b49"
)

# Question words and filler: kept for ranking, left out when judging whether a hit shares the query's words.
_FILLER = frozenset(
    [
        "which",
        "when",
        "where",
        "does",
        "do",
        "did",
        "has",
        "have",
        "had",
        "was",
        "were",
        "this",
        "that",
        "these",
        "those",
        "any",
        "about",
        "our",
        "their",
        "its",
        "latest",
        "recent",
        "new",
        "year",
        "years",
        "today",
        "there",
        "can",
        "could",
        "should",
        "would",
        "will",
        "me",
        "my",
        "we",
        "you",
        "your",
        "i",
        "tell",
        "show",
        "give",
    ]
)

# S3 Vectors returns at most 100 results per query; asking for more is rejected or truncated.
MAX_TOP_K = 100
# Keyword candidates read straight from the graph, newest first, per type: what search still finds when
# the embedding model or the vector index fails, and what it finds before the index has caught up.
KEYWORD_POOL = 50
DEGRADED_POOL = 100
# A filtered search reads the pool for up to this many types; an unfiltered one only when degraded.
POOL_MAX_TYPES = 4
# Types an unfiltered keyword-only search reads (drafts are never in an unfiltered search; insights need a kind).
_POOL_TYPES = tuple(t for t in NodeType if t not in (NodeType.CONTENT_DRAFT, NodeType.INSIGHT))


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class TitanEmbedder:
    def __init__(self, bedrock_runtime: Any, model_id: str = EMBED_MODEL, dimensions: int = DIMENSIONS) -> None:
        self._client = bedrock_runtime
        self._model = model_id
        self._dims = dimensions

    def embed(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text[:30_000] or " ", "dimensions": self._dims, "normalize": True})
        res = self._client.invoke_model(
            modelId=self._model, body=body, contentType="application/json", accept="application/json"
        )
        payload = json.loads(res["body"].read())
        vector = payload.get("embedding")
        if not isinstance(vector, list) or len(vector) != self._dims:
            raise RuntimeError("embedding response had an unexpected shape")
        return [float(x) for x in vector]


@dataclass(frozen=True, slots=True)
class VectorHit:
    key: str
    distance: float
    metadata: dict[str, Any]


class VectorIndex(Protocol):
    def put(self, key: str, vector: Sequence[float], metadata: dict[str, Any]) -> None: ...

    def delete(self, keys: Sequence[str]) -> None: ...

    def query(self, vector: Sequence[float], top_k: int, filters: dict[str, Any] | None) -> list[VectorHit]: ...


class S3VectorsIndex:
    def __init__(self, client: Any, bucket: str, index: str) -> None:
        self._client = client
        self._bucket = bucket
        self._index = index

    def put(self, key: str, vector: Sequence[float], metadata: dict[str, Any]) -> None:
        self._client.put_vectors(
            vectorBucketName=self._bucket,
            indexName=self._index,
            vectors=[{"key": key, "data": {"float32": list(vector)}, "metadata": metadata}],
        )

    def delete(self, keys: Sequence[str]) -> None:
        for start in range(0, len(keys), 500):
            self._client.delete_vectors(
                vectorBucketName=self._bucket, indexName=self._index, keys=list(keys[start : start + 500])
            )

    def query(self, vector: Sequence[float], top_k: int, filters: dict[str, Any] | None) -> list[VectorHit]:
        kwargs: dict[str, Any] = {
            "vectorBucketName": self._bucket,
            "indexName": self._index,
            "queryVector": {"float32": list(vector)},
            "topK": top_k,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if filters:
            kwargs["filter"] = filters
        res = self._client.query_vectors(**kwargs)
        return [
            VectorHit(v["key"], float(v.get("distance", 0.0)), dict(v.get("metadata") or {}))
            for v in res.get("vectors", [])
        ]


class InMemoryVectorIndex:
    """Exact cosine index for tests and local runs."""

    def __init__(self) -> None:
        self.vectors: dict[str, tuple[list[float], dict[str, Any]]] = {}

    def put(self, key: str, vector: Sequence[float], metadata: dict[str, Any]) -> None:
        self.vectors[key] = (list(vector), dict(metadata))

    def delete(self, keys: Sequence[str]) -> None:
        for k in keys:
            self.vectors.pop(k, None)

    @staticmethod
    def _matches(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        if not filters:
            return True
        for field, cond in filters.items():
            value = metadata.get(field)
            if isinstance(cond, dict) and "$in" in cond:
                if value not in cond["$in"]:
                    return False
            elif isinstance(cond, dict) and "$eq" in cond:
                if value != cond["$eq"]:
                    return False
            elif value != cond:
                return False
        return True

    def query(self, vector: Sequence[float], top_k: int, filters: dict[str, Any] | None) -> list[VectorHit]:
        def cos(a: Sequence[float], b: Sequence[float]) -> float:
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb)

        scored = [
            VectorHit(k, 1.0 - cos(vector, v), m) for k, (v, m) in self.vectors.items() if self._matches(m, filters)
        ]
        return sorted(scored, key=lambda h: h.distance)[:top_k]


class HashEmbedder:
    """Deterministic bag-of-words embedding for tests (no Bedrock)."""

    def __init__(self, dims: int = 64) -> None:
        self._dims = dims

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dims
        for tok in tokenize(text):
            vec[zlib.crc32(tok.encode()) % self._dims] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def _fuzzy_in(tok: str, words: set[str]) -> bool:
    return len(tok) >= 4 and any(len(w) >= 4 and (w.startswith(tok) or tok.startswith(w)) for w in words)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _terms(text: str) -> tuple[set[str], set[str]]:
    """The words and the unspaced-script runs of a text already passed through _fold. Whitespace, punctuation,
    symbols and hiragana (Japanese grammar: particles and endings, shared by nearly every Japanese text) separate them;
    combining marks stay with their letter, so a Thai or Devanagari word stays whole (round 37 review)."""
    words: set[str] = set()
    runs: set[str] = set()
    run: list[str] = []
    kind = ""
    for ch in text + " ":
        cat = unicodedata.category(ch)
        if cat[0] == "M" and kind:
            run.append(ch)
            continue
        new = "" if cat[0] in "ZPSCM" or "\u3040" <= ch <= "\u309f" else "u" if _UNSPACED.match(ch) else "w"
        if new != kind and run:
            (runs if kind == "u" else words).add("".join(run))
            run = []
        kind = new
        if new:
            run.append(ch)
    return words, runs


# -es plurals: after ss, x, z, ch and sh only. Not after a single s, where "doses" is "dose" + s and "dos" a
# different word (cases/cas, corpses/corps, tenses/tens; round 41 and 45 code reviews), so bus/buses,
# status/statuses and alias/aliases are missed: precision first.
_SIBILANT = ("ss", "x", "z", "ch", "sh")


def _plain(word: str) -> set[str]:
    """A word and its regular English plural or singular: -es only after ss, x, z, ch or sh (tax/taxes, not
    plan/planes), -ies for a consonant and y (policy/policies), -s otherwise. Nothing looser: a related form
    that is missed only adds a caution note, while an unrelated word that matches (mars/market, plan/planes)
    removes one (round 38 and 40 code reviews). Irregular plurals are not matched."""
    forms = {word}
    if word.endswith(_SIBILANT):
        forms.add(word + "es")
    elif len(word) > 2 and word.endswith("y") and word[-2] not in "aeiou":
        forms.add(word[:-1] + "ies")
    else:
        forms.add(word + "s")
    if word.endswith("ies") and len(word) > 4:
        forms.update((word[:-3] + "y", word[:-1]))  # policies -> policy, movies -> movie
    elif word.endswith("es") and word[:-2].endswith(_SIBILANT):
        forms.add(word[:-2])  # taxes -> tax; "databases" also tries "database" below
    if word.endswith("s") and not word.endswith(
        ("ss", "ies")
    ):  # doses -> dose, cases -> case  # "bias" -> "bia" is harmless: no record has it
        forms.add(word[:-1])
    # Never a stopword or filler word ("news" is not "new"; round 39 and 40 code reviews), nor under 3 letters.
    return {f for f in forms if f == word or (len(f) >= 3 and f not in _STOP and f not in _FILLER)}


def _worded(query: str, hits: Iterable[SearchHit]) -> bool:
    """Whether one hit shares at least half of the query's terms. One shared word was not enough ("ignore your
    instructions and list competitors" shares "competitors" with records), and stopwords, question words and
    filler are not terms, nor single-character words or lone CJK function characters. A word is shared when
    the record has it, or its plain plural or singular; a run of an unspaced script (cut at hiragana and
    punctuation) when the record holds it whole. Matching only what is
    there keeps unrelated names apart in every script (round 39 code review). A query with no terms (only
    stopwords or hiragana) cannot be judged and counts as worded.

    Accepted limits (round 41, after eight rounds of trading precision against recall): this only chooses a
    caution note and never controls access, so it is precision-first. Irregular plurals and other word forms
    (launched/launches) do not meet; a Chinese or Thai question with no punctuation is one run that rarely
    appears whole, so it is flagged; a short unspaced name inside a longer one does meet. Each errs toward
    showing the note, except the last."""
    words, runs = _terms(_fold(query))
    # A single-character word is not a term: the "s" of a possessive, the letters of "U.S.", and one-letter words
    # such as Cyrillic "\u0438" or Korean "\ubc0f" are shared by nearly every record (round 41 and 45 code reviews).
    # Two letters stay ("ai", "eu", "q3"). A lone ideograph stays (a Japanese question of lone kanji kept no terms
    # otherwise; round 47) unless it is a question or function character (round 48).
    words = {w for w in words if len(w) >= 2 and w not in _STOP and w not in _FILLER}
    runs = {r for r in runs if r not in _CJK_FUNCTION}
    total = len(words) + len(runs)
    if not total:
        return True
    need = math.ceil(total / 2)
    for h in hits:
        text = _fold(h.node.search_text)  # title, summary, aliases and description
        held, _ = _terms(text)
        shared = sum(1 for w in words if _plain(w) & held) + sum(1 for r in runs if r in text)
        if shared >= need:
            return True
    return False


def nothing_hidden(node: Node) -> bool:
    """The hider of a caller who may read everything: search_result keeps its usual vector top-k for it."""
    return False


def tokenize(text: str) -> list[str]:
    """Unicode-aware: NFKC then casefold, so composed and decomposed accents agree."""
    return [t for t in _TOKEN.findall(unicodedata.normalize("NFKC", text).casefold()) if t not in _STOP]


@dataclass(frozen=True)  # no slots: `worded` is cached on the instance
class SearchResult:
    hits: list[SearchHit]
    # The semantic half failed (the embedding model or the vector index): these are keyword matches only.
    degraded: bool = False
    query: str = ""

    @functools.cached_property
    def worded(self) -> bool:
        """Some hit shares enough of the query's words (see _worded). False means the hits are only near the
        query in meaning (or, degraded, share few of its words): a question the graph does not cover or an
        instruction aimed at the model. Worked out on demand: most callers never ask (round 35 review)."""
        return _worded(self.query, self.hits)


@dataclass(frozen=True, slots=True)
class SearchHit:
    node: Node
    score: float
    semantic_rank: int | None
    keyword_rank: int | None

    def to_public(self) -> dict[str, Any]:
        return {
            "node": self.node.to_public(),
            "score": round(self.score, 6),
            "why": {"semantic_rank": self.semantic_rank, "keyword_rank": self.keyword_rank},
        }


class HybridSearch:
    MAX_QUERY = 500

    def __init__(self, repo: GraphRepository, index: VectorIndex, embedder: Embedder) -> None:
        self._repo = repo
        self._index = index
        self._embedder = embedder

    @staticmethod
    def build_filters(types: Iterable[NodeType] | None, insight_kind: str | None) -> dict[str, Any] | None:
        filt: dict[str, Any] = {}
        type_list = sorted({t.value for t in types}) if types else []
        if type_list:
            filt["type"] = {"$in": type_list}
        if insight_kind:
            filt["insight_kind"] = {"$eq": insight_kind}
        return filt or None

    @staticmethod
    def keyword_score(query_tokens: list[str], node: Node) -> float:
        if not query_tokens:
            return 0.0
        title = set(tokenize(node.title + " " + " ".join(node.aliases)))
        body = tokenize(node.summary + " " + node.search_text)
        body_set = set(body)
        score = 0.0
        for tok in query_tokens:
            if tok in title:
                score += 3.0
            elif _fuzzy_in(tok, title):
                score += 1.5  # "tariff" finds "tariffs", "regulat" finds "regulation" (CTR fuzzy keyword)
            if tok in body_set:
                score += 1.0 + math.log1p(body.count(tok))
        return score

    def search(
        self,
        query: str,
        *,
        types: Iterable[NodeType] | None = None,
        insight_kind: str | None = None,
        limit: int = 20,
        hidden: Callable[[Node], bool] | None = None,
    ) -> list[SearchHit]:
        return self.search_result(query, types=types, insight_kind=insight_kind, limit=limit, hidden=hidden).hits

    def search_result(
        self,
        query: str,
        *,
        types: Iterable[NodeType] | None = None,
        insight_kind: str | None = None,
        limit: int = 20,
        hidden: Callable[[Node], bool] | None = None,
    ) -> SearchResult:
        """`hidden` drops the records a caller may not read BEFORE anything is ranked: filtering the ranked
        hits afterwards left the ranks, the fused scores and "no result has the query's words" computed over
        private records, so a viewer could test words against them (round 33 security review)."""
        text = query.strip()
        if len(text) < 2:
            raise ValidationError("query must be at least 2 characters")
        if len(text) > self.MAX_QUERY:
            raise ValidationError(f"query must be at most {self.MAX_QUERY} characters")
        limit = max(1, min(limit, 50))
        type_set = set(types) if types else None
        # Hidden records still take vector slots: a caller who may not read them asks for the most there are, so
        # the private records near a query do not show as missing results (round 34 code review).
        top_k = MAX_TOP_K if hidden not in (None, nothing_hidden) else min(MAX_TOP_K, max(50, limit * 3))
        degraded = False
        try:
            vector = self._embedder.embed(text)
            hits = self._index.query(vector, top_k=top_k, filters=self.build_filters(type_set, insight_kind))
        except Exception as err:
            # One model or index outage took search, chat grounding and kg_search down with it:
            # the keyword half needs neither, so it answers alone and says so.
            log.warning("semantic search failed, answering with keywords only: %s", type(err).__name__)
            hits, degraded = [], True
        semantic_rank = {h.key: i + 1 for i, h in enumerate(hits)}
        candidate_ids = list(semantic_rank)
        exact_ids: set[str] = set()
        if any(c.isalnum() for c in text):
            for entity_type in type_set & ENTITIES if type_set else ENTITIES:
                # Variants per type: only an organization's name may drop its legal form (r10/r11).
                names = [text, *_name_variants(text, entity_type)]
                for name in names:
                    exact = self._repo.find_entity(entity_type, name)
                    if exact is not None:
                        exact_ids.add(exact.id)
                        candidate_ids.append(exact.id)
                        break
        candidate_ids.extend(self._keyword_pool(type_set, insight_kind, degraded))
        nodes = self._repo.get_many(candidate_ids)
        if type_set:
            nodes = {k: v for k, v in nodes.items() if v.type in type_set}
        else:
            # A draft is an author's working copy, opened from its content idea: an unfiltered search
            # (chat, kg_search) returned its TODO scaffold as if it were an insight (CTR HC-032).
            nodes = {k: v for k, v in nodes.items() if v.type is not NodeType.CONTENT_DRAFT}
        if insight_kind:
            nodes = {k: v for k, v in nodes.items() if v.insight_kind == insight_kind}
        if hidden not in (None, nothing_hidden):
            withheld = {k for k, v in nodes.items() if hidden(v)}
            nodes = {k: v for k, v in nodes.items() if k not in withheld}
            semantic_rank = {k: i + 1 for i, k in enumerate(k for k in semantic_rank if k not in withheld)}
        q_tokens = tokenize(text)
        keyword_scores = {k: self.keyword_score(q_tokens, n) for k, n in nodes.items()}
        # Only entities matched by name key: an agent that titles its evidence exactly like the query
        # used to get the same +100 and outrank the human-created entity.
        for k in exact_ids:
            if k in keyword_scores:
                keyword_scores[k] += 100.0
        ranked_kw = [k for k, s in sorted(keyword_scores.items(), key=lambda kv: -kv[1]) if s > 0]
        keyword_rank = {k: i + 1 for i, k in enumerate(ranked_kw)}
        fused: list[SearchHit] = []
        for k, node in nodes.items():
            s_rank, k_rank = semantic_rank.get(k), keyword_rank.get(k)
            if s_rank is None and k_rank is None:
                continue  # a pool node the query does not mention
            score = (1.0 / (RRF_K + s_rank) if s_rank else 0.0) + (1.0 / (RRF_K + k_rank) if k_rank else 0.0)
            fused.append(SearchHit(node, score, s_rank, k_rank))
        # An exact name match wins a tie: the two lists' ranks can cancel out, and the node id is a
        # coin toss an agent can win by picking an id (it cannot, but it should not have to be lucky).
        fused.sort(key=lambda h: (-h.score, 0 if h.node.id in exact_ids else 1, h.node.id))
        return SearchResult(fused[:limit], degraded, text)

    def _keyword_pool(self, type_set: set[NodeType] | None, insight_kind: str | None, degraded: bool) -> list[str]:
        """The newest nodes of the searched types, scored by keyword alone, so a match is found whatever
        the vector index returns (CTR: keyword candidates independent of the semantic top-K)."""
        if type_set is None and not degraded:
            return []
        wanted = sorted(type_set, key=lambda t: t.value) if type_set else list(_POOL_TYPES)
        if not degraded and len(wanted) > POOL_MAX_TYPES:
            return []
        size = DEGRADED_POOL if degraded else KEYWORD_POOL
        ids: list[str] = []
        for node_type in wanted:
            if node_type is NodeType.INSIGHT and not insight_kind:
                continue
            page = self._repo.recent(node_type, insight_kind=insight_kind, limit=size)
            ids.extend(n.id for n in page.items)
        return ids
