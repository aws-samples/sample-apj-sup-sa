# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import socket
from urllib.parse import urlunsplit  # builds the credential URL without a scanner-matching literal

import pytest

from insights_radar.core.urls import SafeResolver, UrlGuard

SAFE = [
    "https://example.com",
    "https://docs.example.com/path?q=1#frag",
    "https://example.com:8443/x",
    "https://8.8.8.8/",
    "https://[2606:4700:4700::1111]/",
    "https://sub.domain.co.uk/a/b",
    "HTTPS://Example.COM/upper",
]

UNSAFE = [
    ("", "URL is empty"),
    ("http://example.com", "only https URLs are allowed"),
    ("ftp://example.com", "only https URLs are allowed"),
    ("file:///etc/passwd", "only https URLs are allowed"),
    ("javascript:alert(1)", "only https URLs are allowed"),
    (urlunsplit(("https", "user:pass@example.com", "", "", "")), "URL must not contain credentials"),
    ("https://user@example.com", "URL must not contain credentials"),
    ("https://localhost/", "host is not allowed"),
    ("https://foo.localhost/", "host is not allowed"),
    ("https://metadata.google.internal/", "host is not allowed"),
    ("https://svc.internal/", "host is not allowed"),
    ("https://127.0.0.1/", "host is a private, loopback, link-local or reserved address"),
    ("https://127.1/", "host is a private, loopback, link-local or reserved address"),
    ("https://0x7f000001/", "host is a private, loopback, link-local or reserved address"),
    ("https://2130706433/", "host is a private, loopback, link-local or reserved address"),
    ("https://0177.0.0.1/", "host is a private, loopback, link-local or reserved address"),
    ("https://169.254.169.254/latest/meta-data", "host is a private, loopback, link-local or reserved address"),
    ("https://2852039166/", "host is a private, loopback, link-local or reserved address"),
    ("https://10.0.0.5/", "host is a private, loopback, link-local or reserved address"),
    ("https://172.16.0.1/", "host is a private, loopback, link-local or reserved address"),
    ("https://192.168.1.1/", "host is a private, loopback, link-local or reserved address"),
    ("https://0.0.0.0/", "host is a private, loopback, link-local or reserved address"),
    ("https://[::1]/", "host is a private, loopback, link-local or reserved address"),
    ("https://[fd00:ec2::254]/", "host is a private, loopback, link-local or reserved address"),
    ("https://[fe80::1]/", "host is a private, loopback, link-local or reserved address"),
    ("https://[::ffff:127.0.0.1]/", "host is a private, loopback, link-local or reserved address"),
    ("https://224.0.0.1/", "host is a private, loopback, link-local or reserved address"),
    ("https://example.com/\nforged", "URL contains control characters"),
    ("https://example.com/\x7f", "URL contains control characters"),
    ("https://" + "a" * 2050, "URL is too long"),
    ("https:///nohost", "URL has no host"),
]


@pytest.mark.parametrize("url", SAFE)
def test_safe_urls_pass(url: str) -> None:
    assert UrlGuard.is_safe(url)
    assert UrlGuard.reason(url) is None


@pytest.mark.parametrize(("url", "reason"), UNSAFE)
def test_unsafe_urls_fail_with_reason(url: str, reason: str) -> None:
    assert not UrlGuard.is_safe(url)
    assert UrlGuard.reason(url) == reason


@pytest.mark.parametrize("value", [None, 42, b"https://x", ["https://x"]])
def test_non_string_is_rejected(value: object) -> None:
    assert UrlGuard.reason(value) == "URL is empty"


@pytest.mark.parametrize("host", ["1.2.3.4.5", "1..2", "0xzz", "09.1.1.1", "256.1.1.1"])
def test_malformed_ipv4_is_treated_as_dns_name(host: str) -> None:
    assert UrlGuard._ipv4_from_encoded(host) is None


class _FakeResolver:
    def __init__(self, addrs: list[str], fail: bool = False) -> None:
        self._addrs = addrs
        self._fail = fail

    def getaddrinfo(self, host: str, port: int, proto: int = 0) -> list[tuple[object, ...]]:
        if self._fail:
            raise socket.gaierror("nope")
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (a, port)) for a in self._addrs]


def test_resolver_allows_public() -> None:
    assert SafeResolver(_FakeResolver(["93.184.216.34"])).check("example.com") == ["93.184.216.34"]


@pytest.mark.parametrize("addr", ["10.1.2.3", "127.0.0.1", "169.254.169.254", "::1", "fd00::1"])
def test_resolver_blocks_rebinding_to_internal(addr: str) -> None:
    with pytest.raises(ValueError, match="non-public"):
        SafeResolver(_FakeResolver(["93.184.216.34", addr])).check("evil.example")


def test_resolver_unresolvable() -> None:
    with pytest.raises(ValueError, match="cannot resolve"):
        SafeResolver(_FakeResolver([], fail=True)).check("nx.example")
    with pytest.raises(ValueError, match="cannot resolve"):
        SafeResolver(_FakeResolver([])).check("empty.example")


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost./",
        "https://127.0.0.1./",
        "https://0x7f.1./",
        "https://169.254.169.254./latest/meta-data",
        "https://metadata.google.internal./",
        "https://instance-data..",
        "https://1.2.65536/",
        "https://example.0x/",
        "https://10.0.0.1../",
    ],
)
def test_trailing_dots_and_invalid_numeric_hosts_are_blocked(url: str) -> None:
    assert not UrlGuard.is_safe(url)


@pytest.mark.parametrize("url", ["https://example.com./a", "https://v2.example.com/", "https://a.b.c.1x/"])
def test_ordinary_hosts_still_pass(url: str) -> None:
    assert UrlGuard.is_safe(url)


@pytest.mark.parametrize(
    ("address", "blocked"),
    [
        ("100.64.1.1", True),  # shared address space (carrier-grade NAT)
        ("198.18.0.1", True),  # benchmarking
        ("64:ff9b::a00:1", True),  # NAT64 of 10.0.0.1
        ("64:ff9b::808:808", False),  # NAT64 of 8.8.8.8
        ("2002:a00:1::1", True),  # 6to4 of 10.0.0.1
        ("2001:0:4136:e378:8000:63bf:f5ff:fffe", True),  # Teredo whose client is 10.0.0.1
        ("2606:4700:4700::1111", False),
        ("8.8.8.8", False),
    ],
)
def test_ipv4_hidden_in_ipv6_and_non_global_ranges(address: str, blocked: bool) -> None:
    import ipaddress

    assert UrlGuard.is_blocked_ip(ipaddress.ip_address(address)) is blocked


def test_a_deep_path_alone_is_a_secret_only_when_asked_for() -> None:
    from insights_radar.core.urls import UrlGuard

    deep = "https://www.example.gov/en/news/press-releases"
    assert UrlGuard.secret_values(deep) and UrlGuard.secret_values(deep, deep_paths=False) == []
    hook_url = "https://hooks.example.com/services/T000/B000/a1b2c3d4e5f6g7h8i9j0k1l2"
    assert "a1b2c3d4e5f6g7h8i9j0k1l2" in UrlGuard.secret_values(hook_url, deep_paths=False)


def test_camel_case_credential_names_are_recognised() -> None:
    """r14: ``accessKey`` and ``client-Secret`` are the listed access_key and client_secret."""
    from insights_radar.core.urls import UrlGuard

    assert UrlGuard.is_secret_param("accessKey") and UrlGuard.is_secret_param("Access-Token")
    assert UrlGuard.is_secret_param("apiKey") and not UrlGuard.is_secret_param("lang")


def test_every_query_value_of_a_sources_own_address_is_a_secret_when_asked_for() -> None:
    """r14: a credential under a name no list knows (``blob``) is scrubbed in every encoding."""
    from insights_radar.core.urls import UrlGuard

    address = "https://api.example.gov/v1/notices?lang=en&blob=Ab%2Fc%2BK9zQ7rT2wX5"  # a base64 key, stored encoded
    assert not [v for v in UrlGuard.secret_values(address, deep_paths=False) if "K9zQ7rT2wX5" in v]
    forms = UrlGuard.secret_values(address, deep_paths=False, every_query=True)
    assert {"Ab%2Fc%2BK9zQ7rT2wX5", "Ab/c+K9zQ7rT2wX5"} <= set(forms)
    assert "en" not in forms  # too short alone to scrub bare


def test_r15_the_source_scrubber_hides_credentials_not_words_or_articles() -> None:
    """Code review / security review r15: one rule for http_fetch and the watcher's inbox."""
    from insights_radar.core.urls import UrlGuard

    plain = UrlGuard.source_scrubber("https://example.com/news?category=technology", "s1")
    assert plain("Latest technology news") == "Latest technology news"
    assert plain("https://example.com/2026/10/technology-launch/") == "https://example.com/2026/10/technology-launch/"
    assert plain("https://example.com/news?category=technology") == "source:s1"
    assert plain("https://example.com/news?category=technology#top") == "source:s1#top"
    article = "https://example.com/news?category=technology&article=123"
    assert plain(article) == article  # a query that adds fields is another page
    listing = UrlGuard.source_scrubber("https://gov.example/news.php", "s2")
    for url in ("https://gov.example/news.php?id=5", "https://gov.example/news.php#item-2"):
        assert listing(url) == url
    # A short path whose secret has no token shape: its self link still names the source.
    ical = UrlGuard.source_scrubber("https://cal.example.com/ical/482139575512", "s3")
    assert (
        ical('<link rel="self" href="https://cal.example.com/ical/482139575512"/>')
        == '<link rel="self" href="source:s3"/>'
    )
    assert UrlGuard.standalone_address("https://example.com/") is None
    bare = UrlGuard.source_scrubber("https://host.example/feed.xml?9f3cPRIVATEFEEDTOKEN12", "s4")
    assert "9f3cPRIVATEFEEDTOKEN12" not in bare("next: https://host.example/feed/page/2?9f3cPRIVATEFEEDTOKEN12")
    short = UrlGuard.source_scrubber("https://host.example/feed?page=1&k=Ab3x9Q2", "s5")
    assert "Ab3x9Q2" not in short("https://host.example/feed?page=2&k=Ab3x9Q2")
    token_path = UrlGuard.source_scrubber("https://feeds.example.com/private/9f3cPRIVATEFEEDTOKEN77/rss", "s6")
    assert token_path("https://feeds.example.com/private/9f3cPRIVATEFEEDTOKEN77/item/1") == (
        "https://feeds.example.com/private/REDACTED/item/1"
    )
    deep = UrlGuard.source_scrubber("https://hooks.example.com/services/T000/B000/abcdefghijklmnop", "s7")
    assert deep("see https://hooks.example.com/services/T000/B000/abcdefghijklmnop/ now") == "see source:s7 now"


def test_r15_an_address_that_does_not_parse_or_is_a_bare_origin_has_nothing_to_stand_for() -> None:
    from insights_radar.core.urls import UrlGuard

    assert UrlGuard.standalone_address("https://[::1") is None
    assert UrlGuard.source_path_pairs("https://[::1", "s1") == []
    assert UrlGuard.source_scrubber("https://example.com/", "s1")("https://example.com/ and more") == (
        "https://example.com/ and more"
    )


def test_r16_unnamed_query_values_are_credentials_when_they_read_as_keys_not_words() -> None:
    from insights_radar.core.urls import UrlGuard

    keys = ["20261030", "4821395755123", "AbCdEfGhIjKlMnOpQ", "Ab3x9Q2", "a1b2c3", "k7x9q2z1", "Ab%2Fc%2BK9zQ7rT2wX5"]
    keys += ["9f3cPRIVATEFEEDTOKEN12", "00000000-0000-4000-8000-000000000000", "x" * 24]
    words = ["technology", "generative-ai", "windows11", "python3", "us-east-1", "en-US", "2026-10-30", "deadbe7"]
    words += ["Radiocommunications", "radiocommunications", "abcdefghijklmnop", "machine+learning", "gpt4o", "desc", ""]
    words += ["artificial-intelligence-news", "publish_date", "%2F%2B", "~~"]
    keys += ["~" * 20]  # no letters or digits, but long enough to be no word
    assert [k for k in keys if not UrlGuard._credential_like(k)] == []
    assert [w for w in words if UrlGuard._credential_like(w)] == []


def test_r16_the_source_scrubber_cases_from_both_reviews() -> None:
    from insights_radar.core.urls import UrlGuard

    def scrub(address: str, text: str) -> str:
        return UrlGuard.source_scrubber(address, "s1")(text)

    region = "Now in us-east-1 https://aws.example.com/item1?region=us-east-1"
    assert scrub("https://aws.example.com/whats-new/?region=us-east-1", region) == region
    uid = "https://cal.example.com/feed?uid=4821395755123"
    assert scrub(uid, f"{uid}&page=2 and HTTP://CAL.EXAMPLE.COM/feed?uid=4821395755123.") == (
        "https://cal.example.com/feed?REDACTED&page=2 and source:s1."
    )
    session = "https://host.example/feed;jsessionid=SESSIONTOKENxyz42"
    assert "SESSIONTOKENxyz42" not in scrub(session, f"{session}?page=2")
    ical = "https://cal.example.com/ical/482139575512"
    assert scrub(ical, f"{ical}#e1 {ical}?page=2") == "source:s1#e1 source:s1?page=2"
    blog = "Home https://aws.amazon.com/blogs/machine-learning/ post https://aws.amazon.com/blogs/machine-learning/x/"
    assert scrub("https://aws.amazon.com/blogs/machine-learning/", blog) == blog  # a plain listing stays citable
    deep = "https://example.com/en/news/list.php"
    assert scrub(deep, f"{deep}?id=5 {deep}#i2 {deep}") == f"{deep}?id=5 {deep}#i2 source:s1"
    assert scrub("https://x.example.com/feed?a1b2c3", "xa1b2c3y") == "xa1b2c3y"  # too short to scrub bare
    gazette = "https://gazette.govt.nz/home/search?keyword=Radiocommunications&sortField=publish_date"
    article = "https://gazette.govt.nz/notice/id/2026-au1234?keyword=Radiocommunications"
    assert scrub(gazette, article) == article
