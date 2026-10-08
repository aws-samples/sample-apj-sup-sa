"""API tier: Cognito sign-in, a JWT-authorised HTTP API and a read-only Lambda (Data API, no VPC).

``auth_mode`` selects who can sign in:

* ``cognito`` (public sample): users created by an administrator only (no self
  sign-up), TOTP MFA required, Cognito threat protection enforced.
* ``midway`` (Amazon-internal copy): the same user pool federated to an OIDC
  identity provider (Amazon Federate, backed by Midway); native sign-in is disabled,
  so only federated users get tokens.

Either way API Gateway validates the JWT before the Lambda runs, and the Lambda's
database role can only SELECT.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from cdk_nag import NagSuppressions
from constructs import Construct

from .data_stack import DB_NAME, DataStack

_LAMBDAS = Path(__file__).resolve().parent.parent / "lambdas"
ROUTES = ("/models", "/runs", "/filters", "/comparisons", "/cells")


class ApiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        data: DataStack,
        web_origin: str,
        auth_mode: str,
        federate: dict[str, str] | None = None,
        **kw,
    ) -> None:
        super().__init__(scope, cid, **kw)

        self.user_pool = cognito.UserPool(
            self,
            "Users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(
                min_length=14,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
                temp_password_validity=Duration.days(3),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            feature_plan=cognito.FeaturePlan.PLUS,
            standard_threat_protection_mode=cognito.StandardThreatProtectionMode.FULL_FUNCTION,
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        domain = self.user_pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"bench-tiers-{self.account}"),
        )

        providers = [cognito.UserPoolClientIdentityProvider.COGNITO]
        idp = None
        if auth_mode == "midway":
            if not federate or not federate.get("issuer_url") or not federate.get("client_id"):
                raise ValueError("auth_mode=midway needs federate_issuer_url and federate_client_id context")
            idp = cognito.UserPoolIdentityProviderOidc(
                self,
                "Federate",
                user_pool=self.user_pool,
                name="Federate",
                issuer_url=federate["issuer_url"],
                client_id=federate["client_id"],
                # The client secret is read from Secrets Manager at deploy time; it never
                # appears in the template or the repository.
                client_secret_value=SecretValue.secrets_manager(federate["client_secret_name"]),
                scopes=["openid", "email"],
                attribute_mapping=cognito.AttributeMapping(email=cognito.ProviderAttribute.other("email")),
            )
            providers = [cognito.UserPoolClientIdentityProvider.custom("Federate")]

        self.client = self.user_pool.add_client(
            "WebClient",
            generate_secret=False,  # public SPA client: authorization code + PKCE
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"{web_origin}/"],
                logout_urls=[f"{web_origin}/"],
            ),
            supported_identity_providers=providers,
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.hours(8),
        )
        if idp is not None:
            self.client.node.add_dependency(idp)

        # ---- read-only Lambda (no VPC: the Data API is a regional HTTPS endpoint)
        log_group = logs.LogGroup(self, "ApiFnLogs", retention=logs.RetentionDays.ONE_MONTH)
        role = iam.Role(self, "ApiFnRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[log_group.log_group_arn, f"{log_group.log_group_arn}:log-stream:*"],
            )
        )
        fn = lambda_.Function(
            self,
            "ApiFn",
            role=role,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(_LAMBDAS / "api")),
            timeout=Duration.seconds(30),
            memory_size=256,
            reserved_concurrent_executions=10,  # bounds cost and Data API load
            log_group=log_group,
            environment={
                "CLUSTER_ARN": data.cluster.cluster_arn,
                "READER_SECRET_ARN": data.reader_secret.secret_arn,
                "DB_NAME": DB_NAME,
            },
        )
        data.cluster.grant_data_api_access(fn)
        data.reader_secret.grant_read(fn)
        data.key.grant_decrypt(fn)

        # ---- HTTP API with JWT authoriser on every route
        access_logs = logs.LogGroup(self, "ApiAccessLogs", retention=logs.RetentionDays.ONE_MONTH)
        self.http_api = apigw.HttpApi(
            self,
            "Api",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=[web_origin],
                allow_methods=[apigw.CorsHttpMethod.GET],
                allow_headers=["authorization"],
                max_age=Duration.hours(1),
            ),
            create_default_stage=False,
        )
        stage = self.http_api.add_stage(
            "Prod",
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=20, burst_limit=40),
        )
        cfn_stage = stage.node.default_child
        cfn_stage.access_log_settings = apigw.CfnStage.AccessLogSettingsProperty(
            destination_arn=access_logs.log_group_arn,
            format='{"requestId":"$context.requestId","ip":"$context.identity.sourceIp",'
            '"time":"$context.requestTime","route":"$context.routeKey","status":"$context.status",'
            '"sub":"$context.authorizer.claims.sub","latency":"$context.responseLatency"}',
        )
        authorizer = authorizers.HttpJwtAuthorizer(
            "Jwt",
            jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}",
            jwt_audience=[self.client.user_pool_client_id],
        )
        integration = integrations.HttpLambdaIntegration("ApiFnIntegration", fn)
        for path in ROUTES:
            self.http_api.add_routes(
                path=path, methods=[apigw.HttpMethod.GET], integration=integration, authorizer=authorizer
            )

        CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.client.user_pool_client_id)
        CfnOutput(self, "CognitoDomain", value=domain.base_url())

        NagSuppressions.add_resource_suppressions(
            role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Lambda creates log streams at runtime; wildcard limited to its own log group.",
                    "appliesTo": [{"regex": "/^Resource::<ApiFnLogs.*\\.Arn>:log-stream:\\*$/"}],
                }
            ],
            apply_to_children=True,
        )
