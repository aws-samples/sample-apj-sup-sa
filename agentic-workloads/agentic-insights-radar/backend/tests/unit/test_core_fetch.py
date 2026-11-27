# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import codecs
import socket
import threading
from typing import Any
from urllib.parse import urlunsplit  # builds the credential URL without a scanner-matching literal

import httpcore
import pytest
from httpcore._backends.mock import MockStream

from insights_radar.core.fetch import (
    MAX_BYTES,
    FetchError,
    PinnedBackend,
    SafeFetcher,
    _bom_charset,
    _charset,
    _declared_charset,
    _header_text,
    as_uri,
    html_to_text,
    parse_feed,
    parse_html,
    tls_context,
)
from insights_radar.core.urls import SafeResolver


def raw(status: int, headers: dict[str, str], body: bytes) -> list[bytes]:
    head = f"HTTP/1.1 {status} X\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    head += f"Content-Length: {len(body)}\r\n\r\n"
    return [head.encode("latin-1"), body]


class Scripted(httpcore.NetworkBackend):
    """One scripted response per new connection; records the hosts connected to."""

    def __init__(self, *responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.hosts: list[str] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        self.hosts.append(host)
        return MockStream(list(self.responses.pop(0)))

    def connect_unix_socket(self, *a: Any, **kw: Any) -> httpcore.NetworkStream:  # pragma: no cover
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:  # pragma: no cover
        pass


HTML = b"""<html><head><title>Desk news</title><style>.x{}</style><script>alert(1)</script></head>
<body><h1>Acme ships</h1><p>New   motor.</p><noscript>hidden</noscript><div>Second</div></body></html>"""


def test_fetches_html_as_text() -> None:
    backend = Scripted(raw(200, {"Content-Type": "text/html; charset=utf-8"}, HTML))
    page = SafeFetcher(backend).fetch("https://news.example.com/a")
    assert page["title"] == "Desk news" and "alert" not in page["text"] and "hidden" not in page["text"]
    assert "Acme ships" in page["text"] and "New motor." in page["text"] and not page["truncated"]
    assert backend.hosts == ["news.example.com"]


def test_follows_safe_redirects_and_refuses_unsafe_ones() -> None:
    backend = Scripted(raw(301, {"Location": "/b"}, b""), raw(200, {"Content-Type": "application/json"}, b'{"a":1}'))
    page = SafeFetcher(backend).fetch("https://news.example.com/a")
    assert page["url"] == "https://news.example.com/b" and page["text"] == '{"a":1}'
    evil = Scripted(raw(302, {"Location": "https://169.254.169.254/latest/meta-data"}, b""))
    with pytest.raises(FetchError, match="refused"):
        SafeFetcher(evil).fetch("https://news.example.com/a")
    loop = Scripted(*[raw(302, {"Location": "/again"}, b"") for _ in range(6)])
    with pytest.raises(FetchError, match="too many redirects"):
        SafeFetcher(loop).fetch("https://news.example.com/a")


@pytest.mark.parametrize(
    "url",
    [
        "http://news.example.com",
        "https://localhost/",
        "https://10.1.2.3/",
        urlunsplit(("https", "user:pw@example.com", "/", "", "")),
        "https://0x7f000001/",
    ],
)
def test_refuses_unsafe_urls_before_connecting(url: str) -> None:
    backend = Scripted()
    with pytest.raises(FetchError, match="refused"):
        SafeFetcher(backend).fetch(url)
    assert backend.hosts == []


def test_refuses_binary_and_errors() -> None:
    with pytest.raises(FetchError, match="not text"):
        SafeFetcher(Scripted(raw(200, {"Content-Type": "application/pdf"}, b"%PDF"))).fetch("https://a.example.com")
    with pytest.raises(FetchError, match="HTTP 404"):
        SafeFetcher(Scripted(raw(404, {"Content-Type": "text/html"}, b"no"))).fetch("https://a.example.com")


def test_caps_size_and_text() -> None:
    big = b"a" * (MAX_BYTES + 10)
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/plain"}, big))).fetch("https://a.example.com")
    assert page["truncated"] and len(page["text"]) == 60_000


def test_untyped_html_and_charset() -> None:
    page = SafeFetcher(Scripted(raw(200, {}, b"<html><body>caf\xe9</body></html>"))).fetch("https://a.example.com")
    assert page["text"] == "caf\ufffd"
    latin = raw(200, {"Content-Type": "text/plain; charset=latin-1"}, b"caf\xe9")
    assert SafeFetcher(Scripted(latin)).fetch("https://a.example.com")["text"] == "caf\u00e9"
    assert _charset("text/plain; charset=nope") == "utf-8" and _charset('text/html; charset="UTF-8"') == "utf-8"
    # Quadratic decoders are refused: a 2 MiB punycode body took two minutes to decode.
    assert _charset("text/html; charset=punycode") == "utf-8" and _charset("text/html; charset=idna") == "utf-8"
    assert (
        _charset("text/html; charset=windows-1252") == "cp1252"
        and _charset("text/html; charset=Shift_JIS") == "shift_jis"
    )


def test_a_document_declared_encoding_is_used_when_the_header_names_none() -> None:
    # Japan MIC's RSS: plain text/xml, Shift_JIS declared in the XML prolog; it decoded as garbled UTF-8.
    title = "\u96fb\u6ce2\u6cd5"  # "Radio Act" in Japanese
    rss = f'<?xml version="1.0" encoding="Shift_JIS" ?><rss><channel><title>{title}</title>'
    rss += f"<item><title>{title}</title><description>d</description></item></channel></rss>"
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/xml"}, rss.encode("shift_jis")))).fetch(
        "https://a.example.com"
    )
    assert title in page["text"] and "\ufffd" not in page["text"]
    html = '<html><head><meta charset="euc-kr"></head><body>\uc804\ud30c</body></html>'.encode("euc-kr")
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/html"}, html))).fetch("https://a.example.com")
    assert page["text"] == "\uc804\ud30c"
    # The header still wins over the document, as in a browser.
    both = raw(200, {"Content-Type": "text/plain; charset=latin-1"}, b'<?xml encoding="Shift_JIS"?>caf\xe9')
    assert SafeFetcher(Scripted(both)).fetch("https://a.example.com")["text"].endswith("caf\u00e9")
    assert _bom_charset(b"\xef\xbb\xbfx") == "utf-8-sig" and _bom_charset(b"\xff\xfex") == "utf-16"
    assert _bom_charset(b"\xfe\xffx") == "utf-16" and _bom_charset(b"x") is None
    # UTF-32's marks before UTF-16's: FF FE 00 00 also starts with FF FE.
    assert _bom_charset(b"\xff\xfe\x00\x00x") == "utf-32" and _bom_charset(b"\x00\x00\xfe\xffx") == "utf-32"
    # A declaration read in ASCII bytes cannot be UTF-16 or UTF-32: a mislabelled page is UTF-8.
    assert _declared_charset(b'<meta charset="utf-16">') == "utf-8"
    assert _declared_charset(b'<?xml version="1.0" encoding="UTF-32LE"?>') == "utf-8"
    assert _declared_charset(b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">') == "cp1252"
    # A declared name passes the same codec checks as a header's; no declaration is None.
    assert _declared_charset(b'<?xml version="1.0" encoding="punycode"?>') == "utf-8"
    assert _declared_charset(b"<html><body>plain</body></html>") is None and _charset("text/html") is None


def test_a_byte_order_mark_wins_over_the_header_as_in_a_browser() -> None:
    body = "\ufeffcaf\u00e9".encode("utf-16-le")
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/plain; charset=iso-8859-1"}, body))).fetch(
        "https://a.example.com"
    )
    assert page["text"].lstrip("\ufeff") == "caf\u00e9"
    # A UTF-8 mark is dropped, not left as a U+FEFF at the start of the text.
    body = codecs.BOM_UTF8 + "caf\u00e9".encode()
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/plain; charset=iso-8859-1"}, body))).fetch(
        "https://a.example.com"
    )
    assert page["text"] == "caf\u00e9"


def test_a_raw_utf8_location_is_followed_once_encoded() -> None:
    # A Thai site answering 302 with raw UTF-8 bytes in Location: latin-1 made it mojibake, encoded twice.
    target = "/th-th/\u0e01\u0e0e"
    first = [
        b"HTTP/1.1 302 X\r\nLocation: " + target.encode("utf-8") + b"\r\nContent-Length: 0\r\n\r\n",
        b"",
    ]
    backend = Scripted(first, raw(200, {"Content-Type": "text/plain"}, b"ok"))
    page = SafeFetcher(backend).fetch("https://a.example.com/start")
    assert page["url"] == "https://a.example.com/th-th/%E0%B8%81%E0%B8%8E" and page["text"] == "ok"
    assert _header_text(b"caf\xe9") == "caf\u00e9"  # not UTF-8: latin-1, as before


def test_a_non_ascii_address_is_percent_encoded_as_a_browser_sends_it() -> None:
    # Thai DFT links to /th-th/ + Thai script; the HTTP client refused the raw string with a TypeError.
    thai = "https://www.dft.go.th/th-th/\u0e01\u0e0e?q=\u0e01#\u0e01"
    assert as_uri(thai) == "https://www.dft.go.th/th-th/%E0%B8%81%E0%B8%8E?q=%E0%B8%81#%E0%B8%81"
    assert as_uri("https://a.example.com/x%20y?a=1&b=%E0") == "https://a.example.com/x%20y?a=1&b=%E0"
    assert as_uri("https://b\u00fccher.example/\u00e9") == "https://xn--bcher-kva.example/%C3%A9"
    # A host with upper-case letters (hostname is lower-cased, so str.replace missed it), with a port and
    # user info kept.
    assert as_uri("https://\u00c9X.example:8443/a") == "https://xn--x-9fa.example:8443/a"
    assert as_uri("https://u\u00e9@\u00e9.example/") == "https://u%C3%A9@xn--9ca.example/"
    assert as_uri("https://[::1]/\u00e9") == "https://[::1]/%C3%A9"
    with pytest.raises(FetchError, match="not a valid address"):
        as_uri("https://" + "\u00e4" * 70 + ".example/")
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/plain"}, b"ok"))).fetch(thai)
    assert page["text"] == "ok" and page["url"].startswith("https://www.dft.go.th/th-th/%E0%B8%81")


def test_the_tls_context_verifies_like_a_browser() -> None:
    import ssl

    context = tls_context()
    # Still verifying: certificate required, host name checked, default roots loaded.
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    # Not strict: TWCA's Global Root has no Subject Key Identifier, so strict mode refused every
    # Taiwanese government site.
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_html_to_text_handles_unclosed_skips() -> None:
    title, text = html_to_text("<title>T</title><svg><text>x</text></svg>after</svg></svg>more")
    assert title == "T" and text == "aftermore"


def test_links_are_absolute_and_capped() -> None:
    body = b'<html><body><a href="/b">b</a><a href="https://x.example.com/c#f">c</a><a>none</a></body></html>'
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/html"}, body))).fetch("https://a.example.com/d/")
    assert page["links"] == ["https://a.example.com/b", "https://x.example.com/c#f"]
    many = "".join(f'<a href="/{i}">x</a>' for i in range(2100))
    assert len(parse_html(many)[2]) == 2000


def test_meta_refresh_is_followed_and_guarded() -> None:
    redirect = b'<html><head><meta http-equiv="refresh" content="0;URL=\'start.html\'"></head><body></body></html>'
    # The redirect page is read in full, so the keep-alive connection is reused for the next request.
    backend = Scripted(
        raw(200, {"Content-Type": "text/html"}, redirect)
        + raw(200, {"Content-Type": "text/html"}, b"<html><body>Welcome</body></html>")
    )
    page = SafeFetcher(backend).fetch("https://docs.example.com/guide/")
    assert page["url"] == "https://docs.example.com/guide/start.html" and page["text"] == "Welcome"
    evil = b'<meta http-equiv="Refresh" content="0; url=https://169.254.169.254/">'
    with pytest.raises(FetchError, match="refused"):
        SafeFetcher(Scripted(raw(200, {"Content-Type": "text/html"}, evil))).fetch("https://docs.example.com/")
    odd = b'<meta http-equiv="refresh" content="5"><meta name="x"><p>' + b"long text " * 30 + b"</p>"
    assert (
        "long text"
        in SafeFetcher(Scripted(raw(200, {"Content-Type": "text/html"}, odd))).fetch("https://docs.example.com/")[
            "text"
        ]
    )


# ---- pinned connections ----------------------------------------------------------------------


class FixedResolver(SafeResolver):
    def __init__(self, answer: list[str] | Exception) -> None:
        super().__init__()
        self.answer = answer

    def check(self, host: str, port: int = 443) -> list[str]:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_pinned_backend_connects_to_vetted_address_only() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted: list[Any] = []
    t = threading.Thread(target=lambda: accepted.append(server.accept()), daemon=True)
    t.start()
    stream = PinnedBackend(FixedResolver(["127.0.0.2", "127.0.0.1"])).connect_tcp("example.com", port, timeout=2)
    t.join(2)
    assert accepted and stream.get_extra_info("server_addr")[0] == "127.0.0.1"
    stream.close()
    server.close()


def test_pinned_backend_refuses_internal_and_unreachable() -> None:
    with pytest.raises(httpcore.ConnectError, match="non-public"):
        PinnedBackend(FixedResolver(ValueError("example.com resolves to a non-public address"))).connect_tcp(
            "example.com", 443
        )
    with pytest.raises(httpcore.ConnectError, match="could not connect"):
        PinnedBackend(FixedResolver(["127.0.0.1"])).connect_tcp("example.com", 1, timeout=1)
    assert isinstance(PinnedBackend()._resolver, SafeResolver)


RSS = (
    b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>Desk  news</title><link>https://news.example.com/</link>
<item><title><![CDATA[Acme ships a 20,000-hour motor]]></title><link>https://news.example.com/motor</link>
<guid>urn:ignored</guid><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Acme&amp;#39;s new &lt;b&gt;motor&lt;/b&gt;.&lt;/p&gt;</description>
<content:encoded><![CDATA[<p>"""
    + b"Full article. " * 5000
    + b"""</p>]]></content:encoded></item>
<item><title>Second</title><link>https://news.example.com/2</link><description>"""
    + b"word " * 200
    + b"""</description></item>
</channel></rss>"""
)

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Post</title><link rel="alternate" href="https://blog.example.com/p"/><id>tag:x</id>
<updated>2026-10-05T10:00:00Z</updated><summary>Short.</summary></entry></feed>"""


def test_rss_feeds_become_item_lists_without_full_content() -> None:
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "application/rss+xml"}, RSS))).fetch(
        "https://news.example.com/feed"
    )
    assert page["title"] == "Desk news" and not page["truncated"]
    first, second = page["text"].split("\n\n")
    assert first == (
        "1. Acme ships a 20,000-hour motor\n   link: https://news.example.com/motor\n"
        "   date: Mon, 05 Oct 2026 10:00:00 GMT\n   Acme's new motor ."
    )
    assert "Full article" not in page["text"] and second.startswith("2. Second\n   link: https://news.example.com/2")
    assert second.endswith(" ...") and len(second.splitlines()[-1]) <= 3 + 400 + 4


def test_atom_feeds_and_non_feeds() -> None:
    page = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/xml"}, ATOM))).fetch("https://blog.example.com/atom")
    assert page["title"] == "Blog"
    assert page["text"] == "1. Post\n   link: https://blog.example.com/p\n   date: 2026-10-05T10:00:00Z\n   Short."
    assert parse_feed("<?xml version='1.0'?><note>hi</note>") is None  # XML, not a feed
    assert parse_feed("<rss><channel><title>Empty</title></channel></rss>") is None  # a feed with no items
    sitemap = raw(
        200, {"Content-Type": "application/xml"}, b"<urlset><url><loc>https://a.example.com/</loc></url></urlset>"
    )
    assert "https://a.example.com/" in SafeFetcher(Scripted(sitemap)).fetch("https://a.example.com/s.xml")["text"]


def test_feed_entities_are_not_expanded() -> None:
    bomb = b"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>
<rss><channel><item><title>&b;&b;</title><link>https://x.example.com/</link></item></channel></rss>"""
    _, text = parse_feed(bomb.decode()) or ("", "")
    assert "aaaaaaaaaa" not in text and text.startswith("1. ")
    other = "<rss><channel><item><title>T</title><![if gte mso 9]><link>https://x.example.com/</link></item></channel></rss>"
    assert parse_feed(other) == ("", "1. T\n   link: https://x.example.com/")  # non-CDATA sections are ignored


def test_feed_links_prefer_the_page_link_over_identifiers() -> None:
    rss = (
        "<rss><channel><item><title>A</title><guid isPermaLink='false'>abc-123</guid>"
        "<link>https://example.com/a</link></item>"
        "<item><title>B</title><guid>https://example.com/b</guid></item>"
        "<item><title>C</title><guid isPermaLink='false'>opaque</guid></item></channel></rss>"
    )
    _, text = parse_feed(rss) or ("", "")
    assert "link: https://example.com/a" in text and "abc-123" not in text and "opaque" not in text
    assert "link: https://example.com/b" in text
    atom = (
        "<feed><title>Releases</title><entry><title>v1</title><id>tag:github.com,2008:Repository/1/v1</id>"
        "<link rel='self' href='https://api.example.com/v1'/>"
        "<link rel='alternate' href='https://github.com/o/r/releases/v1'/>"
        "<content type='html'>&lt;p&gt;Notes for v1&lt;/p&gt;</content></entry>"
        "<entry><title>v2</title><id>https://example.com/v2</id>"
        "<summary>Short</summary><content>Long body</content></entry></feed>"
    )
    _, text = parse_feed(atom) or ("", "")
    assert (
        "link: https://github.com/o/r/releases/v1" in text
        and "tag:github.com" not in text
        and "api.example.com" not in text
    )
    assert "Notes for v1" in text  # content-only entries get a summary
    assert "link: https://example.com/v2" in text and "Short" in text and "Long body" not in text


@pytest.mark.parametrize("filler", ["<", "< ", "&lt;", "<x", "<!-- >", "<a b='>", "<![CDATA[", "<![CDATA[<"])
def test_a_feed_full_of_angle_brackets_parses_in_linear_time(filler: str) -> None:
    import time

    body = filler * (2 * 1024 * 1024 // len(filler))  # MAX_BYTES worth
    xml = (
        f"<rss><channel><title>F</title><item><title>t</title><description>{body}</description></item></channel></rss>"
    )
    started = time.perf_counter()
    parsed = parse_feed(xml)
    assert parsed is not None and time.perf_counter() - started < 2.0


def test_a_plain_cdata_description_is_kept() -> None:
    item = "<item><title>t</title><description><![CDATA[plain words]]></description></item>"
    xml = f"<rss><channel><title>F</title>{item}</channel></rss>"
    parsed = parse_feed(xml)
    assert parsed is not None and "plain words" in parsed[1]


def test_real_page_charsets_are_honoured_and_odd_codecs_are_not() -> None:
    for declared, name in (
        ("iso-8859-9", "iso8859-9"),
        ("TIS-620", "tis-620"),
        ("cp1256", "cp1256"),
        ("utf-16-le", "utf-16-le"),
    ):
        assert _charset(f"text/html; charset={declared}") == name
    for odd in ("base64", "zlib", "rot13", "unicode_escape", "raw-unicode-escape", "punycode", "idna", "undefined"):
        assert _charset(f"text/html; charset={odd}") == "utf-8", odd


def test_every_accepted_charset_decodes_a_large_body_quickly() -> None:
    import encodings.aliases
    import time

    body = bytes(range(256)) * 4096  # 1 MiB of every byte value
    for codec in sorted(set(encodings.aliases.aliases.values())):
        name = _charset(f"text/html; charset={codec}")
        started = time.perf_counter()
        body.decode(name, errors="replace")
        assert time.perf_counter() - started < 1.0, codec


def test_atom_xhtml_content_keeps_its_nested_paragraphs() -> None:
    entry = (
        '<entry><title>t</title><link href="https://example.com/a"/>'
        '<content type="xhtml"><div><p>Intro</p><p>More detail</p></div></content></entry>'
    )
    parsed = parse_feed(f'<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>{entry}</feed>')
    assert parsed is not None and "Intro" in parsed[1] and "More detail" in parsed[1]


@pytest.mark.parametrize(
    "description",
    [
        '<![CDATA[<p><img src="x.jpg"/> Important summary text</p>]]>',
        "Line one&lt;br/&gt;Important summary text",
        "<![CDATA[Line one<br/>Important summary text]]>",
    ],
)
def test_summaries_survive_cdata_and_self_closed_tags(description: str) -> None:
    item = f"<item><title>t</title><link>https://example.com/a</link><description>{description}</description></item>"
    parsed = parse_feed(f"<rss><channel><title>F</title>{item}</channel></rss>")
    assert parsed is not None and "Important summary text" in parsed[1]
    assert "https://example.com/a" in parsed[1]


def test_atom_xhtml_content_keeps_text_after_a_line_break() -> None:
    entry = '<entry><title>t</title><content type="xhtml"><div>Before<br/>After</div></content></entry>'
    parsed = parse_feed(f'<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>{entry}</feed>')
    assert parsed is not None and "Before" in parsed[1] and "After" in parsed[1]


def test_markup_inside_a_field_separates_words_and_a_stray_item_tag_is_harmless() -> None:
    entry = '<entry><title>t</title><content type="xhtml"><div><p>one<br/>two</p><p>three</p></div></content></entry>'
    parsed = parse_feed(f'<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>{entry}</feed>')
    assert parsed is not None and "one two three" in parsed[1]
    item = "<item><title>t</title><description>x<item/>more</description></item>"
    assert parse_feed(f"<rss><channel><title>F</title>{item}</channel></rss>") is not None
    entry = '<entry><title>t</title><content type="xhtml"><div>x<entry/>more</div></content></entry>'
    assert parse_feed(f'<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>{entry}</feed>') is not None


def test_self_closed_tags_inside_a_field_neither_blank_the_item_nor_cut_the_field() -> None:
    items = (
        "<item><title>Real</title><link>https://a.example.com/x</link><description>x<item/>more</description></item>"
        "<item><title>Dated</title><description>x<pubDate/>after</description><pubDate>Tue, 6 Oct 2026</pubDate></item>"
    )
    parsed = parse_feed(f"<rss><channel><title>F</title>{items}</channel></rss>")
    assert parsed is not None
    body = parsed[1]
    assert "1. Real" in body and "https://a.example.com/x" in body and "x more" in body
    assert "x after" in body and "Tue, 6 Oct 2026" in body


def test_an_empty_item_element_is_skipped() -> None:
    items = "<item/><item><title>Kept</title></item>"
    parsed = parse_feed(f"<rss><channel><title>F</title>{items}</channel></rss>")
    assert parsed is not None and parsed[1].startswith("1. Kept")


def test_feed_extractor_ignores_a_declaration_that_is_not_cdata() -> None:
    # Python 3.14's HTMLParser handles CDATA itself, so this branch is only reached directly.
    from insights_radar.core.fetch import _FeedExtractor

    parser = _FeedExtractor()
    parser.unknown_decl("DOCTYPE x")
    assert parser.items == [] and parser.title == ""


def test_the_raw_body_is_kept_only_on_request() -> None:
    body = b"<rss><channel><item><title>T</title><link>https://a.example.com/1</link></item></channel></rss>"
    plain = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/xml"}, body))).fetch("https://a.example.com/feed")
    assert "raw" not in plain
    kept = SafeFetcher(Scripted(raw(200, {"Content-Type": "text/xml"}, body))).fetch(
        "https://a.example.com/feed", keep_raw=True
    )
    assert kept["raw"] == body.decode() and "1. T" in kept["text"]


def test_a_compressed_body_sent_anyway_is_decoded_and_capped() -> None:
    import gzip
    import zlib as _zlib

    from insights_radar.core.fetch import USER_AGENT

    gz = raw(200, {"Content-Type": "text/plain", "Content-Encoding": "gzip"}, gzip.compress(b"hello gzip"))
    assert SafeFetcher(Scripted(gz)).fetch("https://a.example.com")["text"] == "hello gzip"
    deflated = raw(200, {"Content-Type": "text/plain", "Content-Encoding": "deflate"}, _zlib.compress(b"hello deflate"))
    assert SafeFetcher(Scripted(deflated)).fetch("https://a.example.com")["text"] == "hello deflate"
    bomb = raw(200, {"Content-Type": "text/plain", "Content-Encoding": "gzip"}, gzip.compress(b"a" * (MAX_BYTES + 50)))
    page = SafeFetcher(Scripted(bomb)).fetch("https://a.example.com")
    assert page["truncated"] and len(page["text"]) == 60_000
    broken = raw(200, {"Content-Type": "text/plain", "Content-Encoding": "gzip"}, b"not gzip at all")
    with pytest.raises(FetchError, match="compressed body could not be read"):
        SafeFetcher(Scripted(broken)).fetch("https://a.example.com")
    brotli = raw(200, {"Content-Type": "text/plain", "Content-Encoding": "br"}, b"\x1b\x00")
    with pytest.raises(FetchError, match="br-compressed"):
        SafeFetcher(Scripted(brotli)).fetch("https://a.example.com")
    identity = raw(200, {"Content-Type": "text/plain", "Content-Encoding": "identity"}, b"plain")
    assert SafeFetcher(Scripted(identity)).fetch("https://a.example.com")["text"] == "plain"
    assert USER_AGENT.startswith("Mozilla/5.0 (compatible; AgenticInsightsRadar/1.0; +https://")
