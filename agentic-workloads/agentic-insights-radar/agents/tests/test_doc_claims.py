# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The customer documents quote the runtime's limits word for word. These tests keep them in step
with the code, which is what a reader (and a security reviewer) decides from."""

from __future__ import annotations

from pathlib import Path

from insights_radar_agents import tool_policy
from insights_radar_agents.module_runtime import STATE_CHECK_SECONDS

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
GUIDE = (ROOT / "docs/implementation-guide.md").read_text(encoding="utf-8")
THREAT = (ROOT / "docs/security/threat-model.md").read_text(encoding="utf-8")
# The order the documents list the caps in.
CAP_ORDER = ("web_search", "browser", "code_interpreter", "mcp", "web_fetch")


def test_p2003_the_documented_per_run_tool_caps_match_the_policy() -> None:
    assert set(tool_policy.CAPS) == set(CAP_ORDER)  # a new runtime-side grant needs a documented cap
    caps = [str(tool_policy.CAPS[grant]) for grant in CAP_ORDER]
    assert "(" + ", ".join(caps) + ")" in THREAT
    assert ", ".join(caps[:-1]) + f" and {caps[-1]} calls" in GUIDE
    assert "`http_fetch`" in GUIDE and "`http_fetch`" in THREAT


def test_p2003_every_tool_the_runtime_builds_is_gated_somewhere() -> None:
    # http_fetch is a runtime-side tool, so it must not be mistaken for one the backend gates.
    assert "http_fetch" not in tool_policy.BACKEND_TOOLS
    assert "kg_save_output" in tool_policy.BACKEND_TOOLS


def test_p2002_the_docs_do_not_claim_the_kill_switch_stops_a_running_agent_at_once() -> None:
    assert STATE_CHECK_SECONDS == 60.0
    for text in (README, GUIDE, THREAT):
        assert "stops every model call at once" not in text
        assert "stops all agent work at once" not in text
    assert "within a minute" in README
    assert "at most once a minute" in GUIDE and "at most once a minute" in THREAT


def test_p2014_the_threat_model_describes_the_grants_the_code_enforces() -> None:
    assert "they also hold outbound tools" not in THREAT  # the pre-r1 trifecta claim
    assert "Gaps, Content ideas and Content drafts read product knowledge and hold no outbound grant" in THREAT
    # Derived from the built-ins, not written out: CR2-03 removed Trends' web_search and the pinned
    # sentence kept claiming it was outbound (A3-026).
    from insights_radar.modules.model import OUTBOUND_GRANTS, ToolGrant
    from insights_radar.modules.templates import builtin_modules

    outbound = [m.name for m in builtin_modules() if m.grants & OUTBOUND_GRANTS]
    private = [m for m in builtin_modules() if ToolGrant.PRODUCT_KNOWLEDGE in m.grants]
    assert not [m.name for m in private if m.grants & OUTBOUND_GRANTS]
    assert f"{' and '.join(outbound)} hold the outbound grants and never read product knowledge" in THREAT
    assert "`private_writer`" in THREAT
    assert "Exfiltration through a module that holds an outbound grant" in THREAT


def test_p2061_the_gaps_prompt_does_not_offer_a_tool_the_module_lacks() -> None:
    """Gaps holds product_knowledge, so it holds no outbound grant at all (the validator refuses the
    combination). Its workflow used to say "decide with product knowledge, the web and the graph",
    which made the model try an unavailable tool or assert web verification it never did (P2-061)."""
    from importlib import resources

    gap = resources.files("insights_radar.modules").joinpath("prompt_templates", "gap.md").read_text("utf-8")
    assert "the web and the graph" not in gap
    assert "no web access" in gap


def test_p2022_the_threat_model_does_not_claim_every_browser_address_is_checked() -> None:
    """Only the `url` of a navigate the *model* asks for is checked; a click, history navigation or a
    redirect the page starts is not (P2-022)."""
    assert "Every address the AgentCore Browser is sent to passes `UrlGuard`" not in THREAT
    assert "Every address the *model* sends the AgentCore Browser to passes" in THREAT
    assert "is not logged as a navigation" in THREAT


def test_p2027_the_matrix_says_how_the_runtime_reaches_the_backend() -> None:
    matrix = (ROOT / "docs/security/security-matrix.md").read_text(encoding="utf-8")
    assert "SigV4 to the `AgentTools` Lambda function URL" in matrix
    assert "HTTPS; SigV4 to the API |" not in matrix


def test_p2035_the_threat_model_says_chat_history_isolation_is_code_only() -> None:
    """IAM cannot narrow AgentCore Memory to one actor, so the chat role can read every user's
    conversations and only the code keeps them apart (P2-035). A reviewer must know that."""
    assert "Per-user chat history is isolated in code, not by IAM" in THREAT
    assert "no condition key for the actor" in THREAT


def test_p2046_the_cost_estimate_counts_the_modules_that_really_search() -> None:
    """After the r1 grant changes only Scout, Signals and Trends hold web_search, so the estimate's
    "5 modules with web search x 120 runs x 10 queries = 6,000" was 6,000 queries and USD 42 for a
    deployment that makes 3,600 and spends about 25 (P2-046)."""
    from insights_radar.modules.model import ToolGrant
    from insights_radar.modules.templates import builtin_modules

    cost = (ROOT / "docs/cost-estimate.md").read_text(encoding="utf-8")
    searching = [m for m in builtin_modules() if ToolGrant.WEB_SEARCH in m.grants]
    names = sorted(m.name for m in searching)
    assert names == ["Scout", "Signals"], names
    queries = len(searching) * 120 * 10
    assert f"{len(searching)} modules with the `web_search` grant" in cost
    assert f"{queries:,} queries" in cost
    assert f"{queries:,} tool invocations" in cost
    assert f"{queries * 0.007:.2f}" in cost  # 25.20
    # No built-in holds code_interpreter any more, so that line must not price one.
    assert not [m for m in builtin_modules() if ToolGrant.CODE_INTERPRETER in m.grants]
    assert "no built-in module holds the `code_interpreter` grant" in cost


def test_p2047_the_cost_estimate_prices_the_sensitive_information_filter() -> None:
    """The guardrail gained a sensitive-information policy, which Bedrock bills separately from
    content filters for every output text unit (P2-047)."""
    cost = (ROOT / "docs/cost-estimate.md").read_text(encoding="utf-8")
    assert "Guardrails, sensitive information filter" in cost
    assert "Sensitive information filter: $0.10 per 1,000 text units" in cost
