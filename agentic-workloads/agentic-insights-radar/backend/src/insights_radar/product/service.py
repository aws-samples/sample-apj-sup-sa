# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Product profile and document service (ops table + S3 + knowledge base).

Uploads use a presigned **POST** (not PUT) so S3 itself enforces the size limit
(``content-length-range``) and the exact key and content type; the browser cannot write
anywhere else. After the upload the client calls ``complete``: the service checks the object's
real size and type, writes the metadata sidecar and starts ingestion.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso, parse_iso
from insights_radar.core.dynamo import Query
from insights_radar.core.errors import ConflictError, NotFoundError, ValidationError
from insights_radar.core.ids import IdGenerator
from insights_radar.graph.repository import _from_ddb, _to_ddb
from insights_radar.product.crawler import CrawlReport
from insights_radar.product.knowledge import DOCS_PREFIX, KnowledgeBase
from insights_radar.product.model import (
    MAX_DOCUMENT_BYTES,
    IngestionStatus,
    KnowledgeDocument,
    ProductProfile,
)

log = logging.getLogger(__name__)

MAX_PRODUCTS = 20
MAX_DOCUMENTS_PER_PRODUCT = 500
UPLOAD_TTL_SECONDS = 900
PENDING_GRACE_SECONDS = 86_400
# How long an abandoned upload's row outlives the sweep's cutoff.
UPLOAD_ROW_MARGIN_SECONDS = 86_400
RECRAWL_DAYS = 7
STUCK_CRAWL_HOURS = 1
CrawlRequester = Callable[[str], None]


_SETTLED = (IngestionStatus.READY, IngestionStatus.FAILED)
# Ops-table flag: a sync was asked for while another job was running.
_OWED_INGESTION = {"pk": "KB#INGESTION", "sk": "OWED"}


def _covers(job_started: str | None, written_at: str | None) -> bool:
    """An ingestion job covers a write when it started after it."""
    if not job_started or not written_at:
        return False
    return parse_iso(job_started) >= parse_iso(written_at)


class ProductService:
    def __init__(
        self,
        table: Any,
        s3: Any,
        bucket: str,
        kb: KnowledgeBase | None,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        request_crawl: CrawlRequester | None = None,
    ) -> None:
        self._t = table
        self._request_crawl = request_crawl
        self._s3 = s3
        self._bucket = bucket
        self._kb = kb
        self._clock = clock or SystemClock()
        self._ids = ids or IdGenerator(self._clock)

    # ---- profiles ------------------------------------------------------------------

    @staticmethod
    def _profile_from_item(raw: dict[str, Any]) -> ProductProfile:
        i = _from_ddb(raw)
        return ProductProfile(
            id=i["id"],
            name=i["name"],
            summary=i.get("summary", ""),
            capabilities=tuple(i.get("capabilities", [])),
            segments=tuple(i.get("segments", [])),
            competitors=tuple(i.get("competitors", [])),
            doc_urls=tuple(i.get("doc_urls", [])),
            notes=i.get("notes", ""),
            web_status=IngestionStatus(i.get("web_status", "none")),
            web_pages=int(i.get("web_pages", 0)),
            web_crawled_at=i.get("web_crawled_at"),
            created_by=i.get("created_by", ""),
            created_at=i.get("created_at", ""),
            updated_at=i.get("updated_at", ""),
            version=int(i.get("version", 1)),
            stats=i.get("stats") or {},
        )

    def _put_profile(
        self, p: ProductProfile, condition: str | None = None, values: dict[str, Any] | None = None
    ) -> None:
        item = {
            "pk": f"PRODUCT#{p.id}",
            "sk": "META",
            "gsi1pk": "PRODUCTS",
            "gsi1sk": p.name.lower(),
            "kind": "product",
            **{k: v for k, v in p.to_public().items() if v not in (None, [], {})},
        }
        kwargs: dict[str, Any] = {"Item": _to_ddb(item)}
        if condition:
            kwargs["ConditionExpression"] = condition
        if values:
            kwargs["ExpressionAttributeValues"] = values
        self._t.put_item(**kwargs)

    def _set_system_fields(self, product_id: str, **fields: Any) -> None:
        """Update only the crawl/status fields, and only while the product exists. A whole-profile put
        from a crawl or status refresh would undo a concurrent edit, or bring back a deleted product."""
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":f{i}": v.value if isinstance(v, IngestionStatus) else v for i, v in enumerate(fields.values())}
        try:
            self._t.update_item(
                Key={"pk": f"PRODUCT#{product_id}", "sk": "META"},
                UpdateExpression="SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(fields))),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=_to_ddb(values),
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError(f"product {product_id} not found") from err
            raise

    def _start_ingestion(self) -> None:
        """Start a knowledge-base sync, or record that one is owed: a job that is already running listed
        the bucket before this change and will not pick it up."""
        if self._kb and self._kb.start_ingestion() is None:
            self._t.put_item(Item={**_OWED_INGESTION, "requested_at": iso(self._clock.now())})

    def ingestion_owed(self) -> bool:
        return "Item" in self._t.get_item(Key=_OWED_INGESTION, ConsistentRead=True)

    def retry_owed_ingestion(self, status: IngestionStatus) -> bool:
        """Once the running job has ended, start the sync that was asked for while it ran."""
        if not self._kb or status not in _SETTLED or not self.ingestion_owed():
            return False
        if self._kb.start_ingestion() is None:
            return False
        self._t.delete_item(Key=_OWED_INGESTION)
        return True

    def list_all(self) -> list[ProductProfile]:
        res = self._t.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("PRODUCTS"))
        return [self._profile_from_item(i) for i in res.get("Items", [])]

    def get(self, product_id: str) -> ProductProfile:
        res = self._t.get_item(Key={"pk": f"PRODUCT#{product_id}", "sk": "META"}, ConsistentRead=True)
        if "Item" not in res:
            raise NotFoundError(f"product {product_id} not found")
        return self._profile_from_item(res["Item"])

    def create(self, draft: ProductProfile, actor: str) -> ProductProfile:
        if len(self.list_all()) >= MAX_PRODUCTS:
            raise ValidationError(f"at most {MAX_PRODUCTS} product profiles")
        now = iso(self._clock.now())
        p = ProductProfile.validate(
            replace(
                draft,
                id=self._ids.short(12),
                created_by=actor,
                created_at=now,
                updated_at=now,
                version=1,
                web_status=IngestionStatus.PENDING if draft.doc_urls else IngestionStatus.NONE,
                web_pages=0,
                web_crawled_at=None,
            )
        )
        self._put_profile(p, "attribute_not_exists(pk)")
        if p.doc_urls and self._request_crawl:
            # Best effort: the product is already saved, and due_for_crawl retries a PENDING crawl, so
            # a throttled Lambda must not turn a committed create into a 500 the client retries (and
            # so into a duplicate product, since names are not unique).
            self._try_crawl(p.id)
            return self.get(p.id)
        return p

    def _try_crawl(self, product_id: str) -> None:
        try:
            self.request_crawl(product_id)
        except Exception:
            log.exception(json.dumps({"event": "crawl.request_failed", "product": product_id}))

    # A re-crawl of the same site, started again and again, runs parallel ten-minute crawls writing
    # the same keys: a request is ignored while one is in flight and recent.
    RESYNC_DEBOUNCE_SECONDS = 600

    def request_crawl(self, product_id: str) -> None:
        if not self._request_crawl:
            return
        p = self.get(product_id)
        self._set_system_fields(product_id, stats={**p.stats, "crawl_requested_at": iso(self._clock.now())})
        self._request_crawl(product_id)

    EDITABLE = frozenset({"name", "summary", "capabilities", "segments", "competitors", "doc_urls", "notes"})
    LIST_FIELDS = frozenset({"capabilities", "segments", "competitors", "doc_urls"})

    def update(self, product_id: str, changes: dict[str, Any], expected_version: int) -> ProductProfile:
        bad = set(changes) - self.EDITABLE
        if bad:
            raise ValidationError(f"fields cannot be changed: {', '.join(sorted(bad))}")
        for key, value in changes.items():
            is_list = key in self.LIST_FIELDS
            if is_list:
                ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
            else:
                ok = isinstance(value, str)
            if not ok:
                raise ValidationError(f"{key} must be {'a list of strings' if is_list else 'a string'}")
        current = self.get(product_id)
        if current.version != expected_version:
            raise ConflictError("product was changed by someone else; reload and retry")
        conv: dict[str, Any] = {k: tuple(v) if isinstance(v, list) else v for k, v in changes.items()}
        p = ProductProfile.validate(
            replace(current, **conv, updated_at=iso(self._clock.now()), version=current.version + 1)
        )
        urls_changed = p.doc_urls != current.doc_urls
        if urls_changed:
            p = replace(p, web_status=IngestionStatus.PENDING if p.doc_urls else IngestionStatus.NONE)
        try:
            self._put_profile(p, "version = :v", {":v": expected_version})
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError("product was changed by someone else; reload and retry") from err
            raise
        if urls_changed:
            if p.doc_urls:
                self._try_crawl(p.id)
            else:
                self.store_crawl(p.id, CrawlReport())  # no documentation any more: remove crawled pages
                p = self.get(p.id)
        return p

    def delete(self, product_id: str) -> None:
        p = self.get(product_id)
        for d in self.list_documents(product_id):
            self.delete_document(product_id, d.id, resync=False)
        self._delete_prefix(f"{DOCS_PREFIX}{p.id}/web/")
        self._start_ingestion()
        self._t.delete_item(Key={"pk": f"PRODUCT#{product_id}", "sk": "META"})

    def refresh_status(
        self, product_id: str, known: tuple[IngestionStatus, str | None] | None = None
    ) -> ProductProfile:
        """Move a crawled product from indexing to ready (or failed) once a job that started after the
        crawl has ended. ``known`` is the ingestion status the caller has already read: the sweep used
        to call ListIngestionJobs once and then again for every product, every five minutes."""
        p = self.get(product_id)
        if not self._kb or p.web_status is not IngestionStatus.INDEXING:
            return p
        status, started = known if known is not None else self._kb.source_status()
        if status not in _SETTLED or not _covers(started, p.web_crawled_at) or self.ingestion_owed():
            return p
        self._set_system_fields(product_id, web_status=status)
        return replace(p, web_status=status)

    def resync(self, product_id: str) -> None:
        """Re-crawl the documentation and re-index everything."""
        p = self.get(product_id)
        if p.doc_urls:
            requested = p.stats.get("crawl_requested_at")
            if p.web_status in (IngestionStatus.PENDING, IngestionStatus.INDEXING) and requested:
                recent = parse_iso(str(requested)) > self._clock.now() - timedelta(seconds=self.RESYNC_DEBOUNCE_SECONDS)
                if recent:
                    raise ConflictError(
                        "a crawl of this product is already running; wait for it to finish",
                        {"code": "crawl_running"},
                    )
            self._set_system_fields(p.id, web_status=IngestionStatus.PENDING)
            # Best effort, like create and update: the crawl is already queued (PENDING) and the sweep
            # retries it, so a failed invoke is no 500 for a change that took effect (B3-031).
            self._try_crawl(p.id)
        else:
            self._start_ingestion()

    def sweep_expired_uploads(self) -> int:
        """Delete the objects of upload rows that expired without being completed.

        They were never validated (size, type) and have no sidecar, yet stayed in the bucket forever
        and were picked up by the next sync, because the knowledge base's data source covers the whole
        docs/ prefix. The rows expire by TTL; the objects need removing.
        """
        removed = 0
        for p in self.list_all():
            for doc in self._raw_documents(p.id):
                if str(doc.get("status")) != IngestionStatus.PENDING.value:
                    continue
                requested = str(doc.get("uploaded_at") or "")
                if not requested or parse_iso(requested) > self._clock.now() - timedelta(
                    seconds=UPLOAD_TTL_SECONDS + PENDING_GRACE_SECONDS
                ):
                    continue
                self._s3.delete_object(Bucket=self._bucket, Key=str(doc["s3_key"]))
                self._t.delete_item(Key={"pk": doc["pk"], "sk": doc["sk"]})
                removed += 1
        if removed:
            # Their passages stay in the knowledge base (retrievable without a product filter) until the
            # next sync: start one now (B3-030).
            self._start_ingestion()
        return removed

    def due_for_crawl(self) -> list[str]:
        """Products last crawled over a week ago, and crawls that were requested but never finished."""
        now = self._clock.now()
        stale, stuck = now - timedelta(days=RECRAWL_DAYS), now - timedelta(hours=STUCK_CRAWL_HOURS)
        due = []
        for p in self.list_all():
            if not p.doc_urls:
                continue
            if p.web_status is IngestionStatus.PENDING:
                requested = p.stats.get("crawl_requested_at")
                if requested is None or parse_iso(str(requested)) < stuck:
                    due.append(p.id)
            elif p.web_status is not IngestionStatus.INDEXING and (
                p.web_crawled_at is None or parse_iso(p.web_crawled_at) < stale
            ):
                due.append(p.id)
        return due

    def store_crawl(
        self, product_id: str, report: CrawlReport, crawled: tuple[str, ...] | None = None
    ) -> ProductProfile:
        """Write crawled pages as documents, remove pages that disappeared, start ingestion.

        ``crawled`` is the set of documentation addresses the crawl started from: a crawl begun before an
        administrator replaced them finished later, wrote the old site's pages and deleted the new
        crawl's as stale (B3-032). Such a report is discarded."""
        p = self.get(product_id)
        if crawled is not None and tuple(crawled) != p.doc_urls:
            log.warning(json.dumps({"event": "crawl.stale_report", "product": product_id}))
            return p
        prefix = f"{DOCS_PREFIX}{product_id}/web/"
        keep: set[str] = set()
        for page in report.pages:
            key = f"{prefix}{page.key}.txt"
            keep.update({key, f"{key}.metadata.json"})
            body = f"{page.title}\n{page.url}\n\n{page.text}" if page.title else f"{page.url}\n\n{page.text}"
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=body.encode("utf-8"), ContentType="text/plain")
            self._s3.put_object(
                Bucket=self._bucket,
                Key=f"{key}.metadata.json",
                Body=KnowledgeBase.sidecar(product_id, page.title or page.url, page.url),
                ContentType="application/json",
            )
        self._delete_prefix(prefix, keep)
        written = iso(self._clock.now())  # before the sync starts, so the job that covers it is recognisable
        self._start_ingestion()
        if report.pages:
            status = IngestionStatus.INDEXING if self._kb else IngestionStatus.READY
        else:
            status = IngestionStatus.FAILED if p.doc_urls else IngestionStatus.NONE
        fields: dict[str, Any] = {
            "web_status": status,
            "web_pages": len(report.pages),
            "web_crawled_at": written,
            "stats": {**p.stats, "crawl": report.to_public()},
        }
        try:
            self._set_system_fields(product_id, **fields)
        except NotFoundError:
            self._delete_prefix(prefix)  # deleted while it was being crawled: drop what was just written
            raise
        return replace(p, **fields)

    def _delete_prefix(self, prefix: str, keep: set[str] | None = None) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            stale = [{"Key": o["Key"]} for o in page.get("Contents", []) if o["Key"] not in (keep or set())]
            if stale:
                self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": stale, "Quiet": True})

    # ---- documents -----------------------------------------------------------------

    @staticmethod
    def _doc_from_item(raw: dict[str, Any]) -> KnowledgeDocument:
        i = _from_ddb(raw)
        return KnowledgeDocument(
            i["id"],
            i["product_id"],
            i["filename"],
            i["content_type"],
            int(i["size"]),
            i["s3_key"],
            IngestionStatus(i["status"]),
            i["uploaded_by"],
            i["uploaded_at"],
            i.get("error"),
        )

    def list_documents(self, product_id: str) -> list[KnowledgeDocument]:
        return [self._doc_from_item(i) for i in self._raw_documents(product_id)]

    def _raw_documents(self, product_id: str) -> list[dict[str, Any]]:
        return Query.all_items(
            self._t, KeyConditionExpression=Key("pk").eq(f"PRODUCT#{product_id}") & Key("sk").begins_with("DOC#")
        )

    def presign_upload(self, product_id: str, filename: str, size: int, actor: str) -> dict[str, Any]:
        self.get(product_id)
        safe = KnowledgeDocument.safe_filename(filename)
        content_type = KnowledgeDocument.content_type_for(safe)
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DOCUMENT_BYTES:
            raise ValidationError(f"size must be between 1 byte and {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
        # An upload that was presigned but never completed stops counting once its URL has expired.
        stale = iso(self._clock.now() - timedelta(seconds=UPLOAD_TTL_SECONDS))
        live = [
            d
            for d in self.list_documents(product_id)
            if not (d.status is IngestionStatus.PENDING and d.uploaded_at < stale)
        ]
        if len(live) >= MAX_DOCUMENTS_PER_PRODUCT:
            raise ValidationError(f"a product can have at most {MAX_DOCUMENTS_PER_PRODUCT} documents")
        doc_id = self._ids.short(12)
        key = f"{DOCS_PREFIX}{product_id}/{doc_id}/{safe}"
        post = self._s3.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            # Exactly the declared size: the browser knows it, and S3 then refuses anything else.
            Conditions=[{"Content-Type": content_type}, ["content-length-range", size, size]],
            ExpiresIn=UPLOAD_TTL_SECONDS,
        )
        doc = KnowledgeDocument(
            doc_id, product_id, safe, content_type, size, key, IngestionStatus.PENDING, actor, iso(self._clock.now())
        )
        self._t.put_item(
            Item={
                "pk": f"PRODUCT#{product_id}",
                "sk": f"DOC#{doc_id}",
                "kind": "document",
                **{k: v for k, v in {**doc.to_public(), "s3_key": key}.items() if v is not None},
                # Abandoned uploads expire (the table's TTL); completing the upload rewrites the item without it.
                # Well after the sweep's cutoff: a row DynamoDB expired first left its object behind, never
                # found again (B3-030).
                "ttl": int(self._clock.now().timestamp())
                + UPLOAD_TTL_SECONDS
                + PENDING_GRACE_SECONDS
                + UPLOAD_ROW_MARGIN_SECONDS,
            }
        )
        return {"document": doc.to_public(), "upload": {"url": post["url"], "fields": post["fields"]}}

    def complete_upload(self, product_id: str, doc_id: str) -> KnowledgeDocument:
        doc = self._get_document(product_id, doc_id)
        if doc.status is not IngestionStatus.PENDING:  # a repeat would re-run ingestion and reset the status
            raise ConflictError("this upload is already complete", {"status": doc.status.value})
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=doc.s3_key)
        except ClientError as err:
            raise ValidationError("the file has not been uploaded yet") from err
        size = int(head.get("ContentLength", 0))
        if size != doc.size or head.get("ContentType") != doc.content_type:
            self._s3.delete_object(Bucket=self._bucket, Key=doc.s3_key)
            failed = replace(doc, status=IngestionStatus.FAILED, error="uploaded file failed validation")
            self._put_doc(failed)
            raise ValidationError("the uploaded file does not match the declared type or size")
        self._s3.put_object(
            Bucket=self._bucket,
            Key=f"{doc.s3_key}.metadata.json",
            Body=KnowledgeBase.sidecar(product_id, doc.filename),
            ContentType="application/json",
        )
        # uploaded_at becomes when the file arrived, which is before the sync that covers it starts
        ready = replace(doc, size=size, status=IngestionStatus.INDEXING, uploaded_at=iso(self._clock.now()))
        self._put_doc(ready)
        self._start_ingestion()
        return ready

    def _get_document(self, product_id: str, doc_id: str) -> KnowledgeDocument:
        res = self._t.get_item(Key={"pk": f"PRODUCT#{product_id}", "sk": f"DOC#{doc_id}"}, ConsistentRead=True)
        if "Item" not in res:
            raise NotFoundError(f"document {doc_id} not found")
        return self._doc_from_item(res["Item"])

    def _put_doc(self, doc: KnowledgeDocument) -> None:
        body = {k: v for k, v in {**doc.to_public(), "s3_key": doc.s3_key}.items() if v is not None}
        self._t.put_item(Item={"pk": f"PRODUCT#{doc.product_id}", "sk": f"DOC#{doc.id}", "kind": "document", **body})

    def delete_document(self, product_id: str, doc_id: str, resync: bool = True) -> None:
        doc = self._get_document(product_id, doc_id)
        self._s3.delete_object(Bucket=self._bucket, Key=doc.s3_key)
        self._s3.delete_object(Bucket=self._bucket, Key=f"{doc.s3_key}.metadata.json")
        self._t.delete_item(Key={"pk": f"PRODUCT#{product_id}", "sk": f"DOC#{doc_id}"})
        if resync:
            self._start_ingestion()

    def mark_documents_indexed(self, product_id: str, status: IngestionStatus, job_started: str | None) -> int:
        """Settle the documents the finished job covers: uploaded before it started, with no sync owed."""
        if status not in _SETTLED or self.ingestion_owed():
            return 0
        changed = 0
        for d in self.list_documents(product_id):
            if d.status is IngestionStatus.INDEXING and _covers(job_started, d.uploaded_at):
                self._put_doc(replace(d, status=status))
                changed += 1
        return changed
