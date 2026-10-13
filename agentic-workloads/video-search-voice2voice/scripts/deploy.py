#!/usr/bin/env python
"""Deploy Talk2Vid behind CloudFront.

    browser ──HTTPS──▶ CloudFront (Basic Auth) ──▶ ALB ──▶ Fargate task
       │                                                      │
       └──────── voice media ──▶ Daily cloud ◀── outbound ─────┘

Inbound on our infrastructure: 443 to CloudFront only. The voice media never
touches this stack, so there is no UDP path to open.

The image is built by CodeBuild (no local Docker needed) and pushed to ECR; the
runtime is one CloudFormation stack.

    uv run python scripts/deploy.py                 # build + deploy (or update)
    uv run python scripts/deploy.py --skip-build    # redeploy the current image
    uv run python scripts/deploy.py --status        # URL, task state, recent logs
    uv run python scripts/deploy.py --park          # scale to 0 (stop paying for Fargate)
    uv run python scripts/deploy.py --resume        # scale back to 1
    uv run python scripts/deploy.py --teardown      # delete everything
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import secrets
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from talk2vid import config  # noqa: E402

STACK = "talk2vid"
ECR_REPO_NAME = "talk2vid"
BUILD_PROJECT = "talk2vid-image-build"
BUILD_ROLE = "Talk2VidCodeBuildRole"
SECRET_NAME = "talk2vid/runtime"
SOURCE_KEY = f"{config.S3_PREFIX}/build/source.zip"

# Only what the image needs; keeps the upload small and avoids shipping secrets.
INCLUDE = ("pyproject.toml", "uv.lock", "deploy/Dockerfile", "deploy/entrypoint.sh",
           "deploy/buildspec.yml")
INCLUDE_TREES = ("talk2vid", "client")
EXCLUDE_PARTS = ("__pycache__", ".pyc", ".DS_Store")


def clients(region: str) -> dict:
    return {
        "s3": boto3.client("s3", region_name=region),
        "ecr": boto3.client("ecr", region_name=region),
        "cb": boto3.client("codebuild", region_name=region),
        "cfn": boto3.client("cloudformation", region_name=region),
        "iam": boto3.client("iam"),
        "sm": boto3.client("secretsmanager", region_name=region),
        "ecs": boto3.client("ecs", region_name=region),
        "logs": boto3.client("logs", region_name=region),
        "ec2": boto3.client("ec2", region_name=region),
        "sts": boto3.client("sts", region_name=region),
    }


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def make_source_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in INCLUDE:
            path = ROOT / rel
            if not path.exists():
                raise FileNotFoundError(path)
            zf.write(path, rel)
        for tree in INCLUDE_TREES:
            for path in sorted((ROOT / tree).rglob("*")):
                if path.is_dir() or any(part in str(path) for part in EXCLUDE_PARTS):
                    continue
                zf.write(path, str(path.relative_to(ROOT)))
    return buf.getvalue()


def ensure_ecr(c: dict) -> str:
    try:
        repo = c["ecr"].create_repository(
            repositoryName=ECR_REPO_NAME,
            imageScanningConfiguration={"scanOnPush": True},
            encryptionConfiguration={"encryptionType": "AES256"},
        )["repository"]
        print(f"  created ECR repo {ECR_REPO_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
            raise
        repo = c["ecr"].describe_repositories(repositoryNames=[ECR_REPO_NAME])["repositories"][0]
        print(f"  reusing ECR repo {ECR_REPO_NAME}")
    return repo["repositoryUri"]


def ensure_build_role(c: dict, account: str, region: str, bucket: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "codebuild.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/codebuild/*",
            },
            {
                "Sid": "Source",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": f"arn:aws:s3:::{bucket}/{config.S3_PREFIX}/build/*",
            },
            {
                "Sid": "Ecr",
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                    "ecr:BatchGetImage",
                ],
                "Resource": "*",
            },
        ],
    }
    try:
        arn = c["iam"].create_role(
            RoleName=BUILD_ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Talk2Vid CodeBuild: builds the runtime image",
        )["Role"]["Arn"]
        print(f"  created role {BUILD_ROLE}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        arn = c["iam"].get_role(RoleName=BUILD_ROLE)["Role"]["Arn"]
    c["iam"].put_role_policy(
        RoleName=BUILD_ROLE, PolicyName=f"{BUILD_ROLE}Policy", PolicyDocument=json.dumps(policy)
    )
    return arn


def ensure_build_project(c: dict, role_arn: str, repo_uri: str, bucket: str, region: str) -> None:
    spec = {
        "name": BUILD_PROJECT,
        "source": {
            "type": "S3",
            "location": f"{bucket}/{SOURCE_KEY}",
            "buildspec": "deploy/buildspec.yml",
        },
        "artifacts": {"type": "NO_ARTIFACTS"},
        "environment": {
            # ARM so the image matches Fargate ARM64; privileged for docker build.
            "type": "ARM_CONTAINER",
            "image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",
            "computeType": "BUILD_GENERAL1_LARGE",
            "privilegedMode": True,
            "environmentVariables": [
                {"name": "ECR_REPO", "value": repo_uri},
                {"name": "AWS_REGION", "value": region},
            ],
        },
        "serviceRole": role_arn,
        "timeoutInMinutes": 30,
    }
    # A role created seconds ago is not yet assumable by CodeBuild; IAM is
    # eventually consistent and this call is the first thing to notice.
    for attempt in range(10):
        try:
            c["cb"].create_project(**spec)
            print(f"  created CodeBuild project {BUILD_PROJECT}")
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ResourceAlreadyExistsException":
                c["cb"].update_project(**spec)
                print(f"  updated CodeBuild project {BUILD_PROJECT}")
                return
            if code == "InvalidInputException" and "AssumeRole" in str(exc) and attempt < 9:
                print(f"  waiting for IAM propagation (attempt {attempt + 1})")
                time.sleep(6)
                continue
            raise


def run_build(c: dict, bucket: str, tag: str) -> None:
    print("  uploading source…")
    c["s3"].put_object(
        Bucket=bucket, Key=SOURCE_KEY, Body=make_source_zip(), ServerSideEncryption="AES256"
    )
    build_id = c["cb"].start_build(
        projectName=BUILD_PROJECT,
        environmentVariablesOverride=[{"name": "IMAGE_TAG", "value": tag}],
    )["build"]["id"]
    print(f"  build {build_id} started (this takes ~4-7 min the first time)")

    last_phase = None
    while True:
        build = c["cb"].batch_get_builds(ids=[build_id])["builds"][0]
        phase, status = build.get("currentPhase"), build["buildStatus"]
        if phase != last_phase:
            print(f"    {phase}")
            last_phase = phase
        if status != "IN_PROGRESS":
            if status != "SUCCEEDED":
                print(f"  build {status}", file=sys.stderr)
                tail_build_log(c, build)
                raise SystemExit(1)
            print(f"  build succeeded ({tag})")
            return
        time.sleep(10)


def tail_build_log(c: dict, build: dict, lines: int = 40) -> None:
    logs = build.get("logs", {})
    group, stream = logs.get("groupName"), logs.get("streamName")
    if not (group and stream):
        return
    events = c["logs"].get_log_events(
        logGroupName=group, logStreamName=stream, limit=lines, startFromHead=False
    )["events"]
    print("  --- build log tail ---", file=sys.stderr)
    for ev in events:
        print("   ", ev["message"].rstrip(), file=sys.stderr)


# --------------------------------------------------------------------------- #
# Secrets and stack
# --------------------------------------------------------------------------- #
def ensure_secret(c: dict) -> str:
    payload = json.dumps(
        {"DEEPGRAM_API_KEY": config.DEEPGRAM_API_KEY, "DAILY_API_KEY": config.DAILY_API_KEY}
    )
    if not config.DEEPGRAM_API_KEY or not config.DAILY_API_KEY:
        raise SystemExit("DEEPGRAM_API_KEY and DAILY_API_KEY must both be set in .env")
    try:
        arn = c["sm"].create_secret(
            Name=SECRET_NAME,
            SecretString=payload,
            Description="Talk2Vid runtime API keys",
        )["ARN"]
        print(f"  created secret {SECRET_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceExistsException":
            raise
        arn = c["sm"].put_secret_value(SecretId=SECRET_NAME, SecretString=payload)["ARN"]
        print(f"  updated secret {SECRET_NAME}")
    return arn


def stack_params(c: dict, image_uri: str, secret_arn: str, gate: dict) -> list[dict]:
    vpcs = c["ec2"].describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise SystemExit("no default VPC in this region; pass your own subnets instead")
    vpc_id = vpcs[0]["VpcId"]
    subnets = c["ec2"].describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "default-for-az", "Values": ["true"]},
        ]
    )["Subnets"]
    chosen = [s["SubnetId"] for s in sorted(subnets, key=lambda s: s["AvailabilityZone"])[:3]]
    return [
        {"ParameterKey": "ImageUri", "ParameterValue": image_uri},
        {"ParameterKey": "VpcId", "ParameterValue": vpc_id},
        {"ParameterKey": "SubnetIds", "ParameterValue": ",".join(chosen)},
        {"ParameterKey": "SecretArn", "ParameterValue": secret_arn},
        {"ParameterKey": "AssetBucket", "ParameterValue": config.S3_BUCKET},
        {"ParameterKey": "HarnessArn", "ParameterValue": config.AGENTCORE_HARNESS_ARN},
        {"ParameterKey": "OriginSecret", "ParameterValue": gate["origin_secret"]},
        {"ParameterKey": "BasicAuthB64", "ParameterValue": gate["basic_b64"]},
        {"ParameterKey": "LlmModelId", "ParameterValue": config.LLM_MODEL_ID},
    ]


def load_or_make_gate() -> dict:
    """Keep the password stable across redeploys."""
    path = config.DATA_DIR / "deploy-gate.json"
    if path.exists():
        return json.loads(path.read_text())
    password = secrets.token_urlsafe(12)
    gate = {
        "user": "demo",
        "password": password,
        "basic_b64": base64.b64encode(f"demo:{password}".encode()).decode(),
        "origin_secret": secrets.token_urlsafe(24),
    }
    path.write_text(json.dumps(gate, indent=1))
    path.chmod(0o600)
    return gate


def deploy_stack(c: dict, params: list[dict]) -> dict:
    template = (ROOT / "deploy" / "app.yaml").read_text()
    exists = True
    try:
        c["cfn"].describe_stacks(StackName=STACK)
    except ClientError:
        exists = False

    kwargs = dict(
        StackName=STACK,
        TemplateBody=template,
        Parameters=params,
        Capabilities=["CAPABILITY_IAM"],
        Tags=[{"Key": "app", "Value": "talk2vid"}],
    )
    if exists:
        try:
            c["cfn"].update_stack(**kwargs)
            print("  stack update started")
        except ClientError as exc:
            if "No updates are to be performed" in str(exc):
                print("  stack already up to date")
                return stack_outputs(c)
            raise
        waiter = "stack_update_complete"
    else:
        c["cfn"].create_stack(**kwargs, OnFailure="DELETE", EnableTerminationProtection=False)
        print("  stack create started (CloudFront takes ~5-8 min)")
        waiter = "stack_create_complete"

    print("  waiting for the stack…")
    try:
        c["cfn"].get_waiter(waiter).wait(
            StackName=STACK, WaiterConfig={"Delay": 15, "MaxAttempts": 120}
        )
    except Exception:
        show_stack_failures(c)
        raise
    return stack_outputs(c)


def show_stack_failures(c: dict) -> None:
    events = c["cfn"].describe_stack_events(StackName=STACK)["StackEvents"]
    print("\n  stack failures:", file=sys.stderr)
    for ev in events[:40]:
        if "FAILED" in ev.get("ResourceStatus", ""):
            print(
                f"    {ev.get('LogicalResourceId')}: {ev.get('ResourceStatusReason', '')[:200]}",
                file=sys.stderr,
            )


def stack_outputs(c: dict) -> dict:
    stack = c["cfn"].describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


# --------------------------------------------------------------------------- #
# Operate
# --------------------------------------------------------------------------- #
def wait_healthy(c: dict, out: dict, timeout: float = 420.0) -> bool:
    """Watch the service until a task is running and passing the ALB health check."""
    import urllib.error
    import urllib.request

    gate = load_or_make_gate()
    url = f"{out['Url']}/api/health"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Basic {gate['basic_b64']}"}
    )
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        tasks = c["ecs"].list_tasks(cluster=out["ClusterName"], serviceName=out["ServiceName"])
        state = "no task yet"
        if tasks["taskArns"]:
            desc = c["ecs"].describe_tasks(cluster=out["ClusterName"], tasks=tasks["taskArns"])
            task = desc["tasks"][0]
            state = f"{task['lastStatus']} / health={task.get('healthStatus', 'UNKNOWN')}"
        if state != last:
            print(f"    task: {state}")
            last = state
        try:
            with urllib.request.urlopen(request, timeout=8) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read())
                    print(f"  live: {payload}")
                    return True
        except Exception:
            pass
        time.sleep(10)
    return False


def status(c: dict) -> int:
    try:
        out = stack_outputs(c)
    except ClientError:
        print("not deployed")
        return 1
    gate = load_or_make_gate()
    print(f"url:      {out['Url']}")
    print(f"login:    {gate['user']} / {gate['password']}")
    print(f"alb:      {out['AlbDns']} (403 unless via CloudFront)")
    tasks = c["ecs"].list_tasks(cluster=out["ClusterName"], serviceName=out["ServiceName"])
    if tasks["taskArns"]:
        task = c["ecs"].describe_tasks(cluster=out["ClusterName"], tasks=tasks["taskArns"])["tasks"][0]
        print(f"task:     {task['lastStatus']} health={task.get('healthStatus')} ")
    else:
        print("task:     none running (parked?)")
    try:
        streams = c["logs"].describe_log_streams(
            logGroupName=out["LogGroupName"], orderBy="LastEventTime", descending=True, limit=1
        )["logStreams"]
        if streams:
            events = c["logs"].get_log_events(
                logGroupName=out["LogGroupName"],
                logStreamName=streams[0]["logStreamName"],
                limit=15,
                startFromHead=False,
            )["events"]
            print("recent logs:")
            for ev in events:
                print("   ", ev["message"].rstrip()[:160])
    except ClientError:
        pass
    return 0


def scale(c: dict, count: int) -> int:
    out = stack_outputs(c)
    c["ecs"].update_service(
        cluster=out["ClusterName"], service=out["ServiceName"], desiredCount=count
    )
    print(f"service scaled to {count}")
    return 0


def teardown(c: dict) -> int:
    print("deleting stack (CloudFront takes a few minutes to disable)…")
    try:
        c["cfn"].delete_stack(StackName=STACK)
        c["cfn"].get_waiter("stack_delete_complete").wait(
            StackName=STACK, WaiterConfig={"Delay": 20, "MaxAttempts": 90}
        )
        print("  stack deleted")
    except ClientError as exc:
        print(f"  stack: {exc}")

    for label, call in (
        ("codebuild project", lambda: c["cb"].delete_project(name=BUILD_PROJECT)),
        ("ecr repo", lambda: c["ecr"].delete_repository(repositoryName=ECR_REPO_NAME, force=True)),
        (
            "secret",
            lambda: c["sm"].delete_secret(
                SecretId=SECRET_NAME, ForceDeleteWithoutRecovery=True
            ),
        ),
        (
            "codebuild role",
            lambda: (
                c["iam"].delete_role_policy(RoleName=BUILD_ROLE, PolicyName=f"{BUILD_ROLE}Policy"),
                c["iam"].delete_role(RoleName=BUILD_ROLE),
            ),
        ),
    ):
        try:
            call()
            print(f"  deleted {label}")
        except ClientError as exc:
            print(f"  {label}: {exc.response['Error']['Code']}")
    print("\nNote: the S3 bucket and the AgentCore harness are left in place.")
    print("Remove those with provision_aws.py / provision_agentcore.py --teardown.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true", help="reuse the image already in ECR")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--park", action="store_true",
                    help="scale the service to 0 (the ALB stays up and keeps billing)")
    ap.add_argument("--resume", action="store_true", help="scale the service back to 1")
    ap.add_argument("--teardown", action="store_true")
    args = ap.parse_args()

    region = config.AWS_REGION
    c = clients(region)
    account = c["sts"].get_caller_identity()["Account"]

    if args.status:
        return status(c)
    if args.park:
        return scale(c, 0)
    if args.resume:
        return scale(c, 1)
    if args.teardown:
        return teardown(c)

    for name, value in (
        ("TALK2VID_S3_BUCKET", config.S3_BUCKET),
        ("TALK2VID_HARNESS_ARN", config.AGENTCORE_HARNESS_ARN),
    ):
        if not value:
            raise SystemExit(f"{name} is not set in .env — run the provision scripts first")

    print(f"account={account} region={region}")
    print("image:")
    repo_uri = ensure_ecr(c)
    tag = time.strftime("%Y%m%d-%H%M%S")
    if args.skip_build:
        tag = "latest"
        print("  skipping build, using :latest")
    else:
        role = ensure_build_role(c, account, region, config.S3_BUCKET)
        ensure_build_project(c, role, repo_uri, config.S3_BUCKET, region)
        run_build(c, config.S3_BUCKET, tag)

    print("secrets:")
    secret_arn = ensure_secret(c)
    gate = load_or_make_gate()

    print("stack:")
    out = deploy_stack(c, stack_params(c, f"{repo_uri}:{tag}", secret_arn, gate))

    print("\nwaiting for the service to come up:")
    healthy = wait_healthy(c, out)

    print()
    print("=" * 62)
    print(f"  URL:      {out['Url']}")
    print(f"  Login:    {gate['user']} / {gate['password']}")
    print("=" * 62)
    if not healthy:
        print("\nThe service did not report healthy yet. Check:")
        print("  uv run python scripts/deploy.py --status")
        return 1
    print("\nPark it when you are done (stops the Fargate charge):")
    print("  uv run python scripts/deploy.py --park")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
