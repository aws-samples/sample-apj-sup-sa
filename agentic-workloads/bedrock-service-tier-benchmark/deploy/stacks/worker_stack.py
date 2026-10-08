"""Worker tier: a daily Fargate Spot task (discovery agent, then the benchmark, then load into Aurora).

Network: public subnet with a public IP so the task reaches AWS APIs without a NAT
gateway; its security group has **no ingress** and allows egress on TCP 443 only.
(For a private-subnet variant with a NAT gateway or VPC endpoints, see deploy/README.md.)
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from cdk_nag import NagSuppressions
from constructs import Construct

from . import checkov
from .data_stack import DB_NAME, DataStack

_SAMPLE_ROOT = Path(__file__).resolve().parents[2]


class WorkerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        data: DataStack,
        schedule_expression: str,
        benchmark_args: str,
        agent_model_id: str,
        arch: str = "arm64",
        **kw,
    ) -> None:
        super().__init__(scope, cid, **kw)
        if arch not in ("arm64", "x86_64"):
            raise ValueError("worker_arch must be arm64 or x86_64")
        arm = arch == "arm64"

        cluster = ecs.Cluster(self, "Cluster", vpc=data.vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)
        cluster.enable_fargate_capacity_providers()

        sg = ec2.SecurityGroup(
            self, "TaskSg", vpc=data.vpc, description="Worker: no ingress, HTTPS egress only", allow_all_outbound=False
        )
        sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "AWS APIs and AWS documentation over HTTPS")

        image = ecr_assets.DockerImageAsset(
            self,
            "Image",
            directory=str(_SAMPLE_ROOT),
            file="deploy/worker/Dockerfile",
            # arm64 (Graviton) is cheaper; x86_64 for build hosts without arm64 emulation.
            platform=ecr_assets.Platform.LINUX_ARM64 if arm else ecr_assets.Platform.LINUX_AMD64,
            exclude=[".venv", "deploy/.venv", "deploy/cdk.out", "**/node_modules", ".git", ".ash", ".holmes"],
        )

        log_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.THREE_MONTHS)
        task_role = iam.Role(self, "TaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        exec_role = iam.Role(self, "ExecRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        task = ecs.FargateTaskDefinition(
            self,
            "Task",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64 if arm else ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=task_role,
            execution_role=exec_role,
        )
        task.add_container(
            "worker",
            image=ecs.ContainerImage.from_docker_image_asset(image),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="worker", log_group=log_group),
            readonly_root_filesystem=True,
            environment={
                "CLUSTER_ARN": data.cluster.cluster_arn,
                "WRITER_SECRET_ARN": data.writer_secret.secret_arn,
                "DB_NAME": DB_NAME,
                "BENCHMARK_ARGS": benchmark_args,
                "AGENT_MODEL_ID": agent_model_id,
                "AWS_REGION": self.region,
            },
        )
        # Writable scratch on a read-only root filesystem. Fargate applies the ownership of the
        # image's /work (the non-root `bench` user) to this ephemeral volume.
        task.add_volume(name="work")
        task.default_container.add_mount_points(
            ecs.MountPoint(container_path="/work", source_volume="work", read_only=False)
        )

        # ---- least-privilege task role
        acct, region = self.account, self.region
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockInvoke",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{acct}:inference-profile/*",
                    "arn:aws:bedrock:*::inference-profile/*",
                    f"arn:aws:bedrock:*:{acct}:project/default",
                ],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockRead",
                actions=["bedrock:ListFoundationModels", "bedrock:ListInferenceProfiles"],
                resources=["*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="BearerTokens",
                actions=["bedrock:CallWithBearerToken", "bedrock-mantle:CallWithBearerToken"],
                resources=["*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="Mantle",
                actions=["bedrock-mantle:CreateInference"],
                resources=[f"arn:aws:bedrock-mantle:*:{acct}:project/*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="Aurora",
                # Transactions: registry upserts are atomic (begin/commit/rollback).
                actions=[
                    "rds-data:ExecuteStatement",
                    "rds-data:BatchExecuteStatement",
                    "rds-data:BeginTransaction",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction",
                ],
                resources=[data.cluster.cluster_arn],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriterSecret",
                actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                resources=[data.writer_secret.secret_arn],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriterSecretKey",
                actions=["kms:Decrypt"],
                resources=[data.key.key_arn],
                conditions={"StringEquals": {"kms:ViaService": f"secretsmanager.{region}.amazonaws.com"}},
            )
        )

        # ---- daily schedule (EventBridge Scheduler -> ECS RunTask on Fargate Spot)
        sched_role = iam.Role(self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        sched_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task.task_definition_arn],
                conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
            )
        )
        sched_role.add_to_policy(
            iam.PolicyStatement(actions=["iam:PassRole"], resources=[task_role.role_arn, exec_role.role_arn])
        )
        scheduler.CfnSchedule(
            self,
            "Daily",
            schedule_expression=schedule_expression,
            schedule_expression_timezone="UTC",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=cluster.cluster_arn,
                role_arn=sched_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=1),
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task.task_definition_arn,
                    task_count=1,
                    capacity_provider_strategy=[
                        scheduler.CfnSchedule.CapacityProviderStrategyItemProperty(
                            capacity_provider="FARGATE_SPOT", weight=1
                        )
                    ],
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=[s.subnet_id for s in data.vpc.public_subnets],
                            security_groups=[sg.security_group_id],
                            assign_public_ip="ENABLED",
                        )
                    ),
                ),
            ),
        )
        self.cluster, self.task, self.security_group = cluster, task, sg
        checkov.skip(log_group, checkov.LOGS_DEFAULT_KEY)

        NagSuppressions.add_resource_suppressions(
            task_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "The benchmark must reach every multi-tier model the agent discovers, so model and "
                    "inference-profile ARNs are wildcarded within the bedrock service; List* and "
                    "CallWithBearerToken do not support resource-level permissions; Mantle is limited to "
                    "this account's projects.",
                }
            ],
            apply_to_children=True,
        )
        NagSuppressions.add_resource_suppressions(
            exec_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "CDK-generated ECR/logs permissions for the task's own image and log group.",
                }
            ],
            apply_to_children=True,
        )
        NagSuppressions.add_resource_suppressions(
            task,
            [{"id": "AwsSolutions-ECS2", "reason": "Environment holds only ARNs and options, no secrets."}],
        )
