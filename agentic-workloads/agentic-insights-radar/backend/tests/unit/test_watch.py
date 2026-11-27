# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock, iso
from insights_radar.modules.model import Source, SourceType
from insights_radar.modules.watch import (
    FIRST_WINDOW,
    PENDING_MAX,
    Item,
    SourceWatcher,
    WatchState,
    WatchStore,
    _same_site,
    extract_items,
    fingerprint,
    normalise,
    parse_date,
)

FEED = """<?xml version="1.0"?><rss><channel><title>T</title>
<item><title>Fresh rule</title><link>https://gov.example.com/a</link><pubDate>{fresh}</pubDate></item>
<item><title>Old rule</title><link>/b</link><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate></item>
<item><title>By guid only</title><guid>urn:x:7</guid></item>
<item><title>No link at all</title></item>
<item><description>neither title nor link</description></item>
</channel></rss>"""
ATOM = """<feed><title>A</title><entry><title>Atom entry</title><id>https://gov.example.com/e/1</id>
<updated>2026-10-02T07:00:00Z</updated></entry></feed>"""


def src(sid: str = "s1", address: str = "https://gov.example.com/news", enabled: bool = True) -> Source:
    return Source(sid, "scout", "News", SourceType.WEB_PAGE, address, enabled=enabled)


class Fetcher:
    def __init__(self, pages: dict[str, Any], robots: str | Exception = "") -> None:
        self.pages = pages
        self.robots = robots
        self.robots_reads = 0
        self.closed = 0

    def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
        assert deadline > 0
        if url.endswith("/robots.txt"):
            self.robots_reads += 1
            if isinstance(self.robots, Exception):
                raise self.robots
            return {"text": self.robots}
        assert keep_raw
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    def close(self) -> None:
        self.closed += 1


def page(
    raw: str, url: str = "https://gov.example.com/news", ctype: str = "text/html", text: str = ""
) -> dict[str, Any]:
    return {"raw": raw, "url": url, "content_type": ctype, "text": text}


def test_addresses_compare_without_tracking_fragments_or_order() -> None:
    a = normalise("HTTPS://Gov.Example.com/x/?utm_source=m&b=2&a=1#top")
    assert a == normalise("https://gov.example.com/x?a=1&b=2&fbclid=z") == "https://gov.example.com/x?a=1&b=2"
    assert normalise("https://gov.example.com") == "https://gov.example.com/"
    assert len(fingerprint("x")) == 20 and fingerprint("x") != fingerprint("y")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-26", "2026-10-26T00:00:00.000Z"),
        ("2026-10-26T01:02:03", "2026-10-26T01:02:03.000Z"),
        ("2026-10-26T01:02:03+09:00", "2026-10-25T16:02:03.000Z"),
        ("Mon, 26 Oct 2026 01:00:00 +0000", "2026-10-26T01:00:00.000Z"),
        ("Mon, 26 Oct 2026 01:00:00", "2026-10-26T01:00:00.000Z"),
    ],
)
def test_dates_as_feeds_and_apis_state_them(value: str, expected: str) -> None:
    parsed = parse_date(value)
    assert parsed is not None and iso(parsed) == expected


@pytest.mark.parametrize("value", ["", "26 Oct", None, 7, "2026.10.30"])
def test_unreadable_dates_are_none(value: Any) -> None:
    assert parse_date(value) is None


def test_json_api_records_with_an_address_or_an_id() -> None:
    fr = {
        "results": [
            {"title": "Rule A", "html_url": "https://www.federalregister.gov/d/1", "publication_date": "2026-10-01"}
        ]
    }
    au = {"value": [{"id": "F2026L01407", "name": "Autonomous Sanctions", "makingDate": "2026-10-20T00:00:00"}]}
    deep: Any = {"a": [{"b": {"c": [{"d": {"e": {"title": "too deep", "url": "https://x.example.com/1"}}}]}}]}
    items = extract_items(page(json.dumps([fr, au, deep, "skip", {"title": "no id or url"}]), ctype="application/json"))
    assert [(i.title, i.url, i.date) for i in items] == [
        ("Rule A", "https://www.federalregister.gov/d/1", "2026-10-01"),
        ("Autonomous Sanctions", "", "2026-10-20T00:00:00"),
    ]
    assert items[1].fp == fingerprint("id:F2026L01407")


def test_json_fields_in_any_case_with_language_variants_and_site_relative_links() -> None:
    gov_cn = [
        {"TITLE": "State Council notice", "URL": "https://www.gov.cn/zhengce/1.htm", "DOCRELPUBTIME": "2026-10-26"}
    ]
    tid = [
        {
            "date": "2026-10-28",
            "tc_title": "zh",
            "en_title": "Trade circular",
            "en_url": "en/tradecircular/c1.html",
            "id": 7,
        }
    ]
    items = extract_items(
        page(json.dumps(gov_cn + tid), url="https://www.tid.gov.hk/js/data/2026.json", ctype="application/json")
    )
    assert [(i.title, i.url, i.date) for i in items] == [
        ("State Council notice", "https://www.gov.cn/zhengce/1.htm", "2026-10-26"),
        ("Trade circular", "https://www.tid.gov.hk/en/tradecircular/c1.html", "2026-10-28"),
    ]
    assert list(extract_items(page(json.dumps([{"title": True, "id": 1}]), ctype="application/json"))) == []


def test_records_that_share_an_address_stay_distinct() -> None:
    tid = [
        {"date": "2026-10-28", "en_title": "BUD Fund enhancement", "en_url": "en/"},
        {"date": "2026-09-01", "en_title": "FTA with Peru", "en_url": "en/"},
    ]
    items = extract_items(
        page(json.dumps(tid), url="https://www.tid.gov.hk/js/data/whatsnew.json", ctype="application/json")
    )
    assert [i.url for i in items] == ["https://www.tid.gov.hk/en/"] * 2 and len({i.fp for i in items}) == 2
    feed = (
        "<rss><channel>"
        + "".join(
            f"<item><title>Notice {n}</title><link>https://gov.example.com/kokuji.htm</link></item>" for n in (296, 297)
        )
        + "<item><title>Alone</title><link>https://gov.example.com/a</link></item></channel></rss>"
    )
    got = extract_items(page(feed, ctype="text/xml"))
    assert len({i.fp for i in got}) == 3 and got[2].fp == fingerprint(normalise("https://gov.example.com/a"))


def test_text_that_only_looks_like_json_is_read_as_a_page() -> None:
    items = extract_items(page('{"not json <a href="/n/1">A notice title here</a>', ctype="text/html"))
    assert [i.url for i in items] == ["https://gov.example.com/n/1"]


def test_feed_entries_with_link_guid_or_title() -> None:
    items = extract_items(page(FEED.format(fresh="Fri, 02 Oct 2026 06:00:00 +0000"), ctype="text/xml"))
    assert [(i.title, i.url) for i in items] == [
        ("Fresh rule", "https://gov.example.com/a"),
        ("Old rule", "https://gov.example.com/b"),
        ("By guid only", ""),
        ("No link at all", ""),
    ]
    assert items[2].fp == fingerprint("id:urn:x:7") and items[3].fp == fingerprint("title:No link at all")
    atom = extract_items(page(ATOM, ctype="application/atom+xml"))
    assert [(i.title, i.url, i.date) for i in atom] == [
        ("Atom entry", "https://gov.example.com/e/1", "2026-10-02T07:00:00Z")
    ]


def test_page_links_on_the_same_site_and_text_rows() -> None:
    html = """<html><body><script><a href="/s">script link text</a></script>
    <a href="/n/1">Regulation No. 45/2026 on radio equipment</a>
    <a href="https://docs.gov.example.com/n/2">Decree on spectrum fees 2026</a>
    <a href="https://other.example.org/x">Partner site link text</a>
    <a href="http://gov.example.com/insecure">Insecure link text</a>
    <a href="mailto:x@gov.example.com">mail us at this address</a>
    <a href="#top">Back to the top of page</a>
    <a href="/short">Short</a>
    <a>no href anchor text</a></body></html>"""
    text = "Regulation No. 45/2026 on radio equipment\nViewed 845 times by the public this week\nshort line"
    items = extract_items(page(html, text=text))
    assert [i.url for i in items] == [
        "https://gov.example.com/n/1",
        "https://docs.gov.example.com/n/2",
        "",  # a text row has no link of its own (and the page address may hold a credential)
    ]
    assert items[-1].title == "Viewed 845 times by the public this week"
    # The counter is masked: another day's count is the same item.
    again = extract_items(page(html, text="Viewed 912 times by the public this week"))
    assert again[-1].fp == items[-1].fp


def test_sites_with_a_second_level_public_suffix() -> None:
    assert _same_site("jdih.kemendag.go.id", "www.kemendag.go.id")
    assert not _same_site("www.kemenkeu.go.id", "www.kemendag.go.id")
    assert _same_site("www.example.com:443", "news.example.com")


def test_state_public_view() -> None:
    state = WatchState(pending=[{"fp": "a"}], checked_at="t", status="ok", items=3, dropped=1)
    assert state.to_public() == {"checked_at": "t", "status": "ok", "error": "", "items": 3, "pending": 1, "dropped": 1}


def test_store_round_trip_conflict_ack_and_delete(ops_table: Any) -> None:
    store = WatchStore(ops_table)
    assert store.get("scout", "s1") == WatchState()
    state = WatchState(seen=["a", "b"], pending=[{"fp": "a", "url": "u"}, {"fp": "b", "url": "v"}], status="ok")
    assert store.put("scout", "s1", state) and state.version == 1
    stale = WatchState(version=0)
    assert not store.put("scout", "s1", stale)  # someone wrote since it was read
    assert store.many("scout", ["s1", "nope"])["s1"].pending == state.pending
    assert store.ack("scout", "s1", ["a"]) == 1 and [p["fp"] for p in store.get("scout", "s1").pending] == ["b"]
    assert store.ack("scout", "s1", ["zz"]) == 0
    store.delete("scout", "s1")
    assert store.get("scout", "s1") == WatchState()


def test_store_reads_every_page_of_a_module(ops_table: Any) -> None:
    calls: list[dict[str, Any]] = []

    class Paged:
        def query(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [{"pk": "WATCH#scout", "sk": "SRC#s1", "status": "ok"}], "LastEvaluatedKey": {"k": 1}}
            return {
                "Items": [
                    {"pk": "WATCH#scout", "sk": "SRC#s2", "status": "error"},
                    {"pk": "WATCH#scout", "sk": "SRC#gone"},
                ]
            }

    got = WatchStore(Paged()).many("scout", ["s1", "s2"])
    assert {k: v.status for k, v in got.items()} == {"s1": "ok", "s2": "error"} and len(calls) == 2


def test_store_raises_other_write_errors_and_gives_up_an_endless_race() -> None:
    boom = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    class Failing:
        def put_item(self, **_: Any) -> None:
            raise boom

    with pytest.raises(ClientError):
        WatchStore(Failing()).put("scout", "s1", WatchState())

    class Racing(WatchStore):
        def get(self, slug: str, source_id: str) -> WatchState:
            return WatchState(pending=[{"fp": "a"}])

        def put(self, slug: str, source_id: str, state: WatchState) -> bool:
            return False

    assert Racing(None).ack("scout", "s1", ["a"]) == 0


def test_first_check_queues_only_todays_items(ops_table: Any, clock: FixedClock) -> None:
    fresh = (clock.now() - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    fetcher = Fetcher({"https://gov.example.com/news": page(FEED.format(fresh=fresh), ctype="text/xml")})
    store = WatchStore(ops_table)
    watcher = SourceWatcher(store, lambda: fetcher, clock)
    (result,) = watcher.check_all("scout", [src(), src("off", enabled=False)])
    assert result.to_public() == {"source_id": "s1", "status": "ok", "new": 1, "items": 4, "error": ""}
    state = store.get("scout", "s1")
    assert [p["title"] for p in state.pending] == ["Fresh rule"] and len(state.seen) == 4
    assert fetcher.closed == 1 and timedelta(hours=24) == FIRST_WINDOW


def test_later_checks_queue_unseen_items_but_not_an_old_archive(ops_table: Any, clock: FixedClock) -> None:
    store = WatchStore(ops_table)
    store.put("scout", "s1", WatchState(seen=[fingerprint(normalise("https://gov.example.com/a"))], status="ok"))
    recent = (clock.now() - timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    fetcher = Fetcher({"https://gov.example.com/news": page(FEED.format(fresh=recent), ctype="text/xml")})
    result = SourceWatcher(store, lambda: fetcher, clock).check("scout", src())
    # "Fresh rule" was seen; "Old rule" is from 2024 (an archive coming into view); the undated two are new.
    assert result.new == 2
    titles = [p["title"] for p in store.get("scout", "s1").pending]
    assert titles == ["By guid only", "No link at all"]
    # The same page again: nothing new, nothing queued twice.
    assert SourceWatcher(store, lambda: fetcher, clock).check("scout", src()).new == 0
    assert len(store.get("scout", "s1").pending) == 2


def test_the_inbox_keeps_the_newest_items_and_counts_the_dropped(ops_table: Any, clock: FixedClock) -> None:
    store = WatchStore(ops_table)
    store.put("scout", "s1", WatchState(seen=["x"], pending=[{"fp": f"old{i}"} for i in range(PENDING_MAX)]))
    links = "".join(f'<a href="/n/{i}">Notice number {i} on equipment</a>' for i in range(5))
    fetcher = Fetcher({"https://gov.example.com/news": page(links)})
    SourceWatcher(store, lambda: fetcher, clock).check("scout", src())
    state = store.get("scout", "s1")
    assert (
        len(state.pending) == PENDING_MAX
        and state.dropped == 5
        and state.pending[-1]["title"].startswith("Notice number 4")
    )


def test_failures_and_empty_pages_are_the_sources_status(ops_table: Any, clock: FixedClock) -> None:
    store = WatchStore(ops_table)

    class NoClose:
        def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
            if url.endswith("/robots.txt"):
                return {"text": ""}
            raise TimeoutError("read timed out")

    failed = SourceWatcher(store, NoClose, clock).check("scout", src())
    assert failed.status == "error" and failed.error == "TimeoutError: read timed out"

    class Refused:
        def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
            if url.endswith("/robots.txt"):
                return {"text": ""}
            raise RuntimeError(f"{url} returned HTTP 429")

    keyed = SourceWatcher(store, Refused, clock).check(
        "scout", src("s9", "https://gov.example.com/api?api_key=Secret123456")
    )
    assert keyed.error == "RuntimeError: <source address> returned HTTP 429"
    assert store.get("scout", "s1").status == "error"
    blank = Fetcher({"https://gov.example.com/news": page("<html><body><div id=app></div></body></html>")})
    store.put("scout", "s2", WatchState(seen=["kept"]))
    empty = SourceWatcher(store, lambda: blank, clock).check("scout", src("s2"))
    state = store.get("scout", "s2")
    assert empty.status == "empty" and state.seen == ["kept"] and state.error == "the page listed no items"


def test_a_checkpoint_that_stays_busy_is_reported(clock: FixedClock) -> None:
    class Busy(WatchStore):
        def get(self, slug: str, source_id: str) -> WatchState:
            return WatchState()

        def put(self, slug: str, source_id: str, state: WatchState) -> bool:
            return False

    fetcher = Fetcher({"https://gov.example.com/news": page('<a href="/n/1">A notice title here</a>')})
    result = SourceWatcher(Busy(None), lambda: fetcher, clock).check("scout", src())
    assert result.status == "error" and "busy" in result.error
    assert SourceWatcher(Busy(None), lambda: fetcher, clock).check_all("scout", []) == []


def test_item_pending_form() -> None:
    assert Item("f", "u", "t", "d").to_pending("now") == {
        "fp": "f",
        "url": "u",
        "title": "t",
        "date": "d",
        "found_at": "now",
    }


def test_a_source_robots_txt_disallows_is_an_error_and_robots_is_read_once_per_host(
    ops_table: Any, clock: FixedClock
) -> None:
    links = '<a href="/n/1">A notice title here</a>'
    pages = {"https://gov.example.com/news": page(links), "https://gov.example.com/other": page(links)}
    fetcher = Fetcher(pages, robots="User-agent: AgenticInsightsRadar\nDisallow: /news\n")
    watcher = SourceWatcher(WatchStore(ops_table), lambda: fetcher, clock, workers=1)
    blocked, allowed = watcher.check_all("scout", [src(), src("s2", "https://gov.example.com/other")])
    assert blocked.status == "error" and "robots.txt does not allow AgenticInsightsRadar" in blocked.error
    assert allowed.status == "ok" and fetcher.robots_reads == 1
    unreadable = Fetcher(pages, robots=TimeoutError("slow"))
    assert SourceWatcher(WatchStore(ops_table), lambda: unreadable, clock).check("scout", src()).status == "ok"


def test_a_checkpoint_deleted_during_a_check_does_not_come_back(ops_table: Any) -> None:
    store = WatchStore(ops_table)
    state = WatchState(seen=["a"], status="ok")
    assert store.put("scout", "s1", state)  # version 1
    store.delete("scout", "s1")  # the source's address changed meanwhile
    assert not store.put("scout", "s1", state)  # the in-flight write, read at version 1
    assert store.get("scout", "s1") == WatchState()


def test_a_checkpoint_from_another_address_is_a_new_baseline(ops_table: Any, clock: FixedClock) -> None:
    store = WatchStore(ops_table)
    old = Fetcher({"https://gov.example.com/news": page('<a href="/n/1">A notice title here</a>')})
    SourceWatcher(store, lambda: old, clock).check("scout", src())
    assert store.get("scout", "s1").seen
    new = Fetcher({"https://gov.example.com/other": page('<a href="/m/2">Another notice title</a>')})
    SourceWatcher(store, lambda: new, clock).check("scout", src(address="https://gov.example.com/other"))
    state = store.get("scout", "s1")
    # The old page's items are gone: the new address starts its own baseline (nothing undated queued).
    assert state.pending == [] and len(state.seen) == 1


def test_a_check_gives_up_when_the_checkpoint_vanished_during_its_fetch(ops_table: Any, clock: FixedClock) -> None:
    store = WatchStore(ops_table)
    store.put("scout", "s1", WatchState(seen=["a"], status="ok"))

    class Deleting(Fetcher):
        def fetch(self, url: str, deadline: float, keep_raw: bool = False) -> dict[str, Any]:
            if not url.endswith("/robots.txt"):
                store.delete("scout", "s1")  # the address changed while this check fetched the old one
            return super().fetch(url, deadline, keep_raw)

    fetcher = Deleting({"https://gov.example.com/news": page('<a href="/n/1">A notice title here</a>')})
    result = SourceWatcher(store, lambda: fetcher, clock).check("scout", src())
    assert result.status == "error" and "changed during the check" in result.error
    assert store.get("scout", "s1") == WatchState()
