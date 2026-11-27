# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Bedrock Knowledge Base integration for product knowledge.

One knowledge base serves every product profile:

* one S3 data source over ``docs/`` holds every product's uploaded documents and its crawled
  documentation pages (``docs/<product>/web/``, see ``crawler.py``);
* each file has a ``<key>.metadata.json`` sidecar carrying ``product_id`` (and ``source_url`` for
  crawled pages), so retrieval is scoped to one product.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from insights_radar.core.clock import iso
from insights_radar.core.defang import Defang
from insights_radar.core.errors import ValidationError
from insights_radar.product.model import IngestionStatus, ProductProfile

DOCS_PREFIX = "docs/"
_JOB_STATUS = {
    "STARTING": IngestionStatus.INDEXING,
    "IN_PROGRESS": IngestionStatus.INDEXING,
    "COMPLETE": IngestionStatus.READY,
    "FAILED": IngestionStatus.FAILED,
    "STOPPING": IngestionStatus.INDEXING,
    "STOPPED": IngestionStatus.FAILED,
}


@dataclass(frozen=True, slots=True)
class Passage:
    text: str
    source: str
    score: float
    product_id: str | None

    def to_public(self) -> dict[str, Any]:
        return {"text": self.text, "source": self.source, "score": round(self.score, 4), "product_id": self.product_id}


PASSAGE_MAX = 4000


class KnowledgeBase:
    def __init__(self, agent_client: Any, runtime_client: Any, kb_id: str, s3_source_id: str, bucket_arn: str) -> None:
        self._agent = agent_client
        self._rt = runtime_client
        self._kb = kb_id
        self._s3_source = s3_source_id
        self._bucket_arn = bucket_arn

    def start_ingestion(self) -> str | None:
        try:
            res = self._agent.start_ingestion_job(knowledgeBaseId=self._kb, dataSourceId=self._s3_source)
        except self._agent.exceptions.ConflictException:
            return None  # a job is already running; it will pick up the change
        return str(res["ingestionJob"]["ingestionJobId"])

    def source_status(self) -> tuple[IngestionStatus, str | None]:
        """Status of the latest ingestion job and when it started: it covers what was in the bucket then."""
        res = self._agent.list_ingestion_jobs(
            knowledgeBaseId=self._kb,
            dataSourceId=self._s3_source,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
            maxResults=1,
        )
        jobs = res.get("ingestionJobSummaries", [])
        if not jobs:
            return IngestionStatus.NONE, None
        job = jobs[0]
        status = _JOB_STATUS.get(str(job.get("status")), IngestionStatus.PENDING)
        started = job.get("startedAt")
        if isinstance(started, datetime):
            return status, iso(started)
        return status, str(started) if started else None

    @staticmethod
    def sidecar(product_id: str, filename: str, source_url: str | None = None) -> bytes:
        attrs = {"product_id": product_id, "filename": filename}
        if source_url:
            attrs["source_url"] = source_url
        return json.dumps({"metadataAttributes": attrs}).encode()

    def retrieve(
        self, query: str, profile: ProductProfile | None, limit: int = 6, defang: bool = True
    ) -> list[Passage]:
        """Passages for the query. ``defang`` folds and marker-strips them for a model; the route that
        shows them to people passes False, so users are not given altered quotations of their own
        documents (line breaks and tables flattened, characters folded)."""
        text = query.strip()
        if len(text) < 2 or len(text) > 1000:
            raise ValidationError("query must be 2-1000 characters")
        config: dict[str, Any] = {"numberOfResults": max(1, min(limit, 20))}
        if profile is not None:
            config["filter"] = {"equals": {"key": "product_id", "value": profile.id}}
        res = self._rt.retrieve(
            knowledgeBaseId=self._kb,
            retrievalQuery={"text": text},
            retrievalConfiguration={"vectorSearchConfiguration": config},
        )
        out: list[Passage] = []
        for r in res.get("retrievalResults", []):
            loc = r.get("location", {})
            meta = r.get("metadata", {}) or {}
            uri = (loc.get("s3Location", {}) or {}).get("uri", "")
            source = str(meta.get("source_url") or f"document:{meta.get('filename') or uri.rsplit('/', 1)[-1]}")
            pid = meta.get("product_id")
            out.append(
                Passage(
                    # Documents and crawled pages are untrusted text: folded and stripped of markers like
                    # every other input that reaches a model.
                    self._text(str(r.get("content", {}).get("text", "")), defang),
                    source,
                    float(r.get("score", 0.0)),
                    str(pid) if pid else None,
                )
            )
        return out

    @staticmethod
    def _text(raw: str, defang: bool) -> str:
        return Defang.neutralize(raw, PASSAGE_MAX) if defang else raw[:PASSAGE_MAX]
