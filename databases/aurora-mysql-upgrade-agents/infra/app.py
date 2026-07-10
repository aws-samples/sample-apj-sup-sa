#!/usr/bin/env python3
"""CDK app entry point for Aurora MySQL Upgrade multi-agent deployment."""
import os
import re
import secrets
import sys
from pathlib import Path

import aws_cdk as cdk
from dotenv import load_dotenv

from cdk_aurora_upgrade.stack import AuroraUpgradeAgentStack

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"❌ Required environment variable {name} is not set. "
            f"Copy infra/.env.example to infra/.env and fill in the values."
        )
    return value


def _split(name: str) -> list[str]:
    raw = _require(name)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_deployment_suffix() -> str:
    """Return a stable per-deployment suffix, generating + persisting one on first run.

    The suffix is appended to AgentCore runtime names so multiple deployments
    in the same account/region don't collide. It must persist across redeploys
    of the same stack — otherwise CloudFormation would replace the runtime on
    every `cdk deploy`. We persist it to infra/.env.
    """
    existing = os.environ.get("DEPLOYMENT_SUFFIX", "").strip()
    if existing:
        if not re.fullmatch(r"[a-z0-9]{4,12}", existing):
            raise SystemExit(
                f"❌ DEPLOYMENT_SUFFIX={existing!r} must be 4-12 lowercase alphanumeric chars."
            )
        return existing

    suffix = secrets.token_hex(3)  # 6 lowercase hex chars
    line = f"DEPLOYMENT_SUFFIX={suffix}\n"
    if ENV_PATH.exists():
        existing_text = ENV_PATH.read_text()
        prefix = "" if existing_text.endswith("\n") or not existing_text else "\n"
        ENV_PATH.write_text(existing_text + prefix + line)
    else:
        ENV_PATH.write_text(line)
    print(
        f"ℹ️  Generated DEPLOYMENT_SUFFIX={suffix} and saved it to {ENV_PATH}. "
        f"Future deploys will reuse this value.",
        file=sys.stderr,
    )
    os.environ["DEPLOYMENT_SUFFIX"] = suffix
    return suffix


account = _require("CDK_DEFAULT_ACCOUNT")
region = _require("CDK_DEFAULT_REGION")
suffix = _resolve_deployment_suffix()

# DB credentials live in Secrets Manager; agents resolve them at run time.
# DB_SECRET_ID may be a secret name or a full ARN. Build the ARN for the IAM
# grant (a name maps to arn:...:secret:<name>, with a 6-char suffix wildcard
# added in the stack to match Secrets Manager's auto-generated suffix).
db_secret_id = _require("DB_SECRET_ID")
if db_secret_id.startswith("arn:"):
    db_secret_arn = db_secret_id
else:
    db_secret_arn = f"arn:aws:secretsmanager:{region}:{account}:secret:{db_secret_id}"

app = cdk.App()

AuroraUpgradeAgentStack(
    app,
    "AuroraUpgradeAgentStack",
    env=cdk.Environment(account=account, region=region),
    vpc_id=_require("VPC_ID"),
    subnet_ids=_split("SUBNET_IDS"),
    security_group_ids=_split("SECURITY_GROUP_IDS"),
    reports_bucket_name=f"{_require('REPORTS_BUCKET_NAME')}-{suffix}",
    model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6").strip(),
    db_secret_arn=db_secret_arn,
    agent_names={
        "orchestrator": os.environ.get(
            "ORCHESTRATOR_NAME", f"aurora_upgrade_orchestrator_{suffix}"
        ),
        "variables_compare": os.environ.get(
            "VARIABLES_COMPARE_NAME", f"aurora_variables_compare_{suffix}"
        ),
        "error_log_analyzer": os.environ.get(
            "ERROR_LOG_ANALYZER_NAME", f"aurora_error_log_analyzer_{suffix}"
        ),
        "upgrade_readiness": os.environ.get(
            "UPGRADE_READINESS_NAME", f"aurora_upgrade_readiness_analyzer_{suffix}"
        ),
        "query_risk_scorer": os.environ.get(
            "QUERY_RISK_SCORER_NAME", f"aurora_query_risk_scorer_{suffix}"
        ),
        "plan_diff": os.environ.get(
            "PLAN_DIFF_NAME", f"aurora_plan_diff_{suffix}"
        ),
    },
)

app.synth()
