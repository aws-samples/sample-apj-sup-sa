"""Job-description URL scraping (mirrors resume parse-back) — OFF the response_gap clock.

A convenience that lets a student paste a job-posting link instead of copy-pasting the text: we
fetch the page, deterministically extract its readable text, then run a Bedrock structured
extraction into {job_title, job_description} for the SPA to prefill and the student to review/edit.

Privacy/scope (Constitution III): NOTHING is persisted here. The returned text is handed back to the
SPA only; the job description persists later at session-create, under consent. The raw page text and
the URL are never logged.

SSRF hardening: this endpoint fetches an attacker-controllable URL on the server. We therefore only
allow http(s), resolve the host and reject any loopback/private/link-local/reserved/metadata address,
follow redirects manually (re-validating every hop), and cap the response size, timeout, and redirect
count. (DNS-rebinding between our check and httpx's own resolve is a residual risk accepted for this
demo; the guard still blocks the common SSRF targets and all redirect-based pivots.)
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx

from .config import settings

log = logging.getLogger("backend")

ScrapeStatus = Literal["scraped", "partial"]

_MAX_BYTES = 2 * 1024 * 1024          # cap the downloaded page (job pages are small)
_TIMEOUT_S = 8.0                       # off the gap clock, but bound the wait
_MAX_REDIRECTS = 4
_TEXT_LIMIT = 20000                    # chars fed to the model (mirror resume_parse cap)
_FALLBACK_LIMIT = 8000                 # raw-text fallback length when the model is unavailable

_USER_AGENT = "InterviewCoachBot/1.0 (+job-description-import)"

_EXTRACTION_INSTRUCTION = (
    "You extract a clean job posting from the visible text of a careers/job page for an "
    "interview-prep tool. Return ONLY valid JSON, with no prose:\n"
    "{\n"
    '  "job_title": string|null,\n'
    '  "job_description": string\n'
    "}\n"
    "job_description: the role's responsibilities, requirements, and qualifications as readable "
    "plain text. Drop navigation, ads, cookie banners, and site boilerplate. Keep it faithful — do "
    "not invent. If the text is clearly NOT a job posting, set job_description to an empty string."
)


class JobScrapeError(Exception):
    """A JD URL could not be fetched/extracted; surfaced to the SPA as a 422 with a friendly message
    so the student can paste the description manually instead. The message is safe to show."""


@dataclass
class ScrapeResult:
    job_title: str | None
    job_description: str
    scrape_status: ScrapeStatus


def _assert_public_url(url: str) -> tuple[str, str]:
    """Validate scheme + reject any non-public resolved address (SSRF guard). Returns (url, host)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise JobScrapeError("Enter a job posting link starting with http:// or https://")
    host = parsed.hostname
    if not host:
        raise JobScrapeError("That doesn't look like a valid link.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise JobScrapeError("We couldn't reach that link.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise JobScrapeError("That link points to a non-public address.")
    return url, host


def _fetch(url: str) -> str:
    """Fetch the page as text, following redirects manually so each hop is re-validated (SSRF)."""
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/*;q=0.8"}
    current = url
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=False, headers=headers) as client:
            for _ in range(_MAX_REDIRECTS):
                _assert_public_url(current)
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            raise JobScrapeError("We couldn't reach that link.")
                        current = urljoin(current, loc)
                        continue
                    resp.raise_for_status()
                    ctype = resp.headers.get("content-type", "").lower()
                    if "html" not in ctype and "text" not in ctype:
                        raise JobScrapeError("That link isn't a readable web page.")
                    total = 0
                    chunks: list[bytes] = []
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= _MAX_BYTES:
                            break
                    match = re.search(r"charset=([\w-]+)", ctype)
                    enc = match.group(1) if match else "utf-8"
                    return b"".join(chunks)[:_MAX_BYTES].decode(enc, errors="replace")
    except JobScrapeError:
        raise
    except httpx.HTTPError as exc:
        # Type only — never log the URL.
        log.info("JD fetch failed (%s)", type(exc).__name__)
        raise JobScrapeError("We couldn't reach that link.") from exc
    raise JobScrapeError("That link redirected too many times.")


class _TextExtractor(HTMLParser):
    """Minimal stdlib HTML -> readable text. Drops script/style/etc.; block tags become line breaks.
    Stdlib-only so the backend image needs no extra HTML-parsing dependency."""

    _SKIP = {"script", "style", "noscript", "head", "svg", "template", "iframe"}
    _BLOCK = {
        "p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "ul", "ol", "table", "header", "footer", "nav",
    }

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:  # noqa: ARG002
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list) -> None:  # noqa: ARG002
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _coerce(raw: str) -> dict:
    """Parse the model's JSON, tolerating a ```json code fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return json.loads(text)


def _structured_extract(page_text: str, url: str) -> ScrapeResult:  # noqa: ARG001 - url kept for symmetry
    """Bedrock structured extraction (setup window). On any failure, degrade to a 'partial' result
    that returns the cleaned raw page text so the student still gets a usable prefill (never blocks)."""
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        resp = client.converse(
            modelId=settings.bedrock_model_id,
            system=[{"text": _EXTRACTION_INSTRUCTION}],
            messages=[{"role": "user", "content": [{"text": page_text[:_TEXT_LIMIT]}]}],
            inferenceConfig={"maxTokens": 1200, "temperature": 0.0},
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        data = _coerce(raw)
    except Exception as exc:  # noqa: BLE001 - degrade to raw-text fallback; never surface internals
        log.warning("JD structured-extraction failed (%s); returning raw page text", type(exc).__name__)
        return ScrapeResult(
            job_title=None,
            job_description=page_text[:_FALLBACK_LIMIT].strip(),
            scrape_status="partial",
        )

    title = (data.get("job_title") or None) if isinstance(data, dict) else None
    if isinstance(title, str):
        title = title.strip() or None
    desc = ((data.get("job_description") if isinstance(data, dict) else "") or "").strip()
    if not desc:
        return ScrapeResult(
            job_title=title,
            job_description=page_text[:_FALLBACK_LIMIT].strip(),
            scrape_status="partial",
        )
    return ScrapeResult(job_title=title, job_description=desc, scrape_status="scraped")


def scrape_job_description(url: str) -> ScrapeResult:
    """Full off-gap-clock scrape: SSRF-guarded fetch -> text extract -> structured extraction.

    Raises JobScrapeError (safe message) when the URL can't be fetched or has no readable text, so
    the caller returns a 422 and the SPA falls back to manual paste.
    """
    url = (url or "").strip()
    if not url:
        raise JobScrapeError("Enter a job posting link.")
    # Tolerate a pasted bare domain (e.g. "company.com/careers"): default to https.
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", url):
        url = "https://" + url

    html = _fetch(url)
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text()
    if len(text) < 50:
        raise JobScrapeError(
            "We couldn't find readable text at that link. Please paste the description instead."
        )
    return _structured_extract(text, url)
