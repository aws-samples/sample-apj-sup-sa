# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Allowlist HTML sanitiser for stored descriptions (ported from the internal radar).

Allowed tags: ``p, br, strong, em, a, code``; the only attribute is ``href`` on ``<a>``,
restricted to ``http:``, ``https:``, ``mailto:``, relative paths and fragments. Every
other tag is dropped but its text survives (escaped); ``script``/``style``/``iframe``/
``object``/``template`` lose their content too. The output is idempotent:
``sanitize(sanitize(x)) == sanitize(x)``.

Hrefs are canonicalised the way a browser's URL parser sees them before the scheme check
(C0 control characters and whitespace entities removed), which closes the
``jav&#9;ascript:`` class of bypass the internal radar fixed as B1.
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from typing import ClassVar

_SAFE_SCHEME = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)
_ANY_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f\ufffd]")
_TAG_RE = re.compile(r"<[^>]*>")


class HtmlSanitizer:
    ALLOWED: ClassVar[frozenset[str]] = frozenset({"p", "br", "strong", "em", "a", "code"})
    DROP_CONTENT: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "iframe", "object", "template", "noscript", "svg", "math"}
    )
    MAX_INPUT = 200_000

    @staticmethod
    def canonical_href(href: str) -> str:
        """The href as a browser's URL parser sees it, for the scheme check.

        Entities are decoded (again: the HTML parser has already decoded once, so a
        double-encoded ``jav&amp;#97;script:`` is judged as ``javascript:``), then controls,
        whitespace, U+FFFD and format characters (zero-width, bidi) are removed.
        """
        s = _CONTROL.sub("", unescape(href))
        return "".join(c for c in s if unicodedata.category(c) != "Cf")

    @classmethod
    def is_safe_href(cls, href: str) -> bool:
        canon = cls.canonical_href(href)
        if not canon:
            return False
        if re.match(r"^[/\\]{2}", canon) or re.match(r"^/\\", canon):
            return False  # protocol-relative or backslash variants
        if _SAFE_SCHEME.match(canon):
            return True
        return not _ANY_SCHEME.match(canon)

    @classmethod
    def sanitize(cls, html: str) -> str:
        if not html:
            return ""
        if len(html) > cls.MAX_INPUT:
            html = html[: cls.MAX_INPUT]
        if "<" not in html and cls._escaped_tags(html):
            # Markup an agent copied escaped ("&lt;p&gt;...") showed its tags as text: with no real tag
            # in the input, decode it once, then sanitize as usual (CTR HC-123).
            html = unescape(html)
        parser = _AllowlistParser(cls)
        parser.feed(html)
        parser.close()
        return parser.result()

    @classmethod
    def _escaped_tags(cls, html: str) -> bool:
        return any(m.group(1).lower() in cls.ALLOWED for m in _ESCAPED_TAG_RE.finditer(html))

    @staticmethod
    def to_text(html: str) -> str:
        """Plain-text projection (for embeddings, previews and keyword search)."""
        return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", html or ""))).strip()


_ESCAPED_TAG_RE = re.compile(r"&lt;/?([a-zA-Z][a-zA-Z0-9]*)\b[^&]{0,200}?/?&gt;")


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: str) -> str:
    return _escape_text(s).replace('"', "&quot;")


class _AllowlistParser(HTMLParser):
    def __init__(self, policy: type[HtmlSanitizer]) -> None:
        super().__init__(convert_charrefs=True)
        self._policy = policy
        self._out: list[str] = []
        self._open: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._policy.DROP_CONTENT:
            self._drop_depth += 1
            return
        if self._drop_depth or tag not in self._policy.ALLOWED:
            return
        if tag == "br":
            self._out.append("<br>")
            return
        if tag == "a":
            if "a" in self._open:
                return  # nested anchors are flattened
            href = next((v for k, v in attrs if k.lower() == "href" and v is not None), "")
            if not self._policy.is_safe_href(href):
                return
            self._out.append(f'<a href="{_escape_attr(_CONTROL.sub("", href))}">')
            self._open.append("a")
            return
        self._out.append(f"<{tag}>")
        self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br" and not self._drop_depth:
            self._out.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._policy.DROP_CONTENT:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth or tag not in self._open:
            return
        while True:  # ``tag`` is open, so the loop always reaches it
            top = self._open.pop()
            self._out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self._out.append(_escape_text(data))

    def result(self) -> str:
        while self._open:
            self._out.append(f"</{self._open.pop()}>")
        return "".join(self._out)
