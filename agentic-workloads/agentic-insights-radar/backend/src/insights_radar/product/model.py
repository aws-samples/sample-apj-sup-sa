# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Product profiles and knowledge documents.

A product profile tells the gap and content modules what "our product" is: a summary, its
capabilities, the segments it targets, who it competes with, and where its public documentation
lives. Private documents are uploaded and indexed into the same Bedrock knowledge base, tagged
with the profile id so retrieval stays per product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from insights_radar.core.errors import ValidationError
from insights_radar.core.urls import UrlGuard

PRODUCT_NAME_MAX = 120
PRODUCT_TEXT_MAX = 4000
MAX_CAPABILITIES = 50
MAX_DOC_URLS = 20
MAX_LIST_ITEM = 300
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
}
_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class IngestionStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


def _clean_list(values: list[str] | tuple[str, ...], limit: int, label: str) -> tuple[str, ...]:
    out = tuple(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
    if len(out) > limit:
        raise ValidationError(f"at most {limit} {label}")
    if any(len(v) > MAX_LIST_ITEM for v in out):
        raise ValidationError(f"each of the {label} must be at most {MAX_LIST_ITEM} characters")
    return out


@dataclass(frozen=True, slots=True)
class ProductProfile:
    id: str
    name: str
    summary: str = ""
    capabilities: tuple[str, ...] = ()
    segments: tuple[str, ...] = ()
    competitors: tuple[str, ...] = ()
    doc_urls: tuple[str, ...] = ()
    notes: str = ""
    web_status: IngestionStatus = IngestionStatus.NONE
    web_pages: int = 0
    web_crawled_at: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: int = 1
    stats: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "capabilities": list(self.capabilities),
            "segments": list(self.segments),
            "competitors": list(self.competitors),
            "doc_urls": list(self.doc_urls),
            "notes": self.notes,
            "web_status": self.web_status.value,
            "web_pages": self.web_pages,
            "web_crawled_at": self.web_crawled_at,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "stats": self.stats,
        }

    def to_viewer(self) -> dict[str, Any]:
        """The profile for someone who does not manage products: its name, summary and the hosts of its
        documentation. A documentation address can carry a token in its query or path, the notes hold
        unreleased plans and the crawl report quotes errors, so none of them is shown -- audit rows
        hash the same addresses and modules see only hosts (B3-017). Same keys as ``to_public``."""
        return {
            **self.to_public(),
            "capabilities": [],
            "segments": [],
            "competitors": [],
            "doc_urls": list(dict.fromkeys(UrlGuard.origin(u) for u in self.doc_urls)),
            "notes": "",
            "created_by": "",
            "stats": {},
            "restricted": True,
        }

    def prompt_block(self) -> str:
        """Plain-text summary injected into module prompts (defanged by the runtime)."""
        lines = [f"Product: {self.name}"]
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        if self.capabilities:
            lines.append("Capabilities: " + "; ".join(self.capabilities))
        if self.segments:
            lines.append("Target segments: " + "; ".join(self.segments))
        if self.competitors:
            lines.append("Known competitors: " + "; ".join(self.competitors))
        if self.doc_urls:
            lines.append("Public documentation: " + " ".join(self._prompt_doc_urls()))
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        return "\n".join(lines)

    def _prompt_doc_urls(self) -> list[str]:
        """Documentation addresses with credential query values redacted. A gated documentation link
        commonly carries an access token (``?token=``, a share link), and the block reaches a module's
        system prompt, where one prompt injection on a fetched page can carry it out."""
        return [UrlGuard.redact_secrets(u)[0] for u in self.doc_urls]

    def public_prompt_block(self) -> str:
        """The profile for a module that reads untrusted pages and can send requests out: only the name and
        the public documentation addresses, never the summary, capabilities, segments, competitors or
        notes, which can hold unreleased plans."""
        lines = [f"Product: {self.name}"]
        if self.doc_urls:
            # Only the hosts, as source_list shows a source: a share link or token can sit in the path
            # (``/share/<token>/guide``), which no query redaction catches (SR2-06).
            hosts = dict.fromkeys(UrlGuard.origin(u) for u in self.doc_urls)
            lines.append("Public documentation: " + " ".join(hosts))
        return "\n".join(lines)

    @staticmethod
    def validate(p: ProductProfile) -> ProductProfile:
        name = p.name.strip()
        if not 1 <= len(name) <= PRODUCT_NAME_MAX:
            raise ValidationError(f"name must be 1-{PRODUCT_NAME_MAX} characters")
        if len(p.summary) > PRODUCT_TEXT_MAX:
            raise ValidationError(f"summary must be at most {PRODUCT_TEXT_MAX} characters")
        if len(p.notes) > PRODUCT_TEXT_MAX:
            raise ValidationError(f"notes must be at most {PRODUCT_TEXT_MAX} characters")
        urls = _clean_list(p.doc_urls, MAX_DOC_URLS, "documentation URLs")
        bad = [(u, UrlGuard.reason(u)) for u in urls if UrlGuard.reason(u)]
        if bad:
            raise ValidationError("documentation URLs are not allowed", {"errors": [f"{u}: {why}" for u, why in bad]})
        return replace(
            p,
            name=name,
            summary=p.summary.strip(),
            notes=p.notes.strip(),
            doc_urls=urls,
            capabilities=_clean_list(p.capabilities, MAX_CAPABILITIES, "capabilities"),
            segments=_clean_list(p.segments, MAX_CAPABILITIES, "segments"),
            competitors=_clean_list(p.competitors, MAX_CAPABILITIES, "competitors"),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    id: str
    product_id: str
    filename: str
    content_type: str
    size: int
    s3_key: str
    status: IngestionStatus
    uploaded_by: str
    uploaded_at: str
    error: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "status": self.status.value,
            "uploaded_by": self.uploaded_by,
            "uploaded_at": self.uploaded_at,
            "error": self.error,
        }

    @staticmethod
    def safe_filename(filename: str) -> str:
        base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        cleaned = _FILENAME_RE.sub("_", base).strip("._")[:120]
        if not cleaned or "." not in cleaned:
            raise ValidationError("filename must have a name and an extension")
        return cleaned

    @staticmethod
    def content_type_for(filename: str) -> str:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_DOCUMENT_TYPES:
            raise ValidationError(f"file type {ext} is not supported; allowed: {', '.join(ALLOWED_DOCUMENT_TYPES)}")
        return ALLOWED_DOCUMENT_TYPES[ext]
