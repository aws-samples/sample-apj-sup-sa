"""Aurora MySQL Upgrade multi-agent stack.

Creates:
- S3 reports bucket
- IAM runtime role (shared by all agents — trusts bedrock-agentcore)
- Docker images (ARM64) built from each agent folder under deploy/agents/
- Bedrock AgentCore Runtimes for every agent, wired into the customer's VPC.

Assumes VPC, subnets, security groups, and RDS already exist — this stack
only provisions what the agent platform itself needs.
"""
from pathlib import Path
from typing import Mapping

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    CustomResource,
    DockerImage,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import custom_resources as cr
from constructs import Construct

# Package layout (deploy/ is the package root that gets shipped to customers):
#   deploy/
#     infra/cdk_aurora_upgrade/stack.py  (this file — parents[2] = deploy/)
#     agents/
#       orchestrator/        (Docker build context)
#       variables-compare/   (Docker build context)
#       error-log-analyzer/  (Docker build context)
#       upgrade-readiness/   (Docker build context)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambda" / "agent_runtime_cr"


def _pascal(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.replace("_", "-").split("-"))


class AuroraUpgradeAgentStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc_id: str,
        subnet_ids: list[str],
        security_group_ids: list[str],
        reports_bucket_name: str,
        agent_names: Mapping[str, str],
        model_id: str,
        db_secret_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------------
        # Validate network inputs against the existing VPC
        # ------------------------------------------------------------------
        ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
        for idx, sid in enumerate(subnet_ids):
            ec2.Subnet.from_subnet_id(self, f"Subnet{idx}", sid)
        for idx, gid in enumerate(security_group_ids):
            ec2.SecurityGroup.from_security_group_id(self, f"Sg{idx}", gid)

        # ------------------------------------------------------------------
        # S3 reports bucket
        # ------------------------------------------------------------------
        reports_bucket = s3.Bucket(
            self,
            "ReportsBucket",
            bucket_name=reports_bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=False,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ------------------------------------------------------------------
        # Shared runtime IAM role (all agents, including orchestrator)
        # ------------------------------------------------------------------
        runtime_role = iam.Role(
            self,
            "AgentRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Execution role for Aurora MySQL upgrade agents on Bedrock AgentCore",
        )
        for managed in (
            "AmazonBedrockFullAccess",
            "CloudWatchLogsFullAccess",
            "AmazonEC2ContainerRegistryReadOnly",
        ):
            runtime_role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(managed)
            )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DeleteNetworkInterface",
                    "ec2:AssignPrivateIpAddresses",
                    "ec2:UnassignPrivateIpAddresses",
                ],
                resources=["*"],
            )
        )
        reports_bucket.grant_read_write(runtime_role)
        # Orchestrator invokes the other agents via AgentCore
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime",
                ],
                resources=["*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )
        # Agents resolve the DB password from Secrets Manager at run time
        # (the password never travels in the invocation payload). Scope read
        # access to the customer-provided secret. A 6-char suffix wildcard
        # covers Secrets Manager's auto-appended suffix when an ARN is given.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[db_secret_arn, f"{db_secret_arn}-??????"],
            )
        )

        # ------------------------------------------------------------------
        # CustomResource Lambda — drives AgentCore runtime lifecycle
        # ------------------------------------------------------------------
        # Bundle latest boto3/botocore with the handler — the Python 3.12
        # Lambda runtime ships an older botocore that doesn't know about
        # AgentCore VPC params (networkMode/networkModeConfig).
        cr_handler = _lambda.Function(
            self,
            "AgentRuntimeCrHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.on_event",
            code=_lambda.Code.from_asset(
                str(LAMBDA_DIR),
                bundling=BundlingOptions(
                    image=DockerImage.from_registry(
                        "public.ecr.aws/sam/build-python3.12:latest"
                    ),
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output "
                        "&& cp -au . /asset-output",
                    ],
                ),
            ),
            timeout=Duration.minutes(10),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        # CreateAgentRuntime triggers a chain of internal calls (workload
        # identity, endpoints, credential providers, etc.) — granting the
        # full bedrock-agentcore* surface keeps deploys/teardowns from
        # whack-a-moling on individual missing actions. This role is only
        # used by the deployer Lambda, not by the agents themselves.
        cr_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:*",
                    "bedrock-agentcore-control:*",
                ],
                resources=["*"],
            )
        )
        cr_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[runtime_role.role_arn],
            )
        )
        provider = cr.Provider(
            self,
            "AgentRuntimeCrProvider",
            on_event_handler=cr_handler,
            log_retention=logs.RetentionDays.ONE_MONTH,
        )

        # ------------------------------------------------------------------
        # Build images + create runtimes for every agent folder.
        # Key = folder name under agents/ (also used as CDK logical ID prefix).
        # Value = key into the agent_names config mapping.
        # ------------------------------------------------------------------
        agents = {
            "orchestrator":       "orchestrator",
            "variables-compare":  "variables_compare",
            "error-log-analyzer": "error_log_analyzer",
            "upgrade-readiness":  "upgrade_readiness",
            "query-risk-scorer":  "query_risk_scorer",
            "plan-diff":          "plan_diff",
        }

        runtimes: dict[str, CustomResource] = {}
        for slug, name_key in agents.items():
            logical = _pascal(slug)  # e.g. "variables-compare" -> "VariablesCompare"
            image = ecr_assets.DockerImageAsset(
                self,
                f"{logical}Image",
                directory=str(PACKAGE_ROOT / "agents" / slug),
                platform=ecr_assets.Platform.LINUX_ARM64,
            )
            runtimes[slug] = CustomResource(
                self,
                f"{logical}Agent",
                service_token=provider.service_token,
                properties={
                    "AgentRuntimeName": agent_names[name_key],
                    "ContainerUri": image.image_uri,
                    "RoleArn": runtime_role.role_arn,
                    "SubnetIds": subnet_ids,
                    "SecurityGroupIds": security_group_ids,
                    "Region": self.region,
                    "ModelId": model_id,
                },
            )

        # ------------------------------------------------------------------
        # Outputs
        # ------------------------------------------------------------------
        CfnOutput(self, "ReportsBucketName", value=reports_bucket.bucket_name)
        CfnOutput(self, "RuntimeRoleArn", value=runtime_role.role_arn)
        CfnOutput(
            self,
            "OrchestratorArn",
            value=runtimes["orchestrator"].get_att_string("AgentRuntimeArn"),
            description="Invoke this ARN from your application",
        )
        CfnOutput(
            self,
            "VariablesCompareArn",
            value=runtimes["variables-compare"].get_att_string("AgentRuntimeArn"),
        )
        CfnOutput(
            self,
            "ErrorLogAnalyzerArn",
            value=runtimes["error-log-analyzer"].get_att_string("AgentRuntimeArn"),
        )
        CfnOutput(
            self,
            "UpgradeReadinessArn",
            value=runtimes["upgrade-readiness"].get_att_string("AgentRuntimeArn"),
        )
        CfnOutput(
            self,
            "QueryRiskScorerArn",
            value=runtimes["query-risk-scorer"].get_att_string("AgentRuntimeArn"),
        )
        CfnOutput(
            self,
            "PlanDiffArn",
            value=runtimes["plan-diff"].get_att_string("AgentRuntimeArn"),
        )
