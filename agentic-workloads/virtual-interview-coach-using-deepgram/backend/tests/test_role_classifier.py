"""Unit tests for the deterministic JD -> role_family classifier (no DB, no LLM).

The classifier keys MUST match the bank taxonomy's role_family values, or generated+embedded domain
rows are unreachable at selection. These tests pin the routing for every classifier-backed family and
guard against keyword cross-talk (e.g. a SWE JD mentioning "reconciliation" must not classify as
finance), and confirm an unmapped role degrades to None (General-only fallback, FR-222).
"""

from __future__ import annotations

from src.prep.blueprint import classify_role_family as classify


def test_software_engineering():
    assert classify(
        "Backend Software Engineer",
        "Build and debug production services in Python; design APIs and microservices.",
    ) == "software_engineering"


def test_data_analytics():
    assert classify(
        "Data Analyst",
        "SQL, dashboards, business intelligence, ETL, reporting and analytics.",
    ) == "data_analytics"


def test_product_management():
    assert classify(
        "Product Manager",
        "Own the product roadmap, prioritization, stakeholder alignment, user research, go-to-market.",
    ) == "product_management"


def test_finance():
    assert classify(
        "Financial Analyst",
        "FP&A, budgeting, forecasting, variance analysis, financial reporting under GAAP.",
    ) == "finance"


def test_sales():
    assert classify(
        "Account Executive",
        "Manage a sales pipeline: prospecting, objection handling, closing deals against a revenue target with CRM.",
    ) == "sales"


def test_unmapped_role_returns_none():
    # Not in the classifier -> None so the blueprint falls back to General-only (domain_coverage_reduced).
    assert classify(
        "Registered Nurse",
        "Patient triage, bedside care, and medication administration on a busy ward.",
    ) is None


def test_cross_talk_swe_jd_with_reconciliation_stays_swe():
    # "reconciliation" is a finance keyword, but a SWE JD that mentions a payments-reconciliation
    # pipeline must still classify as software_engineering (more SWE keyword hits win).
    assert classify(
        "Backend Software Engineer",
        "Own event-driven payments reconciliation pipelines in Python; debug production services; "
        "design APIs and work with relational databases.",
    ) == "software_engineering"
