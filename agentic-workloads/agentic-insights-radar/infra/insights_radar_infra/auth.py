# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cognito: users, the three role groups, the first administrator and the app clients."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from aws_cdk import Aws, Duration, RemovalPolicy, SecretValue
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from insights_radar_infra.config import APP_NAME, RETENTION_DAYS, DeployConfig

ROLE_GROUPS = {
    "admin": "Administrators: users, modules, products, budgets, kill switch",
    "editor": "Editors: radars, suggestions, feedback, drafts",
    "viewer": "Viewers: read and chat (members of no group are viewers too)",
}
# The MCP server signs users in from their own machine through a loopback redirect.
MCP_CALLBACKS = ["http://localhost:8765/callback", "http://127.0.0.1:8765/callback"]


# The federated sign-in hook's code: its own folder, deployed as a small asset.
HOOKS = Path(__file__).parent / "hooks"


class Auth(Construct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        config: DeployConfig,
        key: kms.IKey | None = None,
    ) -> None:
        super().__init__(scope, cid)
        self._key = key
        self._e2e_sign_in = config.e2e_sign_in
        self._idp: cognito.CfnUserPoolIdentityProvider | None = None
        self.sign_out_role: iam.Role | None = None
        # RETAIN_ON_UPDATE_OR_DELETE: a failed first deployment still cleans up, so a retry does not collide.
        removal = (
            RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
            if config.retain_data
            else RemovalPolicy.DESTROY
        )
        self.user_pool = cognito.UserPool(
            self,
            "Users",
            user_pool_name=f"{config.prefix}-users",
            self_sign_up_enabled=False,  # administrators invite users
            sign_in_aliases=cognito.SignInAliases(email=True),
            sign_in_case_sensitive=False,
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            # Federated sign-in maps the provider's email onto the pool's at every sign-in, and Cognito
            # refuses to update an immutable attribute, so it must be mutable there. Users still cannot
            # change it themselves: the app clients do not grant aws.cognito.signin.user.admin.
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=config.federated)
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
                temp_password_validity=Duration.days(3),
            ),
            mfa=cognito.Mfa.REQUIRED,  # TOTP is set up at first sign-in
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            user_invitation=cognito.UserInvitationConfig(
                email_subject=f"You have been invited to {APP_NAME}",
                email_body=(
                    f"An administrator invited you to {APP_NAME}. Sign in with {{username}} and the temporary "
                    "password {####}. You will be asked to choose a new password."
                ),
            ),
            deletion_protection=config.protect,
            removal_policy=removal,
        )
        self.federated = config.federated
        self.provider_name = config.oidc_provider_name if config.federated else None
        for name, description in ROLE_GROUPS.items():
            cognito.CfnUserPoolGroup(
                self,
                f"Group{name.title()}",
                user_pool_id=self.user_pool.user_pool_id,
                group_name=name,
                description=description,
            )
        domain_prefix = f"{config.prefix}-{Aws.ACCOUNT_ID}"
        self.domain_name = f"{domain_prefix}.auth.{Aws.REGION}.amazoncognito.com"
        self.domain = self.user_pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
            managed_login_version=cognito.ManagedLoginVersion.CLASSIC_HOSTED_UI,
        )
        if config.federated:
            self._federate(config)
            return
        admin = cognito.CfnUserPoolUser(
            self,
            "FirstAdmin",
            user_pool_id=self.user_pool.user_pool_id,
            username=config.admin_email,
            desired_delivery_mediums=["EMAIL"],
            # Validation stacks: no invitation mail (it would bounce and use the 50-a-day sender quota).
            message_action="SUPPRESS" if config.suppress_invitation else None,
            user_attributes=[
                cognito.CfnUserPoolUser.AttributeTypeProperty(
                    name="email", value=config.admin_email
                ),
                cognito.CfnUserPoolUser.AttributeTypeProperty(
                    name="email_verified", value="true"
                ),
            ],
        )
        attach = cognito.CfnUserPoolUserToGroupAttachment(
            self,
            "FirstAdminGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="admin",
            username=config.admin_email,
        )
        attach.node.add_dependency(admin)
        for group in self.node.children:
            if isinstance(group, cognito.CfnUserPoolGroup):
                attach.node.add_dependency(group)

    def _federate(self, config: DeployConfig) -> None:
        """One OpenID Connect provider, the only way in: no account, password or invitation mail is made."""
        secret = SecretValue.secrets_manager(
            str(config.oidc_client_secret_arn),
            version_id=config.oidc_client_secret_version,
        )
        self._idp = cognito.CfnUserPoolIdentityProvider(
            self,
            "Oidc",
            user_pool_id=self.user_pool.user_pool_id,
            provider_name=config.oidc_provider_name,
            provider_type="OIDC",
            provider_details={
                "client_id": config.oidc_client_id,
                # A {{resolve:secretsmanager:...}} reference: CloudFormation reads it at deploy time.
                "client_secret": secret.unsafe_unwrap(),
                "authorize_scopes": "openid email",
                "oidc_issuer": config.oidc_issuer,
                "attributes_request_method": "GET",
            },
            attribute_mapping={"email": "email", "email_verified": "email_verified"},
        )
        if config.oidc_sign_out_principal:
            # The provider's user-lifecycle service signs a person out of this pool when they leave or are
            # blocked there: it may list users and end their sessions here, nothing else and no other pool.
            # No aws:SourceAccount condition: the provider calls from its own account, not this one, and
            # config refuses an amazonaws.com principal, so no service in this account can use the role.
            self.sign_out_role = iam.Role(
                self,
                "SignOutRole",
                assumed_by=iam.ServicePrincipal(config.oidc_sign_out_principal),
                description=f"{APP_NAME} identity provider sign-out",
            )
            self.sign_out_role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "cognito-idp:ListUsers",
                        "cognito-idp:AdminUserGlobalSignOut",
                        "cognito-idp:DescribeIdentityProvider",
                    ],
                    resources=[self.user_pool.user_pool_arn],
                )
            )
        log_group = logs.LogGroup(
            self,
            "PreTokenLogs",
            retention=logs.RetentionDays[RETENTION_DAYS[config.log_retention_days]],
            removal_policy=RemovalPolicy.RETAIN
            if config.retain_data
            else RemovalPolicy.DESTROY,
            encryption_key=self._key,
        )
        # Its own role, writing to its own log group only: the hook needs no other access.
        role = iam.Role(
            self,
            "PreTokenRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=f"{APP_NAME} sign-in token hook",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[log_group.log_group_arn],
            )
        )
        hook = lambda_.Function(
            self,
            "PreToken",
            role=role,
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="pre_token.handler",
            code=lambda_.Code.from_asset(str(HOOKS), exclude=["__pycache__"]),
            timeout=Duration.seconds(5),
            memory_size=128,
            environment={
                "ADMIN_EMAILS": json.dumps(list(config.administrators)),
                "TRUST_PROVIDER_EMAIL": str(config.oidc_trust_email).lower(),
            },
            environment_encryption=self._key,
            description="Names the configured administrators as admins in the sign-in token",
        )
        self.user_pool.add_trigger(cognito.UserPoolOperation.PRE_TOKEN_GENERATION, hook)
        # Its own policy rather than the role's default one: the function must not wait for the pool, which
        # waits for the function (its trigger), and the grant names this pool only.
        iam.Policy(
            self,
            "PreTokenGroups",
            roles=[role],
            statements=[
                iam.PolicyStatement(
                    actions=["cognito-idp:AdminAddUserToGroup"],
                    resources=[self.user_pool.user_pool_arn],
                )
            ],
        )
        self.pre_token = hook

    def add_clients(self, site_url: str) -> None:
        """Public PKCE clients: the web app and the MCP server. Short-lived tokens, refresh in 12 h.

        Both sign in only through the hosted UI (authorization code with PKCE) and refresh tokens: no SRP
        or password flow, so a script cannot try passwords against InitiateAuth with these client ids.
        """

        def client(
            cid: str, callbacks: list[str], logouts: list[str], srp: bool = False
        ) -> cognito.UserPoolClient:
            made = self.user_pool.add_client(
                cid,
                generate_secret=False,
                o_auth=cognito.OAuthSettings(
                    flows=cognito.OAuthFlows(authorization_code_grant=True),
                    scopes=[
                        cognito.OAuthScope.OPENID,
                        cognito.OAuthScope.EMAIL,
                        cognito.OAuthScope.PROFILE,
                    ],
                    callback_urls=callbacks,
                    logout_urls=logouts,
                ),
                supported_identity_providers=[
                    cognito.UserPoolClientIdentityProvider.custom(self.provider_name)
                    if self.provider_name
                    else cognito.UserPoolClientIdentityProvider.COGNITO
                ],
                prevent_user_existence_errors=True,
                enable_token_revocation=True,
                # Federated: the short lifetimes company identity providers often ask for (access
                # 10 minutes, ID one hour, refresh 10 hours).
                id_token_validity=Duration.hours(1),
                access_token_validity=Duration.minutes(10)
                if self.provider_name
                else Duration.hours(1),
                refresh_token_validity=Duration.hours(10)
                if self.provider_name
                else Duration.hours(12),
            )
            # CDK leaves ExplicitAuthFlows unset when no flow is chosen, and Cognito then allows SRP and
            # custom auth by default: name the one flow these clients use.
            cfn = cast(cognito.CfnUserPoolClient, made.node.default_child)
            if self._idp is not None:
                made.node.add_dependency(self._idp)
            cfn.explicit_auth_flows = [
                *(["ALLOW_USER_SRP_AUTH"] if srp else []),
                "ALLOW_REFRESH_TOKEN_AUTH",
            ]
            return made

        self.web_client = client(
            "WebClient", [f"{site_url}/auth/callback"], [f"{site_url}/"]
        )
        self.mcp_client = client(
            "McpClient",
            MCP_CALLBACKS,
            ["http://localhost:8765/signed-out"],
            srp=self._e2e_sign_in,
        )
        self.issuer = f"https://cognito-idp.{Aws.REGION}.amazonaws.com/{self.user_pool.user_pool_id}"
        self.discovery_url = f"{self.issuer}/.well-known/openid-configuration"
        self.domain_url = self.domain.base_url()
