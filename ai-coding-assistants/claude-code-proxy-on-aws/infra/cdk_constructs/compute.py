"""ComputeConstruct for ECS cluster, ECR, and IAM resources."""

from __future__ import annotations

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from infra.config import InfraContext
from infra.cdk_constructs.data import DataConstruct
from infra.cdk_constructs.network import NetworkConstruct
from infra.cdk_constructs.observability import ObservabilityConstruct


class ComputeConstruct(Construct):
    """Provision ECS cluster, ECR repository, and IAM roles."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        context: InfraContext,
        network: NetworkConstruct,
        data: DataConstruct,
        observability: ObservabilityConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.context = context

        # Resolve the parent stack for account/region references
        stack = Stack.of(self)

        self.ecs_log_group = logs.LogGroup(
            self,
            "EcsLogGroup",
            log_group_name=f"/ecs/{context.resource_name('gateway')}",
            retention=context.log_retention,
            removal_policy=context.resource_removal_policy,
        )

        self.gateway_ecr_repository = ecr.Repository(
            self,
            "GatewayEcrRepository",
            repository_name=context.resource_name("gateway"),
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            encryption=ecr.RepositoryEncryption.AES_256,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Delete untagged images after the default retention window",
                    rule_priority=1,
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=Duration.days(context.ecr_untagged_retention_days),
                ),
                ecr.LifecycleRule(
                    description="Keep the most recent tagged images",
                    rule_priority=2,
                    tag_status=ecr.TagStatus.ANY,
                    max_image_count=context.ecr_tagged_image_count,
                ),
            ],
            removal_policy=context.resource_removal_policy,
        )

        self.gateway_execution_role = iam.Role(
            self,
            "GatewayExecutionRole",
            role_name=context.resource_name("gateway-execution-role"),
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        self.gateway_ecr_repository.grant_pull(self.gateway_execution_role)
        data.db_secret.grant_read(self.gateway_execution_role)

        self.gateway_task_role = iam.Role(
            self,
            "GatewayTaskRole",
            role_name=context.resource_name("gateway-task-role"),
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        self.gateway_task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    # Bedrock can evaluate foundation-model access against both regional and
                    # regionless ARNs for the same invocation, so this policy must cover both.
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{stack.region}:{stack.account}:inference-profile/*",
                ],
            )
        )
        self.gateway_task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["aps:RemoteWrite"],
                resources=[observability.amp_workspace_arn],
            )
        )
        self.gateway_task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "identitystore:ListUsers",
                    "identitystore:DescribeUser",
                ],
                resources=[
                    f"arn:aws:identitystore::{Stack.of(self).account}:identitystore/{context.identity_store_id}",
                    "arn:aws:identitystore:::user/*"
                ],
            )
        )
        data.db_secret.grant_read(self.gateway_task_role)
        data.anthropic_api_key_secret.grant_read(self.gateway_task_role)
        data.virtual_key_kms_key.grant_encrypt_decrypt(self.gateway_task_role)

        # --- Origin secret for API Gateway origin verification ---
        self.origin_secret = secretsmanager.Secret(
            self,
            "ApiGwOriginSecret",
            secret_name=context.resource_name("apigw-origin-secret"),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=48,
            ),
        )
        self.origin_secret.grant_read(self.gateway_execution_role)

        self.ecs_cluster = ecs.Cluster(
            self,
            "EcsCluster",
            cluster_name=context.resource_name("cluster"),
            vpc=network.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
            enable_fargate_capacity_providers=True,
        )
