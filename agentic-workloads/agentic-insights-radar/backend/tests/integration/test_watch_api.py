# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

import pytest

from insights_radar.api import workers
from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from insights_radar.modules.model import SourceType
from insights_radar.modules.watch import WatchState
from tests.integration.harness import Client, TestContext, ensure_running


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    return Client(Application(ctx))


def _token(ctx: TestContext, slug: str = "scout") -> str:
    ensure_running(ctx, slug)
    return ctx.tokens.issue(slug, "run-1", ctx.modules.get(slug).tools, 600, None)


def _sources(ctx: TestContext) -> tuple[Any, Any]:
    a = ctx.modules.add_source("scout", "Feed A", SourceType.RSS, "https://a.example.com/feed")
    b = ctx.modules.add_source("scout", "Page B", SourceType.WEB_PAGE, "https://b.example.com/news")
    pending = [
        {"fp": f"f{i}", "title": f"Item {i}", "url": f"https://a.example.com/{i}", "date": "", "found_at": "t"}
        for i in range(45)
    ]
    ctx.watch.put(
        "scout",
        a.id,
        WatchState(seen=["x"], pending=pending, checked_at="2026-10-02T06:00:00.000Z", status="ok", items=3),
    )
    return a, b


def test_the_scout_sees_each_sources_inbox_and_watch_status(api: Client, ctx: TestContext) -> None:
    a, b = _sources(ctx)
    status, body = api.tool("source_list", _token(ctx))
    assert status == 200
    by_id = {s["id"]: s for s in body["result"]["sources"]}
    assert by_id[a.id]["watched"] and by_id[a.id]["watch"]["pending"] == 45
    assert len(by_id[a.id]["inbox"]) == 40 and by_id[a.id]["inbox"][0] == {
        "fp": "f0",
        "title": "Item 0",
        "url": "https://a.example.com/0",
        "date": "",
    }
    # Never checked: not watched, no inbox, read as before.
    assert not by_id[b.id]["watched"] and by_id[b.id]["watch"] is None and by_id[b.id]["inbox"] == []


def test_the_runtime_acknowledges_a_finished_passs_items(api: Client, ctx: TestContext) -> None:
    a, _ = _sources(ctx)
    tok = _token(ctx)
    status, body = api.tool("source_inbox_ack", tok, {"source_id": a.id, "fps": ["f0", "f1", "nope"]})
    assert status == 200 and body["result"] == {"id": a.id, "acked": 2}
    assert len(ctx.watch.get("scout", a.id).pending) == 43
    for bad in (
        {"source_id": a.id},
        {"source_id": a.id, "fps": "f0"},
        {"source_id": a.id, "fps": [1]},
        {"source_id": a.id, "fps": ["x" * 41]},
    ):
        assert api.tool("source_inbox_ack", tok, bad)[0] == 400, bad
    assert api.tool("source_inbox_ack", tok, {"source_id": a.id, "fps": ["f"] * 501})[0] == 400
    assert api.tool("source_inbox_ack", tok, {"source_id": "nope000000", "fps": []})[0] == 404


def test_managers_see_watch_status_and_can_ask_for_a_check(api: Client, ctx: TestContext) -> None:
    a, b = _sources(ctx)
    status, body, _ = api.call("GET", "/api/modules/scout/sources")
    watch = {s["id"]: s["watch"] for s in body["items"]}
    assert status == 200 and watch[a.id]["pending"] == 45 and watch[b.id] is None
    status, body, _ = api.call("GET", "/api/modules/scout/sources", role="viewer")
    assert status == 200 and "watch" not in body["items"][0]
    assert api.call("POST", "/api/modules/scout/sources/check")[:2] == (202, {"requested": True})
    assert ctx.watch_requests == 1
    assert api.call("POST", "/api/modules/scout/sources/check", role="viewer")[0] == 403
    assert api.call("POST", "/api/modules/nope/sources/check")[0] == 404
    ctx.watch_enabled = False
    status, body, _ = api.call("POST", "/api/modules/scout/sources/check")
    assert status == 400 and "no source watcher" in body["message"]


def test_deleting_a_source_or_moving_its_address_resets_its_checkpoint(ctx: TestContext) -> None:
    a, b = _sources(ctx)
    ctx.modules.update_source("scout", a.id, {"name": "Renamed"})
    assert ctx.watch.get("scout", a.id).pending  # a rename keeps the checkpoint
    ctx.modules.update_source("scout", a.id, {"address": "https://a.example.com/other-feed"})
    assert ctx.watch.get("scout", a.id) == WatchState()
    ctx.watch.put("scout", b.id, WatchState(seen=["y"], status="ok"))
    ctx.modules.delete_source("scout", b.id)
    assert ctx.watch.get("scout", b.id) == WatchState()


def test_the_scheduled_check_covers_enabled_scout_modules_only(
    ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fetcher:
        def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
            if "b.example.com" in url:
                raise TimeoutError("slow")
            return {
                "raw": "<rss><channel><item><title>New rule today</title><link>https://a.example.com/r1</link></item></channel></rss>",
                "url": url,
                "content_type": "text/xml",
                "text": "",
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(workers, "SafeFetcher", Fetcher)
    ctx.modules.add_source("scout", "Feed A", SourceType.RSS, "https://a.example.com/feed")
    ctx.modules.add_source("scout", "Page B", SourceType.WEB_PAGE, "https://b.example.com/news")
    assert workers.watch({}, None, ctx) == {}  # the Scout ships turned off
    scout = ctx.modules.get("scout")
    ctx.modules.update("scout", {"enabled": True}, scout.version)
    out = workers.watch({}, None, ctx)
    assert out == {"scout": {"ok": 1, "empty": 0, "error": 1, "new": 0}}  # first check: a baseline


def test_clearing_the_read_position_starts_the_source_over(ctx: TestContext) -> None:
    a, b = _sources(ctx)
    ctx.modules.update_source("scout", a.id, {"cursor": "2026-10-01T00:00:00Z"})
    ctx.modules.update_source("scout", a.id, {"cursor": ""})
    assert ctx.watch.get("scout", a.id) == WatchState() and ctx.modules.get_source("scout", a.id).cursor == ""
    # Clearing an empty position changes nothing else.
    ctx.watch.put("scout", b.id, WatchState(seen=["y"], status="ok"))
    ctx.modules.update_source("scout", b.id, {"cursor": ""})
    assert ctx.watch.get("scout", b.id).seen == ["y"]


def test_what_the_scout_sees_of_the_watcher_never_holds_a_sources_credential(api: Client, ctx: TestContext) -> None:
    keyed = ctx.modules.add_source(
        "scout", "Keyed API", SourceType.PUBLIC_API, "https://api.example.gov/notices?api_key=S3cr3tK3yValue123456"
    )
    tokened = ctx.modules.add_source(
        "scout", "Private feed", SourceType.RSS, "https://feeds.example.com/private/9f3cPRIVATEFEEDTOKEN77/rss"
    )
    item = {"fp": "f1", "date": "", "found_at": "t"}
    ctx.watch.put(
        "scout",
        keyed.id,
        WatchState(
            seen=["x"],
            pending=[
                {
                    **item,
                    "title": "Rule seen at https://api.example.gov/notices?api_key=S3cr3tK3yValue123456",
                    "url": "",
                }
            ],
            status="error",
            error="FetchError: https://api.example.gov/notices?api_key=S3cr3tK3yValue123456 returned HTTP 429",
        ),
    )
    ctx.watch.put(
        "scout",
        tokened.id,
        WatchState(
            seen=["x"],
            pending=[
                {**item, "title": "Item 1", "url": "https://feeds.example.com/private/9f3cPRIVATEFEEDTOKEN77/item/1"}
            ],
            status="ok",
        ),
    )
    _, body = api.tool("source_list", _token(ctx))
    text = str(body["result"])
    assert "S3cr3tK3yValue123456" not in text and "9f3cPRIVATEFEEDTOKEN77" not in text
    by_id = {s["id"]: s for s in body["result"]["sources"]}
    assert by_id[keyed.id]["watch"]["error"] == f"FetchError: source:{keyed.id} returned HTTP 429"
    assert by_id[tokened.id]["inbox"][0]["url"] == "https://feeds.example.com/private/REDACTED/item/1"


def test_a_credential_under_a_name_the_guard_does_not_know_is_hidden_however_it_is_echoed(
    api: Client, ctx: TestContext
) -> None:
    """r14: ``accessKey`` is no listed name, and a page echoing the query reordered or re-encoded slipped past
    the standalone-address rule. Every query value of a source's own address is now scrubbed."""
    src = ctx.modules.add_source(
        "scout", "Odd key", SourceType.PUBLIC_API, "https://api.example.gov/notices?lang=en&accessKey=Fa/k+e0KEY000000"
    )
    item = {"fp": "f1", "date": "", "found_at": "t"}
    echoes = [
        "https://api.example.gov/notices?accessKey=Fa/k+e0KEY000000&lang=en",
        "https://api.example.gov/notices?accessKey=Fa%2Fk%20e0KEY000000&page=2",
        "https://api.example.gov/notices?accessKey=Fa%2Fk+e0KEY000000",
    ]
    ctx.watch.put(
        "scout",
        src.id,
        WatchState(seen=["x"], pending=[{**item, "fp": f"f{i}", "title": "t", "url": u} for i, u in enumerate(echoes)]),
    )
    _, body = api.tool("source_list", _token(ctx))
    text = str(body["result"])
    for form in ("Fa/k+e0KEY000000", "Fa%2Fk%20e0KEY000000", "Fa%2Fk+e0KEY000000", "e0KEY000000"):
        assert form not in text


def test_a_source_the_watcher_cannot_list_is_read_by_the_scout_as_before(api: Client, ctx: TestContext) -> None:
    a, b = _sources(ctx)
    ctx.watch.put("scout", b.id, WatchState(seen=[], status="empty", checked_at="2026-10-02T06:00:00.000Z"))
    _, body = api.tool("source_list", _token(ctx))
    by_id = {s["id"]: s for s in body["result"]["sources"]}
    assert by_id[a.id]["watched"] and not by_id[b.id]["watched"]


def test_a_refused_address_change_keeps_the_sources_checkpoint(ctx: TestContext) -> None:
    from insights_radar.core.errors import ValidationError as Refused

    a, _ = _sources(ctx)
    with pytest.raises(Refused):
        ctx.modules.update_source("scout", a.id, {"address": "http://insecure.example.com/feed"})
    assert ctx.watch.get("scout", a.id).pending


def test_a_plain_listings_inbox_links_stay_fetchable(api: Client, ctx: TestContext) -> None:
    plain = ctx.modules.add_source(
        "scout", "ML blog", SourceType.WEB_PAGE, "https://aws.amazon.com/blogs/machine-learning/"
    )
    url = "https://aws.amazon.com/blogs/machine-learning/new-post/"
    ctx.watch.put(
        "scout",
        plain.id,
        WatchState(seen=["x"], pending=[{"fp": "f", "title": "New post", "url": url, "date": ""}], status="ok"),
    )
    _, body = api.tool("source_list", _token(ctx))
    assert {s["id"]: s for s in body["result"]["sources"]}[plain.id]["inbox"][0]["url"] == url


def test_a_credential_the_guard_does_not_name_never_reaches_the_scout(api: Client, ctx: TestContext) -> None:
    keyed = ctx.modules.add_source(
        "scout", "Keyed", SourceType.PUBLIC_API, "https://api.example.gov/notices?accessKey=Fake0Key0000"
    )
    item = {
        "fp": "f",
        "title": "N 42",
        "url": "https://api.example.gov/notices?accessKey=Fake0Key0000#n-42",
        "date": "",
    }
    ctx.watch.put("scout", keyed.id, WatchState(seen=["x"], pending=[item], status="ok"))
    _, body = api.tool("source_list", _token(ctx))
    inbox = {s["id"]: s for s in body["result"]["sources"]}[keyed.id]["inbox"]
    assert inbox[0]["url"] == f"source:{keyed.id}#n-42" and "Fake0Key0000" not in str(body)


def test_the_scheduled_check_gives_up_on_a_source_changed_during_its_fetch(ctx: TestContext) -> None:
    from insights_radar.modules.watch import SourceWatcher

    a = ctx.modules.add_source("scout", "Feed A", SourceType.RSS, "https://a.example.com/feed")
    rss = (
        "<rss><channel><item><title>New rule today</title><link>https://a.example.com/r1</link></item></channel></rss>"
    )

    class Fetcher:
        def __init__(self, change: dict[str, Any] | None) -> None:
            self.change = change

        def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
            if not url.endswith("/robots.txt") and self.change is not None:
                if self.change:
                    ctx.modules.update_source("scout", a.id, self.change)
                else:
                    ctx.modules.delete_source("scout", a.id)
            return {"raw": rss, "url": url, "content_type": "text/xml", "text": ""}

        def close(self) -> None:
            pass

    for change in ({"address": "https://a.example.com/other"}, {"enabled": False}):
        current = ctx.modules.get_source("scout", a.id)
        out = SourceWatcher(ctx.watch, lambda c=change: Fetcher(c), ctx.clock, current=ctx.modules.get_source).check(
            "scout", current
        )
        assert out.error == "the source changed during the check", change
        ctx.modules.update_source("scout", a.id, {"address": "https://a.example.com/feed", "enabled": True})
    ok = SourceWatcher(ctx.watch, lambda: Fetcher(None), ctx.clock, current=ctx.modules.get_source)
    assert ok.check("scout", ctx.modules.get_source("scout", a.id)).status == "ok"
    gone = SourceWatcher(ctx.watch, lambda: Fetcher({}), ctx.clock, current=ctx.modules.get_source)
    assert gone.check("scout", ctx.modules.get_source("scout", a.id)).error == "the source changed during the check"


def test_a_deep_listings_inbox_links_stay_fetchable(api: Client, ctx: TestContext) -> None:
    deep = ctx.modules.add_source(
        "scout", "Press", SourceType.WEB_PAGE, "https://www.example.gov/en/news/press-releases"
    )
    url = "https://www.example.gov/en/news/press-releases/2026-10-30-new-rule"
    ctx.watch.put(
        "scout",
        deep.id,
        WatchState(seen=["x"], pending=[{"fp": "f", "title": "New rule", "url": url, "date": ""}], status="ok"),
    )
    _, body = api.tool("source_list", _token(ctx))
    assert {s["id"]: s for s in body["result"]["sources"]}[deep.id]["inbox"][0]["url"] == url


def test_a_plain_listings_query_and_anchor_articles_stay_fetchable(api: Client, ctx: TestContext) -> None:
    """Code review r14: nothing to hide in https://gov.example/news.php, so ?id= and # articles are kept as they are."""
    plain = ctx.modules.add_source("scout", "News", SourceType.WEB_PAGE, "https://gov.example/news.php")
    urls = ["https://gov.example/news.php?id=5123", "https://gov.example/news.php#item-2"]
    pending = [{"fp": f"f{i}", "title": "t", "url": u, "date": ""} for i, u in enumerate(urls)]
    ctx.watch.put("scout", plain.id, WatchState(seen=["x"], pending=pending, status="ok"))
    _, body = api.tool("source_list", _token(ctx))
    inbox = {s["id"]: s for s in body["result"]["sources"]}[plain.id]["inbox"]
    assert [i["url"] for i in inbox] == urls


def test_a_source_that_cannot_be_read_again_fails_alone_not_the_sweep(ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.modules.watch import SourceWatcher

    a = ctx.modules.add_source("scout", "Feed A", SourceType.RSS, "https://a.example.com/feed")

    class Fetcher:
        def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
            return {"raw": "<rss><channel></channel></rss>", "url": url, "content_type": "text/xml", "text": ""}

    def throttled(slug: str, source_id: str) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "GetItem")

    out = SourceWatcher(ctx.watch, Fetcher, ctx.clock, current=throttled).check("scout", a)
    assert out.status == "error"
    assert out.error == "the source could not be read again (ProvisionedThroughputExceededException)"
    assert not ctx.watch.get("scout", a.id).version  # nothing stored from a page it could not confirm


def test_a_throttled_checkpoint_fails_one_source_not_the_sweep(ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.modules.watch import SourceWatcher

    a = ctx.modules.add_source("scout", "Feed A", SourceType.RSS, "https://a.example.com/feed")

    class Store:
        def get(self, slug: str, source_id: str) -> Any:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "GetItem")

    out = SourceWatcher(Store(), lambda: None, ctx.clock).check("scout", a)  # type: ignore[arg-type]
    assert (out.status, out.error) == ("error", "the checkpoint could not be read or written (ThrottlingException)")


def test_the_inbox_fingerprint_is_never_rewritten(api: Client, ctx: TestContext) -> None:
    """Code review r16: fp is the backend's own hash, sent back to acknowledge the item."""
    src = ctx.modules.add_source("scout", "Hex key", SourceType.RSS, "https://x.example.com/feed?k=deadbe7a9")
    ctx.watch.put(
        "scout",
        src.id,
        WatchState(seen=["x"], pending=[{"fp": "00deadbe7a9000000000", "title": "t", "url": "", "date": ""}]),
    )
    _, body = api.tool("source_list", _token(ctx))
    assert {s["id"]: s for s in body["result"]["sources"]}[src.id]["inbox"][0]["fp"] == "00deadbe7a9000000000"
