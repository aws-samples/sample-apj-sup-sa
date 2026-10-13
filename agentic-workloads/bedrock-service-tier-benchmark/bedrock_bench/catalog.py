"""Parse the Amazon Bedrock documentation's model catalog ("Models at a glance").

No Bedrock API reports which tiers, APIs or inference scopes a model supports;
that information is published in the documentation's model cards. The cards
mark support with icon images, which text converters drop, so this module
parses the raw HTML with the standard-library parser.

Two page shapes are handled:

* ``models-endpoint-availability.html`` — one row per model with a
  supported / not-supported icon for ``bedrock-runtime`` and ``bedrock-mantle``.
* ``model-card-*.html`` — Model Details (modalities, APIs, endpoints),
  Capabilities (streaming, implicit/explicit prompt caching), the cache table
  (minimum tokens, TTL), Programmatic Access (model, geo and global ids),
  Service Tiers and Regional Availability (in-Region / Geo / Global per region).

Only ``https://docs.aws.amazon.com/bedrock/`` URLs are fetched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

DOCS_BASE = "https://docs.aws.amazon.com/bedrock/latest/userguide/"
AVAILABILITY_URL = DOCS_BASE + "models-endpoint-availability.html"
_ALLOWED_HOST = "docs.aws.amazon.com"
_ALLOWED_PREFIX = "/bedrock/"
_MAX_BYTES = 4 * 1024 * 1024
_REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d{1,2}\b")
_PROFILE_RE = re.compile(r"\b(?:us|eu|apac|jp|au|ca|in|us-gov|global)\.[a-z0-9][\w.:-]*\w")

_YES = ("supported", "green circle", "checkmark")
_NO = ("not-supported", "not supported", "red circle", "x icon")


def _mark(alt: str) -> str:
    a = alt.lower()
    if any(k in a for k in _NO):
        return "N"
    if any(k in a for k in _YES):
        return "Y"
    return ""


def fetch(url: str, timeout: float = 30.0) -> str:
    """Fetch a Bedrock documentation page (HTTPS, docs.aws.amazon.com/bedrock/ only).

    Redirects are not followed, so a redirect cannot move the request off the
    allowed host; the response size is capped.
    """
    u = urlparse(url)
    if u.scheme != "https" or u.hostname != _ALLOWED_HOST or not u.path.startswith(_ALLOWED_PREFIX):
        raise ValueError(f"refusing to fetch non-Bedrock-docs URL: {url!r}")
    headers = {"User-Agent": "bedrock-bench-discovery"}
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ValueError(f"page too large: {url}")
                chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


Cell = list[tuple[str, str]]  # (mark, text) items inside one table cell


class _Tables(HTMLParser):
    """Collects tables keyed by the nearest preceding heading, plus page links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: dict[str, list[list[Cell]]] = {}
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._heading: str | None = None
        self._section = "(top)"
        self._row: list[Cell] | None = None
        self._cell: list[list[str]] | None = None
        self._in_title = False
        self._href: str | None = None
        self._link_text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4"):
            self._heading = ""
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = [["", ""]]
        elif tag == "img" and self._cell is not None:
            m = _mark(a.get("alt") or "")
            if m:
                self._cell.append([m, ""])
        elif tag in ("br", "p", "li") and self._cell is not None and self._cell[-1][1].strip():
            self._cell.append(["", ""])
        elif tag == "a" and a.get("href"):
            self._href, self._link_text = a["href"], ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4") and self._heading is not None:
            self._section = " ".join(self._heading.split())
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append([(m, " ".join(t.split())) for m, t in self._cell if m or t.strip()])
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables.setdefault(self._section, []).append(self._row)
            self._row = None
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._link_text.split())))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._heading is not None:
            self._heading += data
        if self._cell is not None:
            self._cell[-1][1] += data
        if self._href is not None:
            self._link_text += data


def _txt(cell: Cell) -> str:
    return " ".join(t for _, t in cell if t).strip()


def _flag(cell: Cell) -> bool:
    return next((m for m, _ in cell if m), "") == "Y"


@dataclass
class EndpointRow:
    section: str
    model_name: str
    runtime: bool
    mantle: bool
    card_url: str | None = None


def parse_endpoint_availability(html: str, base_url: str = AVAILABILITY_URL) -> list[EndpointRow]:
    """Rows of the endpoint-availability page, with links to each model card."""
    p = _Tables()
    p.feed(html)
    cards = {
        text: urljoin(base_url, href) for href, text in p.links if "model-card-" in href and text
    }
    out: list[EndpointRow] = []
    for section, rows in p.tables.items():
        for r in rows:
            if len(r) >= 3 and _txt(r[0]) and any(m for m, _ in r[1] + r[2]):
                name = _txt(r[0])
                out.append(EndpointRow(section, name, _flag(r[1]), _flag(r[2]), cards.get(name)))
    return out


@dataclass
class ModelCard:
    title: str
    url: str
    input_modalities: list[str] = field(default_factory=list)
    output_modalities: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=dict)
    explicit_cache: dict[str, str] | None = None
    access: list[dict[str, object]] = field(default_factory=list)
    tiers: dict[str, bool] = field(default_factory=dict)
    regions: dict[str, dict[str, bool]] = field(default_factory=dict)

    @property
    def implicit_cache(self) -> bool:
        return self.features.get("Implicit Prompt Caching", False)

    @property
    def cache_min_tokens(self) -> int | None:
        if not self.explicit_cache:
            return None
        digits = re.sub(r"[^\d]", "", self.explicit_cache.get("min_tokens", ""))
        return int(digits) if digits else None


def parse_model_card(html: str, url: str) -> ModelCard:
    p = _Tables()
    p.feed(html)
    t = p.tables
    card = ModelCard(title=p.title.split(" - Amazon Bedrock")[0].strip(), url=url)

    details = t.get("Model Details", [])
    if details:
        hdr = [_txt(c) for c in details[0]]
        cols: dict[str, list[str]] = {h: [] for h in hdr}
        for row in details[1:]:
            for h, cell in zip(hdr, row, strict=False):
                pending = ""
                for m, text in cell:
                    if m:
                        pending = m
                    if text and pending == "Y":
                        cols[h].append(text)
                    if text:
                        pending = ""
        card.input_modalities = cols.get("Input Modalities", [])
        card.output_modalities = cols.get("Output Modalities", [])
        card.apis = cols.get("APIs supported", [])
        card.endpoints = cols.get("Endpoints supported", [])

    for row in t.get("Capabilities and Features", []):
        texts = [_txt(c) for c in row]
        if len(texts) == 3 and texts[0] in ("Yes", "No"):
            card.explicit_cache = {
                "supported": texts[0],
                "min_tokens": texts[1],
                "ttl": texts[2],
            }
            continue
        for cell in row:
            pending = ""
            for m, text in cell:
                if m:
                    pending = m
                if text and pending:
                    card.features[text] = pending == "Y"
                    pending = ""

    access = t.get("Programmatic Access", [])
    if access:
        hdr = [_txt(c) for c in access[0]]
        for row in access[1:]:
            d: dict[str, object] = dict(zip(hdr, [_txt(c) for c in row], strict=False))
            d["geo_ids"] = sorted(set(_PROFILE_RE.findall(str(d.get("Geo inference ID", "")))))
            card.access.append(d)

    tiers = t.get("Service Tiers", [])
    if len(tiers) >= 2:
        hdr = [_txt(c) for c in tiers[0]]
        card.tiers = {h: _flag(c) for h, c in zip(hdr, tiers[1], strict=False)}

    # Regional availability may follow the tiers table or sit under its own heading;
    # a trailing CRIS source->destination table repeats region names, so OR-merge.
    for rows in t.values():
        header: list[str] | None = None
        for row in rows:
            texts = [_txt(c) for c in row]
            if texts and texts[0] == "Region":
                header = texts[1:]
                continue
            if header and texts and _REGION_RE.match(texts[0]):
                cur = card.regions.setdefault(texts[0].split()[0], {})
                for h, c in zip(header, row[1:], strict=False):
                    cur[h] = cur.get(h, False) or _flag(c)
    return card
