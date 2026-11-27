# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Crawl a product's public documentation into the knowledge base's S3 data source.

Bedrock's managed web-crawler data source is only available for OpenSearch Serverless vector
stores. This solution uses S3 Vectors (no idle cost), so it crawls the documentation itself and
stores each page as text under ``docs/<product>/web/`` with a metadata sidecar; the shared S3 data
source then indexes it like an uploaded document.

Scope and manners:

* only pages under a seed URL's host and directory (``https://docs.example.com/guide/`` covers
  ``/guide/**``, not the rest of the site);
* ``robots.txt`` is honoured for the crawler's user agent;
* bounded by page count, depth, total time and a fixed delay between requests;
* every request goes through ``SafeFetcher`` (no internal addresses, pinned connections).
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import time
import urllib.robotparser
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import unquote, urldefrag, urljoin, urlparse

from insights_radar.core.fetch import ROBOTS_AGENT, FetchError
from insights_radar.core.urls import UrlGuard

log = logging.getLogger(__name__)

MAX_PAGES = 200
MAX_DEPTH = 4
TIME_BUDGET_SECONDS = 600.0
DELAY_SECONDS = 0.5
MIN_TEXT_CHARS = 200
SKIP_SUFFIXES = (
    ".md",
    ".txt",
    ".pdf",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".css",
    ".js",
    ".ico",
    ".xml",
    ".json",
)


class Fetcher(Protocol):
    def fetch(self, url: str, deadline_seconds: float | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CrawledPage:
    url: str
    title: str
    text: str

    @property
    def key(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:24]


@dataclass
class CrawlReport:
    pages: list[CrawledPage] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {"pages": len(self.pages), "skipped": self.skipped, "errors": self.errors[:20], "stopped": self.stopped}


def _normalise(url: str) -> str:
    """Canonical https URL without fragment; "" for anything that is not a web link."""
    clean, _ = urldefrag(url.strip())
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    # A non-default port is a different service: keep it (":8443" used to be crawled on 443).
    default = 80 if parsed.scheme == "http" else 443
    host = (parsed.hostname or "").lower() + (f":{port}" if port not in (None, default) else "")
    path = parsed.path or "/"
    return f"https://{host}{path}" + (f"?{parsed.query}" if parsed.query else "")


def _scope(seed: str) -> tuple[str, str]:
    parsed = urlparse(_normalise(seed))
    path = parsed.path
    prefix = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
    return parsed.netloc, prefix


def _resolved_path(path: str) -> str:
    """The path a server will serve: percent-decoded, with dot segments resolved, so
    ``/guide/%2e%2e/admin`` is not taken for something under ``/guide/``."""
    resolved = posixpath.normpath(unquote(path or "/"))
    resolved = "/" + resolved.lstrip("/")  # normpath keeps a leading "//"
    return resolved + "/" if path.endswith("/") and not resolved.endswith("/") else resolved


class DocsCrawler:
    def __init__(
        self,
        fetcher: Fetcher,
        max_pages: int = MAX_PAGES,
        time_budget: float = TIME_BUDGET_SECONDS,
        delay: float = DELAY_SECONDS,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetcher = fetcher
        self._max_pages = max_pages
        self._budget = time_budget
        self._delay = delay
        self._now = now
        self._sleep = sleep
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _allowed_by_robots(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        parser = self._robots.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                body = self._fetcher.fetch(f"https://{host}/robots.txt")
                parser.parse(str(body.get("text", "")).splitlines())
            except Exception:  # no robots.txt (or unreachable): everything in scope is allowed
                parser.parse([])
            self._robots[host] = parser
        return parser.can_fetch(ROBOTS_AGENT, url)

    def crawl(self, seeds: list[str]) -> CrawlReport:
        report = CrawlReport()
        scopes = [_scope(s) for s in seeds if not UrlGuard.reason(s)]
        queue: deque[tuple[str, int]] = deque((_normalise(s), 0) for s in seeds if not UrlGuard.reason(s))
        seen: set[str] = {u for u, _ in queue}
        stored: set[str] = set()
        started = self._now()

        def in_scope(url: str) -> bool:
            p = urlparse(url)
            path = _resolved_path(p.path)
            return any(p.netloc == host and path.startswith(prefix) for host, prefix in scopes)

        while queue:
            if len(report.pages) >= self._max_pages:
                report.stopped = "page_limit"
                break
            if self._now() - started > self._budget:
                report.stopped = "time_limit"
                break
            url, depth = queue.popleft()
            if not self._allowed_by_robots(url):
                report.skipped += 1
                continue
            try:
                # The remaining crawl budget, so one slow host cannot hold the whole crawl open.
                page = self._fetcher.fetch(url, deadline_seconds=self._budget - (self._now() - started))
            except FetchError as err:
                report.errors.append(str(err)[:200])
                continue
            except Exception as err:  # network errors: record and move on
                report.errors.append(f"{url[:200]}: {type(err).__name__}")
                continue
            finally:
                self._sleep(self._delay)
            final = _normalise(str(page.get("url", url)))
            text = str(page.get("text", ""))
            if final in stored:  # reached again through a redirect
                report.skipped += 1
                continue
            stored.add(final)
            seen.add(final)
            if in_scope(final) and len(text) >= MIN_TEXT_CHARS:
                report.pages.append(CrawledPage(final, str(page.get("title", ""))[:300], text))
            else:
                report.skipped += 1
            if depth >= MAX_DEPTH:
                continue
            for link in page.get("links", []):
                nxt = _normalise(urljoin(final, str(link)))
                if not nxt or nxt in seen or nxt.lower().endswith(SKIP_SUFFIXES):
                    continue
                if in_scope(nxt) and not UrlGuard.reason(nxt):
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))
        log.info("crawl finished: %s", report.to_public())
        return report
