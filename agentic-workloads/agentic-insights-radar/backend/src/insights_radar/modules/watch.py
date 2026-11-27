# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The source watcher: which items on a module's sources are new, found without a model.

A Scout used to fetch and read every source's listing on every run, so its cost grew with the number of sources
whether or not anything had changed (24 sources took about 1.4 million weighted tokens a run). The watcher fetches
each enabled source on a schedule, lists the items on it (feed entries, a JSON API's records, a page's links) and
keeps a checkpoint of what it has seen. Items it has not seen before wait in the source's inbox; the Scout then
works only on sources with something waiting, and only on those items.

* **Checkpoint.** Per source: the fingerprints of the items seen (the newest ``SEEN_MAX``), when it was checked,
  and the outcome (ok, an error, or a page with no items), so an unreadable source is visible rather than silent.
* **First check.** Everything on the page is the baseline: only items dated within ``FIRST_WINDOW`` of the check
  go to the inbox, so a new source starts at today instead of its whole archive.
* **Inbox.** At most ``PENDING_MAX`` items per source; the oldest are dropped first and counted. The Scout's
  runtime acknowledges the items of a pass it finished, so a run cut short leaves them for the next one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import urllib.robotparser
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso, parse_iso
from insights_radar.core.errors import NotFoundError
from insights_radar.core.fetch import _STRAY_LT, FEED_PARSE_CHARS, ROBOTS_AGENT, _FeedExtractor
from insights_radar.graph.repository import _from_ddb, _to_ddb
from insights_radar.modules.model import Source

log = logging.getLogger(__name__)

SEEN_MAX = 3000
PENDING_MAX = 100
MAX_ITEMS = 400  # items read from one page: a site map of thousands is not a listing
FIRST_WINDOW = timedelta(hours=24)
# An unseen item dated longer ago than this is an archive page coming into view, not news: seen, not queued.
STALE = timedelta(days=14)
TITLE_MAX = 300
MIN_ANCHOR_TEXT = 8
# A listing that shows its notices as text rows without links (Taiwan's law database, Thai DFT): each line this
# long is an item too, fingerprinted with its digit runs masked so a view counter ("viewed 845 times") or a clock
# on the page is not news on every check.
MIN_LINE = 30
_DIGITS = re.compile(r"\d+")
_URLS = re.compile(r"https?://\S+")
CHECK_DEADLINE_SECONDS = 20.0
_TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_[a-z]+|_ga|ref|source)$", re.IGNORECASE)
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "#")
_URL_KEYS = ("html_url", "url", "link", "href", "permalink", "web_url")
_ID_KEYS = ("document_number", "id", "guid", "uid", "slug")
_TITLE_KEYS = ("title", "name", "headline", "subject")
_DATE_KEYS = ("publication_date", "published_at", "published", "pubdate", "makingdate", "date", "updated", "created")


@dataclass(frozen=True, slots=True)
class Item:
    """One thing listed on a source: its fingerprint, address (when it has one), title and date as stated."""

    fp: str
    url: str
    title: str
    date: str = ""

    def to_pending(self, found_at: str) -> dict[str, str]:
        return {"fp": self.fp, "url": self.url, "title": self.title, "date": self.date, "found_at": found_at}


def normalise(url: str) -> str:
    """An address as the checkpoint compares it: lower-case scheme and host, no fragment, no tracking parameters
    (``utm_*``, ``fbclid``...), no trailing slash, the remaining query sorted."""
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k))
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def parse_date(value: Any) -> datetime | None:
    """A date as feeds and APIs state it: RFC 3339 / ISO 8601 (a date alone too), or RFC 2822 (RSS ``pubDate``).
    A date without a zone is UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return parse_iso(text)
    except ValueError:
        pass
    try:  # ISO without a zone, a date alone included ("2026-10-26", "2026-10-26T00:00:00")
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (ValueError, TypeError, IndexError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _clean(text: str) -> str:
    return " ".join(unescape(re.sub(r"<[^<>]*>", " ", text or "")).split())[:TITLE_MAX]


class _Anchors(HTMLParser):
    """Every ``<a href>`` with its text, outside scripts and styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "a":
            self._href, self._text = dict(attrs).get("href") or None, []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join("".join(self._text).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None and not self._skip:
            self._text.append(data)


def _feed_items(raw: str, base: str) -> list[Item]:
    parser = _FeedExtractor()
    parser.feed(_STRAY_LT.sub("&lt;", raw[:FEED_PARSE_CHARS]))
    parser.close()
    items = []
    for entry in parser.items:
        ids = [v.strip() for v in (entry.get("guid", ""), entry.get("id", "")) if v.strip()]
        link = entry.get("link", "").strip() or next((i for i in ids if i.startswith("https://")), "")
        title = _clean(entry.get("title", ""))
        key = normalise(urljoin(base, link)) if link else (f"id:{ids[0]}" if ids else f"title:{title}")
        if not title and not link:
            continue
        items.append(Item(fingerprint(key), urljoin(base, link) if link else "", title, entry.get("date", "").strip()))
    return items


def _pick(data: dict[str, Any], exact: tuple[str, ...], suffixes: tuple[str, ...]) -> str:
    """The first non-empty text value among ``exact`` keys, then keys ending in one of ``suffixes``, matched
    without regard to case (gov.cn sends ``TITLE`` and ``URL``); an English variant (``en_title``) first."""
    lowered = {str(k).lower(): v for k, v in data.items()}

    def text(v: Any) -> str:
        return str(v).strip() if isinstance(v, str | int) and not isinstance(v, bool) and str(v).strip() else ""

    for key in exact:
        if text(lowered.get(key)):
            return text(lowered[key])
    keys = sorted(
        (k for k in lowered if k.endswith(suffixes) and text(lowered[k])), key=lambda k: (not k.startswith("en"), k)
    )
    return text(lowered[keys[0]]) if keys else ""


def _json_items(data: Any, base: str, depth: int = 0) -> Iterable[Item]:
    if depth > 4:
        return
    if isinstance(data, list):
        for value in data[:MAX_ITEMS]:
            yield from _json_items(value, base, depth + 1)
        return
    if not isinstance(data, dict):
        return
    url = _pick(data, _URL_KEYS, ("_url", "_link", "_href"))
    ident = _pick(data, _ID_KEYS, ("_id",))
    title = _clean(_pick(data, _TITLE_KEYS, ("_title", "_name")))
    if title and (url or ident):
        date = _pick(data, _DATE_KEYS, ("date", "time", "_at"))
        key = normalise(urljoin(base, url)) if url else f"id:{ident}"
        yield Item(fingerprint(key), urljoin(base, url) if url else "", title, date)
        return
    for value in data.values():
        if isinstance(value, dict | list):
            yield from _json_items(value, base, depth + 1)


def _line_items(text: str, base: str, known: set[str]) -> list[Item]:
    items: dict[str, Item] = {}
    for line in text.splitlines():
        clean = " ".join(line.split())
        if len(clean) < MIN_LINE or clean in known:
            continue
        key = "line:" + _DIGITS.sub("#", clean.lower())
        # No address: a row has no link of its own, and the page's address can hold the source's credential.
        items.setdefault(key, Item(fingerprint(key), "", clean[:TITLE_MAX]))
    return list(items.values())


def _html_items(raw: str, base: str, page_text: str = "") -> list[Item]:
    parser = _Anchors()
    parser.feed(raw)
    parser.close()
    origin = urlsplit(base).netloc.lower()
    items: dict[str, Item] = {}
    for href, text in parser.anchors:
        if href.strip().lower().startswith(_SKIP_SCHEMES) or len(text) < MIN_ANCHOR_TEXT:
            continue
        absolute = urljoin(base, href.strip())
        parts = urlsplit(absolute)
        # The page's own links: an item listed here lives on this site (an external link is a share button, a
        # partner logo or a social account); a document host of the same organisation counts too.
        if parts.scheme != "https" or not _same_site(parts.netloc.lower(), origin):
            continue
        key = normalise(absolute)
        items.setdefault(key, Item(fingerprint(key), absolute, _clean(text)))
    linked = list(items.values())
    lines = _line_items(page_text, base, {i.title for i in linked})
    return (linked + lines)[:MAX_ITEMS]


def _same_site(host: str, origin: str) -> bool:
    def site(h: str) -> str:
        labels = h.split(":", maxsplit=1)[0].split(".")
        # example.go.id / example.gov.au / example.co.jp: three labels name the site, else two.
        n = 3 if len(labels) >= 3 and len(labels[-2]) <= 3 and len(labels[-1]) == 2 else 2
        return ".".join(labels[-n:])

    return site(host) == site(origin)


def _distinct(items: list[Item]) -> list[Item]:
    """Items that share an address are told apart by their title: a listing whose every record links to the same
    page (Hong Kong TID's "What's New" links each to ``en/``, Japan Customs' feed two notices to one gazette page)
    would otherwise check as one item, and a later notice behind that address would never be new."""
    counts = Counter(i.fp for i in items)
    return [replace(i, fp=fingerprint(f"{i.fp}|{i.title}")) if counts[i.fp] > 1 else i for i in items]


def extract_items(page: dict[str, Any]) -> list[Item]:
    """The items a fetched page lists, from its raw body: a feed's entries, a JSON API's records (anything with
    a title and an address or id), else the page's same-site links with text."""
    raw = str(page.get("raw") or "")
    base = str(page.get("url") or "")
    ctype = str(page.get("content_type") or "")
    head = raw[:2000].lower()
    if "json" in ctype or raw.lstrip()[:1] in ("{", "["):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if data is not None:
            # An API's relative links are site paths (Hong Kong TID's "en/tradecircular/..."), not paths beside the
            # JSON file: they resolve against the site's root.
            parts = urlsplit(base)
            return _distinct(list(_json_items(data, f"{parts.scheme}://{parts.netloc}/"))[:MAX_ITEMS])
    if "<rss" in head or "<feed" in head or "<rdf:rdf" in head:
        return _distinct(_feed_items(raw, base)[:MAX_ITEMS])
    return _html_items(raw, base, str(page.get("text") or ""))


@dataclass(slots=True)
class WatchState:
    """The checkpoint of one source."""

    seen: list[str] = field(default_factory=list)
    pending: list[dict[str, str]] = field(default_factory=list)
    checked_at: str = ""
    status: str = ""  # "" never checked, "ok", "empty" (no items on the page), "error"
    error: str = ""
    items: int = 0
    dropped: int = 0
    version: int = 0
    # Which address the checkpoint was taken from (a hash): one taken from an earlier address is a new baseline.
    address: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "status": self.status,
            "error": self.error,
            "items": self.items,
            "pending": len(self.pending),
            "dropped": self.dropped,
        }


class WatchStore:
    """Checkpoints in the ops table: ``WATCH#<module>`` / ``SRC#<source id>``, one item per source, written under
    a version condition so the watcher and a Scout acknowledging items never overwrite each other."""

    def __init__(self, table: Any) -> None:
        self._t = table

    @staticmethod
    def _key(slug: str, source_id: str) -> dict[str, str]:
        return {"pk": f"WATCH#{slug}", "sk": f"SRC#{source_id}"}

    @staticmethod
    def _state(raw: dict[str, Any] | None) -> WatchState:
        if not raw:
            return WatchState()
        i = _from_ddb(raw)
        return WatchState(
            seen=list(i.get("seen") or []),
            pending=[dict(p) for p in i.get("pending") or []],
            checked_at=str(i.get("checked_at", "")),
            status=str(i.get("status", "")),
            error=str(i.get("error", "")),
            items=int(i.get("items", 0)),
            dropped=int(i.get("dropped", 0)),
            version=int(i.get("version", 0)),
            address=str(i.get("address", "")),
        )

    def get(self, slug: str, source_id: str) -> WatchState:
        return self._state(self._t.get_item(Key=self._key(slug, source_id), ConsistentRead=True).get("Item"))

    def many(self, slug: str, source_ids: list[str]) -> dict[str, WatchState]:
        """The checkpoints of several sources (one query: the module's partition holds only these)."""
        wanted = set(source_ids)
        out: dict[str, WatchState] = {}
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :p",
            "ExpressionAttributeValues": {":p": f"WATCH#{slug}"},
        }
        while True:
            res = self._t.query(**kwargs)
            for raw in res.get("Items", []):
                sid = str(raw.get("sk", ""))[4:]
                if sid in wanted:
                    out[sid] = self._state(raw)
            if not res.get("LastEvaluatedKey"):
                return out
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def put(self, slug: str, source_id: str, state: WatchState) -> bool:
        """Write ``state`` if nobody wrote since it was read (its ``version``); False when someone did."""
        item = {
            **self._key(slug, source_id),
            "seen": state.seen[-SEEN_MAX:],
            "pending": state.pending[-PENDING_MAX:],
            "checked_at": state.checked_at,
            "status": state.status,
            "error": state.error[:300],
            "items": state.items,
            "dropped": state.dropped,
            "address": state.address,
            "version": state.version + 1,
        }
        # A checkpoint read at version v > 0 is written only over that version: one deleted meanwhile (the source
        # deleted, its address changed, Start over) must not come back with the old page's items.
        condition: dict[str, Any] = (
            {"ConditionExpression": "attribute_not_exists(pk)"}
            if state.version == 0
            else {"ConditionExpression": "version = :v", "ExpressionAttributeValues": {":v": state.version}}
        )
        try:
            self._t.put_item(Item=_to_ddb(item), **condition)
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        state.version += 1
        return True

    def ack(self, slug: str, source_id: str, fps: Iterable[str]) -> int:
        """Remove the given items from a source's inbox: the Scout finished them. Retries a lost race."""
        done = set(fps)
        for _ in range(5):
            state = self.get(slug, source_id)
            keep = [p for p in state.pending if p.get("fp") not in done]
            removed = len(state.pending) - len(keep)
            if not removed:
                return 0
            state.pending = keep
            if self.put(slug, source_id, state):
                return removed
        return 0

    def delete(self, slug: str, source_id: str) -> None:
        self._t.delete_item(Key=self._key(slug, source_id))


@dataclass(frozen=True, slots=True)
class CheckResult:
    source_id: str
    status: str
    new: int
    items: int
    error: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "new": self.new,
            "items": self.items,
            "error": self.error,
        }


class SourceWatcher:
    """Checks a module's enabled sources and fills their inboxes."""

    def __init__(
        self,
        store: WatchStore,
        fetcher_factory: Callable[[], Any],
        clock: Clock | None = None,
        workers: int = 16,
        current: Callable[[str, str], Source] | None = None,
    ) -> None:
        self._store = store
        self._fetcher_factory = fetcher_factory
        self._clock = clock or SystemClock()
        self._workers = workers
        # The source as stored now (``ModuleRepository.get_source``), re-read just before a write: one whose
        # address changed, or that was turned off or deleted, during the fetch must not get the old page's items.
        self._current = current
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._robots_lock = threading.Lock()

    def _allowed(self, fetcher: Any, url: str) -> bool:
        """Whether the site's ``robots.txt`` lets this deployment read ``url``, read once per host for the life of
        the watcher (one sweep). No ``robots.txt``, or one that cannot be read, allows everything, as for the
        documentation crawler."""
        host = urlsplit(url).netloc.lower()
        with self._robots_lock:
            parser = self._robots.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                body = fetcher.fetch(f"https://{host}/robots.txt", CHECK_DEADLINE_SECONDS)
                parser.parse(str(body.get("text") or "").splitlines())
            except Exception:  # no robots.txt (or unreachable): nothing is disallowed
                parser.parse([])
            with self._robots_lock:
                self._robots[host] = parser
        return parser.can_fetch(ROBOTS_AGENT, url)

    def check_all(self, slug: str, sources: list[Source]) -> list[CheckResult]:
        enabled = [s for s in sources if s.enabled]
        if not enabled:
            return []
        with ThreadPoolExecutor(max_workers=min(self._workers, len(enabled))) as pool:
            return list(pool.map(lambda s: self.check(slug, s), enabled))

    def check(self, slug: str, source: Source) -> CheckResult:
        """One source's check. A checkpoint read or write the table refuses (throttled) is this source's error,
        checked again next time, never the whole sweep's (code review r15)."""
        try:
            return self._check(slug, source)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "error")
            return CheckResult(source.id, "error", 0, 0, f"the checkpoint could not be read or written ({code})")

    def _check(self, slug: str, source: Source) -> CheckResult:
        # What was there before the fetch: a checkpoint that is gone after it was deleted meanwhile (the address
        # changed, Start over, the source deleted), and the next check takes the clean baseline instead.
        before = self._store.get(slug, source.id).version
        fetcher = self._fetcher_factory()
        try:
            try:
                if not self._allowed(fetcher, source.address):
                    raise PermissionError(f"robots.txt does not allow {ROBOTS_AGENT} to read this address")
                page = fetcher.fetch(source.address, CHECK_DEADLINE_SECONDS, keep_raw=True)
                items, error = extract_items(page), ""
            except Exception as err:  # any fetch failure is the source's status, never the whole sweep's
                # Without addresses: a fetch error quotes the stored address, which can hold a credential, and
                # the status reaches the Scout's context (source_list) as well as the Sources table.
                items, error = [], _URLS.sub("<source address>", f"{type(err).__name__}: {err}")[:300]
        finally:
            close = getattr(fetcher, "close", None)
            if callable(close):
                close()
        for _ in range(3):
            # Before every attempt: a disable or address change that lands during the retries counts too.
            changed = self._changed(slug, source)
            if changed:
                return CheckResult(source.id, "error", 0, len(items), changed)
            state = self._store.get(slug, source.id)
            if before and not state.version:
                return CheckResult(source.id, "error", 0, len(items), "the source changed during the check")
            state = self._for_address(state, source.address)
            result = self._apply(source.id, state, items, error)
            if self._store.put(slug, source.id, state):
                return result
        return CheckResult(source.id, "error", 0, len(items), "checkpoint was busy; checked again next time")

    def _changed(self, slug: str, source: Source) -> str:
        """Why the checked page must not be stored, or "". A source that cannot be re-read (throttled) is this
        source's error for this sweep, not the whole sweep's (code review r14)."""
        if self._current is None:
            return ""
        try:
            now = self._current(slug, source.id)
        except NotFoundError:
            return "the source changed during the check"
        except ClientError as err:
            return f"the source could not be read again ({err.response.get('Error', {}).get('Code', 'error')})"
        if not now.enabled or normalise(now.address) != normalise(source.address):
            return "the source changed during the check"
        return ""

    @staticmethod
    def _for_address(state: WatchState, address: str) -> WatchState:
        """The checkpoint, emptied (version kept, so the write still lands) when it was taken from another address."""
        digest = fingerprint(normalise(address))
        if state.address and state.address != digest:
            state = WatchState(version=state.version)
        state.address = digest
        return state

    def _apply(self, source_id: str, state: WatchState, items: list[Item], error: str) -> CheckResult:
        now = self._clock.now()
        state.checked_at = iso(now)
        if error:
            state.status, state.error = "error", error
            return CheckResult(source_id, "error", 0, 0, error)
        state.items = len(items)
        if not items:
            # A page that lists nothing (a JavaScript shell, a changed layout) keeps its checkpoint: when the items
            # come back, the ones seen before are not new.
            state.status, state.error = "empty", "the page listed no items"
            return CheckResult(source_id, "empty", 0, 0, state.error)
        first = not state.seen
        seen = set(state.seen)
        new: list[Item] = []
        for item in items:
            if item.fp in seen:
                continue
            dated = parse_date(item.date)
            if first:
                fresh = dated is not None and now - dated <= FIRST_WINDOW
            else:
                fresh = dated is None or now - dated <= STALE
            if fresh:
                new.append(item)
        found_at = iso(now)
        pending = state.pending + [
            i.to_pending(found_at) for i in new if i.fp not in {p.get("fp") for p in state.pending}
        ]
        if len(pending) > PENDING_MAX:
            state.dropped += len(pending) - PENDING_MAX
            pending = pending[-PENDING_MAX:]
        state.pending = pending
        state.seen = [s for s in state.seen if s not in {i.fp for i in items}] + [i.fp for i in items]
        state.status, state.error = "ok", ""
        return CheckResult(source_id, "ok", len(new), len(items))
