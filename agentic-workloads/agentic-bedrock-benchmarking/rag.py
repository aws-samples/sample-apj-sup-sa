"""Tiny RAG: chunk → embed (Titan v2) → persisted numpy index → cosine top-K retrieval."""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
import numpy as np
from botocore.exceptions import BotoCoreError, ClientError

INDEX_DIR = Path.home() / "bedrock-eval" / ".index"
INDEX_FILE = INDEX_DIR / "index.npz"
INDEX_META_FILE = INDEX_DIR / "index.json"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 1024
EMBED_REGION = "us-west-2"  # Titan v2 lives here; independent of the chat region


@dataclass
class Chunk:
    doc_id: str       # filename or "pasted-N"
    chunk_idx: int
    text: str


@dataclass
class Index:
    chunks: list[Chunk]
    vectors: np.ndarray  # shape (N, EMBED_DIM), float32, L2-normalized
    embed_model: str
    embed_region: str

    @property
    def size(self) -> int:
        return len(self.chunks)

    @property
    def doc_count(self) -> int:
        return len({c.doc_id for c in self.chunks})


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """Split into ~chunk_size-char chunks on paragraph/sentence boundaries with overlap."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 <= chunk_size:
            buf = f"{buf}\n\n{p}" if buf else p
            continue
        if buf:
            chunks.append(buf)
        if len(p) <= chunk_size:
            buf = p
        else:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            sub = ""
            for s in sentences:
                if len(sub) + len(s) + 1 <= chunk_size:
                    sub = f"{sub} {s}".strip()
                else:
                    if sub:
                        chunks.append(sub)
                    if len(s) > chunk_size:
                        for i in range(0, len(s), chunk_size - overlap):
                            chunks.append(s[i : i + chunk_size])
                        sub = ""
                    else:
                        sub = s
            buf = sub
    if buf:
        chunks.append(buf)

    if overlap > 0 and len(chunks) > 1:
        out = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = out[-1][-overlap:]
            out.append(f"{tail} {chunks[i]}")
        chunks = out
    return chunks


def extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _normalize(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (v / norms).astype(np.float32)


def _embed_one(client, text: str) -> np.ndarray:
    body = json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True})
    resp = client.invoke_model(modelId=EMBED_MODEL_ID, body=body)
    payload = json.loads(resp["body"].read())
    return np.asarray(payload["embedding"], dtype=np.float32)


def embed_texts(texts: list[str], region: str = EMBED_REGION) -> np.ndarray:
    """Sequential embed (Titan invoke_model has no batch endpoint). Returns L2-normalized (N, D)."""
    client = boto3.client("bedrock-runtime", region_name=region)
    vectors = np.stack([_embed_one(client, t) for t in texts])
    return _normalize(vectors)


def load_index() -> Optional[Index]:
    if not INDEX_FILE.exists() or not INDEX_META_FILE.exists():
        return None
    try:
        meta = json.loads(INDEX_META_FILE.read_text())
        npz = np.load(INDEX_FILE)
        vectors = npz["vectors"]
        chunks = [Chunk(doc_id=c["doc_id"], chunk_idx=c["chunk_idx"], text=c["text"]) for c in meta["chunks"]]
        return Index(chunks=chunks, vectors=vectors, embed_model=meta["embed_model"], embed_region=meta["embed_region"])
    except (KeyError, ValueError, OSError):
        return None


def save_index(index: Index) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "embed_model": index.embed_model,
        "embed_region": index.embed_region,
        "chunks": [{"doc_id": c.doc_id, "chunk_idx": c.chunk_idx, "text": c.text} for c in index.chunks],
    }
    INDEX_META_FILE.write_text(json.dumps(meta))
    np.savez(INDEX_FILE, vectors=index.vectors)


def clear_index() -> None:
    for f in (INDEX_FILE, INDEX_META_FILE):
        if f.exists():
            f.unlink()


def add_document(index: Optional[Index], doc_id: str, text: str) -> Index:
    """Embed `text` and append to (or create) the index. Re-adding a doc_id replaces it."""
    pieces = chunk_text(text)
    if not pieces:
        raise ValueError(f"No content to index from {doc_id}")
    new_chunks = [Chunk(doc_id=doc_id, chunk_idx=i, text=p) for i, p in enumerate(pieces)]
    new_vecs = embed_texts([c.text for c in new_chunks])

    if index is None:
        return Index(chunks=new_chunks, vectors=new_vecs, embed_model=EMBED_MODEL_ID, embed_region=EMBED_REGION)

    keep = [(c, v) for c, v in zip(index.chunks, index.vectors) if c.doc_id != doc_id]
    chunks = [c for c, _ in keep] + new_chunks
    vectors = np.vstack([np.stack([v for _, v in keep])] + [new_vecs]) if keep else new_vecs
    return Index(chunks=chunks, vectors=vectors, embed_model=index.embed_model, embed_region=index.embed_region)


def remove_document(index: Index, doc_id: str) -> Optional[Index]:
    keep = [(c, v) for c, v in zip(index.chunks, index.vectors) if c.doc_id != doc_id]
    if not keep:
        return None
    chunks = [c for c, _ in keep]
    vectors = np.stack([v for _, v in keep])
    return Index(chunks=chunks, vectors=vectors, embed_model=index.embed_model, embed_region=index.embed_region)


@dataclass
class Hit:
    chunk: Chunk
    score: float


def retrieve(index: Index, query: str, k: int = 4) -> list[Hit]:
    qv = embed_texts([query])[0]  # (D,), normalized
    scores = index.vectors @ qv  # cosine since both are normalized
    top = np.argsort(-scores)[:k]
    return [Hit(chunk=index.chunks[i], score=float(scores[i])) for i in top]


def build_augmented_prompt(query: str, hits: list[Hit]) -> str:
    if not hits:
        return query
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(f"[{i}] (source: {h.chunk.doc_id}, chunk {h.chunk.chunk_idx})\n{h.chunk.text}")
    context = "\n\n".join(blocks)
    return (
        "Answer the user's question using ONLY the context below. "
        "If the context is insufficient, say so. Cite sources inline as [1], [2], etc.\n\n"
        f"=== CONTEXT ===\n{context}\n=== END CONTEXT ===\n\n"
        f"Question: {query}"
    )


def doc_id_from_upload(filename: str, data: bytes) -> str:
    digest = hashlib.sha1(data).hexdigest()[:8]
    return f"{filename}#{digest}"
