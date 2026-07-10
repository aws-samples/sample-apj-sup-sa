"""Unit tests for job-description URL scraping (no DB, no network, no LLM).

Two things matter most here: the SSRF guard (this endpoint fetches an attacker-controllable URL on
the server, so loopback/private/link-local/metadata and non-http(s) targets MUST be rejected) and the
stdlib HTML->text extraction (script/style stripped, block tags become line breaks). The Bedrock call
itself is out of scope (it degrades to a raw-text 'partial' fallback, exercised separately).
"""

from __future__ import annotations

import pytest

from src.jd_scrape import (
    JobScrapeError,
    ScrapeResult,
    _assert_public_url,
    _structured_extract,
    _TextExtractor,
    scrape_job_description,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
    ],
)
def test_ssrf_blocks_non_public_addresses(url):
    with pytest.raises(JobScrapeError):
        _assert_public_url(url)


@pytest.mark.parametrize("url", ["ftp://example.com/", "file:///etc/passwd", "gopher://x/"])
def test_ssrf_blocks_non_http_schemes(url):
    with pytest.raises(JobScrapeError):
        _assert_public_url(url)


def test_ssrf_allows_public_host():
    # example.com resolves to a public address; scheme + address check must pass.
    url, host = _assert_public_url("https://example.com/careers/role")
    assert host == "example.com"


def test_html_extractor_strips_scripts_and_keeps_text():
    p = _TextExtractor()
    p.feed(
        "<html><head><style>.x{color:red}</style></head><body>"
        "<nav>Home</nav><h1>Backend Engineer</h1>"
        "<p>Build   reliable    services.</p>"
        "<script>steal()</script>"
        "<ul><li>Python</li><li>SQL</li></ul></body></html>"
    )
    text = p.text()
    assert "steal()" not in text
    assert "color:red" not in text
    assert "Backend Engineer" in text
    assert "Build reliable services." in text  # whitespace collapsed
    assert "Python" in text and "SQL" in text


def test_short_or_empty_url_rejected():
    with pytest.raises(JobScrapeError):
        scrape_job_description("")


def test_structured_extract_degrades_to_partial_without_bedrock():
    # No AWS creds/Bedrock in unit env -> the call fails and we return cleaned raw text, never raising.
    text = (
        "We are hiring a Backend Engineer. Responsibilities: build services. "
        "Requirements: Python, SQL, and 3+ years writing tested code."
    )
    result = _structured_extract(text, "https://example.com/x")
    assert isinstance(result, ScrapeResult)
    assert result.scrape_status == "partial"
    assert "Backend Engineer" in result.job_description
