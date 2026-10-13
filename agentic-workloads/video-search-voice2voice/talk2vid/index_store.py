"""On-disk video index + the retrieval used by the agent's tools.

Vectors live in a sidecar ``.npz`` so the whole index for a video loads in a few
milliseconds and search is a single in-memory matmul — no network hop on the
voice path. S3 holds the durable copy (see :func:`push_to_s3`).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from loguru import logger

from talk2vid import config, media

_WORD_RE = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "these", "those", "as",
    "do", "does", "did", "you", "i", "we", "they", "he", "she", "me", "my", "your", "what",
    "when", "where", "who", "how", "why", "show", "tell", "find", "about", "there", "here",
    "can", "could", "would", "should", "please", "video", "clip",
}
# Dropped only from verbatim phrase matching, where "show me the part where" is
# framing rather than content.
_FILLER = {
    "show", "me", "the", "a", "an", "part", "where", "when", "who", "what", "bit", "moment",
    "please", "find", "jump", "to", "go", "in", "which", "they", "he", "she", "it", "is",
    "are", "was", "were", "do", "does", "did", "about", "of", "on", "at", "that", "this",
    "talk", "talks", "talking", "says", "say", "said", "mention", "mentions", "mentioned",
}


# Cosine range observed between a Nova MME text query and video segments. Used
# as an absolute scale so a weak best match cannot be normalised up to 1.0.
SEMANTIC_FLOOR = 0.18
SEMANTIC_CEILING = 0.45
MIN_HIT_SCORE = 0.20


def tokens(text: str) -> list[str]:
    """Content tokens, for scoring."""
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def raw_tokens(text: str) -> list[str]:
    """All tokens, for verbatim phrase matching (keeps words like "video")."""
    return _WORD_RE.findall(text.lower())


@dataclass
class VideoIndex:
    video_id: str
    meta: dict
    segments: list[dict]
    vectors: np.ndarray | None = None
    vector_spans: np.ndarray | None = None  # shape (N, 2): start, end
    _df: dict[str, int] = field(default_factory=dict, repr=False)

    # --- properties ----------------------------------------------------------
    @property
    def title(self) -> str:
        return self.meta.get("title", self.video_id)

    @property
    def duration(self) -> float:
        return float(self.meta.get("duration", 0.0))

    @property
    def scenario(self) -> str:
        return self.meta.get("scenario", "general")

    @property
    def source_path(self) -> Path:
        """Where the media file actually is on this machine.

        The index records the absolute path it was ingested from, which does not
        exist inside a container or on another host, so fall back to the
        configured video directory by filename.
        """
        recorded = Path(self.meta["source_file"])
        if recorded.exists():
            return recorded
        by_name = config.VIDEO_DIR / recorded.name
        if by_name.exists():
            return by_name
        # Assets synced from S3 are named after the video id, not the original file.
        for candidate in sorted(config.VIDEO_DIR.glob(f"{self.video_id}.*")):
            if candidate.suffix.lower() in (".mp4", ".mov", ".m4v", ".webm"):
                return candidate
        return by_name

    def __post_init__(self) -> None:
        for seg in self.segments:
            seg.setdefault("speech", "")
            seg.setdefault("visual", "")
        for seg in self.segments:
            for tok in set(tokens(seg["speech"] + " " + seg["visual"])):
                self._df[tok] = self._df.get(tok, 0) + 1

    # --- retrieval -----------------------------------------------------------
    def semantic_scores(self, query_vec: np.ndarray) -> list[tuple[int, float]]:
        """Cosine similarity of the query against each embedded video segment,
        mapped onto this index's own segment list."""
        if self.vectors is None or self.vector_spans is None or not len(self.vectors):
            return []
        sims = self.vectors @ query_vec
        ranked: list[tuple[int, float]] = []
        for row, score in enumerate(sims):
            start, end = self.vector_spans[row]
            idx = self.segment_at((float(start) + float(end)) / 2)
            if idx is not None:
                ranked.append((idx, float(score)))
        best: dict[int, float] = {}
        for idx, score in ranked:
            best[idx] = max(best.get(idx, -1.0), score)
        return sorted(best.items(), key=lambda kv: -kv[1])

    def keyword_scores(self, query: str) -> list[tuple[int, float]]:
        """BM25-lite over speech + visual text; catches exact names and numbers
        that embeddings sometimes smooth away."""
        qt = tokens(query)
        if not qt:
            return []
        n = len(self.segments) or 1
        avg_len = sum(len(tokens(s["speech"] + " " + s["visual"])) for s in self.segments) / n or 1
        scored: list[tuple[int, float]] = []
        for i, seg in enumerate(self.segments):
            terms = tokens(seg["speech"] + " " + seg["visual"])
            if not terms:
                continue
            counts: dict[str, int] = {}
            for t in terms:
                counts[t] = counts.get(t, 0) + 1
            score = 0.0
            for t in set(qt):
                tf = counts.get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5))
                score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(terms) / avg_len))
            if score > 0:
                scored.append((i, score))
        return sorted(scored, key=lambda kv: -kv[1])

    def phrase_bonus(self, query: str) -> dict[int, float]:
        """Reward verbatim overlap with the query.

        "Show me where they talk about video understanding" should land on the
        segment that literally says those words, not on a segment that is merely
        about the same broad topic — which is exactly where pure vector search
        and rank fusion tend to drift.
        """
        qt = [t for t in raw_tokens(query) if t not in _FILLER]
        if len(qt) < 2:
            return {}
        lengths = sorted({len(qt), 4, 3, 2}, reverse=True)
        phrases = [
            " ".join(qt[i : i + n]) for n in lengths if n <= len(qt) for i in range(len(qt) - n + 1)
        ]
        bonus: dict[int, float] = {}
        for i, seg in enumerate(self.segments):
            haystack = " ".join(raw_tokens(seg["speech"] + " " + seg["visual"]))
            for phrase in phrases:
                if phrase and phrase in haystack:
                    # Longer verbatim matches count for more.
                    bonus[i] = max(bonus.get(i, 0.0), min(len(phrase.split()) / len(qt), 1.0))
        return bonus

    @staticmethod
    def _scale_semantic(scored: list[tuple[int, float]]) -> dict[int, float]:
        """Absolute cosine scale — an unrelated query must score ~0 everywhere."""
        span = SEMANTIC_CEILING - SEMANTIC_FLOOR
        out = {}
        for idx, cos in scored:
            value = (cos - SEMANTIC_FLOOR) / span
            if value > 0:
                out[idx] = min(value, 1.0)
        return out

    @staticmethod
    def _scale_lexical(scored: list[tuple[int, float]]) -> dict[int, float]:
        """Relative scale, floored: BM25 magnitudes are corpus-dependent."""
        usable = [(i, s) for i, s in scored if s > 0.5]
        if not usable:
            return {}
        hi = max(s for _, s in usable)
        return {i: s / hi for i, s in usable}

    def search(self, query: str, query_vec: np.ndarray | None, top_k: int = 3) -> list[dict]:
        """Weighted fusion of semantic similarity, lexical match and verbatim overlap.

        Weighted normalised scores beat reciprocal-rank fusion here: RRF throws
        away score magnitude, so a segment that is only vaguely related can tie
        with the one that actually contains the answer.
        """
        semantic_raw = self.semantic_scores(query_vec) if query_vec is not None else []
        lexical_raw = self.keyword_scores(query)[:20]
        semantic = self._scale_semantic(semantic_raw)
        lexical = self._scale_lexical(lexical_raw)
        phrases = self.phrase_bonus(query)

        detail: dict[int, dict] = {}
        for idx, score in semantic_raw:
            detail.setdefault(idx, {})["semantic"] = round(score, 4)
        for idx, score in lexical_raw:
            detail.setdefault(idx, {})["lexical"] = round(score, 3)

        fused: dict[int, float] = {}
        for idx in set(semantic) | set(lexical) | set(phrases):
            fused[idx] = (
                0.50 * semantic.get(idx, 0.0)
                + 0.35 * lexical.get(idx, 0.0)
                + 0.30 * phrases.get(idx, 0.0)
            )
            if phrases.get(idx):
                detail.setdefault(idx, {})["phrase"] = round(phrases[idx], 2)

        hits = []
        ranked = [(i, s) for i, s in sorted(fused.items(), key=lambda kv: -kv[1]) if s >= MIN_HIT_SCORE]
        for idx, score in ranked[:top_k]:
            seg = self.segments[idx]
            hits.append(
                {
                    "index": idx,
                    "start": seg["start"],
                    "end": seg["end"],
                    "timecode": media.hhmmss(seg["start"]),
                    "spoken_time": media.spoken_time(seg["start"]),
                    "speech": seg["speech"],
                    "visual": seg["visual"],
                    "score": round(score, 4),
                    "signals": detail.get(idx, {}),
                }
            )
        return hits

    # --- lookups -------------------------------------------------------------
    def segment_at(self, timestamp: float) -> int | None:
        if not self.segments:
            return None
        for i, seg in enumerate(self.segments):
            if seg["start"] <= timestamp < seg["end"]:
                return i
        return min(
            range(len(self.segments)),
            key=lambda i: abs((self.segments[i]["start"] + self.segments[i]["end"]) / 2 - timestamp),
        )

    def window(self, timestamp: float, radius: float = 15.0) -> list[dict]:
        return [s for s in self.segments if s["end"] > timestamp - radius and s["start"] < timestamp + radius]

    def transcript_between(self, start: float, end: float) -> str:
        return " ".join(
            s["speech"] for s in self.segments if s["end"] > start and s["start"] < end and s["speech"]
        ).strip()

    # --- prompt ---------------------------------------------------------------
    def knowledge_pack(self, max_chars: int = 48000) -> str:
        """The compact, timestamped ground truth injected into the LLM context.

        For 5-10 minute clips the whole video fits, so most turns need no
        retrieval at all — the fastest possible path to first audio.
        """
        overview = self.meta.get("overview") or {}
        lines: list[str] = [f"VIDEO: {self.title}", f"LENGTH: {media.hhmmss(self.duration)}"]
        if overview.get("summary"):
            lines += ["", "SUMMARY:", overview["summary"].strip()]
        if overview.get("topics"):
            lines.append("TOPICS: " + ", ".join(map(str, overview["topics"][:12])))
        if overview.get("entities"):
            lines.append("ON SCREEN / MENTIONED: " + ", ".join(map(str, overview["entities"][:15])))
        chapters = overview.get("chapters") or []
        if chapters:
            lines += ["", "CHAPTERS:"]
            for ch in chapters[:20]:
                lines.append(
                    f"  [{media.hhmmss(float(ch.get('start_sec', 0)))}] {ch.get('title', '')}"
                    f" - {ch.get('description', '')}"
                )
        lines += ["", "TIMELINE (SAID = spoken words, SEEN = visual description):"]
        for seg in self.segments:
            stamp = f"[{media.hhmmss(seg['start'])}-{media.hhmmss(seg['end'])}]"
            said = (seg["speech"] or "").strip()
            seen = (seg["visual"] or "").strip()
            row = stamp
            if said:
                row += f' SAID: "{said}"'
            if seen:
                row += f" SEEN: {seen}"
            lines.append(row)
        pack = "\n".join(lines)
        if len(pack) > max_chars:  # long video: drop visual detail before speech
            trimmed = [
                f"[{media.hhmmss(s['start'])}] {(s['speech'] or s['visual'] or '').strip()}"
                for s in self.segments
            ]
            pack = "\n".join(lines[: lines.index("TIMELINE (SAID = spoken words, SEEN = visual description):") + 1] + trimmed)
            pack = pack[:max_chars]
        return pack

    def public_meta(self) -> dict:
        overview = self.meta.get("overview") or {}
        return {
            "video_id": self.video_id,
            "title": self.title,
            "scenario": self.scenario,
            "duration": self.duration,
            "summary": overview.get("summary", ""),
            "topics": overview.get("topics", [])[:6],
            "chapters": [
                {
                    "start": float(c.get("start_sec", 0)),
                    "end": float(c.get("end_sec", 0)),
                    "title": c.get("title", ""),
                }
                for c in (overview.get("chapters") or [])
            ],
            "segments": len(self.segments),
            "has_embeddings": bool(self.vectors is not None and len(self.vectors)),
            "starters": config.scenario(self.scenario).starters,
            "accent": config.scenario(self.scenario).accent,
            "scenario_label": config.scenario(self.scenario).label,
        }


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def index_path(video_id: str) -> Path:
    return config.INDEX_DIR / f"{video_id}.json"


def vector_path(video_id: str) -> Path:
    return config.INDEX_DIR / f"{video_id}.vectors.npz"


def save(video_id: str, meta: dict, segments: list[dict], embeddings: list[dict] | None) -> None:
    payload = {"video_id": video_id, "meta": meta, "segments": segments}
    index_path(video_id).write_text(json.dumps(payload, indent=1))
    if embeddings:
        vectors = np.asarray([e["embedding"] for e in embeddings], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
        spans = np.asarray([[e["start"], e["end"]] for e in embeddings], dtype=np.float32)
        np.savez_compressed(vector_path(video_id), vectors=vectors / norms, spans=spans)
    logger.info(f"index written: {index_path(video_id)}")


def load(video_id: str) -> VideoIndex:
    payload = json.loads(index_path(video_id).read_text())
    vectors = spans = None
    vpath = vector_path(video_id)
    if vpath.exists():
        blob = np.load(vpath)
        vectors, spans = blob["vectors"], blob["spans"]
    return VideoIndex(
        video_id=payload["video_id"],
        meta=payload["meta"],
        segments=payload["segments"],
        vectors=vectors,
        vector_spans=spans,
    )


def load_all() -> dict[str, VideoIndex]:
    out: dict[str, VideoIndex] = {}
    for path in sorted(config.INDEX_DIR.glob("*.json")):
        try:
            idx = load(path.stem)
        except Exception as exc:
            logger.warning(f"skipping {path.name}: {exc}")
            continue
        if not idx.source_path.exists():
            logger.warning(f"{idx.video_id}: source file missing at {idx.source_path}")
        out[idx.video_id] = idx
    return out


def push_to_s3(video_id: str) -> None:
    """Keep the durable copy of the index in the private bucket."""
    if not config.S3_BUCKET:
        return
    import boto3

    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    for path in (index_path(video_id), vector_path(video_id)):
        if path.exists():
            s3.upload_file(
                str(path),
                config.S3_BUCKET,
                f"{config.S3_PREFIX}/index/{path.name}",
                ExtraArgs={"ServerSideEncryption": "AES256"},
            )
    logger.info(f"index synced to s3://{config.S3_BUCKET}/{config.S3_PREFIX}/index/")
