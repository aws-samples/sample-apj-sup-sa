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

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

DB_NAME = "bench"


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
                ec2.SubnetConfiguration(
                    name="isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                ),
            ],
        )
        self.vpc.add_flow_log("FlowLog")

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
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_17_5
            ),
            parameters={"rds.force_ssl": "1", "log_min_duration_statement": "2000"},
        )

        self.cluster = rds.DatabaseCluster(
            self,
            "Cluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_17_5
            ),
            default_database_name=DB_NAME,
            credentials=rds.Credentials.from_generated_secret(
                "bench_admin", encryption_key=self.key
            ),
            writer=rds.ClusterInstance.serverless_v2(
                "writer", enable_performance_insights=True, performance_insight_encryption_key=self.key
            ),
            serverless_v2_min_capacity=0,  # auto-pause when idle: no compute cost
            serverless_v2_max_capacity=2,
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
            self, "EndpointSg", vpc=self.vpc, description="Secrets Manager endpoint", allow_all_outbound=False
        )
        rotation_sg = ec2.SecurityGroup(
            self, "RotationSg", vpc=self.vpc, description="Secret rotation Lambda", allow_all_outbound=False
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
        self.cluster.add_rotation_single_user(
            automatically_after=Duration.days(30),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_group=rotation_sg,
        )

        # Application users. Passwords are generated here; the schema custom resource
        # creates the roles with these passwords and grants least privilege.
        self.writer_secret = self._user_secret("WriterSecret", "bench_writer")
        self.reader_secret = self._user_secret("ReaderSecret", "bench_reader")

    def _user_secret(self, cid: str, username: str) -> sm.Secret:
        return sm.Secret(
            self,
            cid,
            encryption_key=self.key,
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template=f'{{"username": "{username}"}}',
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=32,
            ),
            description=f"bedrock-tier-bench {username}",
        )
