"""Shared gateway task definition builder for the ECS gateway service."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from infra.config import InfraContext

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)

GATEWAY_IMAGE_EXCLUDES = [
    "infra",
    "tests",
    "aidlc-docs",
    "docs",
    ".git",
    "*.md",
    "**/__pycache__",
    "**/*.pyc",
    ".pytest_cache",
    "build",
    "dist",
    "*.egg-info",
]


def build_gateway_task_definition(
    scope: Construct,
    construct_id: str,
    *,
    context: InfraContext,
    family: str,
    execution_role: iam.IRole,
    task_role: iam.IRole,
    db_endpoint: str,
    db_read_endpoint: str,
    db_secret: secretsmanager.ISecret,
    kms_key_id: str,
    amp_remote_write_url: str,
    amp_workspace_id: str,
    log_group: logs.ILogGroup,
    origin_secret: secretsmanager.ISecret | None = None,
    anthropic_api_key_secret_arn: str | None = None,
) -> ecs.FargateTaskDefinition:
    """Create the shared gateway task definition and containers."""

    task_definition = ecs.FargateTaskDefinition(
        scope,
        construct_id,
        family=family,
        cpu=1024,
        memory_limit_mib=2048,
        execution_role=execution_role,
        task_role=task_role,
        runtime_platform=ecs.RuntimePlatform(
            cpu_architecture=ecs.CpuArchitecture.ARM64,
            operating_system_family=ecs.OperatingSystemFamily.LINUX,
        ),
    )

    gateway_image = ecs.ContainerImage.from_asset(
        PROJECT_ROOT,
        file="Dockerfile",
        platform=ecr_assets.Platform.LINUX_ARM64,
        exclude=GATEWAY_IMAGE_EXCLUDES,
    )

    migrate = task_definition.add_container(
        "migrate",
        image=gateway_image,
        essential=False,
        command=["sh", "scripts/migrate-entrypoint.sh"],
        environment={
            "DB_ENDPOINT": db_endpoint,
        },
        secrets={
            "DB_USERNAME": ecs.Secret.from_secrets_manager(db_secret, field="username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, field="password"),
        },
        logging=ecs.LogDrivers.aws_logs(
            stream_prefix="migrate",
            log_group=log_group,
        ),
    )

    gateway_app = task_definition.add_container(
        "gateway-app",
        image=gateway_image,
        cpu=512,
        memory_limit_mib=1024,
        essential=True,
        environment={
            "DB_ENDPOINT": db_endpoint,
            "DB_READ_ENDPOINT": db_read_endpoint,
            "DB_NAME": "claude_proxy",
            "KMS_KEY_ID": kms_key_id,
            "AMP_REMOTE_WRITE_URL": amp_remote_write_url,
            "AMP_WORKSPACE_ID": amp_workspace_id,
            "IDENTITY_STORE_ID": context.identity_store_id,
            "IDENTITY_STORE_REGION": context.identity_store_region,
            "AWS_REGION": context.region,
            **(
                {"ANTHROPIC_API_KEY_SECRET_ARN": anthropic_api_key_secret_arn}
                if anthropic_api_key_secret_arn
                else {}
            ),
        },
        secrets={
            "DB_USERNAME": ecs.Secret.from_secrets_manager(db_secret, field="username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, field="password"),
            **({
                "ADMIN_ORIGIN_VALUE": ecs.Secret.from_secrets_manager(origin_secret),
                "AUTH_ORIGIN_VALUE": ecs.Secret.from_secrets_manager(origin_secret),
            } if origin_secret else {}),
        },
        logging=ecs.LogDrivers.aws_logs(
            stream_prefix="gateway-app",
            log_group=log_group,
        ),
    )
    gateway_app.add_port_mappings(
        ecs.PortMapping(container_port=8000, protocol=ecs.Protocol.TCP)
    )
    gateway_app.add_container_dependencies(
        ecs.ContainerDependency(
            container=migrate,
            condition=ecs.ContainerDependencyCondition.SUCCESS,
        )
    )

    adot_config_yaml = dedent(
        f"""
        receivers:
          otlp:
            protocols:
              grpc:
                endpoint: 0.0.0.0:4317

        processors:
          batch:
            timeout: 60s
            send_batch_size: 50

        exporters:
          prometheusremotewrite:
            endpoint: {amp_remote_write_url}
            auth:
              authenticator: sigv4auth
            resource_to_telemetry_conversion:
              enabled: true

        extensions:
          sigv4auth:
            region: {context.region}
            service: aps

        service:
          extensions: [sigv4auth]
          pipelines:
            metrics:
              receivers: [otlp]
              processors: [batch]
              exporters: [prometheusremotewrite]
        """
    ).strip()
    adot_collector = task_definition.add_container(
        "adot-collector",
        image=ecs.ContainerImage.from_registry(
            "public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1"
        ),
        cpu=256,
        memory_limit_mib=512,
        essential=False,
        environment={
            "AOT_CONFIG_CONTENT": adot_config_yaml,
            "AWS_REGION": context.region,
        },
        logging=ecs.LogDrivers.aws_logs(
            stream_prefix="adot-collector",
            log_group=log_group,
        ),
    )
    adot_collector.add_port_mappings(
        ecs.PortMapping(container_port=4317, protocol=ecs.Protocol.TCP)
    )

    return task_definition
