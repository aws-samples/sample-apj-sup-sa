# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""deploy.sh and destroy.sh, run for real against stand-in ``aws``, ``cdk``, ``npm``, ``uv`` and ``node``
commands, so their safety checks are exercised without an AWS account."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from insights_radar_infra.config import DeployConfig, DigestDelivery

# A fake ARN, passed by name.
OIDC_CLIENT_ARN = "arn:aws:secretsmanager:us-east-1:111122223333:secret:s"

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    "deploy.sh",
    "destroy.sh",
    "publish-site.sh",
    "common.sh",
    "purge.py",
    "validate.sh",
)

AWS = r'''#!PYTHON
"""Stand-in AWS CLI: answers from a rules file, logs every call. A rule with "once" answers one call
and is then dropped, so a test can make a second, identical call see a changed resource."""
import json, os, sys
args = sys.argv[1:]
line = " ".join(args)
with open(os.environ["AIR_TEST_LOG"], "a") as log:
    log.write("aws " + line + "\n")
if "--generate-cli-skeleton" in args:
    sys.exit(0 if args[0] not in json.loads(os.environ.get("AIR_TEST_MISSING_CLI", "[]")) else 252)
rules = json.load(open(os.environ["AIR_TEST_RULES"]))
for index, rule in enumerate(rules):
    if all(part in line for part in rule["match"]):
        if rule.get("once"):
            del rules[index]
            json.dump(rules, open(os.environ["AIR_TEST_RULES"], "w"))
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("code", 0))
sys.exit(0)
'''
CDK = r'''#!PYTHON
"""Stand-in AWS CDK CLI: logs its arguments, fails the first deploy (or any diff) when told to,
writes outputs."""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["AIR_TEST_LOG"], "a") as log:
    log.write("cdk " + " ".join(args) + "\n")
count_file = os.environ["AIR_TEST_LOG"] + ".deploys"
if args and args[0] == "diff" and os.environ.get("AIR_TEST_CDK_FAIL_DIFF"):
    print("the template could not be synthesised")
    sys.exit(1)
if args and args[0] == "destroy" and os.environ.get("AIR_TEST_CDK_FAIL_DESTROY"):
    print(os.environ["AIR_TEST_CDK_FAIL_DESTROY"])
    sys.exit(1)
if args and args[0] == "deploy":
    n = int(open(count_file).read()) + 1 if os.path.exists(count_file) else 1
    open(count_file, "w").write(str(n))
    fail = os.environ.get("AIR_TEST_CDK_FAIL_FIRST")
    if fail and n == 1:
        print(fail)
        sys.exit(1)
    second = os.environ.get("AIR_TEST_CDK_FAIL_SECOND")
    if second and n == 2:
        print(second)
        sys.exit(1)
    if "--outputs-file" in args:
        path = args[args.index("--outputs-file") + 1]
        json.dump({"Stack": {"AlarmTopicArn": "arn:aws:sns:us-east-1:111122223333:alarms"}}, open(path, "w"))
sys.exit(0)
'''
NPM = r"""#!PYTHON
import os, shutil, sys
args = sys.argv[1:]
if "ci" in args and "--prefix" in args:
    target = os.path.join(args[args.index("--prefix") + 1], "node_modules", ".bin")
    os.makedirs(target, exist_ok=True)
    shutil.copy(os.environ["AIR_TEST_CDK"], os.path.join(target, "cdk"))
sys.exit(0)
"""
NODE = r"""#!PYTHON
import sys
if "--version" in sys.argv:
    print("v22.0.0")
sys.exit(0)
"""
UV = "#!/bin/sh\nexit 0\n"
BUILD = """from pathlib import Path
root = Path(__file__).resolve().parent.parent
for d in ("lambda", "agents", "site"):
    (root / "build" / d).mkdir(parents=True, exist_ok=True)
(root / "build" / "site" / "index.html").write_text("<html></html>")
"""


@dataclass
class Run:
    code: int
    out: str
    calls: list[str]

    def cdk(self, verb: str) -> list[str]:
        return [c for c in self.calls if c.startswith(f"cdk {verb}")]


@dataclass
class Sandbox:
    root: Path
    bin: Path
    rules: list[dict[str, Any]] = field(default_factory=list)

    def answer(
        self,
        *match: str,
        stdout: str = "",
        stderr: str = "",
        code: int = 0,
        once: bool = False,
    ) -> None:
        self.rules.append(
            {
                "match": list(match),
                "stdout": stdout,
                "stderr": stderr,
                "code": code,
                "once": once,
            }
        )

    def stack(self, status: str, outputs: dict[str, str] | None = None) -> None:
        self.answer("describe-stacks", "StackStatus", stdout=f"{status}\n")
        rows = "".join(f"{k}\t{v}\n" for k, v in (outputs or {}).items())
        self.answer("describe-stacks", "Outputs", stdout=rows)

    def run(self, script: str, *args: str, env: dict[str, str] | None = None) -> Run:
        log = self.root / "calls.log"
        log.write_text("")
        rules = self.root / "rules.json"
        # Defaults after the test's own answers: no stack, an empty resource list, a confirmed subscription.
        defaults = [
            {
                "match": ["describe-stacks", "StackStatus"],
                "stderr": "Stack with id X does not exist",
                "code": 255,
            },
            {"match": ["sts get-caller-identity"], "stdout": "111122223333\n"},
            {
                "match": ["list-stack-resources"],
                "stdout": json.dumps({"StackResourceSummaries": []}),
            },
            {
                "match": ["head-bucket"],
                "stderr": "An error occurred (404) when calling HeadBucket",
                "code": 254,
            },
            {"match": ["list-subscriptions-by-topic"], "stdout": "1\n"},
        ]
        rules.write_text(json.dumps(self.rules + defaults))
        full = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "AIR_TEST_LOG": str(log),
            "AIR_TEST_RULES": str(rules),
            "AIR_TEST_CDK": str(self.bin / "cdk"),
            "AWS_REGION": "us-east-1",
            **(env or {}),
        }
        # Only the repository's own deployment scripts, run without a shell from a temporary copy.
        assert script in DEPLOYMENT_SCRIPTS, script
        assert all(isinstance(a, str) for a in args)
        path = str(self.root / "deployment" / script)
        # Every argv element is a literal (Holmes/semgrep dangerous-subprocess-use-audit, which ignores inline
        # markers): the script and its arguments reach a fixed launcher through the environment, so nothing a
        # test passes is ever parsed by a shell. The interpreter path goes in executable=, as build.py does.
        launch = {
            **full,
            "AIR_TEST_SCRIPT": path,
            "AIR_TEST_ARGC": str(len(args)),
            **{f"AIR_TEST_ARG_{i}": a for i, a in enumerate(args)},
        }
        if script.endswith(".py"):
            done = subprocess.run(
                [
                    "python3",
                    "-c",
                    (
                        "import os, runpy, sys; s = os.environ['AIR_TEST_SCRIPT']; n = int(os.environ['AIR_TEST_ARGC']); "
                        "sys.argv = [s] + [os.environ[f'AIR_TEST_ARG_{i}'] for i in range(n)]; "
                        "sys.path[0] = os.path.dirname(s); runpy.run_path(s, run_name='__main__')"
                    ),
                ],
                executable=sys.executable,
                env=launch,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        else:
            done = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        'args=(); for ((i = 0; i < AIR_TEST_ARGC; i++)); do v="AIR_TEST_ARG_$i"; args+=("${!v}"); done; '
                        'exec bash "$AIR_TEST_SCRIPT" "${args[@]}"'
                    ),
                ],
                env=launch,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        return Run(
            done.returncode, done.stdout + done.stderr, log.read_text().splitlines()
        )


DEPLOYMENT_SCRIPTS = frozenset(
    p.name
    for p in (Path(__file__).resolve().parents[2] / "deployment").iterdir()
    if p.suffix in (".sh", ".py")
)


def _executable(path: Path, body: str) -> None:
    path.write_text(body.replace("#!PYTHON", f"#!{sys.executable}"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def box(tmp_path: Path) -> Sandbox:
    (tmp_path / "deployment").mkdir()
    (tmp_path / "infra").mkdir()
    # deploy.sh asks the real digestDelivery parser how synth will read a value (scan cycle 67).
    shutil.copytree(
        ROOT / "infra" / "insights_radar_infra",
        tmp_path / "infra" / "insights_radar_infra",
    )
    for name in SCRIPTS:
        shutil.copy(ROOT / "deployment" / name, tmp_path / "deployment" / name)
    (tmp_path / "deployment" / "build.py").write_text(BUILD)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (
        ("aws", AWS),
        ("cdk", CDK),
        ("npm", NPM),
        ("node", NODE),
        ("uv", UV),
    ):
        _executable(stubs / name, body)
    return Sandbox(tmp_path, stubs)


def _context(call: str) -> dict[str, str]:
    parts = call.split()
    return dict(parts[i + 1].split("=", 1) for i, p in enumerate(parts) if p == "-c")


# ---- deploy.sh ---------------------------------------------------------------------------------


def test_a_fresh_install_is_created_unprotected_then_protected(box: Sandbox) -> None:
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code == 0, run.out
    first, second = run.cdk("deploy")
    assert _context(first)["deletionProtection"] == "false"
    assert _context(second)["deletionProtection"] == "true"
    assert "--require-approval never" in first  # not a terminal
    assert (
        "Account 111122223333, Region us-east-1, stack AgenticInsightsRadar" in run.out
    )
    assert "Data protection: on" in run.out
    assert any("get-inference-profile" in c for c in run.calls)  # preflight ran


def test_an_update_keeps_the_options_it_was_deployed_with(box: Sandbox) -> None:
    options = {
        "retainData": "true",
        "logRetentionDays": "30",
        "chatModel": "haiku-4-5",
        "alarmEmail": "ops@example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {"FirstAdmin": "first@example.com", "DeployOptions": json.dumps(options)},
    )
    run = box.run("deploy.sh", "someone@example.com")
    assert run.code == 0, run.out
    (deploy,) = run.cdk("deploy")
    ctx = _context(deploy)
    assert ctx["logRetentionDays"] == "30" and ctx["chatModel"] == "haiku-4-5"
    assert (
        ctx["alarmEmail"] == "ops@example.com"
        and ctx["adminEmail"] == "first@example.com"
    )
    assert "deletionProtection" not in ctx
    assert "Keeping the first administrator first@example.com" in run.out


def test_sr11_e2e_sign_in_is_never_kept_from_the_deployed_options(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps({"e2eSignIn": "true"})})
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code == 0, run.out
    assert _context(run.cdk("deploy")[0])["e2eSignIn"] == "false"
    assert "Turning off e2eSignIn" in run.out
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_E2E_SIGN_IN": "TRUE"})
    assert run.code == 0, run.out
    assert _context(run.cdk("deploy")[0])["e2eSignIn"] == "true"
    assert "WARNING: AIR_E2E_SIGN_IN=true" in run.out


def test_an_explicit_change_is_announced(box: Sandbox) -> None:
    box.stack(
        "UPDATE_COMPLETE", {"DeployOptions": json.dumps({"logRetentionDays": "30"})}
    )
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_LOG_RETENTION_DAYS": "90"}
    )
    assert run.code == 0, run.out
    assert "Changing logRetentionDays from 30 to 90" in run.out
    assert _context(run.cdk("deploy")[0])["logRetentionDays"] == "90"


def test_turning_off_data_retention_on_a_live_stack_needs_an_explicit_flag(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps({"retainData": "true"})})
    refused = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_RETAIN_DATA": "false"}
    )
    assert refused.code != 0 and "--allow-data-loss" in refused.out
    assert refused.cdk("deploy") == []
    allowed = box.run(
        "deploy.sh",
        "admin@example.com",
        "--allow-data-loss",
        env={"AIR_RETAIN_DATA": "false"},
    )
    assert allowed.code == 0, allowed.out
    assert "Data protection: OFF" in allowed.out


@pytest.mark.parametrize(
    ("status", "advice"),
    [
        ("UPDATE_IN_PROGRESS", "wait for it to finish"),
        ("UPDATE_ROLLBACK_FAILED", "continue-update-rollback"),
        ("DELETE_FAILED", "destroy.sh --stack AgenticInsightsRadar"),
    ],
)
def test_a_stack_that_cannot_be_updated_stops_before_the_build(
    box: Sandbox, status: str, advice: str
) -> None:
    box.stack(status)
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code != 0 and advice in run.out
    assert run.cdk("deploy") == [] and not (box.root / "build" / "site").exists()


STACK_ARN = (
    "arn:aws:cloudformation:us-east-1:111122223333:stack/AgenticInsightsRadar/"
    "31809730-c163-11f1-98e9-0efff4fe56f5"
)


def _gateways(box: Sandbox, *rows: tuple[str, str], owned: str = "") -> None:
    box.answer("list-gateways", stdout="".join(f"{i}\t{n}\n" for i, n in rows))
    box.answer("describe-stacks", "StackId", stdout=f"{STACK_ARN}\n")
    box.answer(
        "list-stack-resources", "AWS::BedrockAgentCore::Gateway", stdout=f"{owned}\n"
    )


def test_p3002_a_leftover_gateway_with_the_name_this_deploy_creates_stops_it(
    box: Sandbox,
) -> None:
    """The live failure: "A gateway with name ... already exists" late in the update, then a
    rollback. deploy.sh finds it first, says which gateway, and points at the recovery steps."""
    box.stack("UPDATE_ROLLBACK_COMPLETE", {"FirstAdmin": "first@example.com"})
    _gateways(
        box,
        ("air-web-0efff4fe56f5-abcdefghij", "air-web-0efff4fe56f5"),
        ("air-web-wlfmydc6jb", "air-web"),
        owned="air-web-wlfmydc6jb",
    )
    run = box.run("deploy.sh")
    assert run.code != 0
    assert (
        "gateway air-web-0efff4fe56f5-abcdefghij has the name this deploy creates (air-web-0efff4fe56f5)"
        in run.out
    )
    assert "Clearing an orphaned" in run.out
    assert "air-web-wlfmydc6jb" not in run.out  # the stack's own
    assert run.cdk("deploy") == []


def test_p3002_another_leftover_gateway_is_reported_and_the_deploy_goes_on(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_ROLLBACK_COMPLETE", {"FirstAdmin": "first@example.com"})
    _gateways(
        box,
        ("air-web-k-aa8kjdiesg", "air-web-k"),  # the live us-east-1 orphan
        ("air-web-wlfmydc6jb", "air-web"),
        (
            "air-webtest-web-0123456789ab-zzzzzzzzzz",
            "air-webtest-web-0123456789ab",
        ),  # another prefix
        ("someone-elses-gw", "air-web-search-tool"),  # not a name this deployment uses
        owned="air-web-wlfmydc6jb",
    )
    run = box.run("deploy.sh")
    assert run.code == 0, run.out
    assert "  air-web-k-aa8kjdiesg (air-web-k)" in run.out
    for other in ("air-web-wlfmydc6jb", "air-webtest", "someone-elses-gw"):
        assert other not in run.out, other
    assert len(run.cdk("deploy")) == 1


def test_p3002_a_fresh_install_reports_leftovers_of_its_prefix(box: Sandbox) -> None:
    box.answer(
        "list-gateways",
        stdout="air-web-0123456789ab-q1w2e3r4t5\tair-web-0123456789ab\n",
    )
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code == 0, run.out
    assert "air-web-0123456789ab-q1w2e3r4t5 (air-web-0123456789ab)" in run.out
    assert not any("StackId" in c for c in run.calls)  # a new stack has no id yet


def test_p3002_a_failed_gateway_listing_is_a_warning(box: Sandbox) -> None:
    box.answer("list-gateways", stderr="AccessDeniedException: not allowed\n", code=254)
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code == 0, run.out
    assert "warning: cannot list AgentCore gateways in us-east-1" in run.out
    assert "AccessDeniedException" in run.out


@pytest.mark.parametrize(
    ("failing", "message"),
    [
        (
            "AWS::BedrockAgentCore::Gateway",
            "cannot list the resources of AgenticInsightsRadar",
        ),
        ("StackId", "cannot read the stack id of AgenticInsightsRadar"),
    ],
)
def test_p3002_an_unreadable_stack_stops_the_gateway_check(
    box: Sandbox, failing: str, message: str
) -> None:
    box.stack("UPDATE_COMPLETE", {"FirstAdmin": "first@example.com"})
    box.answer("list-gateways", stdout="")
    box.answer(failing, stderr="Throttling\n", code=255)
    run = box.run("deploy.sh")
    assert run.code != 0 and message in run.out
    assert run.cdk("deploy") == []


STACK_VAULT = "AgenticInsightsRadarBackupsVaultD3B9F9C3"  # what CDK names the default stack's vault


@pytest.mark.parametrize("prefix", ["air", "mr-test"])
def test_p3010_deploy_sh_derives_the_vault_name_cdk_gives_it(
    bundles: None, prefix: str
) -> None:
    """deploy.sh matches <stack name without non-alphanumerics>BackupsVault<8 hex>: the name CDK
    derives from the construct path, the same for every creation of the stack."""
    from .conftest import synth

    s = synth(prefix=prefix, backupPlan="true")
    ((_, vault),) = s.resources("AWS::Backup::BackupVault").items()
    stack = (
        "AgenticInsightsRadar" if prefix == "air" else f"AgenticInsightsRadar-{prefix}"
    )
    base = re.sub("[^A-Za-z0-9]", "", stack) + "BackupsVault"
    assert re.fullmatch(f"{base}[0-9A-F]{{8}}", vault["Properties"]["BackupVaultName"])
    script = (ROOT / "deployment" / "deploy.sh").read_text()
    assert 'vault_base="${STACK//[^A-Za-z0-9]/}BackupsVault"' in script
    assert "^${vault_base}[0-9A-F]{8}$" in script


def test_p3010_a_fresh_install_with_backups_stops_on_a_kept_vault(box: Sandbox) -> None:
    box.answer("list-backup-vaults", stdout=f"{STACK_VAULT}\n")
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_BACKUP": "true"})
    assert run.code != 0
    assert (
        f"backup vault {STACK_VAULT} is left from an earlier deployment of AgenticInsightsRadar"
        in run.out
    )
    assert "aws backup delete-backup-vault --backup-vault-name" in run.out
    assert run.cdk("deploy") == []
    # Without backups the kept vault is no obstacle, and nothing looks for it.
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code == 0, run.out
    assert not any("list-backup-vaults" in c for c in run.calls)


def test_p3010_turning_backups_on_again_stops_on_the_orphaned_vault(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE", {"FirstAdmin": "first@example.com"})
    box.answer("list-stack-resources", "AWS::Backup::BackupVault", stdout="None\n")
    box.answer("list-backup-vaults", stdout=f"{STACK_VAULT}\n")
    run = box.run("deploy.sh", env={"AIR_BACKUP": "true"})
    assert run.code != 0 and f"backup vault {STACK_VAULT}" in run.out
    assert run.cdk("deploy") == []


def test_p3010_the_stacks_own_vault_and_other_stacks_vaults_are_not_obstacles(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE", {"FirstAdmin": "first@example.com"})
    box.answer(
        "list-stack-resources", "AWS::Backup::BackupVault", stdout=f"{STACK_VAULT}\n"
    )
    box.answer("list-backup-vaults", stdout=f"{STACK_VAULT}\n")
    run = box.run("deploy.sh", env={"AIR_BACKUP": "true"})
    assert run.code == 0, run.out
    assert not any("list-backup-vaults" in c for c in run.calls)
    # A fresh install: another deployment's vault, and a name that only starts the same, do not match.
    box.rules.clear()
    box.answer(
        "list-backup-vaults",
        stdout="AgenticInsightsRadarmrtestBackupsVault8AB9676D\tAgenticInsightsRadarBackupsVaultCopy\n",
    )
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_BACKUP": "true"})
    assert run.code == 0, run.out


@pytest.mark.parametrize(
    ("failing", "message"),
    [
        ("list-backup-vaults", "cannot list the AWS Backup vaults in us-east-1"),
        (
            "AWS::Backup::BackupVault",
            "cannot list the resources of AgenticInsightsRadar",
        ),
    ],
)
def test_p3010_an_unreadable_vault_list_stops_the_deploy(
    box: Sandbox, failing: str, message: str
) -> None:
    box.stack("UPDATE_COMPLETE", {"FirstAdmin": "first@example.com"})
    box.answer(failing, stderr="Throttling\n", code=255)
    if failing != "AWS::Backup::BackupVault":
        box.answer("AWS::Backup::BackupVault", stdout="None\n")
    run = box.run("deploy.sh", env={"AIR_BACKUP": "true"})
    assert run.code != 0 and message in run.out


def test_web_search_falls_back_when_the_connector_is_unavailable(box: Sandbox) -> None:
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={
            "AIR_TEST_CDK_FAIL_FIRST": "Resource handler returned message: web-search is not available in this Region"
        },
    )
    assert run.code == 0, run.out
    deploys = run.cdk("deploy")
    assert (
        _context(deploys[0])["webSearch"] == "true"
        and _context(deploys[1])["webSearch"] == "false"
    )


def test_an_explicit_web_search_setting_is_never_overridden(box: Sandbox) -> None:
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={
            "AIR_WEB_SEARCH": "true",
            "AIR_TEST_CDK_FAIL_FIRST": "web-search is not available",
        },
    )
    assert run.code != 0 and len(run.cdk("deploy")) == 1


def test_p3_052_an_existing_deployment_never_loses_web_search_to_the_fallback(
    box: Sandbox,
) -> None:
    box.stack(
        "UPDATE_COMPLETE",
        {
            "FirstAdmin": "first@example.com",
            "DeployOptions": json.dumps({"webSearch": "true"}),
        },
    )
    run = box.run(
        "deploy.sh",
        env={
            "AIR_TEST_CDK_FAIL_FIRST": "Resource handler returned message: web-search is not available"
        },
    )
    assert run.code != 0 and len(run.cdk("deploy")) == 1, run.out
    assert "It was not turned off" in run.out


@pytest.mark.parametrize("value", ["admin", "ADMIN"])
def test_p3_060_the_alarm_address_can_go_back_to_the_first_administrator(
    box: Sandbox, value: str
) -> None:
    box.stack(
        "UPDATE_COMPLETE",
        {
            "FirstAdmin": "first@example.com",
            "DeployOptions": json.dumps({"alarmEmail": "gone@example.com"}),
        },
    )
    run = box.run("deploy.sh", env={"AIR_ALARM_EMAIL": value})
    assert run.code == 0, run.out
    assert "alarmEmail" not in _context(run.cdk("deploy")[0])
    assert "alarmEmail=first@example.com (the first administrator)" in run.out


def test_an_unconfirmed_alarm_subscription_is_reported(box: Sandbox) -> None:
    box.answer("list-subscriptions-by-topic", stdout="0\n")
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code == 0, run.out
    assert "alarms notify nobody" in run.out


def test_a_region_without_agentcore_is_refused_before_building(box: Sandbox) -> None:
    box.answer(
        "list-agent-runtimes",
        stderr='Could not connect to the endpoint URL: "https://x"',
        code=255,
    )
    run = box.run("deploy.sh", "admin@example.com", env={"AWS_REGION": "sa-east-1"})
    assert run.code != 0 and "not available in sa-east-1" in run.out
    assert not (box.root / "build" / "site").exists()


def test_an_old_aws_cli_is_refused(box: Sandbox) -> None:
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_TEST_MISSING_CLI": '["s3vectors"]'}
    )
    assert run.code != 0 and "no 'aws s3vectors' command" in run.out


def test_bootstrap_can_use_a_scoped_execution_policy(box: Sandbox) -> None:
    box.answer("CDKToolkit", stderr="does not exist", code=255)
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={
            "AIR_CFN_EXECUTION_POLICIES": "arn:aws:iam::111122223333:policy/air-deploy"
        },
    )
    assert run.code == 0, run.out
    (bootstrap,) = run.cdk("bootstrap")
    assert (
        "--cloudformation-execution-policies arn:aws:iam::111122223333:policy/air-deploy"
        in bootstrap
    )


def test_a_build_without_the_web_app_stops_the_deploy(box: Sandbox) -> None:
    (box.root / "deployment" / "build.py").write_text("")
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code != 0 and "index.html is missing" in run.out


def test_p2050_a_switch_that_is_not_true_or_false_stops_the_deploy(
    box: Sandbox,
) -> None:
    for var, key in (
        ("AIR_WEB_SEARCH", "webSearch"),
        ("AIR_BACKUP", "backupPlan"),
        ("AIR_RETAIN_DATA", "retainData"),
        ("AIR_E2E_SIGN_IN", "e2eSignIn"),
    ):
        run = box.run("deploy.sh", "admin@example.com", env={var: "0"})
        assert run.code != 0, (var, run.out)
        assert f"{key} must be true or false" in run.out, var
        assert run.cdk("deploy") == []


def test_p2070_a_failed_protection_update_says_the_data_is_unprotected(
    box: Sandbox,
) -> None:
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={"AIR_TEST_CDK_FAIL_SECOND": "throttled by CloudFormation"},
    )
    assert run.code != 0, run.out
    assert "DELETION PROTECTION IS OFF" in run.out
    assert "run this script again" in run.out


def test_p2089_chat_memory_retention_is_a_deploy_option(box: Sandbox) -> None:
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_CHAT_MEMORY_DAYS": "7"})
    assert run.code == 0, run.out
    assert _context(run.cdk("deploy")[0])["chatMemoryDays"] == "7"


def test_cr201_an_upgrade_keeps_the_chat_memory_key_it_was_created_with(
    box: Sandbox,
) -> None:
    """A deployment from before chatMemoryKey was recorded has its chat memory on an AWS-owned key.
    Moving it to the data key replaces the memory and deletes every user's chat history, so an
    upgrade keeps it unless the operator asks for both the change and --allow-data-loss."""
    fresh = box.run("deploy.sh", "admin@example.com")
    assert fresh.code == 0, fresh.out
    assert _context(fresh.cdk("deploy")[0])["chatMemoryKey"] == "data-key"

    box.stack(
        "UPDATE_COMPLETE",
        {
            "FirstAdmin": "a@example.com",
            "DeployOptions": json.dumps({"retainData": "true"}),
        },
    )
    kept = box.run("deploy.sh")
    assert kept.code == 0, kept.out
    assert _context(kept.cdk("deploy")[0])["chatMemoryKey"] == "aws-owned"
    assert "Chat history stays on an AWS-owned key" in kept.out

    env = {"AIR_CHAT_MEMORY_KEY": "data-key"}
    refused = box.run("deploy.sh", env=env)
    assert refused.code != 0 and refused.cdk("deploy") == []
    assert "chatMemoryKey from aws-owned to data-key" in refused.out
    assert "delete every user's chat history" in refused.out
    allowed = box.run("deploy.sh", "--allow-data-loss", env=env)
    assert allowed.code == 0, allowed.out
    assert _context(allowed.cdk("deploy")[0])["chatMemoryKey"] == "data-key"

    bad = box.run("deploy.sh", env={"AIR_CHAT_MEMORY_KEY": "cmk"})
    assert bad.code != 0 and "chatMemoryKey must be data-key or aws-owned" in bad.out


def test_cr201_a_deployment_on_the_data_key_keeps_it(box: Sandbox) -> None:
    box.stack(
        "UPDATE_COMPLETE",
        {
            "FirstAdmin": "a@example.com",
            "DeployOptions": json.dumps({"chatMemoryKey": "data-key"}),
        },
    )
    run = box.run("deploy.sh")
    assert run.code == 0, run.out
    assert _context(run.cdk("deploy")[0])["chatMemoryKey"] == "data-key"
    assert "AWS-owned key" not in run.out
    back = box.run("deploy.sh", env={"AIR_CHAT_MEMORY_KEY": "aws-owned"})
    assert back.code != 0 and "--allow-data-loss" in back.out


# The DeployOptions output of the r1 (c408510) us-east-1 stack whose r2 upgrade rolled back at the
# web-search gateway, as `aws cloudformation describe-stacks` returned it.
R1_DEPLOY_OPTIONS = (
    '{"retainData":"true","webSearch":"true","logRetentionDays":"365",'
    '"agentLogRetentionDays":"30","chatModel":"sonnet-5-5","priceClass":"100",'
    '"monthlyBudgetUsd":"0","reservedConcurrency":"false","backupPlan":"false",'
    '"suppressInvitation":"false"}'
)


def test_upgrade_of_the_r1_stack_reads_back_options_that_change_no_create_only_key(
    box: Sandbox,
) -> None:
    """The r1 stack records no chatMemoryKey. Read back, that is aws-owned, so the synthesised
    template keeps the chat memory exactly as deployed; every other recorded option is kept, and no
    data-loss guard trips. (The gateway needs no option: it is replaced, see test_hardening.)"""
    box.stack(
        "UPDATE_ROLLBACK_COMPLETE",
        {"FirstAdmin": "a@example.com", "DeployOptions": R1_DEPLOY_OPTIONS},
    )
    run = box.run("deploy.sh")
    assert run.code == 0, run.out
    context = _context(run.cdk("deploy")[0])
    assert context["chatMemoryKey"] == "aws-owned"
    for key, value in json.loads(R1_DEPLOY_OPTIONS).items():
        assert context[key] == value, key
    assert "--allow-data-loss:" not in run.out
    assert "Chat history stays on an AWS-owned key" in run.out
    # The template that context produces keeps the r1 memory: same name, no key.
    config = DeployConfig.from_context({"adminEmail": "a@example.com", **context}.get)
    assert config.chat_memory_key == "aws-owned"


def test_cr204_the_waf_rate_limit_is_passed_and_kept(box: Sandbox) -> None:
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_WAF_RATE_LIMIT": "10000"})
    assert run.code == 0, run.out
    assert _context(run.cdk("deploy")[0])["wafRateLimit"] == "10000"
    box.stack(
        "UPDATE_COMPLETE", {"DeployOptions": json.dumps({"wafRateLimit": "5000"})}
    )
    kept = box.run("deploy.sh", "admin@example.com")
    assert kept.code == 0, kept.out
    assert _context(kept.cdk("deploy")[0])["wafRateLimit"] == "5000"


def test_cr204_every_recorded_option_is_one_deploy_sh_keeps() -> None:
    """Every DeployOptions key must have a row in deploy.sh's OPTIONS table, or an update silently
    resets it to the default. e2eSignIn (asked for on every deploy) and alarmEmail (an argument)
    are handled on their own."""
    recorded = set(
        json.loads(
            DeployConfig(
                admin_email="a@example.com",
                alarm_email="o@example.com",
                # Every optional option set, so its key is recorded too.
                seed="policy-watch",
                domain_name="radar.example.com",
                parent_zone_id="Z0123456789ABCDEFGHIJ",
                redirect_domains=("old.example.com",),
                certificate_arn="arn:aws:acm:us-east-1:111122223333:certificate/x",
                auth_mode="federated",
                oidc_issuer="https://idp.example.com",
                oidc_client_id="c",
                oidc_client_secret_arn=OIDC_CLIENT_ARN,
                admin_emails=("b@example.com",),
                mcp_issuer="https://idp.example.com",
                mcp_audience="https://radar.example.com",
                mcp_provider_arn="arn:aws:iam::111122223333:oidc-provider/idp.example.com",
                digest=DigestDelivery.parse({"slackMode": "bot"}),
            ).options_json()
        )
    )
    script = (ROOT / "deployment" / "deploy.sh").read_text()
    table = script.split("OPTIONS=(")[1].split(")")[0]
    rows = {
        line.strip().strip('"').split("|")[0]
        for line in table.splitlines()
        if "|" in line
    }
    assert recorded - {"e2eSignIn", "alarmEmail", "solutionVersion"} == rows


def test_cr214_shortening_chat_retention_needs_the_flag(box: Sandbox) -> None:
    box.stack(
        "UPDATE_COMPLETE", {"DeployOptions": json.dumps({"chatMemoryDays": "30"})}
    )
    env = {"AIR_CHAT_MEMORY_DAYS": "7"}
    refused = box.run("deploy.sh", "admin@example.com", env=env)
    assert refused.code != 0 and "chatMemoryDays from 30 to 7" in refused.out
    assert refused.cdk("deploy") == []
    allowed = box.run("deploy.sh", "admin@example.com", "--allow-data-loss", env=env)
    assert allowed.code == 0, allowed.out
    longer = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_CHAT_MEMORY_DAYS": "90"}
    )
    assert longer.code == 0, longer.out


# ---- deploy.sh: review and bootstrap (P2-007, P2-018) --------------------------------------------


def test_p2007_a_declined_diff_deploys_nothing_not_even_the_edge_stack(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "deploy.sh", "admin@example.com", "--diff", env={"AWS_REGION": "eu-west-1"}
    )
    assert run.code != 0 and "Not applied; nothing was deployed." in run.out
    assert (
        run.cdk("deploy") == []
    )  # the edge stack holds the WAF rules: it must not be deployed first
    diffs = run.cdk("diff")
    assert len(diffs) == 2
    assert diffs[0].startswith("cdk diff AgenticInsightsRadar-eu-west-1-Edge ")
    assert diffs[1].startswith("cdk diff AgenticInsightsRadar ")
    assert "webAclArn" in _context(
        diffs[1]
    )  # the main stack cannot synthesise without one


def test_p2007_a_failing_diff_stops_the_deploy(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "deploy.sh", "admin@example.com", "--diff", env={"AIR_TEST_CDK_FAIL_DIFF": "1"}
    )
    assert run.code != 0 and "cdk diff failed for AgenticInsightsRadar" in run.out
    assert run.cdk("deploy") == []


def test_p2018_a_scoped_execution_policy_is_checked_against_the_existing_bootstrap(
    box: Sandbox,
) -> None:
    box.answer("CDKToolkit", "StackStatus", stdout="CREATE_COMPLETE\n")
    box.answer("CloudFormationExecutionPolicies", stdout="\n")  # the default admin role
    mismatch = box.run(
        "deploy.sh",
        "admin@example.com",
        env={
            "AIR_CFN_EXECUTION_POLICIES": "arn:aws:iam::111122223333:policy/air-deploy"
        },
    )
    assert (
        mismatch.code != 0
        and "not the AIR_CFN_EXECUTION_POLICIES you set" in mismatch.out
    )
    assert mismatch.cdk("deploy") == [] and mismatch.cdk("bootstrap") == []
    box.rules = [
        r for r in box.rules if "CloudFormationExecutionPolicies" not in r["match"]
    ]
    box.answer(
        "CloudFormationExecutionPolicies",
        stdout="arn:aws:iam::111122223333:policy/air-deploy\n",
    )
    matched = box.run(
        "deploy.sh",
        "admin@example.com",
        env={
            "AIR_CFN_EXECUTION_POLICIES": "arn:aws:iam::111122223333:policy/air-deploy"
        },
    )
    assert matched.code == 0, matched.out
    assert "already deploys with your CloudFormationExecutionPolicies" in matched.out


def test_p2018_a_bootstrap_stack_that_cannot_be_used_stops_the_deploy(
    box: Sandbox,
) -> None:
    box.answer("CDKToolkit", "StackStatus", stdout="ROLLBACK_FAILED\n")
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code != 0 and "CDKToolkit in us-east-1 is ROLLBACK_FAILED" in run.out
    assert run.cdk("deploy") == []


def test_p2018_an_outdated_bootstrap_stops_the_deploy(box: Sandbox) -> None:
    box.answer("CDKToolkit", "StackStatus", stdout="CREATE_COMPLETE\n")
    box.answer("ssm get-parameter", "/cdk-bootstrap/", stdout="6\n")
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code != 0 and "bootstrap in us-east-1 is version 6" in run.out
    assert run.cdk("deploy") == []


def test_p2012_the_web_app_is_installed_without_running_package_scripts() -> None:
    build = (ROOT / "deployment" / "build.py").read_text()
    assert (
        'npm("ci", "--ignore-scripts", "--no-audit", "--no-fund", cwd=frontend)'
        in build
    )


# ---- destroy.sh --------------------------------------------------------------------------------


def test_destroy_never_deletes_without_naming_the_stack(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE")
    unnamed = box.run("destroy.sh", "--yes")
    assert unnamed.code != 0 and "--yes needs --stack" in unnamed.out
    no_terminal = box.run("destroy.sh")
    assert no_terminal.code != 0 and "not a terminal" in no_terminal.out
    assert unnamed.cdk("destroy") == no_terminal.cdk("destroy") == []


def test_destroy_names_the_account_region_and_stack_and_keeps_an_inventory(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "destroy.sh",
        "--stack",
        "AgenticInsightsRadar-mr-test",
        "--yes",
        env={"AWS_REGION": "", "AWS_DEFAULT_REGION": "eu-west-1"},
    )
    assert run.code == 0, run.out
    assert (
        "Account 111122223333, Region eu-west-1, stack AgenticInsightsRadar-mr-test"
        in run.out
    )
    (destroy,) = run.cdk("destroy")
    assert _context(destroy)["prefix"] == "mr-test" and "webAclArn" in _context(destroy)
    plan = box.root / "build" / "purge-AgenticInsightsRadar-mr-test-eu-west-1.json"
    assert json.loads(plan.read_text())["stack"] == "AgenticInsightsRadar-mr-test"
    assert "purge.py apply" in run.out


@pytest.mark.parametrize("purge", [False, True])
def test_p3014_a_failed_destroy_says_what_is_left_and_how_to_go_on(
    box: Sandbox, purge: bool
) -> None:
    """The live failure: the gateway ends DELETE_FAILED, cdk destroy fails, and the script used to
    exit under set -e with --purge having deleted nothing and no next step printed."""
    box.stack("DELETE_FAILED")
    run = box.run(
        "destroy.sh",
        "--stack",
        "AgenticInsightsRadar",
        "--yes",
        *(["--purge"] if purge else []),
        env={
            "AIR_TEST_CDK_FAIL_DESTROY": "AgentsWebSearchGatewayK8FD3F81A DELETE_FAILED"
        },
    )
    assert run.code != 0
    assert "the stack was not deleted (DELETE_FAILED). Nothing was purged." in run.out
    assert "Clearing an orphaned web-search gateway" in run.out
    plan = "build/purge-AgenticInsightsRadar-us-east-1.json"
    assert f"python3 deployment/purge.py apply {box.root}/{plan}" in run.out
    assert not [c for c in run.calls if c.startswith("aws ") and " delete-" in c]
    if purge:
        assert f"(the list is saved in {box.root}/{plan})" in run.out


def test_destroy_refuses_a_stack_that_does_not_exist(box: Sandbox) -> None:
    run = box.run("destroy.sh", "--stack", "AgenticInsightsRadar-nope", "--yes")
    assert run.code != 0 and "there is no stack AgenticInsightsRadar-nope" in run.out
    assert run.cdk("destroy") == []


def test_destroy_refuses_a_foreign_stack_name(box: Sandbox) -> None:
    run = box.run("destroy.sh", "--stack", "SomeoneElsesStack", "--yes")
    assert run.code != 0 and "not an Agentic Insights Radar stack name" in run.out


# ---- purge.py: backups (P2-001) -----------------------------------------------------------------

KEY = "11111111-2222-3333-4444-555555555555"
KEY_ARN = f"arn:aws:kms:us-east-1:111122223333:key/{KEY}"
VAULT = "air-vault"
RECOVERY_POINT = "arn:aws:backup:us-east-1:111122223333:recovery-point:rp-1"


def _plan(box: Sandbox, *resources: dict[str, str]) -> Path:
    path = box.root / "plan.json"
    path.write_text(
        json.dumps({"stack": "AgenticInsightsRadar", "resources": list(resources)})
    )
    return path


def _live_key(box: Sandbox) -> None:
    box.answer(
        "kms describe-key", stdout=json.dumps({"KeyMetadata": {"KeyState": "Enabled"}})
    )


def test_p2001_the_plan_records_a_backup_vault(box: Sandbox) -> None:
    box.answer(
        "list-stack-resources",
        stdout=json.dumps(
            {
                "StackResourceSummaries": [
                    {
                        "ResourceType": "AWS::Backup::BackupVault",
                        "PhysicalResourceId": VAULT,
                    }
                ]
            }
        ),
    )
    plan = box.root / "plan.json"
    run = box.run("purge.py", "plan", "AgenticInsightsRadar", str(plan))
    assert run.code == 0, run.out
    assert json.loads(plan.read_text())["resources"] == [
        {"type": "AWS::Backup::BackupVault", "id": VAULT, "region": "us-east-1"}
    ]


def test_p2001_a_key_a_kept_vault_still_needs_is_never_scheduled_for_deletion(
    box: Sandbox,
) -> None:
    box.answer(
        "describe-backup-vault", stdout=json.dumps({"EncryptionKeyArn": KEY_ARN})
    )
    box.answer(
        "list-recovery-points-by-backup-vault",
        stdout=json.dumps({"RecoveryPoints": [{"RecoveryPointArn": RECOVERY_POINT}]}),
    )
    _live_key(box)
    # The key comes first in the plan, as CloudFormation lists it: the answer cannot depend on order.
    plan = _plan(
        box,
        {"type": "AWS::KMS::Key", "id": KEY},
        {"type": "AWS::Backup::BackupVault", "id": VAULT},
    )
    run = box.run("purge.py", "apply", str(plan))
    assert run.code == 0, run.out
    assert not [c for c in run.calls if "schedule-key-deletion" in c], run.calls
    assert not [c for c in run.calls if "delete-backup-vault" in c], run.calls
    assert "--delete-backups" in run.out


def test_p2001_delete_backups_removes_the_recovery_points_the_vault_then_the_key(
    box: Sandbox,
) -> None:
    box.answer(
        "describe-backup-vault", stdout=json.dumps({"EncryptionKeyArn": KEY_ARN})
    )
    box.answer(
        "list-recovery-points-by-backup-vault",
        once=True,
        stdout=json.dumps(
            {
                "RecoveryPoints": [
                    {"RecoveryPointArn": RECOVERY_POINT, "EncryptionKeyArn": KEY_ARN}
                ]
            }
        ),
    )
    box.answer(
        "list-recovery-points-by-backup-vault",
        stdout=json.dumps({"RecoveryPoints": []}),
    )
    _live_key(box)
    plan = _plan(
        box,
        {"type": "AWS::Backup::BackupVault", "id": VAULT},
        {"type": "AWS::KMS::Key", "id": KEY},
    )
    run = box.run("purge.py", "apply", str(plan), "--delete-backups")
    assert run.code == 0, run.out
    assert [
        c
        for c in run.calls
        if f"delete-recovery-point --backup-vault-name {VAULT}" in c
    ]
    assert [c for c in run.calls if "delete-backup-vault" in c]
    assert [c for c in run.calls if "schedule-key-deletion" in c]


def test_p3_012_the_plan_records_a_vault_the_stack_left_behind_with_its_key(
    box: Sandbox,
) -> None:
    """backupPlan true to false keeps the vault and stops owning it, so it never appeared in the plan and
    a later purge scheduled the key its recovery points need (P3-012)."""
    box.answer(
        "list-stack-resources",
        stdout=json.dumps(
            {
                "StackResourceSummaries": [
                    {"ResourceType": "AWS::KMS::Key", "PhysicalResourceId": KEY}
                ]
            }
        ),
    )
    box.answer(
        "list-backup-vaults",
        stdout=json.dumps(
            {
                "BackupVaultList": [
                    {
                        "BackupVaultName": "AgenticInsightsRadarBackupsVaultD3B9F9C3",
                        "EncryptionKeyArn": KEY_ARN,
                    },
                    {
                        "BackupVaultName": "someone-elses",
                        "EncryptionKeyArn": KEY_ARN.replace("1111", "9999"),
                    },
                ]
            }
        ),
    )
    plan = box.root / "plan.json"
    assert box.run("purge.py", "plan", "AgenticInsightsRadar", str(plan)).code == 0
    assert json.loads(plan.read_text())["resources"] == [
        {"type": "AWS::KMS::Key", "id": KEY, "region": "us-east-1"},
        {
            "type": "AWS::Backup::BackupVault",
            "id": "AgenticInsightsRadarBackupsVaultD3B9F9C3",
            "region": "us-east-1",
        },
    ]


def test_p3_056_on_demand_table_backups_keep_their_key_or_go_with_delete_backups(
    box: Sandbox,
) -> None:
    box.answer(
        "dynamodb list-backups",
        stdout=json.dumps(
            {
                "BackupSummaries": [
                    {"BackupArn": "arn:aws:dynamodb:us-east-1:1:table/t/backup/b1"}
                ]
            }
        ),
    )
    box.answer("describe-table", stdout=json.dumps({"Table": {}}))
    _live_key(box)
    plan = _plan(
        box,
        {"type": "AWS::DynamoDB::Table", "id": "air-graph", "region": "us-east-1"},
        {"type": "AWS::KMS::Key", "id": KEY, "region": "us-east-1"},
    )
    kept = box.run("purge.py", "apply", str(plan))
    assert kept.code == 0, kept.out
    assert (
        not [c for c in kept.calls if "schedule-key-deletion" in c]
        and "on-demand table backup(s)" in kept.out
    )
    gone = box.run("purge.py", "apply", str(plan), "--delete-backups")
    assert [c for c in gone.calls if "delete-backup --backup-arn" in c]
    assert [c for c in gone.calls if "schedule-key-deletion" in c]


def test_p2001_an_unknown_purge_argument_is_refused(box: Sandbox) -> None:
    plan = _plan(box)
    for args in (("apply", str(plan), "--yes"), ("apply", "--delete-backups")):
        run = box.run("purge.py", *args)
        assert run.code == 2, (args, run.out)


def test_p2001_destroy_refuses_delete_backups_without_purge(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "destroy.sh", "--stack", "AgenticInsightsRadar", "--yes", "--delete-backups"
    )
    assert run.code != 0 and "--delete-backups needs --purge" in run.out
    assert run.cdk("destroy") == []


def test_p2001_destroy_says_which_backups_a_purge_keeps_or_deletes(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE")
    kept = box.run("destroy.sh", "--stack", "AgenticInsightsRadar", "--yes", "--purge")
    assert kept.code == 0, kept.out
    assert "Add --delete-backups" in kept.out
    deleted = box.run(
        "destroy.sh",
        "--stack",
        "AgenticInsightsRadar",
        "--yes",
        "--purge",
        "--delete-backups",
    )
    assert deleted.code == 0, deleted.out
    assert "deleted with every recovery point in it" in deleted.out


def test_p2004_the_plan_records_the_us_east_1_edge_stack_too(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE")
    box.answer(
        "list-stack-resources",
        "-Edge",
        stdout=json.dumps(
            {
                "StackResourceSummaries": [
                    {
                        "ResourceType": "AWS::Logs::LogGroup",
                        "PhysicalResourceId": "aws-waf-logs-air-eu-west-1",
                    },
                    {"ResourceType": "AWS::KMS::Key", "PhysicalResourceId": KEY},
                ]
            }
        ),
    )
    run = box.run(
        "destroy.sh",
        "--stack",
        "AgenticInsightsRadar",
        "--yes",
        env={"AWS_REGION": "eu-west-1"},
    )
    assert run.code == 0, run.out
    plan = json.loads(
        (box.root / "build" / "purge-AgenticInsightsRadar-eu-west-1.json").read_text()
    )
    edge = [r for r in plan["resources"] if r["region"] == "us-east-1"]
    assert [r["id"] for r in edge] == ["aws-waf-logs-air-eu-west-1", KEY]
    assert any(
        "list-stack-resources --stack-name AgenticInsightsRadar-eu-west-1-Edge --region us-east-1"
        in c
        for c in run.calls
    )


def test_p2004_a_missing_edge_stack_is_not_an_error(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE")
    box.answer(
        "list-stack-resources",
        "-Edge",
        stderr="An error occurred (ValidationError) when calling ListStackResources",
        code=255,
    )
    run = box.run(
        "destroy.sh",
        "--stack",
        "AgenticInsightsRadar",
        "--yes",
        env={"AWS_REGION": "eu-west-1"},
    )
    assert run.code == 0, run.out
    assert "no stack AgenticInsightsRadar-eu-west-1-Edge in us-east-1" in run.out


def test_p2004_apply_deletes_a_recorded_resource_in_its_own_region(
    box: Sandbox,
) -> None:
    plan = _plan(
        box,
        {
            "type": "AWS::Logs::LogGroup",
            "id": "aws-waf-logs-air-eu-west-1",
            "region": "us-east-1",
        },
    )
    box.answer(
        "describe-log-groups",
        stdout=json.dumps(
            {"logGroups": [{"logGroupName": "aws-waf-logs-air-eu-west-1"}]}
        ),
    )
    run = box.run("purge.py", "apply", str(plan))
    assert run.code == 0, run.out
    assert any(
        "delete-log-group --log-group-name aws-waf-logs-air-eu-west-1 --region us-east-1"
        in c
        for c in run.calls
    )


def test_p2004_a_plan_with_an_impossible_region_is_refused(box: Sandbox) -> None:
    plan = _plan(
        box, {"type": "AWS::KMS::Key", "id": KEY, "region": "us-east-1; rm -rf /"}
    )
    run = box.run("purge.py", "apply", str(plan))
    assert run.code == 2 and "refusing" in run.out
    assert run.calls == []


def test_p2053_a_fresh_install_refuses_a_retained_waf_log_group(box: Sandbox) -> None:
    box.answer(
        "describe-log-groups",
        "aws-waf-logs-air-us-east-1",
        stdout="aws-waf-logs-air-us-east-1\n",
    )
    run = box.run("deploy.sh", "admin@example.com")
    assert (
        run.code != 0 and "aws-waf-logs-air-us-east-1 is left in us-east-1" in run.out
    )
    assert run.cdk("deploy") == []


def test_p2028_an_upgrade_needs_no_email_because_the_stack_names_its_admin(
    box: Sandbox,
) -> None:
    fresh = box.run("deploy.sh")
    assert fresh.code != 0 and "needed only for a first install" in fresh.out
    box.stack("UPDATE_COMPLETE", {"FirstAdmin": "first@example.com"})
    run = box.run("deploy.sh", "--diff")  # the guide's upgrade command
    assert (
        run.code != 0 and "Not applied; nothing was deployed." in run.out
    )  # declined, not a usage error
    assert _context(run.cdk("diff")[0])["adminEmail"] == "first@example.com"


def test_p2028_an_upgrade_of_a_stack_without_the_output_still_asks(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE")  # no FirstAdmin output
    run = box.run("deploy.sh")
    assert run.code != 0 and "has no FirstAdmin output" in run.out


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AIR_LOG_RETENTION_DAYS": "7"}, "logRetentionDays from 365 to 7"),
        ({"AIR_AGENT_LOG_RETENTION_DAYS": "7"}, "agentLogRetentionDays from 30 to 7"),
        ({"AIR_BACKUP": "false"}, "would stop daily backups"),
    ],
)
def test_p2051_a_change_that_deletes_data_needs_the_flag(
    box: Sandbox, env: dict[str, str], message: str
) -> None:
    options = {
        "retainData": "true",
        "logRetentionDays": "365",
        "agentLogRetentionDays": "30",
        "backupPlan": "true",
    }
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps(options)})
    refused = box.run("deploy.sh", "admin@example.com", env=env)
    assert refused.code != 0 and message in refused.out
    assert "--allow-data-loss" in refused.out and refused.cdk("deploy") == []
    allowed = box.run("deploy.sh", "admin@example.com", "--allow-data-loss", env=env)
    assert allowed.code == 0, allowed.out
    assert len(allowed.cdk("deploy")) == 1


def test_p2051_the_environment_cannot_allow_data_loss(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps({"retainData": "true"})})
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={"AIR_RETAIN_DATA": "false", "AIR_ALLOW_DATA_LOSS": "true"},
    )
    assert run.code != 0 and "--allow-data-loss" in run.out
    assert run.cdk("deploy") == []


def test_p2085_suppress_invitation_cannot_change_on_a_live_stack(box: Sandbox) -> None:
    box.stack(
        "UPDATE_COMPLETE",
        {"DeployOptions": json.dumps({"suppressInvitation": "false"})},
    )
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_SUPPRESS_INVITATION": "true"}
    )
    assert run.code != 0 and "suppressInvitation cannot be changed" in run.out
    assert run.cdk("deploy") == []


def test_p2052_the_preflight_checks_the_chosen_chat_model(box: Sandbox) -> None:
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_CHAT_MODEL": "opus-5-5"})
    assert run.code == 0, run.out
    profiles = [
        c.split("--inference-profile-identifier ")[1].split()[0]
        for c in run.calls
        if "get-inference-profile" in c
    ]
    assert profiles == [
        "global.anthropic.claude-sonnet-5-5",
        "global.anthropic.claude-opus-5-5",
    ]
    assert "Browser and Code Interpreter" in run.out


def test_p2052_the_model_profiles_match_the_backend_catalog() -> None:
    catalog = (ROOT / "backend/src/insights_radar/modules/catalog.py").read_text()
    common = (ROOT / "deployment" / "common.sh").read_text()
    profiles = re.findall(r'"(global\.[a-z0-9.:-]+)"', catalog)
    assert len(profiles) == 6
    for profile in profiles:
        assert f"'{profile}'" in common, profile


def test_p2058_destroy_refuses_a_stack_that_is_changing(box: Sandbox) -> None:
    box.stack("UPDATE_IN_PROGRESS")
    run = box.run("destroy.sh", "--stack", "AgenticInsightsRadar", "--yes")
    assert run.code != 0 and "wait for it to finish" in run.out
    assert run.cdk("destroy") == []


def test_p2058_destroy_checks_for_uv_before_it_deletes_anything(box: Sandbox) -> None:
    """cdk.json synthesises with uv, so without it the destroy used to fail at synth, after the
    typed confirmation (P2-058)."""
    (box.bin / "uv").unlink()
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "destroy.sh",
        "--stack",
        "AgenticInsightsRadar",
        "--yes",
        env={"PATH": f"{box.bin}:/usr/bin:/bin"},
    )
    assert run.code != 0 and "uv is not installed" in run.out
    assert run.cdk("destroy") == []


def test_p2059_publish_site_names_the_account_and_sets_cache_headers(
    box: Sandbox,
) -> None:
    box.answer("describe-stacks", "SiteBucket", stdout="air-site\n")
    box.answer("describe-stacks", "DistributionId", stdout="E123\n")
    run = box.run("publish-site.sh", "--stack", "AgenticInsightsRadar-mr-test")
    assert run.code == 0, run.out
    assert (
        "Account 111122223333, Region us-east-1, stack AgenticInsightsRadar-mr-test"
        in run.out
    )
    sync = [c for c in run.calls if c.startswith("aws s3 sync")]
    assert sync and "public, max-age=31536000, immutable" in sync[0]
    assert (
        "public, max-age=31536000, immutable"
        in (ROOT / "infra/insights_radar_infra/frontend.py").read_text()
    )


def test_p2082_the_deploy_uses_the_reviewed_lock(box: Sandbox) -> None:
    assert "uv sync --frozen" in (ROOT / "deployment" / "deploy.sh").read_text()
    assert '"uv run --frozen' in (ROOT / "infra" / "cdk.json").read_text()


# ---- validate.sh: cleanup with fresh credentials (P2-013, P2-039) -------------------------------


def _state(box: Sandbox, region: str) -> Path:
    state = box.root / "validate-state"
    state.write_text(
        f"PREFIX=mrv1234567890\nWORK_DIR={box.root / 'work'}\nREPO_DIR={box.root}\nREGION={region}\n"
    )
    return state


def test_p2013_cleanup_only_does_nothing_when_no_deployment_was_recorded(
    box: Sandbox,
) -> None:
    run = box.run(
        "validate.sh",
        "--cleanup-only",
        env={"AIR_VALIDATE_STATE": str(box.root / "absent")},
    )
    assert run.code == 0 and "nothing to remove" in run.out
    assert run.calls == []  # not a single AWS call


def test_p2013_cleanup_only_destroys_the_recorded_deployment_and_names_what_is_left(
    box: Sandbox,
) -> None:
    state = _state(box, "us-west-2")
    box.stack(
        "UPDATE_COMPLETE"
    )  # every describe-stacks still finds a stack after the destroy
    run = box.run(
        "validate.sh", "--cleanup-only", env={"AIR_VALIDATE_STATE": str(state)}
    )
    assert run.code != 0, run.out
    assert "Destroying AgenticInsightsRadar-mrv1234567890" in run.out
    assert "stacks were left behind and keep billing" in run.out
    # P2-039: the edge stack of a deployment outside us-east-1 lives in us-east-1.
    assert "AgenticInsightsRadar-mrv1234567890-us-west-2-Edge in us-east-1" in run.out
    assert (
        state.exists()
    )  # still recorded, so another --cleanup-only can finish the job


def test_p2013_cleanup_only_forgets_a_deployment_once_nothing_is_left(
    box: Sandbox,
) -> None:
    state = _state(box, "us-east-1")
    box.answer("describe-stacks", stderr="Stack with id X does not exist", code=255)
    run = box.run(
        "validate.sh", "--cleanup-only", env={"AIR_VALIDATE_STATE": str(state)}
    )
    assert run.code == 0, run.out
    assert "No stack AgenticInsightsRadar-mrv1234567890 to destroy" in run.out
    assert "every stack of this validation was removed" in run.out
    assert not state.exists()


def test_sr210_cleanup_only_refuses_a_prefix_no_validation_run_creates(
    box: Sandbox,
) -> None:
    state = box.root / "validate-state"
    for prefix in ("air", "mrv1", "prod-mrv1234567890"):
        state.write_text(f"PREFIX={prefix}\nREPO_DIR={box.root}\nREGION=us-east-1\n")
        run = box.run(
            "validate.sh", "--cleanup-only", env={"AIR_VALIDATE_STATE": str(state)}
        )
        assert run.code != 0 and "refusing to destroy it" in run.out, prefix
        assert run.calls == [] and state.exists()


def test_p2013_a_validate_argument_it_does_not_know_is_refused(box: Sandbox) -> None:
    run = box.run("validate.sh", "--purge")
    assert run.code == 2 and "usage: validate.sh" in run.out


def test_p3_007_validate_upgrades_the_previous_release_to_the_code_under_validation(
    box: Sandbox,
) -> None:
    """An upgrade meets service-side refusals, delete ordering and name collisions that a fresh install
    never does, and all three upgrade failures of 8 October were found on a live stack (P3-007)."""
    repo = box.root / "release"
    (repo / "deployment").mkdir(parents=True)
    deploy = repo / "deployment" / "deploy.sh"
    script = ROOT / "deployment" / "destroy.sh"
    shutil.copy(script, repo / "deployment" / "destroy.sh")

    def commit(version: str) -> None:
        deploy.write_text(
            f'#!/usr/bin/env bash\necho "deployed {version} for $AIR_PREFIX" >> "$AIR_TEST_LOG"\n'
        )
        deploy.chmod(0o755)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.com",
                "commit",
                "-qm",
                version,
            ],
            check=True,
        )

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit("v1")
    subprocess.run(["git", "-C", str(repo), "tag", "v1"], check=True)
    commit("v2")
    _executable(box.bin / "curl", "#!/usr/bin/env bash\necho 000\n")
    box.answer(
        "describe-stacks", "OutputKey=='AppUrl'", stdout="https://d1.example.net\n"
    )
    box.answer("describe-stacks", "OutputKey=='UserPoolId'", stdout="us-east-1_x\n")
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "validate.sh",
        env={
            "AIR_VALIDATE_SOURCE": str(repo),
            "AIR_VALIDATE_UPGRADE_FROM": "v1",
            "AIR_VALIDATE_APP_WAIT_SECONDS": "0",
            "TMPDIR": str(box.root),
            "AIR_VALIDATE_CLEANUP": "false",
            "AIR_VALIDATE_STATE": str(box.root / "state"),
        },
    )
    deployed = [c for c in run.calls if c.startswith("deployed")]
    assert [d.split(" for ")[0] for d in deployed] == ["deployed v1", "deployed v2"], (
        run.out
    )
    assert (
        len({d.split(" for ")[1] for d in deployed}) == 1
    )  # one stack, upgraded in place
    assert "PASS: before the upgrade from v1: stack" in run.out
    assert (
        "PASS: the upgrade from v1 completed" in run.out
        and "PASS: after the upgrade: stack" in run.out
    )


def test_validate_deploys_the_sample_folder_of_a_larger_repository(
    box: Sandbox,
) -> None:
    """The sample lives in agentic-workloads/agentic-insights-radar of aws-samples/sample-apj-sup-sa:
    a checkout of that folder is validated from the folder's own files, not the repository root."""
    repo = box.root / "monorepo"
    sample = repo / "agentic-workloads" / "agentic-insights-radar"
    (sample / "deployment").mkdir(parents=True)
    shutil.copy(
        ROOT / "deployment" / "destroy.sh", sample / "deployment" / "destroy.sh"
    )
    deploy = sample / "deployment" / "deploy.sh"
    deploy.write_text(
        "#!/usr/bin/env bash\n"
        'echo "deployed root=$([[ -e README-ROOT.md ]] && echo yes || echo no)"'
        ' >> "$AIR_TEST_LOG"\n'
    )
    deploy.chmod(0o755)
    (repo / "README-ROOT.md").write_text("the repository's own README\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "sample",
        ],
        check=True,
    )
    _executable(box.bin / "curl", "#!/usr/bin/env bash\necho 000\n")
    box.answer(
        "describe-stacks", "OutputKey=='AppUrl'", stdout="https://d1.example.net\n"
    )
    box.answer("describe-stacks", "OutputKey=='UserPoolId'", stdout="us-east-1_x\n")
    box.stack("UPDATE_COMPLETE")
    run = box.run(
        "validate.sh",
        env={
            "AIR_VALIDATE_SOURCE": str(sample),
            "AIR_VALIDATE_APP_WAIT_SECONDS": "0",
            "TMPDIR": str(box.root),
            "AIR_VALIDATE_CLEANUP": "false",
            "AIR_VALIDATE_STATE": str(box.root / "state"),
        },
    )
    deployed = [c for c in run.calls if c.startswith("deployed")]
    assert deployed == ["deployed root=no"], (run.calls, run.out)


def test_validate_clones_only_the_sample_folder_by_default() -> None:
    script = (ROOT / "deployment" / "validate.sh").read_text()
    assert (
        'REPO_URL="${AIR_VALIDATE_REPO:-https://github.com/aws-samples/sample-apj-sup-sa.git}"'
        in script
    )
    assert (
        'SUBDIR="${AIR_VALIDATE_SUBDIR-agentic-workloads/agentic-insights-radar}"'
        in script
    )
    assert 'sparse-checkout set "$SUBDIR"' in script


def test_p3_007_the_validation_workflow_can_run_an_upgrade() -> None:
    workflow = (ROOT / ".github/workflows/deploy-validate.yml").read_text()
    assert (
        "upgrade_from:" in workflow
        and "AIR_VALIDATE_UPGRADE_FROM: ${{ inputs.upgrade_from }}" in workflow
    )
    assert "fetch-depth: 0" in workflow


@pytest.mark.parametrize(
    "script", ["deploy.sh", "destroy.sh", "publish-site.sh", "common.sh", "validate.sh"]
)
def test_scripts_parse(script: str) -> None:
    subprocess.run(["bash", "-n", str(ROOT / "deployment" / script)], check=True)


FEDERATED_OPTIONS = {
    "authMode": "federated",
    "oidcIssuer": "https://idp.example.com",
    "oidcClientId": "c",
    "oidcClientSecretArn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:s",
    "oidcProviderName": "SSO",
}


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AIR_AUTH_MODE": "cognito"}, "authMode cannot be changed"),
        ({"AIR_OIDC_PROVIDER_NAME": "Okta"}, "oidcProviderName cannot be changed"),
        (
            {"AIR_OIDC_ISSUER": "https://other.example.com"},
            "oidcIssuer cannot be changed",
        ),
    ],
)
def test_the_sign_in_identity_cannot_change_on_a_live_stack(
    box: Sandbox, env: dict[str, str], message: str
) -> None:
    """A new provider name or issuer makes every federated user a new account."""
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps(FEDERATED_OPTIONS)})
    run = box.run("deploy.sh", "admin@example.com", env=env)
    assert run.code != 0 and message in run.out
    assert run.cdk("deploy") == []


RENAMED = {
    "domainName": "new.example.com",
    "redirectDomains": "old.example.com",
    "parentZoneId": "Z0123456789ABCDEFGHIJ",
}


@pytest.mark.parametrize(
    ("deployed", "env", "message"),
    [
        # A second rename that puts the newer old name first, or drops the original.
        (
            RENAMED,
            {
                "AIR_DOMAIN_NAME": "third.example.com",
                "AIR_REDIRECT_DOMAINS": "new.example.com,old.example.com",
            },
            "must keep old.example.com first",
        ),
        (RENAMED, {"AIR_REDIRECT_DOMAINS": "none"}, "must keep old.example.com first"),
        # Adding a redirect without renaming: the original records would move to the alias.
        (
            {
                "domainName": "radar.example.com",
                "parentZoneId": "Z0123456789ABCDEFGHIJ",
            },
            {"AIR_REDIRECT_DOMAINS": "alias.example.com"},
            "must start with the name",
        ),
    ],
)
def test_redirect_domains_cannot_move_the_original_dns_records(
    box: Sandbox, deployed: dict[str, str], env: dict[str, str], message: str
) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps(deployed)})
    run = box.run("deploy.sh", "admin@example.com", env=env)
    assert run.code != 0 and message in run.out
    assert run.cdk("deploy") == []


@pytest.mark.parametrize(
    ("deployed", "env"),
    [
        # The first rename, a second rename that keeps the original first, and a plain update.
        (
            {"domainName": "old.example.com", "parentZoneId": "Z0123456789ABCDEFGHIJ"},
            {
                "AIR_DOMAIN_NAME": "new.example.com",
                "AIR_REDIRECT_DOMAINS": "OLD.example.com",
            },
        ),
        (
            RENAMED,
            {
                "AIR_DOMAIN_NAME": "third.example.com",
                "AIR_REDIRECT_DOMAINS": "old.example.com,new.example.com",
            },
        ),
        (RENAMED, {}),
        # A blank first entry is skipped, as config.py skips it.
        (RENAMED, {"AIR_REDIRECT_DOMAINS": " ,OLD.example.com"}),
    ],
)
def test_a_rename_that_keeps_the_original_first_deploys(
    box: Sandbox, deployed: dict[str, str], env: dict[str, str]
) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps(deployed)})
    run = box.run("deploy.sh", "admin@example.com", env=env)
    assert "redirectDomains must" not in run.out
    (deploy,) = run.cdk("deploy")
    assert "redirectDomains=" in deploy


def test_a_federated_stack_redeploys_with_its_recorded_identity(box: Sandbox) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps(FEDERATED_OPTIONS)})
    run = box.run("deploy.sh", "admin@example.com")
    assert "cannot be changed" not in run.out
    (deploy,) = run.cdk("deploy")
    assert "authMode=federated" in deploy and "oidcProviderName=SSO" in deploy
    # No password is sent in this mode: the closing line says how to get in instead.
    assert "sign in through SSO" in run.out and "temporary password" not in run.out


def test_none_clears_an_optional_setting_that_an_update_would_carry_over(
    box: Sandbox,
) -> None:
    # Round 7: an unset variable keeps what the stack was deployed with, so unsetting cannot remove it.
    box.stack(
        "UPDATE_COMPLETE",
        {
            "FirstAdmin": "first@example.com",
            "DeployOptions": json.dumps(
                {"oidcClientSecretVersion": "00000000-0000-4000-8000-000000000000"}
            ),
        },
    )
    run = box.run("deploy.sh", env={"AIR_OIDC_CLIENT_SECRET_VERSION": "NONE"})
    assert run.code == 0, run.out
    assert _context(run.cdk("deploy")[0]).get("oidcClientSecretVersion", "") == ""
    assert (
        "Clearing oidcClientSecretVersion (was 00000000-0000-4000-8000-000000000000"
        in run.out
    )


def test_none_is_an_ordinary_value_for_a_setting_with_a_default(box: Sandbox) -> None:
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_WAF_RATE_LIMIT": "none"})
    assert run.code != 0 or _context(run.cdk("deploy")[0]).get("wafRateLimit") == "none"


def test_c59_purge_schedules_the_kept_digest_secrets_for_deletion(box: Sandbox) -> None:
    """The digest secrets (one holds the live Slack token) are kept with the data: a purge deletes them too,
    with the 7-day recovery window, and leaves one already scheduled alone."""
    live = "arn:aws:secretsmanager:us-east-1:111122223333:secret:SlackBotToken1-aBcDeF"
    gone = "arn:aws:secretsmanager:us-east-1:111122223333:secret:UnsubKey-000000"
    box.answer(f"describe-secret --secret-id {live}", stdout=json.dumps({"ARN": live}))
    box.answer(
        f"describe-secret --secret-id {gone}",
        stdout=json.dumps({"ARN": gone, "DeletedDate": 1}),
    )
    plan = _plan(
        box,
        {"type": "AWS::SecretsManager::Secret", "id": live},
        {"type": "AWS::SecretsManager::Secret", "id": gone},
    )
    run = box.run("purge.py", "apply", str(plan))
    assert run.code == 0, run.out
    deletes = [c for c in run.calls if "delete-secret" in c]
    assert (
        len(deletes) == 1
        and live in deletes[0]
        and "--recovery-window-in-days 7" in deletes[0]
    )


def test_c60_the_plan_records_a_secret_the_stack_left_behind_with_its_key(
    box: Sandbox,
) -> None:
    """Digest delivery switched off keeps its secrets (retainData) and stops owning them: the plan finds them by
    the recorded key, skips one already scheduled and another account's, so the purge deletes them too."""
    kept = "arn:aws:secretsmanager:us-east-1:111122223333:secret:BackendDigestSlackSecret1-aBcDeF"
    box.answer(
        "list-stack-resources",
        stdout=json.dumps(
            {
                "StackResourceSummaries": [
                    {"ResourceType": "AWS::KMS::Key", "PhysicalResourceId": KEY}
                ]
            }
        ),
    )
    box.answer(
        "list-secrets",
        stdout=json.dumps(
            {
                "SecretList": [
                    {
                        "ARN": kept,
                        "KmsKeyId": KEY_ARN,
                        "Description": "air: Slack bot token (xoxb-...) or incoming-webhook URL for digest delivery",
                    },
                    {
                        "ARN": kept.replace("Slack", "Oidc"),
                        "KmsKeyId": KEY_ARN,
                        "Description": "our OIDC client secret",
                    },
                    {
                        "ARN": kept.replace("aBcDeF", "GhIjKl"),
                        "KmsKeyId": KEY_ARN,
                        "DeletedDate": 1,
                    },
                    {
                        "ARN": kept.replace("Slack", "Other"),
                        "KmsKeyId": KEY_ARN.replace("1111", "9999"),
                    },
                ]
            }
        ),
    )
    plan = box.root / "plan.json"
    assert box.run("purge.py", "plan", "AgenticInsightsRadar", str(plan)).code == 0
    found = [
        r
        for r in json.loads(plan.read_text())["resources"]
        if r["type"] == "AWS::SecretsManager::Secret"
    ]
    assert found == [
        {"type": "AWS::SecretsManager::Secret", "id": kept, "region": "us-east-1"}
    ]


def test_c60_purge_calls_a_secret_that_is_already_gone_gone(box: Sandbox) -> None:
    gone = "arn:aws:secretsmanager:us-east-1:111122223333:secret:UnsubKey-111111"
    box.answer(
        f"describe-secret --secret-id {gone}",
        code=254,
        stderr="An error occurred (ResourceNotFoundException)",
    )
    plan = _plan(box, {"type": "AWS::SecretsManager::Secret", "id": gone})
    run = box.run("purge.py", "apply", str(plan))
    assert run.code == 0, run.out
    assert "already gone" in run.out and not [
        c for c in run.calls if "delete-secret" in c
    ]


@pytest.mark.parametrize(
    ("key", "var"),
    [
        ("unsubscribeKeyGeneration", "AIR_UNSUBSCRIBE_KEY_GENERATION"),
        ("slackSecretGeneration", "AIR_SLACK_SECRET_GENERATION"),
    ],
)
def test_c63_a_digest_secret_generation_is_carried_over_and_guarded(
    box: Sandbox, key: str, var: str
) -> None:
    """A secret's generation is a top-level option (scan cycle 63): unset, it is carried over from the deployed
    stack whatever digestDelivery says, even with digest delivery turned off; a lower one replaces that secret,
    so it is refused without --allow-data-loss, like the other changes that lose data."""
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
        "slackMode": "bot",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {"DeployOptions": json.dumps({"digestDelivery": json.dumps(digest), key: "2"})},
    )
    for env in (
        {},
        {
            "AIR_DIGEST_DELIVERY": json.dumps(
                {
                    "emailMode": "ses",
                    "emailSender": "d@example.com",
                    "sesDomain": "example.com",
                }
            )
        },
    ):
        kept = box.run("deploy.sh", "admin@example.com", env=env)
        assert kept.code == 0, kept.out
        assert f"{key} from" not in kept.out
        assert [c for c in kept.cdk("deploy") if f" -c {key}=2 " in f"{c} "], kept.calls
    refused = box.run("deploy.sh", "admin@example.com", env={var: "1"})
    assert refused.code != 0 and f"{key} from 2 to 1" in refused.out, refused.out
    assert refused.cdk("deploy") == []
    allowed = box.run(
        "deploy.sh", "admin@example.com", "--allow-data-loss", env={var: "1"}
    )
    assert allowed.code == 0, allowed.out
    assert f"WARNING: --allow-data-loss: {key} from 2 to 1" in allowed.out
    assert [c for c in allowed.cdk("deploy") if f" -c {key}=1 " in f"{c} "], (
        allowed.calls
    )
    raised = box.run("deploy.sh", "admin@example.com", env={var: "3"})
    assert raised.code == 0 and f"Changing {key} from 2 to 3 ({var})." in raised.out, (
        raised.out
    )
    assert [c for c in raised.cdk("deploy") if f" -c {key}=3 " in f"{c} "], raised.calls


@pytest.mark.parametrize(
    "off", ["none", json.dumps({"slackMode": "off", "emailMode": "off"})]
)
def test_c65_turning_digest_delivery_off_needs_the_flag(box: Sandbox, off: str) -> None:
    """Turning digest delivery fully off retires both secrets, the unsubscribe key with every link already
    sent, so it is refused without --allow-data-loss; the generation guard then has nothing to replace."""
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {
            "DeployOptions": json.dumps(
                {"digestDelivery": json.dumps(digest), "slackSecretGeneration": "2"}
            )
        },
    )
    env = {"AIR_DIGEST_DELIVERY": off, "AIR_SLACK_SECRET_GENERATION": "0"}
    refused = box.run("deploy.sh", "admin@example.com", env=env)
    assert (
        refused.code != 0
        and "turning digest delivery off would retire both digest secrets"
        in refused.out
    )
    assert (
        "would replace that secret" not in refused.out and refused.cdk("deploy") == []
    )
    allowed = box.run("deploy.sh", "admin@example.com", "--allow-data-loss", env=env)
    assert allowed.code == 0, allowed.out
    assert "WARNING: --allow-data-loss: turning digest delivery off" in allowed.out
    assert "would replace that secret" not in allowed.out


@pytest.mark.parametrize(
    ("value", "state"),
    [
        ('{"slackMode":"","emailMode":""}', "off"),
        ('{"slackMode":null}', "off"),
        ('{"emailMode":" off "}', "off"),
        ('{"slackMode":"off","emailMode":"off","sesDmarc":false}', "off"),
        ('{"slackMode":" bot "}', "on"),
        ('{"slackmode":"bot"}', "refused"),
        ('{"slack_mode":"bot"}', "refused"),
        ('{"slackMode ":"bot"}', "refused"),
        ('{"slackMode":1}', "refused"),
        ('{"slackMode":"off","dmResolver":"email"}', "refused"),
        ("not json", "refused"),
        ("[]", "refused"),
    ],
)
def test_c67_the_digest_guards_read_a_value_as_synth_does(
    box: Sandbox, value: str, state: str
) -> None:
    """deploy.sh asks DigestDelivery.parse how synth will read digestDelivery: off meets the turn-off refusal,
    on meets the generation guard, and a value synth refuses meets neither, since synth then stops with the
    real error and pointing at --allow-data-loss would only waive the other guards (scan cycles 66 and 67)."""
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {
            "DeployOptions": json.dumps(
                {"digestDelivery": json.dumps(digest), "slackSecretGeneration": "2"}
            )
        },
    )
    env = {"AIR_DIGEST_DELIVERY": value, "AIR_SLACK_SECRET_GENERATION": "1"}
    run = box.run("deploy.sh", "admin@example.com", env=env)
    turned_off = (
        "turning digest delivery off would retire both digest secrets" in run.out
    )
    lowered = "slackSecretGeneration from 2 to 1 would replace that secret" in run.out
    try:
        DigestDelivery.parse(value)
        parser_message = None
    except ValueError as error:
        parser_message = str(error)
    # The parser's own message stops the deploy (c71), word for word (c73).
    refused = parser_message is not None and f"error: {parser_message}" in run.out
    assert (turned_off, lowered, refused) == {
        "off": (True, False, False),
        "on": (False, True, False),
        "refused": (False, False, True),
    }[state], run.out
    assert run.code != 0 and run.cdk("deploy") == []


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("AIR_DIGEST_DELIVERY", '\n{"slackMode":"bot"}'),
        ("AIR_DIGEST_DELIVERY", '{"slackMode":"bot"}\r'),
        ("AIR_UNSUBSCRIBE_KEY_GENERATION", "1\n"),
        ("AIR_LOG_RETENTION_DAYS", "30\n"),
        ("AIR_DIGEST_DELIVERY", '\u2028{"slackMode":"bot"}'),
        ("AIR_UNSUBSCRIBE_KEY_GENERATION", "1\u2028"),
        ("AIR_CHAT_MEMORY_DAYS", "7\u2029"),
        ("AIR_ALARM_EMAIL", "\nops@example.com"),
        ("AIR_PREFIX", "air\u00e9"),
    ],
)
def test_c68_an_option_with_a_line_break_is_refused(
    box: Sandbox, var: str, value: str
) -> None:
    """The CDK CLI reads -c key=value only up to the first line break, so a value with one would reach synth
    different from what the data-loss guards checked: deploy.sh refuses it before any guard (scan cycle 68)."""
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {
            "DeployOptions": json.dumps(
                {"digestDelivery": json.dumps(digest), "unsubscribeKeyGeneration": "2"}
            )
        },
    )
    run = box.run("deploy.sh", "admin@example.com", env={var: value})
    assert run.code != 0 and "must be printable ASCII on one line" in run.out, run.out
    assert run.cdk("deploy") == []


def test_c68_a_carried_over_digest_value_meets_the_generation_guard(
    box: Sandbox,
) -> None:
    """With AIR_DIGEST_DELIVERY unset, the deployed value is carried over (on), so a lower generation is guarded."""
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {
            "DeployOptions": json.dumps(
                {"digestDelivery": json.dumps(digest), "unsubscribeKeyGeneration": "2"}
            )
        },
    )
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_UNSUBSCRIBE_KEY_GENERATION": "1"}
    )
    assert (
        run.code != 0
        and "unsubscribeKeyGeneration from 2 to 1 would replace that secret" in run.out
    ), run.out


def test_c68_a_parser_that_cannot_run_stops_the_deploy(box: Sandbox) -> None:
    """Only the parser's own refusal skips the digest guards; a parser that cannot run fails closed."""
    (box.root / "infra" / "insights_radar_infra" / "config.py").write_text(
        "import not_a_module_anywhere\n"
    )
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {"DeployOptions": json.dumps({"digestDelivery": json.dumps(digest)})},
    )
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": "none"})
    assert run.code != 0 and "could not run the digestDelivery check" in run.out, (
        run.out
    )
    assert run.cdk("deploy") == []


def test_c70_a_digest_value_too_deep_to_check_stops_the_deploy(box: Sandbox) -> None:
    """Python versions differ in how deep JSON may nest, so a value this check cannot parse might parse in
    synth: it stops the deploy rather than skip both digest guards (scan cycle 70)."""
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {"DeployOptions": json.dumps({"digestDelivery": json.dumps(digest)})},
    )
    # How deep the interpreter here can go varies, so make the parser hit its limit on any value.
    config = box.root / "infra" / "insights_radar_infra" / "config.py"
    config.write_text(
        config.read_text()
        + "\n\ndef _too_deep(cls, raw):\n    raise RecursionError\n\n\nDigestDelivery.parse = classmethod(_too_deep)\n"
    )
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={"AIR_DIGEST_DELIVERY": '{"emailMode":"off"}'},
    )
    assert run.code != 0 and "error: digestDelivery: RecursionError" in run.out, run.out
    assert run.cdk("deploy") == []


def test_c70_unexpected_output_from_the_digest_check_stops_the_deploy(
    box: Sandbox,
) -> None:
    """Anything but on, off, refused or nested from the check stops the deploy (scan cycle 69 #4)."""
    config = box.root / "infra" / "insights_radar_infra" / "config.py"
    config.write_text(config.read_text() + '\nprint("notice")\n')
    digest = {
        "emailMode": "ses",
        "emailSender": "d@example.com",
        "sesDomain": "example.com",
    }
    box.stack(
        "UPDATE_COMPLETE",
        {"DeployOptions": json.dumps({"digestDelivery": json.dumps(digest)})},
    )
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": "none"})
    assert run.code != 0 and "not on or off" in run.out, run.out
    assert run.cdk("deploy") == []


def test_c70_a_carried_over_value_names_the_stack_as_its_source(box: Sandbox) -> None:
    """A value read back from DeployOptions that fails the intake check says so, and how to replace it."""
    box.stack("UPDATE_COMPLETE", {"DeployOptions": json.dumps({"seed": "caf\u00e9"})})
    run = box.run("deploy.sh", "admin@example.com")
    assert (
        run.code != 0
        and "the DeployOptions output of" in run.out
        and "set AIR_SEED to replace it" in run.out
    )


def test_c71_command_line_values_are_checked_before_any_aws_call(box: Sandbox) -> None:
    """A prefix or alarm email that cannot reach synth intact stops the deploy before AWS is called."""
    for env in (
        {"AIR_PREFIX": "air\u00e9"},
        {"AIR_ALARM_EMAIL": "ops\u2028@example.com"},
    ):
        run = box.run("deploy.sh", "admin@example.com", env=env)
        assert run.code != 0 and "must be printable ASCII on one line" in run.out, (
            run.out
        )
        assert run.calls == [], run.calls


def test_c71_an_update_ignores_an_unusable_first_argument(box: Sandbox) -> None:
    """On an update the stack's FirstAdmin replaces the first argument, so that argument is not checked."""
    box.stack(
        "UPDATE_COMPLETE", {"FirstAdmin": "admin@example.com", "DeployOptions": "{}"}
    )
    run = box.run("deploy.sh", "zo\u00eb@example.com")
    assert run.code == 0, run.out
    assert [
        c for c in run.cdk("deploy") if " -c adminEmail=admin@example.com " in f"{c} "
    ], run.calls


def test_c73_an_update_without_a_first_admin_output_blames_the_first_argument(
    box: Sandbox,
) -> None:
    box.stack("UPDATE_COMPLETE", {"DeployOptions": "{}"})
    run = box.run(
        "deploy.sh",
        "zo\u00eb@example.com",
        env={"ADMIN_FROM_STACK": "true", "ADMIN_SOURCE": "x"},
    )
    assert (
        run.code != 0 and "(from the first argument or AIR_ADMIN_EMAIL)" in run.out
    ), run.out


def test_c73_a_refused_carried_over_digest_value_names_its_source(box: Sandbox) -> None:
    recorded = json.dumps(
        {
            "emailMode": "ses",
            "emailSender": "zo\u00eb@example.com",
            "sesDomain": "example.com",
        }
    )
    box.stack(
        "UPDATE_COMPLETE", {"DeployOptions": json.dumps({"digestDelivery": recorded})}
    )
    run = box.run("deploy.sh", "admin@example.com")
    assert (
        run.code != 0
        and "error: digestDelivery.emailSender must be an email address in printable ASCII"
        in run.out
    )
    assert (
        "is the DeployOptions output of" in run.out
        and "set AIR_DIGEST_DELIVERY to replace it" in run.out
    )


def test_c72_a_first_install_refuses_an_unusable_first_argument(box: Sandbox) -> None:
    run = box.run("deploy.sh", "zo\u00eb@example.com")
    assert (
        run.code != 0 and "(from the first argument or AIR_ADMIN_EMAIL)" in run.out
    ), run.out
    assert run.cdk("deploy") == []


def test_c72_an_update_with_an_unusable_first_admin_says_this_release_cannot_update_it(
    box: Sandbox,
) -> None:
    box.stack(
        "UPDATE_COMPLETE", {"FirstAdmin": "zo\u00eb@example.com", "DeployOptions": "{}"}
    )
    run = box.run("deploy.sh", "admin@example.com")
    assert (
        run.code != 0
        and "the FirstAdmin output of" in run.out
        and "cannot update this stack" in run.out
    )
    assert "cdk deploy directly" not in run.out and run.cdk("deploy") == []


def test_c64_lowering_a_generation_with_digest_delivery_off_replaces_nothing(
    box: Sandbox,
) -> None:
    """With digest delivery off the stack has no digest secret, so a lower generation needs no flag (scan cycle 64)."""
    box.stack(
        "UPDATE_COMPLETE", {"DeployOptions": json.dumps({"slackSecretGeneration": "2"})}
    )
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_SLACK_SECRET_GENERATION": "0"}
    )
    assert run.code == 0 and "would replace that secret" not in run.out, run.out
    assert [
        c for c in run.cdk("deploy") if " -c slackSecretGeneration=0 " in f"{c} "
    ], run.calls


SES_ZONE_VALUE = json.dumps(
    {
        "emailMode": "ses",
        "emailSender": "d@mail.example.com",
        "sesDomain": "Mail.Example.com",
        "sesHostedZoneId": "Z0123456789ABCDEFGHIJ",
    }
)
ZID = "Z0123456789ABCDEFGHIJ"


def ses_zone(
    box: Sandbox,
    zone: str = "example.com.",
    private: str = "False",
    ns: dict[str, str] | None = None,
) -> None:
    """Route 53 as the zone check reads it: the zone and its NS record sets by name."""
    for name, answer in (ns or {}).items():
        box.answer(
            "list-resource-record-sets",
            f"--hosted-zone-id {ZID} --start-record-name {name} ",
            stdout=answer,
        )
    box.answer(
        "route53 get-hosted-zone", ZID, "HostedZone.Name", stdout=f"{zone}\t{private}\n"
    )


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ({}, None),
        ({"zone": "mail.example.com."}, None),  # the zone is sesDomain itself
        (
            {"ns": {"team.example.com": "team.example.com.\n"}},
            None,
        ),  # an unrelated delegation is never asked for
        (
            {"ns": {"mail.example.com": "mx.example.com.\n"}},
            None,
        ),  # the next record set is another name
        ({"zone": "EXAMPLE.COM."}, None),
        (
            {"ns": {"mail.example.com": "mail.example.com.\n"}},
            "delegates mail.example.com to another hosted zone",
        ),
        (
            {"ns": {"_domainkey.mail.example.com": "_domainkey.mail.example.com.\n"}},
            "delegates _domainkey.mail.example.com",
        ),
        (
            # Without sesDmarc the stack writes nothing under _dmarc: a DMARC policy hosted elsewhere is fine (cycle 3).
            {"ns": {"_dmarc.mail.example.com": "_DMARC.mail.example.com.\n"}},
            None,
        ),
        (
            {"zone": "com.", "ns": {"example.com": "example.com.\n"}},
            "delegates example.com to another",
        ),
        ({"private": "True"}, "is a private hosted zone"),
        (
            {"zone": "other.org."},
            "is the zone 'other.org', which does not hold sesDomain mail.example.com",
        ),
        (
            {"zone": "ample.com."},
            "which does not hold sesDomain",
        ),  # a suffix that is not a label boundary
        ({"zone": ""}, "is the zone '', which does not hold"),
    ],
)
def test_ses_hosted_zone_must_be_the_zone_the_world_reads(
    box: Sandbox, setup: dict[str, Any], error: str | None
) -> None:
    """16 Nov 2026: A deployment's DKIM records went to the parent zone while its subdomain was delegated, so SES never
    verified the domain and nothing failed. deploy.sh refuses a zone that does not hold sesDomain, is private, or
    delegates sesDomain (or a name SES reads under it) away. Scan cycle 2 dropped the parent check: only public
    DNS can say which zone the parent delegates to."""
    ses_zone(box, **setup)
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": SES_ZONE_VALUE}
    )
    if error is None:
        assert run.code == 0, run.out
        assert run.cdk("deploy")
        lookups = [
            c for c in run.calls if "list-resource-record-sets" in c and ZID in c
        ]
        assert len(lookups) <= 4 and all(
            "--max-items 1" in c for c in lookups
        )  # never the whole zone
        assert "list-hosted-zones-by-name" not in "\n".join(
            run.calls
        )  # no parent lookup (scan cycle 2)
    else:
        assert run.code != 0 and error in run.out, run.out
        assert run.cdk("deploy") == []


@pytest.mark.parametrize(
    "call", ["HostedZone.Name", "--start-record-name mail.example.com "]
)
def test_ses_hosted_zone_that_cannot_be_read_stops_the_deploy(
    box: Sandbox, call: str
) -> None:
    box.answer(
        call, code=254, stderr="AccessDenied\n"
    )  # first: the first matching answer wins
    ses_zone(box)
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": SES_ZONE_VALUE}
    )
    assert (
        run.code != 0
        and f"{ZID} (digestDelivery.sesHostedZoneId; deploy.sh needs route53:"
        in run.out
    ), run.out
    assert run.cdk("deploy") == []


@pytest.mark.parametrize(
    "value",
    [
        "",
        json.dumps(
            {
                "emailMode": "ses",
                "emailSender": "d@example.com",
                "sesDomain": "example.com",
            }
        ),
        json.dumps({"slackMode": "bot"}),
    ],
)
def test_no_ses_hosted_zone_means_no_route53_call(box: Sandbox, value: str) -> None:
    run = box.run(
        "deploy.sh",
        "admin@example.com",
        env={"AIR_DIGEST_DELIVERY": value} if value else {},
    )
    assert run.code == 0 and "route53" not in "\n".join(run.calls), run.out


def test_a_refused_value_stops_the_zone_check_with_the_parsers_message(
    box: Sandbox,
) -> None:
    """Scan cycle 2, finding 5: a value the parser refuses no longer skips the zone check on a first deploy."""
    value = json.dumps(
        {
            "emailMode": "ses",
            "emailSender": "zoë@example.com",
            "sesDomain": "example.com",
            "sesHostedZoneId": ZID,
        }
    )
    run = box.run("deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": value})
    assert (
        run.code != 0
        and "error: digestDelivery.emailSender must be an email address in printable ASCII"
        in run.out
    )
    assert run.cdk("deploy") == [] and "route53" not in "\n".join(run.calls)


def test_ses_zone_check_reads_the_carried_over_value(box: Sandbox) -> None:
    box.stack(
        "UPDATE_COMPLETE",
        {"DeployOptions": json.dumps({"digestDelivery": SES_ZONE_VALUE})},
    )
    ses_zone(box, ns={"mail.example.com": "mail.example.com.\n"})
    run = box.run("deploy.sh", "admin@example.com")
    assert run.code != 0 and "delegates mail.example.com" in run.out, run.out


def test_ses_zone_check_that_cannot_run_says_so(box: Sandbox) -> None:
    (box.root / "infra" / "insights_radar_infra" / "config.py").write_text(
        "raise SystemExit(5)\n"
    )
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": SES_ZONE_VALUE}
    )
    assert run.code != 0 and "could not run the digestDelivery" in run.out, run.out


@pytest.mark.parametrize(
    ("delegated", "advice"),
    [
        ("_dmarc.mail.example.com", "Remove that NS delegation, or leave sesDmarc off"),
        ("_domainkey.mail.example.com", "Remove that NS delegation."),
        (
            "mail.example.com",
            "Set sesHostedZoneId to the hosted zone for mail.example.com.",
        ),
    ],
)
def test_a_delegation_error_gives_advice_that_can_be_followed(
    box: Sandbox, delegated: str, advice: str
) -> None:
    """Scan cycle 3, findings 1 and 2: with sesDmarc on, _dmarc is checked too; a delegation below sesDomain is
    never answered with 'use that zone', which the check itself would then refuse."""
    value = json.loads(SES_ZONE_VALUE) | {"sesDmarc": True}
    ses_zone(box, ns={delegated: f"{delegated}.\n"})
    run = box.run(
        "deploy.sh", "admin@example.com", env={"AIR_DIGEST_DELIVERY": json.dumps(value)}
    )
    assert (
        run.code != 0
        and f"delegates {delegated} to another hosted zone" in run.out
        and advice in run.out
    ), run.out
    assert "Set sesHostedZoneId to the hosted zone for _" not in run.out
