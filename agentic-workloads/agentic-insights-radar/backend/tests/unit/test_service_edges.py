# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Edge paths of the product and radar services not reached by the main scenario tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError, ValidationError
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.product import service as product_service
from insights_radar.product.model import IngestionStatus, ProductProfile
from insights_radar.product.service import ProductService
from insights_radar.radars import service as radar_service
from insights_radar.radars.model import Radar, RadarEntry, RadarSchema, _levenshtein
from insights_radar.radars.service import MemoryBlobStore, RadarService, S3BlobStore
from tests.unit.test_failure_paths import THROTTLE, Chaos

OWNER = Principal(PrincipalKind.USER, "own", Role.EDITOR, "owner@example.com")
ADMIN = Principal(PrincipalKind.USER, "adm", Role.ADMIN, "admin@example.com")


@pytest.fixture
def s3(aws: Any) -> Any:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="example-assets-111122223333-us-east-1")
    return client


# ---- products without a knowledge base ---------------------------------------------------------


def test_products_work_without_knowledge_base(ops_table: Any, s3: Any, clock: FixedClock) -> None:
    svc = ProductService(ops_table, s3, "example-assets-111122223333-us-east-1", None, clock)
    p = svc.create(ProductProfile("", "DeskPro", "Standing desk", doc_urls=("https://docs.example.com",)), "u")
    assert p.web_status is IngestionStatus.PENDING  # no crawler configured: stays pending
    up = svc.presign_upload(p.id, "a.txt", 3, "u")
    s3.put_object(
        Bucket="example-assets-111122223333-us-east-1",
        Key=up["upload"]["fields"]["key"],
        Body=b"abc",
        ContentType="text/plain",
    )
    assert svc.complete_upload(p.id, up["document"]["id"]).status is IngestionStatus.INDEXING
    svc.resync(p.id)
    assert svc.mark_documents_indexed(p.id, IngestionStatus.PENDING, None) == 0
    svc.delete(p.id)
    assert svc.list_all() == []


def test_mark_indexed_skips_documents_not_indexing(ops_table: Any, s3: Any, clock: FixedClock) -> None:
    svc = ProductService(ops_table, s3, "example-assets-111122223333-us-east-1", None, clock)
    p = svc.create(ProductProfile("", "DeskPro", "Standing desk"), "u")
    svc.presign_upload(p.id, "a.txt", 3, "u")  # still pending: never uploaded
    assert svc.mark_documents_indexed(p.id, IngestionStatus.READY, "2100-01-01T00:00:00.000Z") == 0


def test_document_cap(ops_table: Any, s3: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_service, "MAX_DOCUMENTS_PER_PRODUCT", 1)
    svc = ProductService(ops_table, s3, "example-assets-111122223333-us-east-1", None, clock)
    p = svc.create(ProductProfile("", "DeskPro", "Standing desk"), "u")
    svc.presign_upload(p.id, "a.txt", 3, "u")
    with pytest.raises(ValidationError, match="at most 1 documents"):
        svc.presign_upload(p.id, "b.txt", 3, "u")


def test_product_update_reraises_unexpected_errors(ops_table: Any, s3: Any, clock: FixedClock) -> None:
    p = ProductService(ops_table, s3, "example-assets-111122223333-us-east-1", None, clock).create(
        ProductProfile("", "DeskPro", "Desk"), "u"
    )
    bad = ProductService(
        Chaos(ops_table, {"put_item": THROTTLE}), s3, "example-assets-111122223333-us-east-1", None, clock
    )
    with pytest.raises(ClientError):
        bad.update(p.id, {"summary": "x"}, 1)


# ---- radar storage -----------------------------------------------------------------------------


def test_s3_blob_store_roundtrip_and_prefix_delete(s3: Any) -> None:
    store = S3BlobStore(s3, "example-assets-111122223333-us-east-1")
    store.put("radars/a/v000001.json", b"{}")
    store.put("radars/a/v000002.json", b"[]")
    store.put("radars/b/v000001.json", b"{}")
    assert store.get("radars/a/v000002.json") == b"[]"
    store.delete_prefix("radars/a/")
    store.delete_prefix("radars/none/")
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="example-assets-111122223333-us-east-1")["Contents"]]
    assert keys == ["radars/b/v000001.json"]


def test_radar_write_reraises_unexpected_errors(ops_table: Any, clock: FixedClock) -> None:
    store = MemoryBlobStore()
    RadarService(ops_table, store, clock).create(OWNER, "r1", "R1", template="competitor_landscape")
    bad = RadarService(
        Chaos(ops_table, {"transact_write_items": THROTTLE, "put_item": THROTTLE, "update_item": THROTTLE}),
        store,
        clock,
    )
    with pytest.raises(ClientError):
        bad.save(OWNER, "r1", [RadarEntry(key="k", title="A", sector="Direct", ring="Leader")], 1)


def test_radar_list_follows_pages_and_caps(ops_table: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryBlobStore()
    svc = RadarService(ops_table, store, clock)
    for i in range(3):
        svc.create(OWNER, f"r{i}", f"R{i}", template="blank")
    assert len(RadarService(Chaos(ops_table, page=True), store, clock).list_all()) == 3
    monkeypatch.setattr(radar_service, "MAX_RADARS", 3)
    with pytest.raises(ValidationError, match="at most 3 radars"):
        svc.create(OWNER, "r9", "R9", template="blank")


def test_schema_update_detects_concurrent_save(ops_table: Any, clock: FixedClock) -> None:
    svc = RadarService(ops_table, MemoryBlobStore(), clock)
    r = svc.create(OWNER, "r1", "R1", template="competitor_landscape")
    with pytest.raises(ConflictError):
        svc.update_schema(OWNER, "r1", r.schema, r.current_version + 5)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"slug": "Bad Slug"}, "slug must be"),
        ({"slug": "api"}, "reserved"),
        ({"name": " "}, "name must be"),
        ({"purpose": "p" * 301}, "purpose"),
        ({"description": "d" * 2001}, "description"),
        ({"auto_apply_days": 0}, "auto_apply_days"),
        ({"editors": tuple(f"e{i}@example.com" for i in range(51))}, "at most 50 editors"),
    ],
)
def test_radar_validation(changes: dict[str, Any], message: str) -> None:
    base = Radar(slug="ok", name="OK", schema=RadarSchema.from_public({"sectors": ["A"], "rings": ["B"]}))
    with pytest.raises(ValidationError) as err:
        Radar.validate(replace(base, **changes))
    assert any(message in e for e in err.value.details["errors"])


def test_levenshtein_identity() -> None:
    assert _levenshtein("same", "same") == 0


def test_autopilot_holds_a_suggestion_it_cannot_apply(ops_table: Any, clock: FixedClock) -> None:
    """r2 B2-012/B2-049: an apply that fails validation or races a human save is held for the next
    tick, not deleted as superseded."""
    svc = RadarService(ops_table, MemoryBlobStore(), clock)
    svc.create(OWNER, "auto", "Auto", template="competitor_landscape")
    svc.create(OWNER, "manual", "Manual", template="competitor_landscape")
    svc.update_meta(ADMIN, "auto", {"mode": "autopilot", "auto_apply_days": 1, "auto_apply_agent": True}, 1)
    svc.propose(
        "auto",
        "module:curator",
        "r1",
        {"title": "Desky", "sector": "Emerging", "ring": "Watch"},
        "New entrant in three outlets",
        evidence=["fact/1"],
    )
    # a human adds the same title under a different key before autopilot fires
    svc.save(OWNER, "auto", [RadarEntry(key="h1", title="Desky", sector="Direct", ring="Leader")], 1)
    clock.advance(timedelta(days=2))
    assert svc.autopilot_tick() == {"applied": 0, "superseded": 0, "held": 1, "errors": 0}
    assert [s["entry_key"] for s in svc.suggestions("auto")]


def test_crawl_storage_without_knowledge_base(ops_table: Any, s3: Any, clock: FixedClock) -> None:
    from insights_radar.product.crawler import CrawledPage, CrawlReport

    svc = ProductService(ops_table, s3, "example-assets-111122223333-us-east-1", None, clock)
    p = svc.create(ProductProfile("", "DeskPro", "Desk", doc_urls=("https://docs.example.com/",)), "u")
    stored = svc.store_crawl(p.id, CrawlReport(pages=[CrawledPage("https://docs.example.com/", "", "t " * 200)]))
    assert stored.web_status is IngestionStatus.READY and stored.web_pages == 1
    q = svc.create(ProductProfile("", "Other", "Desk"), "u")
    svc.resync(q.id)  # no documentation and no knowledge base: nothing to do
    svc.request_crawl(p.id)  # no crawler configured: nothing to do


def test_fetcher_close() -> None:
    from insights_radar.core.fetch import SafeFetcher

    SafeFetcher().close()
