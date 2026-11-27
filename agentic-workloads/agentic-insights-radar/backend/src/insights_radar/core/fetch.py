# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Fetch public web pages without letting them reach internal networks.

Defences, in order:

1. ``UrlGuard`` on the URL and on every redirect target (https only, no credentials, no
   loopback/private/link-local/metadata hosts, no encoded-IP tricks).
2. ``SafeResolver`` inside the connection: the host is resolved once, every address is
   checked, and the socket connects to a vetted address. Resolution and connection use the same
   answer, so DNS rebinding between "check" and "connect" is not possible. TLS still verifies
   the certificate against the original hostname.
3. Bounded work: 5 redirects, 20 s, 2 MiB, text-like content types only, identity encoding.

The result is plain text (scripts and styles dropped) plus the page's links. Used by the agents'
``http_fetch`` tool and by the product documentation crawler.
"""

from __future__ import annotations

import codecs
import re
import ssl
import time
import zlib
from collections.abc import Callable
from html import unescape
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpcore
from httpcore._backends.sync import SyncBackend

from insights_radar.core.urls import SafeResolver, UrlGuard

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 60_000
MAX_REDIRECTS = 5
# A whole-fetch wall-clock deadline. The read timeout applies per chunk, so a server that sends one
# byte every few seconds kept a fetch alive indefinitely; store_crawl then never ran and
# due_for_crawl retried the stuck crawl every hour.
FETCH_DEADLINE_SECONDS = 45.0
TIMEOUT = {"connect": 5.0, "read": 20.0, "write": 10.0, "pool": 5.0}
_REFRESH = re.compile(r"url\s*=\s*(\S+)", re.IGNORECASE)
TEXT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/xhtml+xml",
    "application/ld+json",
)
# The usual crawler form, ``Mozilla/5.0 (compatible; <bot>/<version>; +<info url>)``: it names this bot as plainly
# as before, but several government sites (Japan's METI and MIC radio-use pages, SGS) refused the bare product
# token with a 403 and accept this form, as they accept Googlebot's.
ROBOTS_AGENT = "AgenticInsightsRadar"
USER_AGENT = (
    f"Mozilla/5.0 (compatible; {ROBOTS_AGENT}/1.0; +https://github.com/aws-samples/sample-apj-sup-sa/tree/main/agentic-workloads/agentic-insights-radar)"
)
# A decoded body is still capped at MAX_BYTES: a small gzip body can expand a thousandfold.
_ENCODINGS = {"gzip": 16 + zlib.MAX_WBITS, "x-gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS}


class FetchError(Exception):
    pass


class PinnedBackend(SyncBackend):
    """Connects only to addresses ``SafeResolver`` approved for this host."""

    def __init__(self, resolver: SafeResolver | None = None) -> None:
        self._resolver = resolver or SafeResolver()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = self._resolver.check(host, port)
        except ValueError as err:
            raise httpcore.ConnectError(str(err)) from err
        last: Exception | None = None
        for addr in addresses:
            try:
                return super().connect_tcp(addr, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as err:
                last = err
        raise httpcore.ConnectError(f"could not connect to {host}") from last


class _TextExtractor(HTMLParser):
    # Page chrome (navigation, headers, footers) is dropped too: it repeats on every page and drowns retrieval.
    SKIP = frozenset(
        {"script", "style", "noscript", "template", "svg", "iframe", "head", "nav", "header", "footer", "aside"}
    )
    BLOCK = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.refresh: str | None = None
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta" and (dict(attrs).get("http-equiv") or "").lower() == "refresh":
            match = _REFRESH.search(dict(attrs).get("content") or "")
            if match:
                self.refresh = match.group(1).strip("'\" ")
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), None)
            if href and len(self.links) < 2000:
                self.links.append(href)
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", joined)).strip()


def html_to_text(html: str) -> tuple[str, str]:
    title, text, _, _ = parse_html(html)
    return title, text


def parse_html(html: str) -> tuple[str, str, list[str], str | None]:
    """Title, readable text, raw ``href`` values and the meta-refresh target of a page."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.title.strip(), parser.text(), parser.links, parser.refresh


class _FeedExtractor(HTMLParser):
    """RSS 2.0 and Atom items as short text records: title, link, date and a summary.

    Full-content feeds (``content:encoded``) run to hundreds of kilobytes, so as plain text the
    newest items crowded out the rest of the feed. No XML parser is used: ``HTMLParser`` does not
    resolve external entities or expand entity definitions.
    """

    ITEM = frozenset({"item", "entry"})
    FIELDS: ClassVar[dict[str, str]] = {
        "title": "title",
        "link": "link",
        "guid": "guid",
        "id": "id",
        "pubdate": "date",
        "published": "date",
        "updated": "date",
        "dc:date": "date",
        "description": "summary",
        "summary": "summary",
        "content": "content",
    }
    SUMMARY_CHARS = 400

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.items: list[dict[str, str]] = []
        self._item: dict[str, str] | None = None
        self._field: str | None = None
        self._field_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.ITEM:
            self._item, self._field = {}, None
            return
        field = self.FIELDS.get(tag)
        if self._item is None:
            self._field = "feed_title" if tag == "title" and not self.title else None
            return
        a = dict(attrs)
        if tag == "link" and "href" in a:  # Atom: <link href="..."/>; the page, not self, enclosure or replies
            href = a.get("href")
            if href and a.get("rel") in (None, "alternate") and not self._item.get("link"):
                self._item["link"] = href
            return
        if tag == "guid" and (a.get("ispermalink") or "").lower() == "false":
            return  # an opaque identifier, never a URL
        if field and field not in self._item:
            self._field, self._field_tag = field, tag
            self._item[field] = ""

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._field and self._item is not None and self._field in self._item:
            # Inside an open field a self-closed tag is markup (<br/>, <img/>, or a stray <item/> or <pubDate/>):
            # it separates words and opens nothing.
            self._item[self._field] += " "
            return
        if tag in self.ITEM:
            return  # an empty <item/> or <entry/> holds nothing
        self.handle_starttag(tag, attrs)  # a self-closed field element, such as Atom <link href="..."/>
        self._field = None

    def handle_endtag(self, tag: str) -> None:
        if tag in self.ITEM and self._item is not None:
            self.items.append(self._item)
            self._item = None
        # Only the end of the field's own element closes it: Atom type="xhtml" content nests <div>, <p>, ...
        if self._item is None or tag == self._field_tag:
            self._field = None
        elif self._field and self._field in self._item:
            self._item[self._field] += " "  # </p><p> separates paragraphs

    def handle_data(self, data: str) -> None:
        # <title> is raw text to HTMLParser, so a CDATA section inside it arrives with its markers.
        data = data.replace("<![CDATA[", "").replace("]]>", "")
        if self._field == "feed_title":
            self.title += data
        elif self._field and self._item is not None:
            self._item[self._field] += data

    def unknown_decl(self, data: str) -> None:  # <![CDATA[...]]>
        if data.startswith("CDATA["):
            self.handle_data(data[6:])

    def text(self) -> str:
        # Descriptions are usually escaped HTML: tags come out as text and need one more unescape.
        out = []
        for i, item in enumerate(self.items, 1):
            # The page link: <link>, else a guid or Atom id that is itself an https URL.
            ids = [v.strip() for v in (item.get("guid", ""), item.get("id", "")) if v.strip().startswith("https://")]
            link = item.get("link", "").strip() or (ids[0] if ids else "")
            # Only SUMMARY_CHARS are kept, so cut long bodies first; and "<[^<>]*>" fails fast on a "<" with
            # no ">" (a page full of "<" made "<[^>]*>" rescan to the end each time: quadratic).
            raw = (item.get("summary", "").strip() or item.get("content", ""))[: self.SUMMARY_CHARS * 20]
            summary = re.sub(r"\s+", " ", unescape(re.sub(r"<[^<>]*>", " ", raw))).strip()
            if len(summary) > self.SUMMARY_CHARS:
                summary = summary[: self.SUMMARY_CHARS].rsplit(" ", 1)[0] + " ..."
            lines = [f"{i}. {' '.join(item.get('title', '').split())}"]
            lines += [f"   {k}: {v.strip()}" for k, v in (("link", link), ("date", item.get("date", ""))) if v.strip()]
            if summary:
                lines.append(f"   {summary}")
            out.append("\n".join(lines))
        return "\n\n".join(out)


# HTMLParser rescans to the end of its buffer for every "<" it cannot close yet, so its time grows with the
# square of the text: a feed is parsed only up to FEED_PARSE_CHARS (its first, newest items), and a "<" with no
# ">" before the next "<" (within 256 characters) is escaped first, so it reads as text.
FEED_PARSE_CHARS = 256 * 1024
_STRAY_LT = re.compile(r"<(?!!\[CDATA\[)(?![^<>]{0,256}>)")  # CDATA sections stay sections


def parse_feed(xml: str) -> tuple[str, str] | None:
    """Title and item list of an RSS or Atom document, or None when it is not a feed."""
    head = xml[:2000].lower()
    if "<rss" not in head and "<feed" not in head and "<rdf:rdf" not in head:
        return None
    parser = _FeedExtractor()
    parser.feed(_STRAY_LT.sub("&lt;", xml[:FEED_PARSE_CHARS]))
    parser.close()
    if parser._item is not None and parser._item.get("title", "").strip():
        parser.items.append(parser._item)  # cut off at FEED_PARSE_CHARS: keep what the last item has
    if not parser.items:
        return None
    return " ".join(parser.title.split()), parser.text()


def tls_context() -> ssl.SSLContext:
    """The default verifying context without Python 3.13's ``VERIFY_X509_STRICT``.

    Strict mode rejects a CA certificate without a Subject Key Identifier, which browsers and Python 3.12
    accept: TWCA's cross-signed Global Root lacks one, so every Taiwanese government site (Executive Yuan
    gazette, Customs Administration, Ministry of Justice laws) failed with "Missing Subject Key Identifier"
    on the 3.14 runtime. The chain is still verified against the trusted roots and the host name checked."""
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class SafeFetcher:
    def __init__(
        self,
        backend: httpcore.NetworkBackend | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._now = now
        self._pool = httpcore.ConnectionPool(
            ssl_context=tls_context(),
            network_backend=backend or PinnedBackend(),
            max_connections=4,
            retries=0,
        )

    def fetch(self, url: str, deadline_seconds: float | None = None, keep_raw: bool = False) -> dict[str, Any]:
        """The page as text and links; with ``keep_raw`` also the decoded body (``raw``), for the source
        watcher, which reads a feed's or a listing's own structure rather than its text rendering."""
        current = url.strip()
        budget = FETCH_DEADLINE_SECONDS if deadline_seconds is None else max(1.0, deadline_seconds)
        deadline = self._now() + budget
        for _ in range(MAX_REDIRECTS + 1):
            current = as_uri(current)
            reason = UrlGuard.reason(current)
            if reason:
                raise FetchError(f"refused {current[:200]}: {reason}")
            if self._now() > deadline:
                raise FetchError(f"{current[:200]} took longer than {budget:.0f}s")
            with self._pool.stream(
                "GET",
                current,
                headers={"user-agent": USER_AGENT, "accept-encoding": "identity", "accept": "*/*"},
                extensions={"timeout": TIMEOUT},
            ) as res:
                headers = {k.decode("latin-1").lower(): _header_text(v) for k, v in res.headers}
                if res.status in (301, 302, 303, 307, 308) and headers.get("location"):
                    current = urljoin(current, headers["location"])
                    continue
                ctype = headers.get("content-type", "").split(";")[0].strip().lower()
                if res.status >= 400:
                    raise FetchError(f"{current[:200]} returned HTTP {res.status}")
                if ctype and not ctype.startswith(TEXT_TYPES):
                    raise FetchError(f"{current[:200]} is {ctype}, not text; use the browser for rich pages")
                body = bytearray()
                for chunk in res.iter_stream():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                    if self._now() > deadline:
                        raise FetchError(f"{current[:200]} took longer than {budget:.0f}s")
                truncated = len(body) > MAX_BYTES
                encoding = headers.get("content-encoding", "").strip().lower()
                if encoding in _ENCODINGS:
                    # Asked for identity, sent compressed anyway (Accenture): decode it, capped like any body.
                    body, cut = _decompress(bytes(body), _ENCODINGS[encoding])
                    truncated = truncated or cut
                elif encoding not in ("", "identity"):
                    raise FetchError(f"{current[:200]} is {encoding}-compressed, which this fetcher cannot read")
                head = bytes(body[:SNIFF_BYTES])
                # A byte-order mark wins over the header, as in a browser (WHATWG "decode"), then the
                # header, then what the document declares.
                charset = _bom_charset(head) or _charset(headers.get("content-type", "")) or _declared_charset(head)
                raw = bytes(body[:MAX_BYTES]).decode(charset or "utf-8", errors="replace")
                links: list[str] = []
                refresh: str | None = None
                feed = parse_feed(raw) if "xml" in ctype or not ctype or ctype == "text/plain" else None
                if feed:
                    title, text = feed
                elif "html" in ctype or (not ctype and "<html" in raw[:2000].lower()):
                    title, text, links, refresh = parse_html(raw)
                else:
                    title, text = "", raw
                if refresh and len(text) < 200:  # a redirect page: follow it like a 3xx
                    current = urljoin(current, refresh)
                    continue
                if len(text) > MAX_TEXT:
                    text, truncated = text[:MAX_TEXT], True
                page: dict[str, Any] = {
                    "url": current,
                    "status": res.status,
                    "content_type": ctype,
                    "title": title[:300],
                    "text": text,
                    "truncated": truncated,
                    "links": [urljoin(current, h) for h in links],
                }
                if keep_raw:
                    page["raw"] = raw
                return page
        raise FetchError(f"too many redirects from {url[:200]}")

    def close(self) -> None:
        self._pool.close()


def _decompress(data: bytes, wbits: int) -> tuple[bytearray, bool]:
    """``data`` inflated to at most MAX_BYTES, and whether it was cut there."""
    try:
        inflater = zlib.decompressobj(wbits)
        out = inflater.decompress(data, MAX_BYTES + 1)
    except zlib.error as err:
        raise FetchError(f"the compressed body could not be read: {err}") from err
    return bytearray(out[:MAX_BYTES]), len(out) > MAX_BYTES


def as_uri(url: str) -> str:
    """``url`` with non-ASCII characters percent-encoded (and an IDN host in punycode), as a browser sends it.

    Thai, Korean, Japanese and Indonesian government sites link to paths in their own script (Thai DFT's
    regulations page is ``/th-th/`` plus the Thai word for "law"); the HTTP client refuses such a string
    outright, so the Scout could not follow those links. Existing ``%XX``
    escapes are kept, and an address that is already ASCII is returned unchanged."""
    if url.isascii():
        return url
    safe = "/%:@!$&'()*+,;=~-._?"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        netloc = parts.netloc
        if not netloc.isascii():
            # Rebuilt, not str.replace'd: hostname is lower-cased, so "https://\u00c9X.example/" kept its
            # host as typed and reached the HTTP client non-ASCII (code review r13).
            userinfo, at, _ = netloc.rpartition("@")
            port = f":{parts.port}" if parts.port is not None else ""
            netloc = f"{quote(userinfo, safe=safe)}{at}{host.encode('idna').decode('ascii')}{port}"
    except (ValueError, UnicodeError) as err:
        raise FetchError(f"{url[:200]} is not a valid address: {err}") from err
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            quote(parts.path, safe=safe),
            quote(parts.query, safe=safe),
            quote(parts.fragment, safe=safe),
        )
    )


# Any text encoding Python knows, except the few whose decoders are slow or are not really charsets: punycode
# and idna decode in quadratic time (a page naming one could pin a run for minutes), and the *_escape codecs
# interpret backslash sequences. Non-text codecs (zlib, base64, ...) are refused too. Anything else falls back to
# UTF-8. A test decodes a large body with every remaining codec.
_REFUSED_CHARSETS = frozenset({"punycode", "idna", "unicode-escape", "raw-unicode-escape", "undefined"})


def _charset(content_type: str) -> str | None:
    """The charset the Content-Type header names, or None when it names none (the body may declare one)."""
    match = re.search(r"""charset=["']?([\w.-]+)""", content_type, re.IGNORECASE)
    return _codec(match.group(1)) if match else None


def _codec(name: str) -> str:
    try:
        info = codecs.lookup(name)
    except LookupError:
        return "utf-8"
    text = getattr(info, "_is_text_encoding", True)
    return info.name if text and info.name.replace("_", "-") not in _REFUSED_CHARSETS else "utf-8"


# Where a document declares its own encoding when the header does not: a byte-order mark, the XML declaration
# (Japan MIC's RSS is ``<?xml ... encoding="Shift_JIS"?>`` served as plain ``text/xml``, so it decoded as
# garbled UTF-8), or an HTML ``<meta charset>`` / ``http-equiv`` tag. Only the first bytes are read, as a
# browser does, and the name passes the same codec checks as a header's.
SNIFF_BYTES = 2048
# UTF-32's marks first: FF FE 00 00 also starts with UTF-16 LE's FF FE.
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),  # -sig drops the mark, so text does not start with U+FEFF
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_XML_DECL = re.compile(rb"""^\s*<\?xml[^>]*?\bencoding\s*=\s*["']([\w.-]+)["']""", re.IGNORECASE)
_META_CHARSET = re.compile(rb"""<meta[^>]+?charset\s*=\s*["']?([\w.-]+)""", re.IGNORECASE)


def _bom_charset(head: bytes) -> str | None:
    for bom, name in _BOMS:
        if head.startswith(bom):
            return name
    return None


def _declared_charset(head: bytes) -> str | None:
    match = _XML_DECL.match(head) or _META_CHARSET.search(head)
    if not match:
        return None
    name = _codec(match.group(1).decode("ascii"))
    # A declaration the sniffer could read in ASCII bytes cannot be UTF-16 or UTF-32: a page mislabelled
    # <meta charset="utf-16"> is UTF-8, as the HTML spec says.
    return "utf-8" if name.startswith(("utf-16", "utf-32", "utf_16", "utf_32")) else name


def _header_text(raw: bytes) -> str:
    """A response header value: UTF-8 when it is valid UTF-8 (a raw Location such as /th-th/ plus Thai
    letters, which browsers read as UTF-8; latin-1 made it mojibake that as_uri then encoded twice),
    else latin-1."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")
