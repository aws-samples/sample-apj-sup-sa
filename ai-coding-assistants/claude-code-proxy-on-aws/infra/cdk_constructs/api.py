"""ApiConstruct for the public ingress layer."""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from constructs import Construct

from infra.config import InfraContext
from infra.cdk_constructs.alb import AlbConstruct
from infra.cdk_constructs.compute import ComputeConstruct
from infra.cdk_constructs.data import DataConstruct
from infra.cdk_constructs.network import NetworkConstruct
from infra.cdk_constructs.observability import ObservabilityConstruct
from infra.stacks.gateway_task_definition import build_gateway_task_definition


class ApiConstruct(Construct):
    """Provision the ALB and API Gateway entrypoints."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        context: InfraContext,
        network: NetworkConstruct,
        compute: ComputeConstruct,
        alb: AlbConstruct,
        data: DataConstruct,
        observability: ObservabilityConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.context = context

        # Origin secret lives in ComputeConstruct to avoid circular dependency
        self.origin_secret = compute.origin_secret

        self.token_api = apigateway.RestApi(
            self,
            "TokenApi",
            rest_api_name=context.resource_name("token-api"),
            description=f"Token Service API ({context.environment})",
            endpoint_types=[apigateway.EndpointType.REGIONAL],
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                logging_level=apigateway.MethodLoggingLevel.ERROR,
                metrics_enabled=True,
                data_trace_enabled=False,
            ),
        )

        # Inject origin secret into stage variable via CloudFormation dynamic reference
        cfn_stage = self.token_api.deployment_stage.node.default_child
        cfn_stage.add_property_override(
            "Variables.originSecret",
            f"{{{{resolve:secretsmanager:{self.origin_secret.secret_name}:SecretString}}}}",
        )

        v1_resource = self.token_api.root.add_resource("v1")
        auth_resource = v1_resource.add_resource("auth")
        token_resource = auth_resource.add_resource("token")

        admin_resource = v1_resource.add_resource("admin")
        admin_proxy_resource = admin_resource.add_resource("{proxy+}")

        self.gateway_task_definition = build_gateway_task_definition(
            self,
            "GatewayTaskDefinition",
            context=context,
            family=context.resource_name("gateway-task"),
            execution_role=compute.gateway_execution_role,
            task_role=compute.gateway_task_role,
            db_endpoint=data.db_endpoint,
            db_read_endpoint=data.db_read_endpoint,
            db_secret=data.db_secret,
            kms_key_id=data.virtual_key_kms_key.key_id,
            amp_remote_write_url=observability.amp_remote_write_url,
            amp_workspace_id=observability.amp_workspace_id,
            log_group=compute.ecs_log_group,
            origin_secret=self.origin_secret,
            anthropic_api_key_secret_arn=data.anthropic_api_key_secret.secret_arn,
        )
        self.gateway_service = ecs.FargateService(
            self,
            "GatewayService",
            service_name=context.resource_name("gateway"),
            cluster=compute.ecs_cluster,
            task_definition=self.gateway_task_definition,
            desired_count=1,
            assign_public_ip=False,
            security_groups=[network.ecs_sg],
            vpc_subnets=ec2.SubnetSelection(subnets=network.private_app_subnets),
            health_check_grace_period=Duration.seconds(120),
        )
        alb.gateway_target_group.add_target(
            self.gateway_service.load_balancer_target(
                container_name="gateway-app",
                container_port=8000,
            )
        )

        # Auto-scaling: scale on CPU utilisation (target 60%)
        scaling = self.gateway_service.auto_scale_task_count(
            min_capacity=1, max_capacity=4
        )
        scaling.scale_on_cpu_utilization(
            "CpuScaling", target_utilization_percent=60
        )

        alb.http_listener.add_target_groups(
            "AdminProxyForwardRule",
            priority=10,
            conditions=[elbv2.ListenerCondition.path_patterns(["/v1/admin", "/v1/admin/*"])],
            target_groups=[alb.gateway_target_group],
        )
        admin_proxy_integration = apigateway.Integration(
            type=apigateway.IntegrationType.HTTP_PROXY,
            integration_http_method="ANY",
            uri=f"http://{alb.alb.load_balancer_dns_name}/v1/admin/{{proxy}}",
            options=apigateway.IntegrationOptions(
                request_parameters={
                    "integration.request.path.proxy": "method.request.path.proxy",
                    "integration.request.header.x-admin-origin": "stageVariables.originSecret",
                    "integration.request.header.x-admin-principal": "context.identity.userArn",
                    "integration.request.header.x-request-id": "context.requestId",
                },
                passthrough_behavior=apigateway.PassthroughBehavior.WHEN_NO_MATCH,
                timeout=Duration.seconds(29),
            ),
        )
        admin_proxy_resource.add_method(
            "ANY",
            admin_proxy_integration,
            authorization_type=apigateway.AuthorizationType.IAM,
            request_parameters={"method.request.path.proxy": True},
        )

        alb.http_listener.add_target_groups(
            "AuthProxyForwardRule",
            priority=20,
            conditions=[elbv2.ListenerCondition.path_patterns(["/v1/auth", "/v1/auth/*"])],
            target_groups=[alb.gateway_target_group],
        )
        auth_token_integration = apigateway.Integration(
            type=apigateway.IntegrationType.HTTP_PROXY,
            integration_http_method="POST",
            uri=f"http://{alb.alb.load_balancer_dns_name}/v1/auth/token",
            options=apigateway.IntegrationOptions(
                request_parameters={
                    "integration.request.header.x-auth-origin": "stageVariables.originSecret",
                    "integration.request.header.x-auth-principal": "context.identity.userArn",
                    "integration.request.header.x-request-id": "context.requestId",
                },
                passthrough_behavior=apigateway.PassthroughBehavior.WHEN_NO_MATCH,
                timeout=Duration.seconds(29),
            ),
        )
        token_resource.add_method(
            "POST",
            auth_token_integration,
            authorization_type=apigateway.AuthorizationType.IAM,
        )
