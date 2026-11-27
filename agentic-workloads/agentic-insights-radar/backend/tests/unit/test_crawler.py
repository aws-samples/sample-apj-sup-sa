# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

import pytest

from insights_radar.core.fetch import FetchError
from insights_radar.product.crawler import CrawledPage, CrawlReport, DocsCrawler, _normalise, _scope

TEXT = "Documentation text. " * 20


class Site:
    """A fake documentation site: url -> (text, links)."""

    def __init__(self, pages: dict[str, tuple[str, list[str]]], robots: str | Exception = "") -> None:
        self.pages = pages
        self.robots = robots
        self.fetched: list[str] = []

    def fetch(self, url: str, deadline_seconds: float | None = None) -> dict[str, Any]:
        self.fetched.append(url)
        if url.endswith("/robots.txt"):
            if isinstance(self.robots, Exception):
                raise self.robots
            return {"url": url, "text": self.robots, "links": []}
        if url not in self.pages:
            raise FetchError(f"{url} returned HTTP 404")
        text, links = self.pages[url]
        if text == "BOOM":
            raise TimeoutError("slow")
        return {"url": url, "title": url.rsplit("/", 1)[-1], "text": text, "links": links}


def crawler(site: Site, **kw: Any) -> DocsCrawler:
    return DocsCrawler(site, delay=0, sleep=lambda s: None, **kw)


def test_crawls_within_scope_and_dedupes() -> None:
    site = Site(
        {
            "https://docs.example.com/guide/": (
                TEXT,
                [
                    "/guide/a",
                    "/guide/b#part",
                    "/blog/x",
                    "https://other.example.com/guide/c",
                    "mailto:help@example.com",
                    "/guide/logo.png",
                    "/guide/a",
                ],
            ),
            "https://docs.example.com/guide/a": (TEXT, ["/guide/"]),
            "https://docs.example.com/guide/b": ("too short", []),
        }
    )
    report = crawler(site).crawl(["https://docs.example.com/guide/"])
    assert [p.url for p in report.pages] == ["https://docs.example.com/guide/", "https://docs.example.com/guide/a"]
    assert report.skipped == 1 and report.stopped is None
    assert "https://docs.example.com/blog/x" not in site.fetched
    assert site.fetched.count("https://docs.example.com/robots.txt") == 1


def test_robots_txt_is_honoured() -> None:
    site = Site(
        {
            "https://docs.example.com/guide/": (TEXT, ["/guide/private/x"]),
            "https://docs.example.com/guide/private/x": (TEXT, []),
        },
        robots="User-agent: *\nDisallow: /guide/private/\n",
    )
    report = crawler(site).crawl(["https://docs.example.com/guide/"])
    assert [p.url for p in report.pages] == ["https://docs.example.com/guide/"] and report.skipped == 1


def test_errors_limits_and_unsafe_seeds() -> None:
    site = Site(
        {
            "https://docs.example.com/g/": (TEXT, ["/g/missing", "/g/slow", "/g/1", "/g/2"]),
            "https://docs.example.com/g/slow": ("BOOM", []),
            "https://docs.example.com/g/1": (TEXT, []),
            "https://docs.example.com/g/2": (TEXT, []),
        },
        robots=ConnectionError("no robots"),
    )
    report = crawler(site, max_pages=2).crawl(["https://docs.example.com/g/", "https://10.0.0.1/admin"])
    assert len(report.pages) == 2 and report.stopped == "page_limit"
    assert any("404" in e for e in report.errors) and any("TimeoutError" in e for e in report.errors)
    assert "https://10.0.0.1/admin" not in site.fetched
    ticks = iter([0.0, 0.0, 1000.0, 1000.0])
    timed = DocsCrawler(site, delay=0, sleep=lambda s: None, time_budget=10, now=lambda: next(ticks))
    assert timed.crawl(["https://docs.example.com/g/"]).stopped == "time_limit"


def test_depth_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from insights_radar.product import crawler as mod

    monkeypatch.setattr(mod, "MAX_DEPTH", 1)
    site = Site(
        {
            "https://d.example.com/": (TEXT, ["/1"]),
            "https://d.example.com/1": (TEXT, ["/2"]),
            "https://d.example.com/2": (TEXT, []),
        }
    )
    assert len(crawler(site).crawl(["https://d.example.com/"]).pages) == 2


def test_helpers() -> None:
    assert _normalise("HTTP://Docs.Example.com/a?b=1#c") == "https://docs.example.com/a?b=1"
    assert _normalise("javascript:alert(1)") == "" and _normalise("https://x.example.com") == "https://x.example.com/"
    assert _scope("https://d.example.com/guide/page.html") == ("d.example.com", "/guide/")
    assert _scope("https://d.example.com/guide/") == ("d.example.com", "/guide/")
    page = CrawledPage("https://d.example.com/a", "A", "t")
    assert len(page.key) == 24 and page.key == CrawledPage("https://d.example.com/a", "B", "u").key
    assert CrawlReport(errors=[str(i) for i in range(30)]).to_public()["errors"] == [str(i) for i in range(20)]


def test_redirect_targets_are_stored_once() -> None:
    class Redirecting(Site):
        def fetch(self, url: str, deadline_seconds: float | None = None) -> dict[str, Any]:
            redirected = url in ("https://d.example.com/", "https://d.example.com/old")
            return super().fetch("https://d.example.com/start" if redirected else url)

    site = Redirecting({"https://d.example.com/start": (TEXT, ["/start", "/notes.md", "/old"])})
    report = crawler(site).crawl(["https://d.example.com/"])
    assert [p.url for p in report.pages] == ["https://d.example.com/start"] and report.skipped == 1
    assert "https://d.example.com/notes.md" not in site.fetched
