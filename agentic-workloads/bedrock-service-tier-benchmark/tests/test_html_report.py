"""Tests for HTML report rendering, including XSS-escaping regressions (CWE-79).

The HTML report interpolates model-supplied strings (model ids, served-tier
labels, error messages) and run metadata. A malicious or malformed model id
must never produce executable markup in the report.
"""

from __future__ import annotations

from bedrock_bench import html_report


def _summary_with(display_name: str, model_id: str, served: str, error: str) -> dict:
    """Build a minimal summary.json-shaped dict with attacker-controlled strings."""
    metric = {
        "n": 1,
        "mean": 0.5,
        "min": 0.5,
        "max": 0.5,
        "p20": 0.5,
        "p50": 0.5,
        "p90": 0.5,
    }
    cell = {
        "label": "x",
        "family": "Fam",
        "model_key": "k",
        "display_name": display_name,
        "transport": "invoke",
        "tier": "flex",
        "region": "us-east-1",
        "model_id": model_id,
        "requested": 1,
        "succeeded": 1,
        "failed": 1,
        "served_tiers": {served: 1},
        "errors": {error: 1},
        "ttft": metric,
        "total_latency": metric,
    }
    return {
        "run_id": "run-test",
        "meta": {
            "account_id": "123",
            "started": "2026-01-01T00:00:00Z",
            "finished": "2026-01-01T00:01:00Z",
            "version": "0.2.0",
        },
        "config": {
            "profile": "p",
            "regions": ["us-east-1"],
            "n_requests": 1,
            "interval_seconds": 1,
            "max_tokens": 10,
            "prompt": "hi",
        },
        "cells": [cell],
    }


def test_malicious_strings_are_escaped():
    payload = "<script>alert(1)</script>"
    html = html_report.render(_summary_with(payload, payload, payload, payload))
    # The raw script tag must NOT appear; the escaped form must.
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_quote_injection_escaped():
    html = html_report.render(_summary_with('a"b', "m'id", "fl<ex", "err&or"))
    # No unescaped angle bracket from the served-tier value leaks through.
    assert "fl<ex" not in html
    assert "fl&lt;ex" in html


def test_reports_p20_p50_p90_and_no_p99():
    html = html_report.render(_summary_with("Model", "m", "flex", "e"))
    # The benchmark reports p20/p50/p90; p99 is not produced anywhere.
    assert ">p20<" in html and ">p50<" in html and ">p90<" in html
    assert "p99" not in html


def test_priority_and_flex_columns_present():
    html = html_report.render(_summary_with("Model", "m", "flex", "e"))
    assert "Flex" in html and "Priority" in html
    assert "Δp50" in html


def test_self_contained_no_external_refs():
    html = html_report.render(_summary_with("Model", "m", "flex", "e"))
    assert "http://" not in html and "https://" not in html


def test_accessibility_landmarks_and_scopes():
    html = html_report.render(_summary_with("Model", "m", "flex", "e"))
    # Language declared, landmarks present, and a skip link for keyboard users.
    assert '<html lang="en"' in html
    assert "<main>" in html and "</main>" in html
    assert 'class="skip-link"' in html
    # Data tables associate headers with cells (WCAG 1.3.1).
    assert 'scope="col"' in html
    assert 'scope="row"' in html
