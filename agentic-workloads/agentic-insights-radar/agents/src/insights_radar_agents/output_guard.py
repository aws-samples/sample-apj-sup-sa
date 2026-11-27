# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Keep addresses that only ever appeared in untrusted content out of a module's published outputs.

A module that reads a hostile page does not have to *follow* the injected instructions to carry them
out: writing the attacker's host name into an insight is enough, because the insight is then indexed,
searched, and read by people and by other modules as a finding (red-team L2-001).

So every write people read passes through this hook, which defangs every address in the free-text
fields unless the run has a reason to cite it:

* the run retrieved it: fetched with ``http_fetch`` (the final address after redirects) or opened in
  the browser, recorded only once the call succeeded (P3-020). Every address on such a host is kept;
* a graph node the run read cites it: the exact ``evidence_url`` of evidence returned by ``kg_get``,
  ``kg_search``, ``kg_neighbors``, ``kg_recent``, ``draft_get_idea`` or ``radar_get`` (A3-002). Trends,
  Gaps, Content ideas and the curator fetch nothing, and citing the evidence they build on is their
  job. Only the exact stored address is kept, never a variant: a module that holds private data
  cannot append it to a cited address. Bare host names of such evidence are kept too;
* it names an entity the run read from the graph (``Booking.com``, ``Character.AI``) (P3-021).

Everything else is defanged: ``https://`` becomes ``hxxps://`` and the dots of the host become
``[.]``, so the address is no longer navigable, no longer a working ``<a href>``, and no longer
matches a search for the host. It stays readable, so an analyst can still see what was claimed.

What counts as an address:

* anything with a scheme and ``//`` (any scheme, not only http), plus the forms a browser reads as an
  absolute address for http and https: ``https:\\host``, ``http:/host``, ``http:host``;
  bracketed IPv6 hosts (P3-064); ``mailto:`` addresses (a link that carries a body);
* a bare host: ``label.label`` whose last label is a delegated top-level domain (the IANA list in
  ``tlds-alpha-by-domain.txt``) or a reserved one (``example``, ``invalid``, ``test``...), written
  with an ASCII full stop or one of the Unicode full stops a browser treats as one, plus IPv4
  literals and ``//host``. A name is not a host when its last label is written with capitals
  (``ASP.NET``, ``Node.JS``), is not a top-level domain (``os.path.join``), is a common file
  extension on a two-label name (``main.py``), or follows an ``@`` (the domain of an email address in
  prose, which is not a link) (P3-021).

HTML descriptions are put through the backend's sanitizer first, so the guard reads every link as a
browser will: character references decoded (``https&#58;//evil&#46;example``, A3-001), control
characters removed. Character references in other text are decoded too when they spell an address
character.

Fields: outputs, evidence and radar suggestions in their free text (SR2-03). Citation fields are kept
as written only at the exact place they belong (P3-017): a radar proposal's ``link`` and ``node_id``.
An entity's name and aliases are identifiers, so only links are defanged in them (A3-015).
``evidence_url`` is never rewritten; the runtime instead stamps the evidence with ``fetched``: whether
the run itself fetched that exact address (SR2-02, P3-020). Drafts are not scrubbed; a person reviews
one before anyone acts on it.

The counts are reported at the top of the run summary, links and bare names apart, and the summary
itself is defanged the same way (A3-017): the run history renders it as Markdown.
"""

from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from insights_radar.core.html import HtmlSanitizer
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

log = logging.getLogger(__name__)

OUTPUT_TOOLS = frozenset({"kg_save_output", "kg_update_output"})
SCRUBBED_FIELDS = ("title", "summary", "description", "attributes")
# Every write whose free text people read or other modules take as a finding, and its fields.
SCRUBBED: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(OUTPUT_TOOLS, SCRUBBED_FIELDS),
    "kg_add_evidence": SCRUBBED_FIELDS,
    "kg_upsert_entity": ("summary", "description", "attributes"),
    "radar_suggest": ("reason", "proposal"),
}
# Identifiers: only links are defanged in them, so "Booking.com" stays a name (A3-015).
LINKS_ONLY: dict[str, tuple[str, ...]] = {"kg_upsert_entity": ("name", "aliases")}
# Citations, kept as written, at the exact path where each belongs and nowhere else (P3-017).
CITATIONS: dict[str, frozenset[tuple[str, ...]]] = {
    "radar_suggest": frozenset({("proposal", "link"), ("proposal", "node_id")}),
}
HTML_FIELDS = frozenset({"description"})
# Backend reads whose results carry the evidence_url of stored evidence, and entity names.
GRAPH_READS = frozenset({"kg_get", "kg_search", "kg_neighbors", "kg_recent", "draft_get_idea", "radar_get"})
ENTITY_TYPES = frozenset({"organization", "person", "product", "topic", "segment"})
BROWSER_TOOL = "browser"
MAX_DEPTH = 6
MAX_WALK = 20

_DOT = "[.\u3002\uff0e\uff61]"  # the full stops a browser reads as a dot in a host name
_LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
# Any character a browser may fold into a host, not only ASCII: a host that starts with a soft hyphen,
# a zero-width space or a full-width letter is the same host to WHATWG URL parsing (SR3-03).
_HOST = r"(?P<host>\[[0-9A-Fa-f:.%]+\]|(?:[A-Za-z0-9._~%-]|[^\x00-\x7f\s])+)(?P<port>:\d+)?"
_REST = r"(?P<rest>[^\s\"'<>]*)"
# Special schemes are absolute after any run of slashes or backslashes, even none, as a browser
# parses them from another scheme's page: "https:\\evil.example", "http:/evil.example".
_SCHEME_URL = re.compile(
    r"(?<![A-Za-z0-9+.-])"
    r"(?:(?P<special>https?):(?P<sep>[/\\]*)|(?P<scheme>[A-Za-z][A-Za-z0-9+.-]{1,15}):(?P<sep2>//))"
    rf"{_HOST}{_REST}",
    re.IGNORECASE,
)
_MAILTO = re.compile(
    r"(?P<scheme>mailto):(?P<user>[^@\s\"'<>/]{1,200})@(?P<host>[A-Za-z0-9.\u3002\uff0e\uff61-]+)", re.I
)
_BARE_HOST = re.compile(
    # Not inside a longer token, not a path segment ("/a/b.com") and not an email's domain, though
    # "//host" is an address.
    rf"(?<![A-Za-z0-9._~%@-])(?:(?<=//)|(?<!/))(?P<host>(?:{_LABEL}{_DOT})+(?P<tld>[A-Za-z]{{2,63}}))"
    # Not in the middle of a longer name, but a sentence's full stop may follow.
    rf"(?![A-Za-z0-9_-])(?!{_DOT}[A-Za-z0-9])"
)
_IPV4 = re.compile(rf"(?<![\d.])(?P<host>\d{{1,3}}(?:{_DOT}\d{{1,3}}){{3}})(?![\d])(?!{_DOT}\d)")
_CHARREF = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});?")
_EVIDENCE_URL = re.compile(r'"evidence_url":\s*"((?:[^"\\]|\\.){1,2048})"')
# A two-label name ending in one of these is a file or a framework far more often than a host.
_SUFFIX_TEXT = """
    js mjs cjs ts tsx jsx py pyc rb php java kt go rs c h cpp hpp cs swift sh bash ps1 bat
    md txt rst csv tsv json yaml yml toml ini cfg conf log lock xml html htm css scss svg png jpg
    jpeg gif webp pdf doc docx xls xlsx ppt pptx zip tar gz tgz exe dll so dylib whl jar war
    """
FILE_SUFFIXES = frozenset(_SUFFIX_TEXT.split())
# Reserved names (RFC 2606, RFC 6761, RFC 7686) are hosts in every resolver that serves them.
RESERVED_TLDS = frozenset({"example", "invalid", "test", "localhost", "local", "onion", "internal", "home", "lan"})
DEFANGED_SCHEME = {"http": "hxxp", "https": "hxxps"}
_DOTS = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
_MARKUP = frozenset("<>&\"'")


@cache
def top_level_domains() -> frozenset[str]:
    """Every delegated top-level domain (IANA), lower case, plus the reserved ones."""
    text = resources.files("insights_radar_agents").joinpath("tlds-alpha-by-domain.txt").read_text("ascii")
    names = {line.strip().lower() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    return frozenset(names) | RESERVED_TLDS


def defang_host(host: str) -> str:
    return re.sub(_DOT, "[.]", host)


def fold_host(host: str) -> str:
    """``host`` as a browser reads it: compatibility forms folded (NFKC) and format characters (soft
    hyphen, zero-width space) dropped (SR3-03)."""
    folded = unicodedata.normalize("NFKC", host)
    return "".join(c for c in folded if unicodedata.category(c) != "Cf")


def has_userinfo(match: re.Match[str]) -> bool:
    """Whether a scheme address carries ``user@`` before its host: ``https://trusted@evil.example/``
    goes to evil.example, so its first label proves nothing (SR3-02), and credentials in an address
    carry data even to a cited host (SR3-04)."""
    authority = re.split(r"[/\\?#]", (match.group("port") or "") + match.group("rest"), maxsplit=1)[0]
    return "@" in authority


def _registrable(host: str) -> str:
    """A host as it is compared: folded, lower case, ASCII full stops, no port, no trailing dot."""
    host = fold_host(host).translate(_DOTS).strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0].rstrip(".")


def normal_url(address: Any) -> str | None:
    """``address`` as two citations of one page compare: references decoded, lower-case scheme and host,
    no default port, no fragment. None when it is not an absolute http(s) address."""
    try:
        parts = urlsplit(html.unescape(str(address or "")).strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.translate(_DOTS).rstrip(".").lower()
    if ":" in host:
        host = f"[{host}]"
    if port and port != {"http": 80, "https": 443}[scheme]:
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def decode_address_refs(text: str) -> str:
    """``text`` with every character reference that spells a printable, non-markup character decoded
    (``&#46;``, ``&period;``, ``&colon;``). References to markup characters and whitespace stay, so
    HTML keeps its meaning (A3-001)."""

    def one(match: re.Match[str]) -> str:
        char = html.unescape(match.group(0))
        if len(char) == 1 and char not in _MARKUP and char.isprintable() and not char.isspace():
            return char
        return match.group(0)

    return _CHARREF.sub(one, text) if "&" in text else text


class OutputAddressGuard(HookProvider):
    """Defangs addresses in writes unless the run retrieved them, a graph node it read cites them, or
    they name an entity it read. ``allow`` records a retrieval."""

    def __init__(self) -> None:
        self.read_hosts: set[str] = set()
        self.fetched_urls: set[str] = set()  # exact pages this run fetched or opened
        self.cited_urls: set[str] = set()  # exact evidence addresses of graph nodes it read
        self.cited_hosts: set[str] = set()
        self.known_names: set[str] = set()  # entity names and aliases it read, lower case
        self.defanged = 0
        self.defanged_links = 0

    def allow(self, address: str) -> None:
        """Record that the run retrieved ``address``: outputs may cite its host as it is."""
        try:
            host = urlsplit(str(address)).hostname
        except ValueError:
            return
        if host:
            self.read_hosts.add(_registrable(f"[{host}]" if ":" in host else host))  # urlsplit drops IPv6 brackets
            url = normal_url(address)
            if url:
                self.fetched_urls.add(url)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    # ---- what the run read --------------------------------------------------------------------

    def after_tool(self, event: AfterToolCallEvent) -> None:
        """Record what a successful call really read. Registered after the untrusted-output fence, which
        means Strands calls this first (``AfterToolCallEvent`` runs its callbacks in reverse), so the
        result is still intact here."""
        name = str(event.tool_use.get("name", ""))
        result = event.result if isinstance(event.result, dict) else {}
        if result.get("status") != "success":
            return
        if name == "http_fetch":
            for block in result.get("content") or []:
                page = block.get("json") if isinstance(block, dict) else None
                if isinstance(page, dict) and page.get("url"):
                    self.allow(str(page["url"]))
        elif name == BROWSER_TOOL:
            raw = event.tool_use.get("input")
            browser_input = raw.get("browser_input") if isinstance(raw, dict) else None
            action = browser_input.get("action") if isinstance(browser_input, dict) else None
            if isinstance(action, dict) and action.get("type") == "navigate" and action.get("url"):
                self.allow(str(action["url"]))  # opened, not just requested (P3-020)
        elif name in GRAPH_READS:
            for block in result.get("content") or []:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    self._record_graph(block["text"])

    def _record_graph(self, text: str) -> None:
        try:
            data: Any = json.loads(text)
        except ValueError:  # cut at the result cap: the citations that survive are still citations
            for match in _EVIDENCE_URL.finditer(text):
                self._cite(json.loads(f'"{match.group(1)}"'))
            return
        self._walk(data, 0)

    def _walk(self, value: Any, depth: int) -> None:
        if depth > MAX_WALK:
            return
        if isinstance(value, list):
            for item in value:
                self._walk(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("evidence_url"), str):
            self._cite(value["evidence_url"])
        if value.get("type") in ENTITY_TYPES:
            for name in [value.get("title"), value.get("name"), *(value.get("aliases") or [])]:
                if isinstance(name, str) and name.strip():
                    self.known_names.add(name.strip().translate(_DOTS).lower())
        for item in value.values():
            if isinstance(item, (dict, list)):
                self._walk(item, depth + 1)

    def _cite(self, address: str) -> None:
        url = normal_url(address)
        if url:
            self.cited_urls.add(url)
            self.cited_hosts.add(_registrable(str(urlsplit(url).hostname)))

    # ---- what the run writes ------------------------------------------------------------------

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = str(event.tool_use.get("name", ""))
        fields = SCRUBBED.get(name)
        if fields is None:
            return
        args = event.tool_use.get("input")
        if not isinstance(args, dict):
            return
        cleaned = dict(args)
        before = self.defanged
        exempt = CITATIONS.get(name, frozenset())
        for field in fields:
            if field in cleaned:
                value = cleaned[field]
                if field in HTML_FIELDS and isinstance(value, str):
                    value = HtmlSanitizer.sanitize(value)  # as the backend will store it, and a browser read it
                cleaned[field] = self._scrub(value, (field,), exempt)
        for field in LINKS_ONLY.get(name, ()):
            if field in cleaned:
                cleaned[field] = self._scrub(cleaned[field], (field,), frozenset(), links_only=True)
        stamped = False
        if name == "kg_add_evidence":
            # Whether the run really fetched the page it cites, decided here and never by the model:
            # whatever it passed is replaced. The autopilot counts only fetched evidence (SR2-02).
            cleaned["fetched"] = normal_url(cleaned.get("evidence_url")) in self.fetched_urls
            stamped = True
        if self.defanged > before:
            log.warning(
                '{"event": "output_address_defanged", "tool": "%s", "addresses": %d}', name, self.defanged - before
            )
        if self.defanged > before or stamped:
            event.tool_use = {**event.tool_use, "input": cleaned}

    def scrub_summary(self, text: str) -> str:
        """The run summary with the same addresses defanged, not counted: it reports an injection, and the
        run history renders it as Markdown, where a live link is one click away (A3-017)."""
        counts = (self.defanged, self.defanged_links)
        cleaned = self._scrub_text(text)
        self.defanged, self.defanged_links = counts
        return cleaned

    def _scrub(
        self, value: Any, path: tuple[str, ...], exempt: frozenset[tuple[str, ...]], links_only: bool = False
    ) -> Any:
        if path in exempt or len(path) > MAX_DEPTH + 1:
            return value
        if isinstance(value, str):
            return self._scrub_text(value, links_only)
        if isinstance(value, dict):
            return {k: self._scrub(v, (*path, str(k)), exempt, links_only) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v, (*path, "*"), exempt, links_only) for v in value]
        return value

    def _kept(self, host: str) -> bool:
        return _registrable(host) in self.read_hosts

    def _scrub_text(self, text: str, links_only: bool = False) -> str:
        text = decode_address_refs(text)

        def scheme_url(match: re.Match[str]) -> str:
            host = match.group("host")
            userinfo = has_userinfo(match)
            if self._kept(host) and not userinfo:
                return match.group(0)
            whole = match.group(0)
            cited = normal_url(whole) in self.cited_urls or normal_url(whole.rstrip(".,;:!?)")) in self.cited_urls
            if cited and not userinfo:  # an address with userinfo is never kept (SR3-02, SR3-04)
                return whole
            self.defanged += 1
            self.defanged_links += 1
            port, rest = match.group("port") or "", match.group("rest")
            special = match.group("special")
            if special:
                return f"{DEFANGED_SCHEME[special.lower()]}:{match.group('sep')}{defang_host(host)}{port}{rest}"
            return f"{match.group('scheme')}[:]//{defang_host(host)}{port}{rest}"

        def mailto(match: re.Match[str]) -> str:
            host = match.group("host")
            # Only a host the run read: a cited host is an attacker's stored evidence host as often as
            # not, and the user part of the address carries data (SR3-05).
            if self._kept(host):
                return match.group(0)
            self.defanged += 1
            self.defanged_links += 1
            return f"{match.group('scheme')}[:]{match.group('user')}@{defang_host(host)}"

        def bare_host(match: re.Match[str]) -> str:
            host, tld = match.group("host"), match.group("tld")
            labels = re.split(_DOT, host)
            if len(labels) == 2 and tld.lower() in FILE_SUFFIXES:
                return host
            if tld != tld.lower() or tld.lower() not in top_level_domains():
                return host  # ASP.NET, Character.AI, os.path.join: names, not hosts
            key = _registrable(host)
            if key in self.read_hosts or key in self.cited_hosts or key in self.known_names:
                return host
            self.defanged += 1
            return defang_host(host)

        def ip(match: re.Match[str]) -> str:
            host = match.group("host")
            if self._kept(host):
                return host
            self.defanged += 1
            return defang_host(host)

        # Scheme-bearing addresses first: their hosts are then plain "[.]" text, which the bare
        # patterns no longer match, so an address is never counted twice.
        text = _MAILTO.sub(mailto, _SCHEME_URL.sub(scheme_url, text))
        if links_only:
            return text
        return _IPV4.sub(ip, _BARE_HOST.sub(bare_host, text))
