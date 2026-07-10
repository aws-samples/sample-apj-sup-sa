"""Behavioural tests for the API/ALB ingress wiring on the service stack.

The ALB, API Gateway, and ECS service all live on the ServiceStack in the
current two-stack layout (FoundationStack + ServiceStack), so these assertions
target ``stack_bundle.service``.
"""

from aws_cdk.assertions import Match, Template


def test_api_stack_uses_runtime_health_check_path(stack_bundle) -> None:
    template = Template.from_stack(stack_bundle.service)
    template.resource_count_is("AWS::ECS::Service", 1)
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"HealthCheckPath": "/v1/healthz"},
    )


def test_api_stack_exposes_admin_proxy_via_api_gateway(stack_bundle) -> None:
    template = Template.from_stack(stack_bundle.service)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {
            "AuthorizationType": "AWS_IAM",
            "HttpMethod": "ANY",
            "RequestParameters": {"method.request.path.proxy": True},
            "Integration": Match.object_like(
                {
                    "Type": "HTTP_PROXY",
                    "IntegrationHttpMethod": "ANY",
                    "RequestParameters": Match.object_like(
                        {
                            "integration.request.path.proxy": "method.request.path.proxy",
                            # Origin verification now uses the API Gateway stage
                            # variable backed by the rotating origin secret,
                            # not a static literal.
                            "integration.request.header.x-admin-origin": (
                                "stageVariables.originSecret"
                            ),
                            "integration.request.header.x-admin-principal": (
                                "context.identity.userArn"
                            ),
                            "integration.request.header.x-request-id": "context.requestId",
                        }
                    ),
                }
            ),
        },
    )


def test_api_stack_forwards_admin_paths_on_http_listener(stack_bundle) -> None:
    template = Template.from_stack(stack_bundle.service)
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::ListenerRule",
        {
            "Actions": Match.array_with([Match.object_like({"Type": "forward"})]),
            "Conditions": Match.array_with(
                [
                    Match.object_like(
                        {
                            "Field": "path-pattern",
                            "PathPatternConfig": {"Values": ["/v1/admin", "/v1/admin/*"]},
                        }
                    )
                ]
            ),
        },
    )
