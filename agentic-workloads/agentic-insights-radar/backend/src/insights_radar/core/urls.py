# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""SSRF guard for every URL the platform or an agent will fetch (ported from the internal radar).

Rejects: non-https schemes, embedded credentials, control characters (log injection),
``localhost``/metadata hostnames, and IP literals in loopback, link-local, private,
reserved, unspecified or multicast ranges, including the decimal/hex/octal/short IPv4
encodings the OS resolver expands (``2852039166``, ``0x7f000001``, ``0177.0.0.1``,
``127.1``). DNS names are allowed: the fetch layer additionally checks the resolved address
before connecting (``SafeResolver``), which closes DNS-rebinding to internal ranges.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from collections.abc import Callable
from typing import ClassVar
from urllib.parse import ParseResult, quote, quote_plus, unquote_plus, urlparse, urlunparse

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


# WHATWG "ends in a number": such hosts must parse as IPv4 or the URL is invalid.
_ENDS_IN_NUMBER = re.compile(r"(?:^|\.)(?:\d+|0x[0-9a-f]*)$")


_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")  # well-known prefix: the last 32 bits are an IPv4 address


# Query parameters whose value is a credential: redacted before a URL is shown to a model. Matched on the
# whole name (case-insensitive, "-" read as "_"), never a substring, so author=, keywords= or signal= keep
# their values and a feed is crawled with the query it was configured with.
SECRET_PARAMS = frozenset(
    {
        "key",
        "apikey",
        "api_key",
        "x_api_key",
        "app_key",
        "appkey",
        "access_key",
        "private_key",
        "subscription_key",
        "hapikey",
        "token",
        "access_token",
        "accesstoken",
        "auth_token",
        "authtoken",
        "id_token",
        "refresh_token",
        "session_token",
        "oauth_token",
        "bearer",
        "secret",
        "client_secret",
        "app_secret",
        "password",
        "passwd",
        "pwd",
        "pass",
        "sig",
        "signature",
        "oauth_signature",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "x_amz_signature",
        "x_amz_credential",
        "x_amz_security_token",
        # Widely used names the first (exact-match) list missed, each a real product's API key:
        "appid",  # OpenWeatherMap and friends
        "code",  # Azure Functions host key
        "api_token",  # generic
        "apitoken",  # the camelCase spelling "apiToken"
        "x_functions_key",  # Azure Functions
        "private_token",  # GitLab
        "personal_token",
        "job_token",  # GitLab CI
        "x_goog_signature",  # Google Cloud Storage signed URLs
        "x_goog_credential",
        "ocp_apim_subscription_key",  # Azure API Management
        "consumer_secret",  # OAuth 1
        "consumer_key",
        "oauth_consumer_secret",
        "token_secret",
        "oauth_token_secret",
        "awsaccesskeyid",  # S3 pre-signed URLs (v2)
        "aws_access_key_id",
        "aws_secret_access_key",
        "secret_key",
        "secret_access_key",
        "api_secret",
        "auth_key",
        "authkey",
        "sas",  # Azure shared access signature
        "sig_token",
    }
)
REDACTED = "REDACTED"
# The shortest value scrubbed out of fetched pages. A credential is long; a value such as ``code=US``
# or ``appid=en`` is not, and scrubbing it replaced every "US" in a fetched page, inside "USD" and
# "STATUS" too, so a scout recorded garbled evidence (CR2-07). The address itself is still redacted
# whatever the value's length: only the page scrub needs the floor.
MIN_SCRUB_LENGTH = 8
# A path this deep is scrubbed whole even with no token-shaped segment: webhook addresses
# (/services/T000/B000/<secret>) put the credential in a plain-looking last segment (B3-016).
PATH_SECRET_SEGMENTS = 3
# A raw query is split on both separators: several servers accept ";", and splitting only on "&" hid
# "api_key=" inside the previous field's value, so it was never redacted.
_QUERY_SEPARATOR = re.compile(r"([&;])")


# After an address with no query of its own, what makes it another page: more of a path, a query or an anchor
# (/news.php?id=5, /news#item-2). A sentence's full stop does not.
_ALONE_NO_QUERY = r"/?(?![\w/~%?#-]|\.[\w/])"
_SECRET_PARAMS_FOLDED = frozenset(p.replace("_", "").replace("-", "") for p in SECRET_PARAMS)


class UrlGuard:
    # https on its standard port, or 8443; any other port would let a source, document or MCP address
    # reach SSH, SMTP or admin services on public hosts.
    ALLOWED_PORTS: ClassVar[frozenset[int]] = frozenset({443, 8443})
    BLOCKED_HOSTS: ClassVar[frozenset[str]] = frozenset(
        {"localhost", "metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal"}
    )
    MAX_LENGTH = 2048

    @staticmethod
    def _ipv4_from_encoded(host: str) -> ipaddress.IPv4Address | None:
        parts = host.split(".")
        if not 1 <= len(parts) <= 4:
            return None
        nums: list[int] = []
        for p in parts:
            if not p:
                return None
            low = p.lower()
            if low.startswith("0x"):
                digits = low[2:]
                if not digits or any(c not in "0123456789abcdef" for c in digits):
                    return None
                n = int(digits, 16)
            elif p.startswith("0") and len(p) > 1:
                if any(c not in "01234567" for c in p):
                    return None
                n = int(p, 8)
            elif p.isdigit():
                n = int(p, 10)
            else:
                return None
            nums.append(n)
        lead, last = nums[:-1], nums[-1]
        if any(x > 255 for x in lead):
            return None
        width = 4 - len(nums) + 1
        if last > 256**width - 1:
            return None
        value = 0
        for x in lead:
            value = value * 256 + x
        value = value * (256**width) + last
        return ipaddress.IPv4Address(value) if 0 <= value <= 0xFFFFFFFF else None

    @classmethod
    def is_blocked_ip(cls, ip: IpAddress) -> bool:
        """Anything not globally routable, including IPv4 carried inside IPv6 (mapped, NAT64, 6to4, Teredo).

        ``is_global`` also excludes shared address space (100.64.0.0/10) and benchmarking ranges that the
        older private/reserved checks let through.
        """
        if isinstance(ip, ipaddress.IPv6Address):
            embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
            if embedded is None and ip in _NAT64:
                embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            if embedded is not None:
                return cls.is_blocked_ip(embedded)
        return bool(not ip.is_global or ip.is_multicast or ip.is_unspecified)

    @classmethod
    def reason(cls, raw: object) -> str | None:
        """``None`` when the URL is safe, else a short human-readable reason."""
        if not isinstance(raw, str) or not raw:
            return "URL is empty"
        if len(raw) > cls.MAX_LENGTH:
            return "URL is too long"
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
            return "URL contains control characters"
        try:
            parsed = urlparse(raw.strip())
        except ValueError:
            return "URL cannot be parsed"
        if parsed.scheme.lower() != "https":
            return "only https URLs are allowed"
        if parsed.username is not None or parsed.password is not None:
            return "URL must not contain credentials"
        try:
            port = parsed.port
        except ValueError:
            return "URL has an invalid port"
        if port is not None and port not in cls.ALLOWED_PORTS:
            return "only ports 443 and 8443 are allowed"
        # Resolvers and browsers treat "host." as "host", so the checks below must too.
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return "URL has no host"
        if host in cls.BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".internal"):
            return "host is not allowed"
        try:
            ip: IpAddress | None = ipaddress.ip_address(host)
        except ValueError:
            ip = cls._ipv4_from_encoded(host)
            if ip is None and _ENDS_IN_NUMBER.search(host):
                return "host looks like an IP address but is not a valid one"
        if ip is not None and cls.is_blocked_ip(ip):
            return "host is a private, loopback, link-local or reserved address"
        return None

    @classmethod
    def is_safe(cls, raw: object) -> bool:
        return cls.reason(raw) is None

    @staticmethod
    def is_secret_param(name: str) -> bool:
        """Whether a query parameter name holds a credential. The name is percent-decoded first, as
        the server decodes it: ``api%5Fkey`` and ``%61pi_key`` are ``api_key`` to the API and must
        be to the redactor too (SR2-07)."""
        # Separators aside, so camelCase spellings (accessKey, clientSecret) match access_key, client_secret.
        folded = unquote_plus(name).strip().lower().replace("-", "").replace("_", "")
        return folded in _SECRET_PARAMS_FOLDED

    @staticmethod
    def _parsed(raw: str) -> ParseResult | None:
        """``raw`` parsed, or None when it cannot be (an unterminated IPv6 bracket, a bad port)."""
        try:
            return urlparse(raw)
        except ValueError:
            return None

    @staticmethod
    def _query_fields(query: str) -> list[str]:
        """``query`` split on ``&`` and ``;``: fields at even indexes, separators at odd ones, so a
        rebuilt query keeps every non-secret value byte for byte."""
        return _QUERY_SEPARATOR.split(query) if query else []

    @classmethod
    def _raw_secret_values(cls, raw: str) -> list[str]:
        parsed = cls._parsed(raw)
        out: list[str] = []
        for index, field in enumerate(cls._query_fields(parsed.query if parsed else "")):
            if index % 2:
                continue
            name, eq, value = field.partition("=")
            if eq and value and cls.is_secret_param(name):
                out.append(value)
        return out

    @staticmethod
    def _secret_shaped(segment: str) -> bool:
        """Whether a path segment looks like a token rather than a word: long, or letters mixed with
        digits (``9f3cPRIVATEFEEDTOKEN``, ``botSECRET8``). A word such as ``services`` or ``rss`` is not
        scrubbed on its own, or every page mentioning it would be corrupted (CR2-07)."""
        if len(segment) >= 20:
            return True
        return (
            len(segment) >= MIN_SCRUB_LENGTH and any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment)
        )

    @classmethod
    def secret_values(cls, raw: str, deep_paths: bool = True, every_query: bool = False) -> list[str]:
        """Every string a fetched page must not echo back to the model, longest first (so a value that
        contains another is scrubbed whole).

        For a credential in the query: the stored (percent-encoded) text and its decoded value, plus the
        two re-encodings a client may echo it as -- a key containing ``+ / =`` (base64, Azure and AWS
        style secrets) is stored encoded, so scrubbing only the decoded value left the key in the fetched
        URL and in echoed pagination links. A value too short to scrub alone (``appid=K7x9Q2a``) is still
        scrubbed as ``name=value`` and as part of the whole stored query.

        A credential can also sit in the path, where no name marks it (``/services/T000/B000/<secret>``,
        ``/feeds/<token>/rss``, ``/bot<token>/getUpdates``): a path with a token-shaped segment, or three
        segments or more, is scrubbed whole, and each token-shaped segment on its own (B3-016).
        """
        parsed = cls._parsed(raw)
        if parsed is None:
            return []
        forms: set[str] = set()

        def add(value: str) -> None:
            decoded = unquote_plus(value)
            forms.update(f for f in (value, decoded, quote(decoded, safe=""), quote_plus(decoded)) if f)

        values = cls._raw_secret_values(raw)
        for value in values:
            if len(unquote_plus(value)) >= MIN_SCRUB_LENGTH:
                add(value)  # too short alone is not scrubbed bare: it would corrupt the page (CR2-07)
        unnamed = False
        if every_query:
            # A source's own address: a value that reads as a credential is one whatever its name (sig=Ab3x9Q2,
            # a bare ?9f3cTOKEN), with its whole field; a word (category=technology) is not, or every page
            # mentioning it, and every article link holding it, would be garbled (CR2-07, code review r15).
            # The query, and ``;`` parameters of the last path segment (;jsessionid=...), which urlparse keeps apart.
            fields = cls._query_fields(parsed.query)[::2] + cls._query_fields(parsed.params)[::2]
            for field in fields:
                name, eq, value = field.partition("=")
                if not cls._credential_like(value if eq else name):  # a bare field is its own value
                    continue
                unnamed = True
                if len(unquote_plus(value if eq else name)) >= MIN_SCRUB_LENGTH:
                    add(value if eq else name)
                elif eq:
                    forms.update({field, unquote_plus(field)})
                else:  # too short to scrub bare (CR2-07): only as a query field
                    forms.update(f"{sep}{form}" for sep in "?&;" for form in (field, unquote_plus(field)))
        for index, field in enumerate(cls._query_fields(parsed.query)):
            name, eq, value = field.partition("=")
            if not index % 2 and eq and value and cls.is_secret_param(name):
                forms.update({field, unquote_plus(field)})
        if (values or unnamed) and len(parsed.query) >= MIN_SCRUB_LENGTH:
            forms.add(parsed.query)
        segments = [seg for seg in parsed.path.split("/") if seg]
        tokens = [seg for seg in segments if cls._secret_shaped(unquote_plus(seg))]
        # ``deep_paths=False``: a deep path alone is not taken for a credential (a listing such as
        # /en/news/press-releases leads to its articles, which must stay fetchable); a token-shaped segment is.
        if tokens or (deep_paths and len(segments) >= PATH_SECRET_SEGMENTS):
            # A short API path such as /v1/rates is no credential, and scrubbing it would garble the
            # fetched API's own documentation; a token-shaped segment or a deep path may be one.
            forms.update(f for f in (parsed.path, parsed.path.rstrip("/")) if len(f) >= MIN_SCRUB_LENGTH)
            for segment in tokens:
                add(segment)
        return sorted(forms, key=len, reverse=True)

    @staticmethod
    def _credential_like(value: str) -> bool:
        """A query value that reads as a key rather than a word, under any name: a PIN or account number (eight
        digits or more), a mixed-case or very long run of letters (AbCdEfGhIjKlMn), a mix of letters and digits that
        is not a plain term (Ab3x9Q2, k7x9q2z1; not windows11, python3 or us-east-1), or twenty characters
        without separators. Scrubbing a word would garble every page that mentions it (CR2-07, reviews r15-r16)."""
        v = unquote_plus(value).rstrip("=")  # base64 padding
        if not v or " " in v:  # a phrase
            return False
        if any(c in v for c in "-_.,:"):  # a term, slug, region or date, unless a long key with dashes (a UUID)
            return len(v) >= 32 and sum(c.isdigit() for c in v) >= 4
        v = v.replace("/", "").replace("+", "")  # base64 (Azure-, AWS-style secrets)
        if not v:
            return False
        digits = sum(c.isdigit() for c in v)
        letters = sum(c.isalpha() for c in v)
        # Mixed case beyond a capitalised word (Radiocommunications is a word, AbCdEfGh is not).
        mixed_case = any(c.isupper() for c in v[1:]) and any(c.islower() for c in v)
        if digits == len(v):
            return len(v) >= 8
        if letters == len(v):
            return len(v) >= 24 or (len(v) >= 12 and mixed_case)
        if digits and letters:
            return len(v) >= 20 or (len(v) >= 6 and (mixed_case or digits >= 3))
        return len(v) >= 20

    @classmethod
    def standalone_address(cls, raw: str) -> re.Pattern[str] | None:
        """The stored address wherever it stands for the source page itself, for a source fetch or the watcher's
        inbox to name the source instead: the guard does not know every credential name, nor every secret path
        (/ical/482139575512, /services/T000/B000/<letters>), and a feed's self link is the stored address.

        Never as the start of another page: a longer path, or a query that adds fields (?section=news&article=5),
        is an article, which must stay fetchable. With no query of its own, an address followed by ``?`` or ``#``
        is another page too (/news.php?id=5, /news#item-2); with one, ``#`` is only an anchor on the source page.
        None for a bare origin (code review r15)."""
        parsed = cls._parsed(raw)
        if parsed is None:
            return None
        long_number = any(seg.isdigit() and len(seg) >= 10 for seg in parsed.path.split("/"))
        if not (parsed.query or parsed.params or long_number or cls.secret_values(raw)):
            # Nothing to hide: a plain listing stays an address the Scout can cite (code review r16).
            return None
        # Any case, http or https, and a sentence's full stop after it still counts as standing alone.
        address = re.escape(raw.rstrip("/")).replace(r"https://", r"https?://", 1)
        if parsed.query or long_number:
            # The source page itself, or an anchor on it (a numeric secret path's ?page=2 and #e1 are its own).
            end = r"/?(?![\w/~%&;=-]|\.[\w/])" if parsed.query else r"/?(?![\w/~%-]|\.[\w/])"
            return re.compile(address + end, re.IGNORECASE)
        return re.compile(address + _ALONE_NO_QUERY, re.IGNORECASE)

    @classmethod
    def source_path_pairs(cls, raw: str, source_id: str) -> list[tuple[str | re.Pattern[str], str]]:
        """A deep path with no token in it, which may still hold a secret no shape gives away
        (/services/T000/B000/<letters>), with its stand-in: hidden where it stands alone, so the articles under
        it, and those addressed by query or anchor (list.php?id=5), stay fetchable. A path with a token needs no
        pair: its token and the whole path are among the secrets (secret_values)."""
        parsed = cls._parsed(raw)
        if parsed is None:
            return []
        origin = f"{parsed.scheme}://{parsed.netloc}"
        bare = origin + parsed.path  # the path, query aside
        if cls.secret_values(bare, deep_paths=False) or not cls.secret_values(bare):
            return []
        pattern = re.compile(re.escape(origin + parsed.path.rstrip("/")) + _ALONE_NO_QUERY)
        return [(pattern, f"{origin}/[source {source_id} path]")]

    @classmethod
    def source_scrubber(cls, raw: str, source_id: str) -> Callable[[str], str]:
        """One rule for everything a source's fetched pages hand the model, in the agents' http_fetch and the
        watcher's inbox (source_list): the stored address where it stands for the source page becomes
        ``source:<id>`` (first, so a credential the guard knows by name does not turn it into an address that
        cannot be fetched), then every form of its credentials becomes REDACTED, then a deep path the source's
        path stand-in."""
        whole = cls.standalone_address(raw)
        secrets = cls.secret_values(raw, deep_paths=False, every_query=True)
        paths = cls.source_path_pairs(raw, source_id)
        name = f"source:{source_id}"

        def clean(text: str) -> str:
            if whole is not None:
                text = whole.sub(name, text)
            for secret in secrets:
                text = text.replace(secret, REDACTED)
            for pattern, stand_in in paths:
                text = (
                    pattern.sub(stand_in, text) if isinstance(pattern, re.Pattern) else text.replace(pattern, stand_in)
                )
            return text

        return clean

    @classmethod
    def redact_secrets(cls, raw: str) -> tuple[str, bool]:
        """``raw`` with the values of credential-like query parameters (``api_key``, ``token``, ...)
        replaced, and whether anything was replaced. What a model reads can be exfiltrated by any
        prompt injection on a page it fetches, so a key in a source address must never reach it."""
        parsed = cls._parsed(raw)
        if parsed is None:
            return REDACTED, True
        fields = cls._query_fields(parsed.query)
        changed = False
        for index, field in enumerate(fields):
            if index % 2:
                continue
            name, eq, value = field.partition("=")
            if eq and value and cls.is_secret_param(name):
                fields[index] = f"{name}={REDACTED}"
                changed = True
        if not changed:
            return raw, False
        return urlunparse(parsed._replace(query="".join(fields))), True

    # Public suffixes under which one registrant owns the next label, for ``site``. Not the whole
    # public suffix list: the hosting platforms and the common second-level registries spelled out,
    # plus the generic rule below for country codes, which is what a forged "second site" under one
    # registrant (a.attacker.co.uk, b.attacker.co.uk) would need.
    MULTI_LABEL_SUFFIXES: ClassVar[frozenset[str]] = frozenset(
        {
            "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk",
            "com.au", "net.au", "org.au", "edu.au", "gov.au",
            "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp",
            "co.nz", "org.nz", "co.za", "org.za", "co.in", "org.in", "co.kr", "or.kr",
            "com.br", "com.cn", "com.hk", "com.sg", "com.tw", "com.mx", "com.tr", "com.ar",
            "github.io", "gitlab.io", "pages.dev", "netlify.app", "vercel.app", "herokuapp.com",
            "azurewebsites.net", "cloudfront.net", "s3.amazonaws.com", "blogspot.com", "appspot.com",
        }
    )  # fmt: skip
    # Second-level labels that country registries hand out as a public suffix (com.my, co.id, com.vn,
    # co.th, com.ph, net.my, gov.sg, ...). Under a two-letter country code these are a suffix, not a
    # registrant: thestar.com.my and malaymail.com.my used to collapse to one "site", com.my, so real
    # two-site corroboration never counted for those markets (B3-007).
    GENERIC_SECOND_LEVEL: ClassVar[frozenset[str]] = frozenset(
        {
            "ac", "biz", "co", "com", "edu", "go", "gob", "gov", "gv", "info", "ltd", "me", "mil", "ne", "net",
            "nic", "nom", "or", "org", "plc", "sch", "web",
        }
    )  # fmt: skip

    @classmethod
    def site(cls, raw: str) -> str:
        """The registrable domain of ``raw``'s host (``www.reuters.com`` -> ``reuters.com``,
        ``a.attacker.co.uk`` -> ``attacker.co.uk``). Corroboration counts sites, and the full host
        let one registrant pose as two (SR2-02). An IP literal is its network (``ip:203.0.113.0/24``,
        a /48 for IPv6): two addresses one owner holds are one site, not two (B3-008)."""
        parsed = cls._parsed(raw)
        host = (parsed.hostname or "").rstrip(".") if parsed else ""
        if not host:
            return REDACTED
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            prefix = 24 if address.version == 4 else 48
            return f"ip:{ipaddress.ip_network(f'{address}/{prefix}', strict=False)}"
        labels = host.split(".")
        country_sld = len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in cls.GENERIC_SECOND_LEVEL
        keep = 3 if country_sld or ".".join(labels[-2:]) in cls.MULTI_LABEL_SUFFIXES else 2
        if ".".join(labels[-3:]) in cls.MULTI_LABEL_SUFFIXES:
            keep = 4
        return ".".join(labels[-keep:])

    @classmethod
    def audit_form(cls, raw: str) -> str:
        """What an audit row records of a stored address: its origin and a short hash of the whole
        address. ``redact_secrets`` covers the query only, so a Slack-style webhook
        (``/services/T000/B000/<secret>``) went to the audit table whole, for 400 days (SR2-08). The
        hash still tells two addresses apart, and confirms which one was meant."""
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        return f"{cls.origin(raw)} (sha256:{digest})"

    @classmethod
    def origin(cls, raw: str) -> str:
        """``https://<host>`` for ``raw``: all a model is ever told about a stored address.

        A credential can sit in the path as easily as in the query (``/feeds/<token>/rss``,
        ``/services/T000/B000/<secret>``, ``/bot<token>/getUpdates``), and no name-based rule can
        spot it, so neither the path nor the query is shown. A run fetches a source by its id.
        """
        parsed = cls._parsed(raw)
        host = parsed.hostname if parsed else None
        return f"https://{host}" if host else REDACTED


class SafeResolver:
    """Resolve a host and refuse if ANY resolved address is internal (DNS-rebinding guard)."""

    def __init__(self, resolver: object | None = None) -> None:
        self._getaddrinfo = getattr(resolver, "getaddrinfo", socket.getaddrinfo)

    def check(self, host: str, port: int = 443) -> list[str]:
        try:
            infos = self._getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as err:
            raise ValueError(f"cannot resolve {host}") from err
        addresses = sorted({str(info[4][0]) for info in infos})
        if not addresses:
            raise ValueError(f"cannot resolve {host}")
        for addr in addresses:
            if UrlGuard.is_blocked_ip(ipaddress.ip_address(addr.split("%")[0])):
                raise ValueError(f"{host} resolves to a non-public address")
        return addresses
