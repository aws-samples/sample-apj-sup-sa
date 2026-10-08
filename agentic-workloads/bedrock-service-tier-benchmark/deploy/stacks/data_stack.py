"""Data tier: an isolated VPC and Aurora PostgreSQL Serverless v2 reachable only via the RDS Data API.

Security posture:

* The cluster lives in isolated subnets (no route to the internet) and its security
  group has **no ingress rules**: nothing connects over the network. All access goes
  through the RDS Data API, authorised by IAM and a Secrets Manager secret.
* Storage, secrets and Performance Insights are encrypted with one customer managed
  KMS key (rotation on).
* ``rds.force_ssl=1``, deletion protection, 7-day backups, IAM-only secret access.
* Three database users: ``admin`` (schema only, rotated), ``bench_writer`` (worker)
  and ``bench_reader`` (API, SELECT only).
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CustomResource, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import custom_resources as cr
from cdk_nag import NagSuppressions
from constructs import Construct

from . import checkov

DB_NAME = "bench"
#: Generated and rotated passwords are alphanumeric: the schema custom resource puts
#: them into DDL (which cannot take bind parameters) after validating that.
_PUNCTUATION = " %+~`#$&*()|[]{}:;<>?!'/@\"\\,.-_=^"
_LAMBDAS = Path(__file__).resolve().parent.parent / "lambdas"


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw) -> None:
        super().__init__(scope, cid, **kw)

        self.key = kms.Key(
            self,
            "DataKey",
            enable_key_rotation=True,
            description="bedrock-tier-bench: Aurora storage, secrets, logs",
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Isolated subnets for Aurora; public subnets only host the worker task
        # (public IP, zero ingress, egress 443). No NAT gateway: nothing needs it.
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
        )
        flow_logs = logs.LogGroup(
            self, "FlowLogGroup", retention=logs.RetentionDays.THREE_MONTHS, encryption_key=self.key
        )
        self.vpc.add_flow_log("FlowLog", destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_logs))

        db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=self.vpc,
            description="Aurora: no ingress (RDS Data API only)",
            allow_all_outbound=False,
        )

        params = rds.ParameterGroup(
            self,
            "Params",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_17_5),
            parameters={"rds.force_ssl": "1", "log_min_duration_statement": "2000"},
        )

        self.cluster = rds.DatabaseCluster(
            self,
            "Cluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_17_5),
            default_database_name=DB_NAME,
            credentials=rds.Credentials.from_generated_secret("bench_admin", encryption_key=self.key),
            writer=rds.ClusterInstance.serverless_v2(
                "writer",
                enable_performance_insights=True,
                performance_insight_encryption_key=self.key,
            ),
            serverless_v2_min_capacity=0,  # auto-pause when idle: no compute cost
            serverless_v2_max_capacity=2,
            monitoring_interval=Duration.seconds(60),
            serverless_v2_auto_pause_duration=Duration.minutes(10),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg],
            parameter_group=params,
            storage_encrypted=True,
            storage_encryption_key=self.key,
            enable_data_api=True,
            deletion_protection=True,
            backup=rds.BackupProps(retention=Duration.days(7)),
            copy_tags_to_snapshot=True,
            cloudwatch_logs_exports=["postgresql"],
            iam_authentication=False,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )

        # Admin password rotation (hosted single-user rotation Lambda in the isolated
        # subnets; it reaches Secrets Manager through an interface endpoint).
        endpoint_sg = ec2.SecurityGroup(
            self,
            "EndpointSg",
            vpc=self.vpc,
            description="Secrets Manager endpoint",
            allow_all_outbound=False,
        )
        rotation_sg = ec2.SecurityGroup(
            self,
            "RotationSg",
            vpc=self.vpc,
            description="Secret rotation Lambda",
            allow_all_outbound=False,
        )
        endpoint_sg.add_ingress_rule(rotation_sg, ec2.Port.tcp(443), "rotation -> Secrets Manager")
        rotation_sg.add_egress_rule(endpoint_sg, ec2.Port.tcp(443), "Secrets Manager endpoint")
        rotation_sg.add_egress_rule(db_sg, ec2.Port.tcp(5432), "rotate DB password")
        db_sg.add_ingress_rule(rotation_sg, ec2.Port.tcp(5432), "secret rotation Lambda only")
        self.vpc.add_interface_endpoint(
            "SecretsManagerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[endpoint_sg],
        )
        admin_rotation = self.cluster.add_rotation_single_user(
            automatically_after=Duration.days(30),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_group=rotation_sg,
        )

        # Application users: secrets attached to the cluster, rotated (multi-user, so
        # there is always one valid password) after the schema resource created them.
        self.writer_secret = self._user_secret("WriterSecret", "bench_writer")
        self.reader_secret = self._user_secret("ReaderSecret", "bench_reader")
        schema = self._schema()
        # The first rotation runs immediately; let the schema resource finish with the admin secret first.
        admin_rotation.node.add_dependency(schema)
        for cid, secret in (
            ("WriterRotation", self.writer_secret),
            ("ReaderRotation", self.reader_secret),
        ):
            rotation = self.cluster.add_rotation_multi_user(
                cid,
                secret=secret,
                automatically_after=Duration.days(30),
                exclude_characters=_PUNCTUATION,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                security_group=rotation_sg,
            )
            rotation.node.add_dependency(schema)

        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-RDS6",
                    "reason": "No network client connects to the database: all access is through the RDS "
                    "Data API (IAM-authorised, Secrets Manager credentials). The security group has no "
                    "ingress except the rotation Lambda.",
                },
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "Aurora enhanced monitoring role uses the AWS-managed AmazonRDSEnhancedMonitoringRole "
                    "policy (CloudWatch Logs for RDS OS metrics only), as AWS documents.",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
                    ],
                },
                {
                    "id": "CdkNagValidationFailure",
                    "reason": "EC23 cannot evaluate security group rules that reference other security groups "
                    "(intrinsics); no rule allows 0.0.0.0/0 ingress.",
                },
            ],
        )

    def _user_secret(self, cid: str, username: str) -> rds.DatabaseSecret:
        secret = rds.DatabaseSecret(
            self,
            cid,
            username=username,
            master_secret=self.cluster.secret,
            encryption_key=self.key,
            exclude_characters=_PUNCTUATION,
        )
        return secret.attach(self.cluster)

    def _schema(self) -> CustomResource:
        """Apply schema.sql and create the reader/writer roles through the Data API (no VPC)."""
        log_group = logs.LogGroup(self, "SchemaLogs", retention=logs.RetentionDays.ONE_MONTH, encryption_key=self.key)
        # Explicit role: logs scoped to this function's log group (no AWS managed policy).
        role = iam.Role(self, "SchemaFnRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[log_group.log_group_arn, f"{log_group.log_group_arn}:log-stream:*"],
            )
        )
        NagSuppressions.add_resource_suppressions(
            role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Log stream names are generated by Lambda at runtime; the wildcard is limited "
                    "to streams of this function's own log group.",
                    "appliesTo": ["Resource::<SchemaLogs6399FB3A.Arn>:log-stream:*"],
                }
            ],
            apply_to_children=True,
        )
        fn = lambda_.Function(
            self,
            "SchemaFn",
            role=role,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(_LAMBDAS / "schema")),
            timeout=Duration.minutes(5),
            reserved_concurrent_executions=1,
            environment={
                "CLUSTER_ARN": self.cluster.cluster_arn,
                "ADMIN_SECRET_ARN": self.cluster.secret.secret_arn,
                "WRITER_SECRET_ARN": self.writer_secret.secret_arn,
                "READER_SECRET_ARN": self.reader_secret.secret_arn,
                "DB_NAME": DB_NAME,
            },
            log_group=log_group,
        )
        self.key.grant_decrypt(fn)
        self.cluster.grant_data_api_access(fn)
        for secret in (self.writer_secret, self.reader_secret):
            secret.grant_read(fn)
        self.key.add_to_resource_policy(
            iam.PolicyStatement(
                actions=[
                    "kms:Encrypt*",
                    "kms:Decrypt*",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:Describe*",
                ],
                principals=[iam.ServicePrincipal(f"logs.{self.region}.amazonaws.com")],
                resources=["*"],
                conditions={
                    "ArnLike": {"kms:EncryptionContext:aws:logs:arn": f"arn:aws:logs:{self.region}:{self.account}:*"}
                },
            )
        )
        checkov.skip(fn, checkov.LAMBDA_NO_VPC, checkov.LAMBDA_NO_DLQ, checkov.LAMBDA_ENV_PLAIN)
        provider = cr.Provider(self, "SchemaProvider", on_event_handler=fn)
        checkov.skip(
            provider.node.find_child("framework-onEvent"),
            checkov.LAMBDA_NO_VPC,
            checkov.LAMBDA_NO_DLQ,
            checkov.LAMBDA_ENV_PLAIN,
            checkov.LAMBDA_CONCURRENCY,
        )
        NagSuppressions.add_resource_suppressions(
            provider,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "CDK custom-resource Provider framework Lambda uses AWSLambdaBasicExecutionRole "
                    "(CloudWatch Logs only); it is CDK-managed and not configurable.",
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Provider framework needs lambda:InvokeFunction on the handler ARN and its "
                    "versions (<arn>:*); scoped to this one function.",
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Provider framework Lambda runtime is chosen by aws-cdk-lib.",
                },
            ],
            apply_to_children=True,
        )
        resource = CustomResource(
            self,
            "Schema",
            service_token=provider.service_token,
            # Re-run when the schema changes.
            properties={"schema": (_LAMBDAS / "schema" / "schema.sql").read_text()},
        )
        resource.node.add_dependency(self.cluster)
        checkov.skip(
            self.cluster,
            (
                "CKV_AWS_162",
                "Access is through the RDS Data API only (Secrets Manager credentials); no network clients.",
            ),
        )
        restrict = self.node.try_find_child("Custom::VpcRestrictDefaultSGCustomResourceProvider")
        if restrict is not None:
            checkov.skip(
                restrict.node.find_child("Handler"),
                checkov.LAMBDA_NO_VPC,
                checkov.LAMBDA_NO_DLQ,
                checkov.LAMBDA_CONCURRENCY,
            )
        return resource
