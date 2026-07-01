from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_logs as logs,
    CfnOutput,
)
from constructs import Construct


class OrchestratorStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        temporal_address = self.node.try_get_context("temporal_address")
        temporal_namespace = self.node.try_get_context("temporal_namespace")

        # ECRリポジトリ
        repo = ecr.Repository(
            self, "OrchestratorRepo",
            repository_name="daf-orchestrator",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        # Temporal API Key (事前にSecrets Managerに手動格納する想定)
        temporal_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "TemporalApiKey",
            secret_name="daf/temporal-api-key",
        )

        # ECS Cluster
        cluster = ecs.Cluster(
            self, "Cluster",
            vpc=vpc,
            cluster_name="daf-orchestrator",
        )

        # Task Definition
        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # IAM: SSM + AgentCore + SecretsManager
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/agents/*"],
        ))
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*"],
        ))
        temporal_secret.grant_read(task_def.task_role)

        # Container
        container = task_def.add_container(
            "Worker",
            image=ecs.ContainerImage.from_ecr_repository(repo, tag="latest"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="daf-orchestrator",
                log_retention=logs.RetentionDays.TWO_WEEKS,
            ),
            environment={
                "TEMPORAL_ADDRESS": temporal_address,
                "TEMPORAL_NAMESPACE": temporal_namespace,
                "AWS_REGION": self.region,
            },
            secrets={
                "TEMPORAL_API_KEY": ecs.Secret.from_secrets_manager(temporal_secret),
            },
            stop_timeout=Duration.seconds(120),
        )

        # Security Group: アウトバウンドのみ
        sg = ec2.SecurityGroup(
            self, "WorkerSG",
            vpc=vpc,
            description="DAF Orchestrator - outbound only",
            allow_all_outbound=True,
        )

        # ECS Service
        service = ecs.FargateService(
            self, "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            security_groups=[sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE_SPOT",
                    weight=1,
                ),
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE",
                    weight=0,
                    base=1,
                ),
            ],
        )

        CfnOutput(self, "ClusterArn", value=cluster.cluster_arn)
        CfnOutput(self, "ServiceArn", value=service.service_arn)
        CfnOutput(self, "RepositoryUri", value=repo.repository_uri)
