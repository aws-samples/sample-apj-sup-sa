"""DataConstruct for Aurora and Secrets Manager resources."""

from __future__ import annotations

from aws_cdk import (
    Duration,
    RemovalPolicy,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_rds as rds,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from infra.config import InfraContext
from infra.cdk_constructs.network import NetworkConstruct


class DataConstruct(Construct):
    """Provision the relational data layer for the platform."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        context: InfraContext,
        network: NetworkConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.context = context

        self.virtual_key_kms_key = kms.Key(
            self,
            "VirtualKeyKmsKey",
            alias=context.kms_alias("virtual-key"),
            description=f"KMS key for Virtual Key encryption ({context.environment})",
            enable_key_rotation=True,
            pending_window=Duration.days(30),
            removal_policy=context.resource_removal_policy,
        )

        self.db_secret = secretsmanager.Secret(
            self,
            "DbSecret",
            secret_name=context.secret_name("db-credentials"),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"dbadmin"}',
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=32,
            ),
        )

        self.anthropic_api_key_secret = secretsmanager.Secret(
            self,
            "AnthropicApiKeySecret",
            secret_name=context.secret_name("anthropic-api-key"),
            description=(
                "Anthropic 1P API key used as Bedrock fallback. "
                'Populate after deploy: aws secretsmanager put-secret-value '
                '--secret-id <arn> --secret-string \'{"api_key":"sk-ant-..."}\''
            ),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"api_key":""}',
                generate_string_key="placeholder",
                password_length=8,
            ),
            removal_policy=context.resource_removal_policy,
        )

        self.db_subnet_group = rds.SubnetGroup(
            self,
            "DbSubnetGroup",
            description="Subnet group for Aurora",
            vpc=network.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=network.private_data_subnets),
            subnet_group_name=context.resource_name("db-subnet-group"),
            removal_policy=context.resource_removal_policy,
        )

        self.aurora_security_group = ec2.SecurityGroup(
            self,
            "AuroraSecurityGroup",
            vpc=network.vpc,
            security_group_name=context.resource_name("aurora-sg"),
            description="Security group for the Aurora cluster",
            allow_all_outbound=False,
        )
        self.aurora_security_group.add_ingress_rule(
            ec2.Peer.security_group_id(network.ecs_sg.security_group_id),
            ec2.Port.tcp(5432),
            "Ingress from ECS tasks",
            remote_rule=True,
        )

        self.aurora_cluster = rds.DatabaseCluster(
            self,
            "AuroraCluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_15,
            ),
            cluster_identifier=context.resource_name("aurora"),
            default_database_name="claude_proxy",
            credentials=rds.Credentials.from_secret(self.db_secret),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            readers=[
                rds.ClusterInstance.serverless_v2(
                    "reader",
                    scale_with_writer=True,
                ),
            ],
            serverless_v2_min_capacity=context.aurora_min_capacity,
            serverless_v2_max_capacity=context.aurora_max_capacity,
            vpc=network.vpc,
            subnet_group=self.db_subnet_group,
            security_groups=[self.aurora_security_group],
            storage_encrypted=True,
            backup=rds.BackupProps(
                retention=context.aurora_backup_retention,
                preferred_window=context.aurora_backup_window,
            ),
            preferred_maintenance_window=context.aurora_maintenance_window,
            deletion_protection=context.is_prod,
            removal_policy=RemovalPolicy.SNAPSHOT if context.is_prod else RemovalPolicy.DESTROY,
            copy_tags_to_snapshot=True,
        )

        self.db_endpoint = self.aurora_cluster.cluster_endpoint.hostname
        self.db_read_endpoint = self.aurora_cluster.cluster_read_endpoint.hostname
        self.db_secret_arn = self.db_secret.secret_arn
        self.aurora_cluster_arn = self.aurora_cluster.cluster_arn
