"""``HviDataStack`` — Stack 1 of the HVI Phase 2 CDK app.

Reproduces the web-relevant subset of ``scripts/10-dynamodb.sh``,
``15-iam.sh``, ``20-vpc.sh`` and ``55-call-archive.sh`` as a single CDK stack:
the eight DynamoDB tables, three IAM roles, the dedicated VPC
(subnets/SGs/endpoints + Neptune subnet group), the durable call-archive S3
bucket, and the Data-owned seed custom resources (form schema + CRM).

Everything downstream (Knowledge, Inference, Agent) consumes this stack's typed
attributes — the VPC, private subnets, security groups, Neptune subnet group,
the three roles, the tables, and the archive bucket — as construct props, never
by name re-lookup.

Deliberate deviations from the reference scripts (all documented in
``DESIGN.md``):

* Table tag ``ManagedBy=hvi-cdk`` (scripts say ``hvi-scripts``) — these tables
  are now managed by the CDK app (NIT-3).
* ``hvi-sagemaker-role``'s read of the Inference-owned ``hvi-artifacts`` bucket
  is a **static-ARN** inline statement, not ``bucket.grant_read``, to avoid a
  Data<->Inference circular stack dependency (MEDIUM-4).
* On the default ``hvi:agentCompute=lambda`` path, ``hvi-agentcore-role`` trusts
  ``lambda.amazonaws.com`` and gains the ``/aws/lambda/*`` logs capability
  (managed ``AWSLambdaBasicExecutionRole``) so the agent Lambda can write its
  own log group; the original ``/aws/bedrock-agentcore/*`` statement is kept as
  a superset (MEDIUM-1). On ``agentcore-cr`` the trust and logs scope stay
  exactly as ``15-iam.sh`` has them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_neptune as neptune,
    aws_s3 as s3,
    custom_resources as cr,
)
from constructs import Construct

from stacks.assets import vendored_asset_code
from stacks.context import HviContext
from stacks.naming import Naming

# infra/cdk/stacks/data_stack.py -> parents: [stacks, cdk, infra, <repo root>]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CDK_ROOT = Path(__file__).resolve().parents[1]
_SEED_DIR = _REPO_ROOT / "seed"
_SEED_RESOURCES = _CDK_ROOT / "seed_resources"

_PROJECT_TAG = ("Project", "health-voice-intake")
# NIT-3: deliberately hvi-cdk (not the scripts' hvi-scripts) — CDK owns these now.
_MANAGED_BY_TAG = ("ManagedBy", "hvi-cdk")

_S = dynamodb.AttributeType.STRING
_N = dynamodb.AttributeType.NUMBER

# Single source of truth for the VPC CIDR (scripts/20-vpc.sh). Exposed as the
# read-only typed prop ``self.vpc_cidr_block`` so Phase 3's Asterisk SIP config
# can set pjsip ``local_net`` to the literal; ``data.vpc.vpc_cidr_block`` raises
# ``CannotPerformOperationVpcCidr`` on the imported VPC (MEDIUM-1).
_VPC_CIDR = "10.42.0.0/16"


class HviDataStack(Stack):
    """The HVI data layer: DynamoDB, IAM, VPC, archive bucket, seeds."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hvi: HviContext,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hvi = hvi
        self.naming = Naming(account=hvi.account, region=hvi.region)

        self._build_tables()
        self._build_vpc()
        self._build_roles()
        self._build_archive_bucket()
        self._build_seed_resources()

    # ------------------------------------------------------------------ tables
    def _table(
        self,
        logical_id: str,
        name: str,
        pk: tuple[str, dynamodb.AttributeType],
        sk: tuple[str, dynamodb.AttributeType] | None = None,
        ttl_attribute: str | None = None,
    ) -> dynamodb.Table:
        kwargs: dict = dict(
            table_name=name,
            partition_key=dynamodb.Attribute(name=pk[0], type=pk[1]),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        if sk is not None:
            kwargs["sort_key"] = dynamodb.Attribute(name=sk[0], type=sk[1])
        if ttl_attribute is not None:
            kwargs["time_to_live_attribute"] = ttl_attribute

        table = dynamodb.Table(self, logical_id, **kwargs)
        cdk.Tags.of(table).add(*_PROJECT_TAG)
        cdk.Tags.of(table).add(*_MANAGED_BY_TAG)
        return table

    def _build_tables(self) -> None:
        n = self.naming

        self.table_call_state = self._table(
            "CallStateTable",
            n.table_call_state,
            pk=("callId", _S),
            sk=("turnIndex", _N),
            ttl_attribute="expiresAt",  # TURN_TTL_DAYS=30, this table only
        )

        self.table_call_session = self._table(
            "CallSessionTable",
            n.table_call_session,
            pk=("callId", _S),
        )
        self.table_call_session.add_global_secondary_index(
            index_name=n.gsi_phone_index,
            partition_key=dynamodb.Attribute(name="phone", type=_S),
            sort_key=dynamodb.Attribute(name="updatedAt", type=_S),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        self.table_call_session.add_global_secondary_index(
            index_name=n.gsi_status_index,
            partition_key=dynamodb.Attribute(name="status", type=_S),
            sort_key=dynamodb.Attribute(name="updatedAt", type=_S),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self.table_crm_cache = self._table(
            "CrmCacheTable",
            n.table_crm_cache,
            pk=("customerId", _S),
        )
        self.table_crm_cache.add_global_secondary_index(
            index_name=n.gsi_phone_index,
            partition_key=dynamodb.Attribute(name="phone", type=_S),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self.table_form_schema = self._table(
            "FormSchemaTable",
            n.table_form_schema,
            pk=("formId", _S),
            sk=("itemId", _S),
        )

        self.table_intake_responses = self._table(
            "IntakeResponsesTable",
            n.table_intake_responses,
            pk=("customerId", _S),
            sk=("callId", _S),
        )

        self.table_consent_ledger = self._table(
            "ConsentLedgerTable",
            n.table_consent_ledger,
            pk=("customerId", _S),
            sk=("grantedAt", _S),
        )

        self.table_call_attempts = self._table(
            "CallAttemptsTable",
            n.table_call_attempts,
            pk=("customerId", _S),
            sk=("attemptDate", _S),
        )

        self.table_gate_audit = self._table(
            "GateAuditTable",
            n.table_gate_audit,
            pk=("callId", _S),
            sk=("checkedAt", _S),
        )

        self.tables = (
            self.table_call_state,
            self.table_call_session,
            self.table_crm_cache,
            self.table_form_schema,
            self.table_intake_responses,
            self.table_consent_ledger,
            self.table_call_attempts,
            self.table_gate_audit,
        )

    # --------------------------------------------------------------------- vpc
    def _build_vpc(self) -> None:
        n = self.naming
        region = self.hvi.region
        az_a = f"{region}a"
        az_b = f"{region}b"

        cfn_vpc = ec2.CfnVPC(
            self,
            "HviVpc",
            cidr_block=_VPC_CIDR,
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags=[
                cdk.CfnTag(key="Name", value=n.vpc),
                cdk.CfnTag(key=_PROJECT_TAG[0], value=_PROJECT_TAG[1]),
            ],
        )
        self._cfn_vpc = cfn_vpc
        # Read-only typed prop for downstream stacks (MEDIUM-1): the literal the
        # CfnVPC uses, so Phase 3's pjsip local_net renders as 10.42.0.0/16.
        self.vpc_cidr_block = _VPC_CIDR

        igw = ec2.CfnInternetGateway(
            self, "Igw", tags=[cdk.CfnTag(key="Name", value=n.igw)]
        )
        igw_attach = ec2.CfnVPCGatewayAttachment(
            self, "IgwAttach", vpc_id=cfn_vpc.ref, internet_gateway_id=igw.ref
        )

        # 2 private /24 across two AZs (Neptune subnet groups need >=2 AZs) +
        # 1 public /24 + IGW — exactly the 3-subnet layout of scripts/20-vpc.sh.
        priv_a = ec2.PrivateSubnet(
            self,
            "PrivateA",
            vpc_id=cfn_vpc.ref,
            availability_zone=az_a,
            cidr_block="10.42.1.0/24",
        )
        priv_b = ec2.PrivateSubnet(
            self,
            "PrivateB",
            vpc_id=cfn_vpc.ref,
            availability_zone=az_b,
            cidr_block="10.42.2.0/24",
        )
        pub_a = ec2.PublicSubnet(
            self,
            "PublicA",
            vpc_id=cfn_vpc.ref,
            availability_zone=az_a,
            cidr_block="10.42.10.0/24",
            map_public_ip_on_launch=True,
        )
        cdk.Tags.of(priv_a).add("Name", n.subnet_private_a)
        cdk.Tags.of(priv_b).add("Name", n.subnet_private_b)
        cdk.Tags.of(pub_a).add("Name", n.subnet_public_a)

        pub_a.add_default_internet_route(igw.ref, igw_attach)

        # NAT in the public subnet by default; dropped under hvi:skipNat=true
        # (inference containers pre-baked with weights -> no egress needed).
        if not self.hvi.skip_nat:
            eip = ec2.CfnEIP(
                self,
                "NatEip",
                domain="vpc",
                tags=[cdk.CfnTag(key="Name", value=n.nat_eip)],
            )
            nat = ec2.CfnNatGateway(
                self,
                "Nat",
                subnet_id=pub_a.subnet_id,
                allocation_id=eip.attr_allocation_id,
                tags=[cdk.CfnTag(key="Name", value=n.nat)],
            )
            priv_a.add_default_nat_route(nat.ref)
            priv_b.add_default_nat_route(nat.ref)

        private_route_table_ids = [
            priv_a.route_table.route_table_id,
            priv_b.route_table.route_table_id,
        ]

        # Free gateway endpoints on the private route tables (keeps DDB/S3 off NAT).
        for svc in ("dynamodb", "s3"):
            ec2.CfnVPCEndpoint(
                self,
                f"{svc.capitalize()}Endpoint",
                vpc_id=cfn_vpc.ref,
                service_name=f"com.amazonaws.{region}.{svc}",
                vpc_endpoint_type="Gateway",
                route_table_ids=private_route_table_ids,
            )

        # Imported IVpc so downstream stacks can place in-VPC Lambdas/clusters.
        # Only the two private subnets are modeled on the import (the single
        # public subnet is asymmetric across AZs and nothing downstream places
        # into it); the PublicSubnet construct itself is still exposed below.
        self.vpc = ec2.Vpc.from_vpc_attributes(
            self,
            "Vpc",
            vpc_id=cfn_vpc.ref,
            availability_zones=[az_a, az_b],
            private_subnet_ids=[priv_a.subnet_id, priv_b.subnet_id],
            private_subnet_route_table_ids=private_route_table_ids,
        )
        self.private_subnets = [priv_a, priv_b]
        self.public_subnet = pub_a

        # Security groups + the exact ingress rules from scripts/20-vpc.sh.
        self.security_group_lambda = ec2.SecurityGroup(
            self,
            "LambdaSg",
            vpc=self.vpc,
            security_group_name=n.sg_lambda,
            description="hvi in-VPC Lambda egress",
            allow_all_outbound=True,
        )
        self.security_group_neptune = ec2.SecurityGroup(
            self,
            "NeptuneSg",
            vpc=self.vpc,
            security_group_name=n.sg_neptune,
            description="hvi Neptune ingress",
            allow_all_outbound=True,
        )
        self.security_group_infer = ec2.SecurityGroup(
            self,
            "InferSg",
            vpc=self.vpc,
            security_group_name=n.sg_infer,
            description="hvi Whisper/OmniVoice inference tasks",
            allow_all_outbound=True,
        )

        self.security_group_neptune.add_ingress_rule(
            self.security_group_lambda, ec2.Port.tcp(8182), "Gremlin from lambda"
        )
        self.security_group_neptune.add_ingress_rule(
            self.security_group_infer, ec2.Port.tcp(8182), "graph query from infer"
        )
        self.security_group_infer.add_ingress_rule(
            self.security_group_lambda, ec2.Port.tcp(8080), "STT/TTS HTTP from lambda"
        )

        # Neptune DB subnet group across the two private subnets (cheap, no
        # cluster) so Knowledge can attach a cluster when the Neptune flag is on.
        neptune.CfnDBSubnetGroup(
            self,
            "NeptuneSubnets",
            db_subnet_group_name=n.neptune_subnet_group,
            db_subnet_group_description="hvi Neptune private subnets",
            subnet_ids=[priv_a.subnet_id, priv_b.subnet_id],
        )
        self.neptune_subnet_group_name = n.neptune_subnet_group

    # ------------------------------------------------------------------- roles
    def _ddb_tables_arns(self) -> list[str]:
        acc = self.hvi.account
        prefix = self.naming  # noqa: F841 (clarity)
        return [
            f"arn:aws:dynamodb:*:{acc}:table/hvi-*",
            f"arn:aws:dynamodb:*:{acc}:table/hvi-*/index/*",
        ]

    def _build_lambda_role(self) -> iam.Role:
        acc = self.hvi.account
        region = self.hvi.region
        role = iam.Role(
            self,
            "LambdaRole",
            role_name=self.naming.lambda_role,
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for hvi voice-intake Lambdas",
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaVPCAccessExecutionRole"
            )
        )

        statements = [
            iam.PolicyStatement(
                sid="DataLayer",
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchGetItem",
                    "dynamodb:BatchWriteItem",
                ],
                resources=self._ddb_tables_arns(),
            ),
            iam.PolicyStatement(
                sid="BedrockReasoning",
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="AgentCoreInvoke",
                actions=[
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListSessions",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="InvokeHviGateway",
                actions=["bedrock-agentcore:InvokeGateway"],
                resources=[
                    f"arn:aws:bedrock-agentcore:{region}:{acc}:gateway/hvi-spike-gw-*"
                ],
            ),
            iam.PolicyStatement(
                sid="TelephonyControl",
                actions=[
                    "chime:CreateSipMediaApplicationCall",
                    "chime:UpdateSipMediaApplicationCall",
                    "chime:GetSipMediaApplication",
                    "chime:ListSipMediaApplications",
                    "chime:ListVoiceConnectors",
                    "chime:GetVoiceConnector",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="OriginateThroughAsterisk",
                actions=["ssm:SendCommand"],
                resources=[
                    f"arn:aws:ssm:{region}::document/AWS-RunShellScript",
                    f"arn:aws:ec2:{region}:{acc}:instance/*",
                ],
            ),
            iam.PolicyStatement(
                sid="OriginateReadResult",
                actions=["ssm:GetCommandInvocation", "ec2:DescribeInstances"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="ConfirmationEmail",
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="SalesforceCredentials",
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:*:{acc}:secret:hvi/salesforce*"
                ],
            ),
            iam.PolicyStatement(
                sid="TranscriptSearch",
                actions=[
                    "es:ESHttpPost",
                    "es:ESHttpPut",
                    "es:ESHttpGet",
                    "es:ESHttpHead",
                ],
                resources=[f"arn:aws:es:*:{acc}:domain/hvi-*/*"],
            ),
            iam.PolicyStatement(
                sid="KnowledgeGraph",
                actions=["neptune-db:*"],
                resources=[f"arn:aws:neptune-db:*:{acc}:*/*"],
            ),
            iam.PolicyStatement(
                sid="AudioAndArchiveBuckets",
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                    "s3:DeleteObject",
                ],
                resources=[
                    f"arn:aws:s3:::hvi-audio-{acc}-*",
                    f"arn:aws:s3:::hvi-audio-{acc}-*/*",
                    f"arn:aws:s3:::hvi-archive-{acc}-*",
                    f"arn:aws:s3:::hvi-archive-{acc}-*/*",
                ],
            ),
            iam.PolicyStatement(
                sid="InvokeSiblingLambdasCrossRegion",
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:*:{acc}:function:hvi-*"],
            ),
            iam.PolicyStatement(
                sid="CallbackScheduling",
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                    "iam:PassRole",
                ],
                resources=["*"],
            ),
        ]
        role.attach_inline_policy(
            iam.Policy(self, "LambdaInline", statements=statements)
        )
        return role

    def _build_agentcore_role(self) -> iam.Role:
        acc = self.hvi.account
        region = self.hvi.region
        lambda_path = self.hvi.agent_compute == "lambda"

        if lambda_path:
            # MEDIUM-1: compute moves to Lambda, so trust lambda and drop the
            # AgentCore SourceArn/SourceAccount condition.
            assumed_by: iam.IPrincipal = iam.ServicePrincipal(
                "lambda.amazonaws.com"
            )
        else:
            assumed_by = iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": acc},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{acc}:*"
                    },
                },
            )

        role = iam.Role(
            self,
            "AgentCoreRole",
            role_name=self.naming.agentcore_role,
            assumed_by=assumed_by,
            description="Execution role for the hvi agent runtime",
        )

        if lambda_path:
            # MEDIUM-1: give the agent Lambda the /aws/lambda/* logs capability
            # so it can write its own log group (silent failure otherwise).
            role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            )

        statements = [
            iam.PolicyStatement(
                sid="PullAgentImage",
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=["*"],
            ),
            # Original AgentCore logs statement — kept as a superset on the
            # Lambda path (harmless), the exact scope on agentcore-cr.
            iam.PolicyStatement(
                sid="Logs",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=[
                    f"arn:aws:logs:*:{acc}:log-group:/aws/bedrock-agentcore/*"
                ],
            ),
            iam.PolicyStatement(
                sid="Observability",
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="ModelInvoke",
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="AgentCoreMemory",
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:GetMemory",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="ToolDataAccess",
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:Query",
                    "dynamodb:BatchWriteItem",
                ],
                resources=self._ddb_tables_arns(),
            ),
            iam.PolicyStatement(
                sid="ToolCallArchive",
                actions=["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::hvi-archive-{acc}-*",
                    f"arn:aws:s3:::hvi-archive-{acc}-*/*",
                ],
            ),
            iam.PolicyStatement(
                sid="ToolSes",
                actions=["ses:SendEmail"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="ToolSearch",
                actions=["es:ESHttpPost", "es:ESHttpPut", "es:ESHttpGet"],
                resources=[f"arn:aws:es:*:{acc}:domain/hvi-*/*"],
            ),
            iam.PolicyStatement(
                sid="ToolGraph",
                actions=["neptune-db:*"],
                resources=[f"arn:aws:neptune-db:*:{acc}:*/*"],
            ),
            iam.PolicyStatement(
                sid="ToolWorkloadIdentity",
                actions=[
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="ToolLambdas",
                actions=["lambda:InvokeFunction"],
                resources=[f"arn:aws:lambda:*:{acc}:function:hvi-*"],
            ),
        ]
        role.attach_inline_policy(
            iam.Policy(self, "AgentCoreInline", statements=statements)
        )
        return role

    def _build_sagemaker_role(self) -> iam.Role:
        acc = self.hvi.account
        role = iam.Role(
            self,
            "SageMakerRole",
            role_name=self.naming.sagemaker_role,
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            description="Execution role for the hvi SageMaker endpoints",
        )
        statements = [
            iam.PolicyStatement(
                sid="PullDlc",
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="Logs",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="AudioBuckets",
                actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::hvi-audio-{acc}-*",
                    f"arn:aws:s3:::hvi-audio-{acc}-*/*",
                ],
            ),
            # MEDIUM-4: static-ARN read of the Inference-owned hvi-artifacts
            # bucket (NOT bucket.grant_read) to avoid a Data<->Inference cycle.
            iam.PolicyStatement(
                sid="ReadModelArtifacts",
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::{self.naming.artifacts_bucket}",
                    f"arn:aws:s3:::{self.naming.artifacts_bucket}/*",
                ],
            ),
        ]
        role.attach_inline_policy(
            iam.Policy(self, "SageMakerInline", statements=statements)
        )
        return role

    def _build_roles(self) -> None:
        self.lambda_role = self._build_lambda_role()
        self.agentcore_role = self._build_agentcore_role()
        self.sagemaker_role = self._build_sagemaker_role()

    # ---------------------------------------------------------- archive bucket
    def _build_archive_bucket(self) -> None:
        bucket = s3.Bucket(
            self,
            "ArchiveBucket",
            bucket_name=self.naming.archive_bucket,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,  # AES256
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,  # TLS-only deny bucket policy
            # DELIBERATELY no Object Lock (DPDP erasure must stay possible) —
            # object_lock_enabled is left unset so no lock config is emitted.
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="abort-incomplete-uploads",
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                ),
                s3.LifecycleRule(
                    id="expire-superseded-versions",
                    prefix="calls/",
                    noncurrent_version_expiration=Duration.days(365),
                ),
            ],
        )
        # BucketKeyEnabled alongside AES256 (scripts/55) — set via escape hatch
        # because the L2 only wires BucketKeyEnabled for the KMS path.
        bucket.node.default_child.add_property_override(
            "BucketEncryption.ServerSideEncryptionConfiguration.0.BucketKeyEnabled",
            True,
        )

        # scripts/55 attaches the archive-write grant to hvi-agentcore-role;
        # modeled with grant_* in the bucket's owning stack (Data).
        bucket.grant_read_write(self.agentcore_role)

        self.archive_bucket = bucket
        self.archive_bucket_name = self.naming.archive_bucket

    # -------------------------------------------------------- seed resources
    @staticmethod
    def _hash_files(paths) -> str:
        digest = hashlib.sha256()
        for path in sorted(str(p) for p in paths):
            digest.update(Path(path).read_bytes())
        return digest.hexdigest()

    def _seed_resource(
        self,
        name: str,
        handler_dir: Path,
        table: dynamodb.Table,
        extra_files: dict[str, str],
        properties: dict,
    ) -> cdk.CustomResource:
        fn = lambda_.Function(
            self,
            f"{name}Fn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(handler_dir, extra_files),
            timeout=Duration.minutes(5),
            memory_size=256,
        )
        # Least-privilege: PutItem/BatchWriteItem on exactly this one table.
        table.grant(fn, "dynamodb:PutItem", "dynamodb:BatchWriteItem")

        provider = cr.Provider(self, f"{name}Provider", on_event_handler=fn)
        resource = cdk.CustomResource(
            self,
            f"{name}Resource",
            service_token=provider.service_token,
            properties=properties,
        )
        resource.node.add_dependency(table)
        return resource

    def _build_seed_resources(self) -> None:
        # Form schema -> hvi-form-schema, keyed (formId, itemId).
        form_files = {
            "form-arogya-sanjeevani-v1.json": str(
                _SEED_DIR / "form-arogya-sanjeevani-v1.json"
            ),
            "form-medical-history-v1.json": str(
                _SEED_DIR / "form-medical-history-v1.json"
            ),
        }
        self.form_schema_seed = self._seed_resource(
            "FormSchemaSeed",
            _SEED_RESOURCES / "form_schema_seed",
            self.table_form_schema,
            extra_files=form_files,
            properties={
                "TableName": self.naming.table_form_schema,
                "SeedFiles": json.dumps(list(form_files.keys())),
                "SeedHash": self._hash_files(form_files.values()),
            },
        )

        # CRM customers -> hvi-crm-cache, keyed customerId.
        crm_file = {"crm-customers.json": str(_SEED_DIR / "crm-customers.json")}
        self.crm_seed = self._seed_resource(
            "CrmSeed",
            _SEED_RESOURCES / "crm_seed",
            self.table_crm_cache,
            extra_files=crm_file,
            properties={
                "TableName": self.naming.table_crm_cache,
                "SeedFile": "crm-customers.json",
                "SeedHash": self._hash_files(crm_file.values()),
            },
        )
