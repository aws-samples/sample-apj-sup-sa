# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The Agentic Insights Radar stack."""

from __future__ import annotations

from typing import Any, cast

from aws_cdk import Aws, CfnElement, CfnOutput, Stack, Tags, Token
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

from insights_radar_infra.agents import Agents
from insights_radar_infra.auth import Auth
from insights_radar_infra.backend import Backend
from insights_radar_infra.backups import Backups
from insights_radar_infra.config import (
    APP_NAME,
    DESCRIPTION,
    RETENTION_DAYS,
    DeployConfig,
)
from insights_radar_infra.data import Data
from insights_radar_infra.edge import EDGE_REGION, WebAcl, logs_key_statement
from insights_radar_infra.frontend import Frontend
from insights_radar_infra.monitoring import Monitoring, Watched
from insights_radar_infra.search import Search


class AgenticInsightsRadarStack(Stack):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        config: DeployConfig,
        web_acl_arn: str | None = None,
        **kwargs: Any,
    ) -> None:
        """``web_acl_arn`` is the edge stack's ACL; required outside us-east-1, where WAF cannot create it."""
        super().__init__(scope, cid, description=DESCRIPTION, **kwargs)
        if Token.is_unresolved(self.region):
            raise ValueError(
                "deploy to a specific Region: set AWS_REGION (CloudFront's WAF depends on it)"
            )
        if web_acl_arn is None and self.region != EDGE_REGION:
            raise ValueError(
                f"outside {EDGE_REGION} the web ACL comes from the edge stack; pass web_acl_arn"
            )
        Tags.of(self).add("application", APP_NAME)
        # Per deployment, so retained keys, buckets and log groups of two deployments can be told
        # apart, and so the optional AWS Budget can be filtered to this deployment (P2-048, P2-049).
        # Activate "deployment" as a cost allocation tag to use it in Cost Explorer and Budgets.
        Tags.of(self).add("deployment", config.prefix)
        Tags.of(self).add("stack", cid)
        prefix = config.prefix

        data = Data(self, "Data", config)
        if web_acl_arn is None:
            web_acl_arn = WebAcl(self, "Waf", config, self.region, data.key).arn
        search = Search(self, "Search", prefix, data.key, data.assets)
        auth = Auth(self, "Auth", config, key=data.key)
        agents = Agents(self, "Agents", config, data.key)
        data.key.add_to_resource_policy(logs_key_statement())

        retention = logs.RetentionDays[RETENTION_DAYS[config.log_retention_days]]
        frontend = Frontend(
            self,
            "Web",
            prefix,
            data.access_logs,
            config.retain_data,
            data.key,
            retention,
            config.price_class,
            domain_name=config.domain_name,
            parent_zone_id=config.parent_zone_id,
            certificate_arn=config.certificate_arn,
            redirect_domains=config.redirect_domains,
        )
        backend = Backend(
            self, "Backend", config, data, search, auth.user_pool.user_pool_id
        )
        api_domain = (
            f"{backend.http_api.api_id}.execute-api.{Aws.REGION}.{Aws.URL_SUFFIX}"
        )
        frontend.build(
            api_domain, auth.domain_name, data.assets_bucket_name, web_acl_arn
        )
        auth.add_clients(frontend.url)
        audiences = [
            auth.web_client.user_pool_client_id,
            auth.mcp_client.user_pool_client_id,
        ]
        backend.set_auth(auth.issuer, audiences)

        agents.runtimes(backend.api_url, auth.discovery_url, audiences, data, retention)
        backend.agent_gates(
            agents.module_runtime.role.role_arn, agents.chat_runtime.role.role_arn
        )
        agents.use_agent_urls(backend.agent_tools_url, backend.chat_usage_url)
        backend.build(
            module_runtime_arn=agents.module_runtime.agent_runtime_arn,
            module_role_arn=agents.module_runtime.role.role_arn,
            chat_runtime_arn=agents.chat_runtime.agent_runtime_arn,
            chat_role_arn=agents.chat_runtime.role.role_arn,
            site_url=frontend.url,
            cognito_domain=auth.domain_name,
            web_client_id=auth.web_client.user_pool_client_id,
            identity_provider=auth.provider_name,
            zones=frontend.zones,
        )
        agents.grant(backend.state_machine, backend.agent_tools, backend.chat_usage)
        data.assets.add_cors_rule(
            allowed_methods=[s3.HttpMethods.POST],
            allowed_origins=[frontend.url],
            allowed_headers=["*"],
            max_age=3600,
        )

        monitoring = Monitoring(self, "Monitoring", data.key, config)
        monitoring.watch(
            Watched(
                functions=backend.functions,
                runs=backend.state_machine,
                run_logs=backend.run_logs,
                dlq=backend.indexer_dlq,
                failed_events=backend.failed_events,
                api=backend.http_api,
                indexer=backend.indexer,
                runtimes=[agents.module_runtime, agents.chat_runtime],
                url_functions=[backend.agent_tools, backend.chat_usage],
                distribution=frontend.distribution,
                web_acl_arn=web_acl_arn,
                api_logs=backend.api_function.log_group,
                gateway=agents.gateway,
                digest_dlq=backend.digest.dlq if backend.digest else None,
                digest_functions=[
                    fn
                    for fn in (
                        (backend.digest.send, backend.digest.feedback)
                        if backend.digest
                        else ()
                    )
                    if fn is not None
                ],
                digest_sender_logs=backend.digest.send.log_group
                if backend.digest
                else None,
                digest_scheduler_logs=backend.digest.schedule.log_group
                if backend.digest
                else None,
                digest_email=bool(
                    backend.digest and backend.digest.unsubscribe_secret is not None
                ),
            )
        )
        if config.backup_plan:
            Backups(self, "Backups", data)

        CfnOutput(
            self, "AppUrl", value=frontend.url, description=f"Open {APP_NAME} here"
        )
        CfnOutput(
            self,
            "FirstAdmin",
            value=config.admin_email,
            description="Administrator who signs in through the identity provider"
            if config.federated
            else "Invitation with a temporary password sent to this address",
        )
        CfnOutput(self, "UserPoolId", value=auth.user_pool.user_pool_id)
        if auth.provider_name:
            CfnOutput(
                self,
                "IdentityProviderName",
                value=auth.provider_name,
                description="The identity provider's name in the user pool",
            )
        if backend.mcp_role is not None:
            CfnOutput(
                self,
                "McpApiUrl",
                value=backend.http_api.api_endpoint,
                description="MCP sign-in without a browser: give MCP users this as AIR_MCP_API_URL (SigV4 signs "
                "this host; the public config does not name it, because calls to it skip CloudFront's firewall)",
            )
        if auth.sign_out_role is not None:
            CfnOutput(
                self,
                "SignOutRoleArn",
                value=auth.sign_out_role.role_arn,
                description="Role the identity provider assumes to sign people out of this user pool",
            )
        if backend.digest is not None:
            if backend.digest.slack_secret is not None:
                CfnOutput(
                    self,
                    "DigestSlackSecretArn",
                    value=backend.digest.slack_secret.secret_arn,
                    description="Put the Slack bot token (or webhook URL) here: it holds a placeholder",
                )
            if backend.digest.unsubscribe_secret is not None:
                CfnOutput(
                    self,
                    "DigestUnsubscribeKeyArn",
                    value=backend.digest.unsubscribe_secret.secret_arn,
                    description="The unsubscribe links' HMAC key (generated; replacing it voids every link already sent)",
                )
            CfnOutput(
                self,
                "DigestSenderRoleArn",
                value=cast(iam.IRole, backend.digest.send.role).role_arn,
                description="The digest sender's role: directoryRoleArn's trust policy must let it assume that role",
            )
            # "Send test now" on a Slack DM looks the alias up from the API too (c2). Both assume directoryRoleArn, the
            # role the directory lookup accepts; these two are the ones that role must trust (scan cycle 4).
            CfnOutput(
                self,
                "DigestApiRoleArn",
                value=cast(iam.IRole, backend.api_function.role).role_arn,
                description="The API's role (Send test now): directoryRoleArn's trust policy must let it assume that role too",
            )
        CfnOutput(
            self,
            "SiteBucket",
            value=frontend.bucket.bucket_name,
            description="Web app files",
        )
        CfnOutput(self, "DistributionId", value=frontend.distribution.distribution_id)
        CfnOutput(
            self,
            "AlarmTopicArn",
            value=monitoring.topic.topic_arn,
            description="Alarm notifications; deploy.sh checks that someone confirmed a subscription",
        )
        CfnOutput(
            self,
            "DeployOptions",
            value=config.options_json(),
            description="The options this deployment was made with; deploy.sh keeps them on updates",
        )
        CfnOutput(
            self,
            "McpClientId",
            value=auth.mcp_client.user_pool_client_id,
            description="App client for the MCP server",
        )
        # The MCP server pins its sign-in host to this value (AIR_COGNITO_DOMAIN). Without it the
        # client accepts any <name>-<12 digits>.auth.<region>.amazoncognito.com, which includes a
        # look-alike hosted UI in an attacker's own account (P2-011).
        CfnOutput(
            self,
            "CognitoDomain",
            value=auth.domain_name,
            description="Sign-in host; set AIR_COGNITO_DOMAIN to this for the MCP server",
        )

        # AgentCore Memory cannot be deleted while it is still CREATING, so a failure anywhere else during
        # its first minutes left the stack in ROLLBACK_FAILED. Everything else now waits for it: whichever
        # resource fails, the memory is ACTIVE by then and the rollback can remove it.
        for child in self.node.children:
            if child not in (agents, data) and not isinstance(child, CfnElement):
                child.node.add_dependency(agents.memory)
        # Data is left out of that list because the memory is encrypted with the data key, so the
        # memory depends on the key; a dependency the other way would be a cycle. Everything else
        # Data creates still waits for the memory.
        for resource in (*data.waits_for_memory, *agents.waits_for_memory):
            resource.node.add_dependency(agents.memory)
        backend.seed_last(
            *cast(
                list[Construct],
                [
                    child
                    for child in self.node.children
                    if child is not backend and not isinstance(child, CfnElement)
                ],
            )
        )
