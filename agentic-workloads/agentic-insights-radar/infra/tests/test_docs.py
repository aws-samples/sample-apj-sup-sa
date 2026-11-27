# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The customer documents quote the code: suppressions word for word, and the numbers that change
decisions (alarms, functions, throttles). These tests keep them from drifting."""

from __future__ import annotations

import json
import re
from pathlib import Path

from insights_radar_infra import backend, backups, config, nag, scanners

from .conftest import Synth

ROOT = Path(__file__).resolve().parents[2]
MATRIX = (ROOT / "docs/security/security-matrix.md").read_text()
README = (ROOT / "README.md").read_text()
GUIDE = (ROOT / "docs/implementation-guide.md").read_text()


def _md(text: str) -> str:
    """A string as it appears inside a Markdown table cell."""
    return text.replace("|", "\\|")


def iam5_rows() -> list[str]:
    rows = []
    for reason, applies in nag.IAM5:
        patterns = ", ".join(f"`{_md(a.regex)}`" for a in applies)
        rows.append(f"| AwsSolutions-IAM5 | {patterns} | {reason} |")
    return rows


def other_rows() -> list[str]:
    """Each non-IAM5 suppression is on one resource; the matrix names that resource (P2-010)."""
    return [
        f"| {rule} | `{path}` | {_md(reason)} |"
        for rule, path, reason in [
            *nag.BY_PATH,
            *nag.DIGEST_BY_PATH,
            *nag.DIGEST_SECRETS,
        ]
    ]


def helper_rows() -> list[str]:
    return [
        f"| {rule} | {reason} |"
        for rule, reason in nag.CDK_DEPLOYMENT + nag.CDK_SDK_CALLS
    ]


def scanner_rows() -> list[tuple[str, str, str]]:
    return [
        (tool, rule, reason)
        for group in (
            scanners.LAMBDA,
            scanners.SYNCHRONOUS,
            scanners.SQS_DRIVEN,
            scanners.CDK_HELPER,
            scanners.POLICY,
            scanners.HMAC_KEY,
            scanners.DISTRIBUTION,
            scanners.LOG_BUCKET,
        )
        for tool, rule, reason in group
    ]


def test_the_matrix_quotes_every_cdk_nag_suppression_exactly() -> None:
    for row in [*iam5_rows(), *other_rows(), *helper_rows()]:
        assert row in MATRIX, row
    quoted = len(re.findall(r"^\| AwsSolutions-IAM5 \| `/", MATRIX, flags=re.MULTILINE))
    assert quoted == len(nag.IAM5)  # no row for a suppression the code no longer has
    assert "Stack-wide suppressions for other rules" not in MATRIX


def test_the_matrix_quotes_every_scanner_skip_exactly() -> None:
    for tool, rule, reason in scanner_rows():
        assert re.search(rf"\| {tool} \| {rule} \| {re.escape(reason)} \|", MATRIX), (
            tool,
            rule,
        )
    assert (
        "Site bucket (WebSite)" not in MATRIX
    )  # the site bucket is versioned now: no skip


def test_the_matrix_names_the_backup_suppression() -> None:
    for name in backups.BACKUP_POLICIES:
        assert name in MATRIX


def test_p2015_the_readme_says_autopilot_needs_an_opt_in_for_agent_proposals() -> None:
    step, faq = (
        README.split("**Review insights.**")[0],
        README.split("only propose them")[1],
    )
    assert "Apply agent proposals without review" in step  # Running the sample, step 5
    assert (
        "Apply agent proposals without review" in faq[:400]
    )  # and the injection FAQ answer
    assert "auto_apply_agent" in GUIDE


def test_p2001_the_cleanup_docs_say_the_data_key_outlives_the_backup_vault() -> None:
    for text in (README, GUIDE):
        assert "--delete-backups" in text
        assert "recovery points are encrypted with this key" in text
    assert "Scheduling that key for deletion makes" in GUIDE


def test_documented_alarm_and_function_counts_match_the_stack(default: Synth) -> None:
    alarms = len(default.props("AWS::CloudWatch::Alarm"))
    functions = sum(
        1 for k in default.resources("AWS::Lambda::Function") if k.startswith("Backend")
    )
    assert f"{alarms} CloudWatch alarms" in README
    assert f"creates {alarms} alarms" in GUIDE
    assert (
        f"{alarms} alarms" in MATRIX and f"AWS Lambda ({functions} functions)" in MATRIX
    )
    assert f"each of the {functions} functions" in GUIDE


def test_documented_throttles_match_the_stack() -> None:
    rate, burst = backend.MODEL_ROUTE_RATE, backend.MODEL_ROUTE_BURST
    assert f"{rate} requests/s (burst {burst})" in GUIDE
    assert f"{rate} per second (burst {burst})" in README
    assert (
        "POST /agent/{proxy+}" not in GUIDE + README + MATRIX
    )  # the agent routes left the HTTP API


def test_sr10_the_validation_role_is_trusted_for_the_protected_environment_only() -> (
    None
):
    workflow = (ROOT / ".github/workflows/deploy-validate.yml").read_text()
    assert "sub repo:<owner>/<repo>:environment:sandbox" in workflow
    assert "required reviewers" in workflow
    assert re.search(r"^\s+environment: sandbox$", workflow, flags=re.MULTILINE)
    assert (
        "(trust policy: aud sts.amazonaws.com, sub repo:<owner>/<repo>:*)"
        not in workflow
    )


def test_validation_credentials_never_pass_through_a_workflow_command() -> None:
    workflow = (ROOT / ".github/workflows/deploy-validate.yml").read_text()
    assert "::add-mask::" not in workflow and "GITHUB_ENV" not in workflow
    assert re.search(r"aws-actions/configure-aws-credentials@[0-9a-f]{40}", workflow)


def test_p2013_the_validation_job_destroys_with_fresh_credentials() -> None:
    workflow = (ROOT / ".github/workflows/deploy-validate.yml").read_text()
    assert workflow.count("aws-actions/configure-aws-credentials@") == 2
    assert 'AIR_VALIDATE_CLEANUP: "false"' in workflow
    assert "./deployment/validate.sh --cleanup-only" in workflow
    assert (
        workflow.count("if: always()") == 2
    )  # the credentials step and the destroy step
    assert "MaxSessionDuration" in workflow


def test_p2038_the_sandbox_environment_setup_restricts_the_branch() -> None:
    workflow = (ROOT / ".github/workflows/deploy-validate.yml").read_text()
    assert "Deployment branches and tags" in workflow
    assert "Selected branches: main only" in workflow


def test_p2041_npx_is_not_a_prerequisite_any_more() -> None:
    assert "npx" not in README
    for name in (
        "validate.sh",
        "deploy.sh",
        "destroy.sh",
        "publish-site.sh",
        "common.sh",
    ):
        assert "npx" not in (ROOT / "deployment" / name).read_text(), name


def test_p2010_the_threat_model_does_not_overclaim_what_cdk_nag_catches() -> None:
    threat = (ROOT / "docs/security/threat-model.md").read_text()
    assert "fail on any unlisted wildcard." not in threat
    assert "STAR_RESOURCE_ACTIONS" in threat
    assert "cdk-nag reports it without the action" in threat


def test_p2091_the_scan_table_is_dated_for_the_release() -> None:
    """The scan and test tables were re-run for the release and say so; no stale caveat remains."""
    threat = (ROOT / "docs/security/threat-model.md").read_text()
    assert (
        "### Static analysis and scans (as of 20 Nov 2026, release candidate)" in threat
    )
    assert "### Tests (as of 20 Nov 2026)" in threat
    assert "predate the round-1 and round-2 review changes" not in threat


def test_p2090_the_docs_site_fails_on_a_broken_link_inside_docs() -> None:
    """`info` for every link class meant `mkdocs build --strict` never failed on a broken anchor or
    page link inside docs/; the hook rewrites ../ links before validation, so the rest can warn and
    --strict turns a warning into a failure (P2-090)."""
    config = (ROOT / "mkdocs.yml").read_text()
    for key in ("not_found", "anchors", "unrecognized_links"):
        assert f"{key}: warn" in config, key
    assert "absolute_links: info" in config  # the rewritten ../ links
    assert "--strict" in (ROOT / ".github/workflows/docs.yml").read_text()


def test_p2042_checkov_scans_the_bucket_deployment_resources() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    synth = ci.split("synthesise templates")[1].split("- name:")[0]
    assert (
        "build/site/index.html" in synth
    )  # without a site those resources are not in the template


def test_p2043_the_cdk_cli_lockfile_is_audited() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "AWS CDK CLI dependency vulnerabilities" in ci
    # frontend, the CDK CLI and the MCP server
    assert ci.count("npm audit --audit-level=moderate --omit=dev") == 3
    mcp = ci.split("\n  mcp:\n")[1].split("\n  e2e-lint:")[0]
    assert "cache-dependency-path: mcp/package-lock.json" in mcp
    assert "npm audit --audit-level=moderate --omit=dev" in mcp


def test_p2045_only_pull_request_runs_are_superseded() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "cancel-in-progress: true" not in ci
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in ci


def test_p2044_contributing_lists_the_gates_ci_enforces() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    for gate in (
        "100% line and branch coverage",
        "ruff check",
        "mypy --strict",
        "shellcheck",
    ):
        assert gate in contributing, gate
    assert "uv run --frozen pytest --collect-only -q" in contributing


def test_p2057_synthesis_does_not_pin_one_python_version() -> None:
    app = json.loads((ROOT / "infra/cdk.json").read_text())["app"]
    # Not "--python 3.12": a host with only 3.13 can still synth. Quoted, or the shell CDK runs it in
    # reads >=3.12 as a redirection to a file named "=3.12" (see test_repo).
    assert '--python ">=3.12"' in app
    assert "--frozen" in app  # P2-082: the reviewed lock, like CI


def test_the_stack_description_names_the_sample_without_a_solution_id() -> None:
    """An aws-samples sample has no Solutions Library id: the description names the sample and its
    version, and the README no longer talks about a Solution ID or the SO0000 placeholder."""
    assert (
        config.DESCRIPTION
        == f"Agentic Insights Radar on AWS (sample) v{config.SOLUTION_VERSION}"
    )
    assert not hasattr(config, "SOLUTION_ID")
    assert "SO0000" not in README and "Solution ID" not in README
    assert "This is sample code, for non-production usage." in README


def test_p2079_the_docs_say_the_outputs_hold_the_operators_email() -> None:
    threat = (ROOT / "docs/security/threat-model.md").read_text()
    assert "Email addresses in stack outputs" in threat
    assert "cloudformation:DescribeStacks" in README


def test_p2005_the_docs_say_the_tool_caps_are_per_run_not_per_day() -> None:
    cost = (ROOT / "docs/cost-estimate.md").read_text()
    assert "Tool caps are per run, not per day" in cost
    assert "no per-day tool ceiling to configure" in cost
    assert "capped **per run** (60, 120, 30, 200, 120) and not per day" in README


def test_p2064_and_p2065_the_custom_domain_advice_says_what_it_needs() -> None:
    assert "Closing the direct address needs a code change" in README
    assert "It is not in this release." in README
    # The custom domain is a deploy option now, so upgrades keep it; the manual steps that every
    # upgrade reverted are gone.
    assert "every upgrade reverts every step below" not in GUIDE
    assert "AIR_DOMAIN_NAME=radar.example.com AIR_PARENT_ZONE_ID=" in GUIDE
    assert "upgrades keep it" in GUIDE


def test_p2054_and_p2055_the_troubleshooting_table_covers_the_two_dead_ends() -> None:
    """Nobody could resend the first administrator's invitation from the app, and redeploying with
    the same alarm email sends no new SNS confirmation (P2-054, P2-055)."""
    assert "admin-create-user" in GUIDE and "--message-action RESEND" in GUIDE
    assert "aws sns subscribe --topic-arn" in GUIDE
    assert "Redeploying with the same address changes nothing" in GUIDE
    assert (
        "| Alarm emails never arrive | Confirm the SNS subscription email, or redeploy"
        not in GUIDE
    )


def test_p2056_the_readme_commands_use_the_stack_the_reader_deployed() -> None:
    """Steps 5 and 3 hard-coded AgenticInsightsRadar while step 3 of Deployment Steps suggests a
    prefix, so following both literally gave "Stack ... does not exist" (P2-056)."""
    assert README.count("--stack-name AgenticInsightsRadar \\") == 0
    assert README.count('--stack-name "$STACK"') == 2
    assert (
        "STACK=AgenticInsightsRadar          # or AgenticInsightsRadar-mr-test"
        in README
    )


def test_p2060_the_docs_say_when_the_seed_function_runs() -> None:
    assert "re-run when the function's code or configuration changes" in GUIDE
    assert "not on an infrastructure-only redeploy" in GUIDE
    assert "and, on every deploy, resets each existing built-in" not in GUIDE


def test_p2067_the_changelog_has_the_sections_the_upgrade_step_sends_you_to() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert "### Changed permissions" in changelog
    assert "### Replaced resources" in changelog
    assert "CHANGELOG.md" in README and "CHANGELOG.md" in GUIDE
    assert "AgentCore Memory (chat history) is not replaced on an upgrade" in changelog
    # CR2-01: the opt-in that does replace it is documented with its flag and its cost.
    assert (
        "AIR_CHAT_MEMORY_KEY=data-key" in changelog and "--allow-data-loss" in changelog
    )
    assert "`chatMemoryKey`" in GUIDE and "AIR_CHAT_MEMORY_KEY" in README


def test_p2071_the_lambda_concurrency_statements_agree() -> None:
    assert "1,000 by default" not in README
    assert "a new account often starts at 10" in README
    assert "an established account has 1,000" in README


def test_p2072_the_scoped_policy_service_list_is_complete() -> None:
    needed = ("function URLs", "AWS X-Ray", "SSM", "AWS Budgets", "AWS Backup")
    requirements = README.split("### AWS account requirements")[1].split("###")[0]
    for service in needed:
        assert service in requirements, service


def test_p2078_and_p2080_the_threat_model_records_the_two_limitations() -> None:
    threat = (ROOT / "docs/security/threat-model.md").read_text()
    assert "CSP `connect-src` allows whole regional AWS hosts" in threat
    assert "unauthenticated one-click unsubscribe link" in threat
    assert "AuthenticateOnUnsubscribe" in threat


def test_p2088_the_threat_model_says_the_site_bucket_keeps_every_build() -> None:
    threat = (ROOT / "docs/security/threat-model.md").read_text()
    assert "The site bucket keeps every build" in threat
    assert "A lifecycle expiry is not safe here" in threat


def test_p3_038_the_readme_cost_table_agrees_with_the_cost_estimate() -> None:
    """The README kept 6,000 web searches, USD 6 of guardrails and a USD 321 total long after the cost
    estimate moved to 2,400 searches, USD 8 and about USD 300 (P3-038)."""
    cost = (ROOT / "docs/cost-estimate.md").read_text(encoding="utf-8")
    start = README.index("| AWS service | Dimensions | Cost [USD] |")
    rows = [line for line in README[start:].splitlines()[2:] if line.startswith("|")]
    rows = rows[
        : next(i for i, r in enumerate(rows) if r.startswith("| **Total**")) + 1
    ]
    values = [float(r.rstrip(" |").rsplit("|", 1)[1].strip(" *")) for r in rows]
    *items, total = values
    assert abs(sum(items) - total) < 0.01, (sum(items), total)
    stated = re.search(r"\| \*\*Typical\*\* \|.*\*\*about (\d+)\*\* \|", cost)
    assert stated and abs(total - int(stated.group(1))) < 5
    assert f"approximately **USD {stated.group(1)} per month**" in README
    searches = re.search(r"= ([\d,]+) queries", cost)
    assert searches and f"{searches.group(1)} queries" in README
    fixed = re.search(r"\| \*\*Fixed total\*\* \| \| \| \*\*about (\d+)\*\* \|", cost)
    assert fixed and f"about USD {fixed.group(1)} is fixed" in README
    assert f"about USD {fixed.group(1)} per month while idle" in GUIDE
