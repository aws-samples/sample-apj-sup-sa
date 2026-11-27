# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AgentCore: module and chat runtimes, chat memory, the guardrail and the web-search gateway."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from aws_cdk import Aws, Duration, Fn
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import custom_resources as cr
from constructs import Construct

from insights_radar_infra.backend import function_url_access
from insights_radar_infra.config import MODEL_KEYS, DeployConfig
from insights_radar_infra.data import Data

ROOT = Path(__file__).resolve().parents[2]
AGENT_BUNDLE = ROOT / "build" / "agents"
# The global cross-Region inference profiles of the model catalog
# (backend/src/insights_radar/modules/catalog.py; infra/tests keep the two lists equal).
MODEL_PROFILES = (
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "global.anthropic.claude-sonnet-5-5",
    "global.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-opus-5-5",
    "global.anthropic.claude-fable-5-1",
)
MODULE_IDLE_MINUTES = 15
CHAT_IDLE_MINUTES = 5
CODE_INTERPRETER_ACTIONS = [
    "bedrock-agentcore:StartCodeInterpreterSession",
    "bedrock-agentcore:InvokeCodeInterpreter",
    "bedrock-agentcore:StopCodeInterpreterSession",
    "bedrock-agentcore:GetCodeInterpreterSession",
]
BROWSER_ACTIONS = [
    "bedrock-agentcore:StartBrowserSession",
    "bedrock-agentcore:StopBrowserSession",
    "bedrock-agentcore:GetBrowserSession",
    "bedrock-agentcore:ConnectBrowserAutomationStream",
]
# The guardrail's filters. Its published version names a hash of them, so any change to a filter
# publishes a new version (CloudFormation publishes one only when the version resource changes).
CONTENT_FILTERS = (
    # PROMPT_ATTACK at MEDIUM: HIGH false-blocks benign questions that quote defensive instructions.
    ("PROMPT_ATTACK", "MEDIUM", "NONE"),
    ("HATE", "HIGH", "HIGH"),
    ("INSULTS", "MEDIUM", "MEDIUM"),
    ("VIOLENCE", "MEDIUM", "MEDIUM"),
    ("SEXUAL", "HIGH", "HIGH"),
    ("MISCONDUCT", "HIGH", "HIGH"),
)
# Credentials never belong in an answer, a draft or an insight: masked in model output only (names,
# addresses and contacts of public people are what the graph records, so they are left alone).
OUTPUT_SECRETS = ("AWS_ACCESS_KEY", "AWS_SECRET_KEY", "CREDIT_DEBIT_CARD_NUMBER")


def guardrail_revision() -> str:
    text = json.dumps(
        {"content": CONTENT_FILTERS, "secrets": OUTPUT_SECRETS}, sort_keys=True
    )
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# The catalog key of each profile above, in the same order as config.MODEL_KEYS (a test keeps the
# three lists in step). Used to give the chat runtime only the model it is configured with (P2-073).
PROFILE_OF = dict(zip(MODEL_KEYS, MODEL_PROFILES, strict=True))


def model_resources(profiles: tuple[str, ...] = MODEL_PROFILES) -> list[str]:
    """Each named profile, and the model it routes to in any Region (a global profile may serve a
    request anywhere) and without a Region, as global cross-Region inference requires."""
    partition, region, account = Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID
    out: list[str] = []
    for profile in profiles:
        model = profile.removeprefix("global.")
        out += [
            f"arn:{partition}:bedrock:{region}:{account}:inference-profile/{profile}",
            f"arn:{partition}:bedrock:*::foundation-model/{model}",
            f"arn:{partition}:bedrock:::foundation-model/{model}",
        ]
    return out


class Agents(Construct):
    def __init__(
        self, scope: Construct, cid: str, config: DeployConfig, key: kms.IKey
    ) -> None:
        """``key`` is the deployment's data key: chat history (personal data), the guardrail, the
        web-search gateway and everything else at rest use it rather than an AWS-owned key (P2-019).

        How each of them takes the key on an upgrade from a deployment that predates it:

        - AgentCore Memory: ``EncryptionKeyArn`` is create-only, so the key cannot be added without
          replacing the memory and deleting every user's conversations. An older deployment keeps an
          AWS-owned key (``chatMemoryKey=aws-owned``) until its operator opts in (CR2-01).
        - Guardrail: ``KmsKeyArn`` is updated in place (CloudFormation: no interruption; Bedrock's
          UpdateGuardrail takes ``kmsKeyId``), so every deployment moves to the data key.
        - Web-search gateway: CloudFormation lists ``KmsKeyArn`` as updatable, but AgentCore refuses
          it ("KMS key cannot be updated for an existing gateway") and that rolled back the r2
          upgrade. The gateway holds no data, so it is replaced instead: a new logical ID and name
          (``<prefix>-web-<stack id>``), and the module runtime gets the new URL in the same deploy."""
        super().__init__(scope, cid)
        if not AGENT_BUNDLE.exists():
            raise FileNotFoundError(
                "build/agents is missing: run `python3 deployment/build.py` first"
            )
        self._config = config
        self._key = key
        self._sdk_call_logs: logs.LogGroup | None = None
        self._guardrail_key: iam.Policy | None = None
        region = Aws.REGION

        generation = (
            config.guardrail_generation
        )  # 1 keeps the original resource; see DeployConfig
        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail" if generation == 1 else f"Guardrail{generation}",
            name=f"{config.prefix}-guardrail"
            if generation == 1
            else f"{config.prefix}-guardrail-{generation}",
            description="Blocks prompt attacks and harmful content in user and run messages",
            blocked_input_messaging="This request was blocked by a safety guardrail. Please rephrase it.",
            blocked_outputs_messaging="The response was blocked by a safety guardrail.",
            kms_key_arn=key.key_arn,
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=kind, input_strength=given, output_strength=answered
                    )
                    for kind, given, answered in CONTENT_FILTERS
                ]
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type=kind,
                        action="ANONYMIZE",
                        input_enabled=False,
                        output_enabled=True,
                        output_action="ANONYMIZE",
                    )
                    for kind in OUTPUT_SECRETS
                ]
            ),
        )
        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description=f"Published for the runtimes (filters {guardrail_revision()})",
        )

        # EncryptionKeyArn and Name are both create-only. A deployment created before the data key
        # was used here keeps its memory as it is (``aws-owned``, the original name): with the same
        # custom name CloudFormation cannot replace it and the whole update rolled back (CR2-01).
        # The data key gets its own name, so an operator who opts in with --allow-data-loss gets a
        # real create-then-delete replacement.
        on_data_key = config.chat_memory_key == "data-key"
        base = f"{config.prefix.replace('-', '_')}_chat"
        self.memory = agentcore.Memory(
            self,
            "ChatMemory",
            memory_name=f"{base}_k" if on_data_key else base,
            description="Short-term chat history per user and session",
            expiration_duration=Duration.days(config.chat_memory_days),
            kms_key=key if on_data_key else None,
        )
        # The memory's service role uses the data key in the role's default policy. The memory refers
        # to the role, never to that policy, so CloudFormation could create the memory before the
        # grant exists and delete the grant before the memory (r3 P3-001, P3-008). The policy names
        # only the key, so waiting for it cannot form a cycle. The L1 resource waits, not the L2:
        # the role is a child of the L2, so the L2 waiting for it would make the role wait for itself.
        memory_role = self.memory.execution_role
        if (
            memory_role is None
        ):  # pragma: no cover - the L2 creates one when none is given
            raise RuntimeError("the chat memory has no execution role")
        memory_grant = memory_role.node.try_find_child("DefaultPolicy")
        if memory_grant is not None:  # only a memory on the data key has one
            self.memory.node.find_child("Memory").node.add_dependency(memory_grant)
        # The role trusts exactly this deployment's memory names (a memory id is "<name>-<suffix>"),
        # both of them: the L2's "<name>*" also matched another deployment whose prefix starts with
        # this one's plus "_chat" (r3 P3-033), and trusting only the new name while a deployment moves
        # to the data-key memory broke the old memory's writes during the update and a rollback (P3-059).
        memory_arn = (
            f"arn:{Aws.PARTITION}:bedrock-agentcore:{region}:{Aws.ACCOUNT_ID}:memory/"
        )
        role_resource = memory_role.node.default_child
        if not isinstance(
            role_resource, iam.CfnRole
        ):  # pragma: no cover - the L2 role is a CfnRole
            raise TypeError("the chat memory role is not a CfnRole")
        role_resource.add_property_override(
            "AssumeRolePolicyDocument.Statement.0.Condition.ArnLike.aws:SourceArn",
            [f"{memory_arn}{base}-*", f"{memory_arn}{base}_k-*"],
        )

        self.gateway: agentcore.Gateway | None = None
        self.gateway_access: iam.Policy | None = None
        if config.web_search:
            # Not a fixed name: the last group of the stack id's uuid is stable for the life of the
            # stack, so the name is deterministic per stack and unique per account, and a gateway left
            # by a deleted stack of the same prefix cannot block a new install. A rolled-back update of
            # the same stack keeps the stack id, so its leftover would still hold the name: the
            # delete-order fix below (r3 P3-001) is what stops leaving one, and deploy.sh stops before
            # an update that would collide with one (P3-002). CloudFormation cannot adopt an existing
            # gateway, so the stack cannot tolerate one.
            gateway_name = Fn.join(
                "-",
                [
                    f"{config.prefix}-web",
                    Fn.select(
                        4, Fn.split("-", Fn.select(2, Fn.split("/", Aws.STACK_ID)))
                    ),
                ],
            )
            # Our own role, with one conditioned trust statement. The CDK L2's generated role has a
            # second, unconditioned bedrock-agentcore.amazonaws.com statement next to the
            # conditioned one, which cancels it: any AgentCore gateway in any account could then be
            # configured with this role ARN and bill its web searches to this account (P2-032).
            gateway_role = iam.Role(
                self,
                "WebSearchGatewayRole",
                description="AgentCore web-search gateway for Agentic Insights Radar",
                assumed_by=iam.ServicePrincipal(
                    "bedrock-agentcore.amazonaws.com",
                    conditions={
                        "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                        # This gateway only (its id is "<name>-<suffix>"): "<prefix>-web*" also matched
                        # another deployment's, such as prefix "<prefix>-webtest" (r3 P3-033).
                        "ArnLike": {
                            "aws:SourceArn": Fn.join(
                                "",
                                [
                                    f"arn:{Aws.PARTITION}:bedrock-agentcore:{region}:{Aws.ACCOUNT_ID}:gateway/",
                                    gateway_name,
                                    "-*",
                                ],
                            )
                        },
                    },
                ),
            )
            # "K": the gateway on the data key. Its key cannot be changed in place, so the gateway
            # created before it (``WebSearchGateway``, ``<prefix>-web``) is replaced rather than
            # updated: CloudFormation creates this one, points the module runtime at its URL, and
            # deletes the old gateway and its target only in the cleanup phase, once nothing refers
            # to them. A new name, because both exist during the update. Nothing is lost: the
            # gateway's only configuration is the target below. A module run that started before the
            # update loses web search when the old gateway goes, which is why the upgrade steps turn
            # on the kill switch first.
            self.gateway = agentcore.Gateway(
                self,
                "WebSearchGatewayK",
                role=gateway_role,
                gateway_name=gateway_name,
                description="Managed web search for module runs (IAM inbound)",
                authorizer_configuration=agentcore.GatewayAuthorizer.using_aws_iam(),
                kms_key=key,
                # No semantic tool search: this gateway has one connector, so the gateway's own
                # tool-search tool would only be one more tool for the model to call (P2-075).
                protocol_configuration=agentcore.McpProtocolConfiguration(
                    instructions="Web search for module runs. One tool: search the public web.",
                    supported_versions=[agentcore.MCPProtocolVersion.MCP_2025_03_26],
                ),
            )
            # What the gateway's role may do, in a policy the gateway and its target wait for (r3
            # P3-001). AgentCore encrypts with the role's key access when it deletes a gateway that is
            # on a customer managed key. In the role's default policy, which the gateway only reached
            # through the role, CloudFormation deleted the grant first, and every gateway DELETE then
            # failed with "not authorized to perform: kms:GenerateDataKey": in an upgrade rollback, in
            # destroy.sh, and when webSearch goes from true to false. A standalone policy, as
            # GuardrailKeyAccess is: it names only the key and the web-search tool, so the dependency
            # cannot become a cycle when a statement naming the gateway is added to the role later.
            self.gateway_access = iam.Policy(
                self,
                "WebSearchGatewayAccess",
                roles=[gateway_role],
                statements=[
                    iam.PolicyStatement(
                        actions=["bedrock-agentcore:InvokeWebSearch"],
                        resources=[
                            f"arn:{Aws.PARTITION}:bedrock-agentcore:{region}:aws:tool/web-search.v1"
                        ],
                    ),
                    # The gateway's own data is encrypted with the data key. Stated here rather than
                    # left to the L2's grant, so the access the delete needs does not depend on it.
                    iam.PolicyStatement(
                        actions=[
                            "kms:Decrypt",
                            "kms:Encrypt",
                            "kms:GenerateDataKey*",
                            "kms:ReEncrypt*",
                        ],
                        resources=[key.key_arn],
                    ),
                ],
            )
            # The L2 also grants the key to the role's default policy (because of ``kms_key``): the
            # role construct covers both, and it is outside the gateway, so this is not a cycle.
            self.gateway.node.add_dependency(self.gateway_access, gateway_role)
            # A new logical ID with the gateway's, so the old target is removed with the old gateway
            # in the cleanup phase, in the old template's order (target first), not replaced first.
            target = agentcore.CfnGatewayTarget(
                self,
                "WebSearchTargetK",
                gateway_identifier=self.gateway.gateway_id,
                name="web",
                description="AgentCore managed web search",
                target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                    mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                        connector=agentcore.CfnGatewayTarget.ConnectorTargetConfigurationProperty(
                            source=agentcore.CfnGatewayTarget.ConnectorSourceProperty(
                                connector_id="web-search"
                            ),
                            configurations=[
                                agentcore.CfnGatewayTarget.ConnectorConfigurationProperty(
                                    name="WebSearch", parameter_values={}
                                )
                            ],
                        )
                    )
                ),
                credential_provider_configurations=[
                    agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                        credential_provider_type="GATEWAY_IAM_ROLE"
                    )
                ],
            )
            target.node.add_dependency(self.gateway, self.gateway_access, gateway_role)

    def _artifact(self, entry: str) -> agentcore.AgentRuntimeArtifact:
        """Both runtimes run the same code zip, uploaded once to the CDK asset bucket."""
        return agentcore.AgentRuntimeArtifact.from_s3(
            s3.Location(
                bucket_name=self._code.s3_bucket_name,
                object_key=self._code.s3_object_key,
            ),
            agentcore.AgentCoreRuntime.PYTHON_3_14,
            [entry],
        )

    def _only_own_code(self, role: iam.IRole) -> None:
        """The L2 grants read on the whole CDK asset bucket; deny every object but this code zip.

        The runtimes read nothing else from S3, so the deny covers every bucket, which also keeps
        other stacks' assets in the shared bootstrap bucket out of reach.
        """
        role.add_to_principal_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["s3:GetObject*"],
                not_resources=[
                    f"arn:{Aws.PARTITION}:s3:::{self._code.s3_bucket_name}/{self._code.s3_object_key}"
                ],
            )
        )

    @property
    def waits_for_memory(self) -> list[Construct]:
        """This construct's other resources, which the stack orders after the memory. Read after the
        runtimes are built, so the SDK-call log group (created with them) is included."""
        return [
            self.guardrail,
            *([self.gateway] if self.gateway else []),
            *([self.gateway_access] if self.gateway_access else []),
            *([self._sdk_call_logs] if self._sdk_call_logs else []),
            *([self._guardrail_key] if self._guardrail_key else []),
        ]

    def runtimes(
        self,
        api_url: str,
        discovery_url: str,
        audiences: list[str],
        data: Data,
        retention: logs.RetentionDays,
    ) -> None:
        self._code = s3_assets.Asset(self, "AgentCode", path=str(AGENT_BUNDLE))
        env = {
            "AIR_API_URL": api_url,
            "AIR_GUARDRAIL_ID": self.guardrail.attr_guardrail_id,
            "AIR_GUARDRAIL_VERSION": self.guardrail_version.attr_version,
        }
        lifecycle = agentcore.LifecycleConfiguration(
            idle_runtime_session_timeout=Duration.minutes(MODULE_IDLE_MINUTES),
            max_lifetime=Duration.hours(8),
        )

        self.module_runtime = agentcore.Runtime(
            self,
            "ModuleRuntime",
            runtime_name=f"{self._config.prefix.replace('-', '_')}_modules",
            description="Runs every analysis module (built-in and custom)",
            agent_runtime_artifact=self._artifact("module_main.py"),
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_iam(),
            environment_variables={
                **env,
                **(
                    {"AIR_GATEWAY_URL": self.gateway.gateway_url or ""}
                    if self.gateway
                    else {}
                ),
            },
            lifecycle_configuration=lifecycle,
        )
        self.chat_runtime = agentcore.Runtime(
            self,
            "ChatRuntime",
            runtime_name=f"{self._config.prefix.replace('-', '_')}_chat",
            description="Answers users' questions with read-only access as the signed-in user",
            agent_runtime_artifact=self._artifact("chat_main.py"),
            # ID tokens carry the app client in `aud` (access tokens use `client_id`).
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_jwt(
                discovery_url, allowed_audience=audiences
            ),
            request_header_configuration=agentcore.RequestHeaderConfiguration(
                allowlisted_headers=["Authorization"]
            ),
            environment_variables={
                **env,
                "AIR_MEMORY_ID": self.memory.memory_id,
                "AIR_CHAT_MODEL_KEY": self._config.chat_model,
            },
            # An idle chat session holds a microVM (billed for memory) and a slot of the account's session
            # quota, which module runs share: it ends after a few idle minutes.
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(CHAT_IDLE_MINUTES),
                max_lifetime=Duration.hours(1),
            ),
        )

        for runtime in (self.module_runtime, self.chat_runtime):
            self._runtime_logs(runtime, data, retention)
            self._only_own_code(runtime.role)
            # A module may be set to any catalog model, so the module runtime gets them all. Chat
            # uses one configured model, so its role gets only that one: a chat-runtime bug or an
            # injected setting cannot reach Opus or Fable (P2-073).
            profiles = (
                MODEL_PROFILES
                if runtime is self.module_runtime
                else (PROFILE_OF[self._config.chat_model],)
            )
            runtime.role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=[
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    resources=model_resources(profiles),
                )
            )
            runtime.role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=["bedrock:ApplyGuardrail"],
                    resources=[self.guardrail.attr_guardrail_arn],
                )
            )
        # The guardrail is encrypted with the data key, so applying it needs the key (P2-019). A
        # policy of its own that the guardrail waits for: on an upgrade the guardrail's key changes in
        # place, and the runtimes may already decrypt with the data key when it does. (The roles'
        # default policies cannot be waited for: they name the guardrail, which would be a cycle.)
        self._guardrail_key = iam.Policy(
            self,
            "GuardrailKeyAccess",
            roles=[self.module_runtime.role, self.chat_runtime.role],
            statements=[
                iam.PolicyStatement(
                    actions=["kms:Decrypt", "kms:DescribeKey"],
                    resources=[self._key.key_arn],
                )
            ],
        )
        self.guardrail.node.add_dependency(self._guardrail_key)

    def _runtime_logs(
        self, runtime: agentcore.Runtime, data: Data, retention: logs.RetentionDays
    ) -> None:
        """Give the log group AgentCore writes for a runtime (chat questions, text from fetched pages) the
        data key and the stack's retention; the service creates it with neither. A CloudFormation log group
        cannot do this: the service can create the group first (it does when a runtime update restarts it),
        and the stack would then fail with AlreadyExists. So: create it if missing, then set both."""
        name = f"/aws/bedrock-agentcore/runtimes/{runtime.agent_runtime_id}-DEFAULT"
        group = {"logGroupName": name}
        arn = f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:{name}"
        policy = cr.AwsCustomResourcePolicy.from_sdk_calls(resources=[arn])
        physical = cr.PhysicalResourceId.of(name)
        if (
            self._sdk_call_logs is None
        ):  # one function serves every AwsCustomResource; its logs as ours
            self._sdk_call_logs = logs.LogGroup(
                self,
                "SdkCallLogs",
                retention=retention,
                encryption_key=data.key,
                removal_policy=data.removal,
            )
        make = cr.AwsSdkCall(
            service="CloudWatchLogs",
            action="createLogGroup",
            parameters=group,
            physical_resource_id=physical,
            ignore_error_codes_matching="ResourceAlreadyExistsException",
        )
        statements = [
            iam.PolicyStatement(actions=["logs:CreateLogGroup"], resources=[arn])
        ]
        if not self._config.retain_data:
            # Delete runs for the old group when a runtime is replaced, after the policy has moved on to the
            # new group's name: allow this runtime's groups by name prefix, not just the current one.
            prefix = f"/aws/bedrock-agentcore/runtimes/{runtime.agent_runtime_name}-*"
            statements.append(
                iam.PolicyStatement(
                    actions=["logs:DeleteLogGroup"],
                    resources=[
                        f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:{prefix}"
                    ],
                )
            )
        create = cr.AwsCustomResource(
            runtime,
            "LogGroup",
            on_create=make,
            on_update=make,  # a replaced runtime has a new ID, so a new group name
            # Kept with the data unless retainData=false, like the stack's own log groups.
            on_delete=None
            if self._config.retain_data
            else cr.AwsSdkCall(
                service="CloudWatchLogs",
                action="deleteLogGroup",
                parameters=group,
                ignore_error_codes_matching="ResourceNotFoundException",
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(statements),
            install_latest_aws_sdk=False,
            log_group=self._sdk_call_logs,
        )
        # One call at a time: CloudWatch Logs refuses a second change to a group while one is in progress.
        previous: Construct = create
        for cid, action, params in (
            (
                "LogRetention",
                "putRetentionPolicy",
                {**group, "retentionInDays": self._config.agent_log_retention_days},
            ),
            ("LogKey", "associateKmsKey", {**group, "kmsKeyId": data.key.key_arn}),
        ):
            # AssociateKmsKey also checks that the caller can describe the key.
            call_policy = (
                cr.AwsCustomResourcePolicy.from_statements(
                    [
                        iam.PolicyStatement(
                            actions=["logs:AssociateKmsKey"], resources=[arn]
                        ),
                        iam.PolicyStatement(
                            actions=["kms:DescribeKey"], resources=[data.key.key_arn]
                        ),
                    ]
                )
                if action == "associateKmsKey"
                else policy
            )
            call = cr.AwsSdkCall(
                service="CloudWatchLogs",
                action=action,
                parameters=params,
                physical_resource_id=physical,
            )
            step = cr.AwsCustomResource(
                runtime,
                cid,
                on_create=call,
                on_update=call,
                policy=call_policy,
                install_latest_aws_sdk=False,
                log_group=self._sdk_call_logs,
            )
            step.node.add_dependency(previous)
            previous = step

    def use_agent_urls(self, tools_url: str, usage_url: str) -> None:
        """Point each runtime at its function URL (created after the runtimes, which they reference)."""
        for runtime, name, url in (
            (self.module_runtime, "AIR_AGENT_TOOLS_URL", tools_url),
            (self.chat_runtime, "AIR_CHAT_USAGE_URL", usage_url),
        ):
            cfn = cast(agentcore.CfnRuntime, runtime.node.default_child)
            cfn.add_property_override(f"EnvironmentVariables.{name}", url)

    def grant(
        self,
        state_machine: sfn.StateMachine,
        agent_tools: lambda_.IFunction,
        chat_usage: lambda_.IFunction,
    ) -> None:
        """Access to the agent function URLs and the run workflow, in standalone policies.

        The workflow references the module runtime, so the runtime must not depend on anything that
        references the workflow. A standalone ``iam.Policy`` attaches to the role without the
        runtime depending on it, which keeps the template free of cycles.
        """
        region, partition = Aws.REGION, Aws.PARTITION
        iam.Policy(
            self,
            "ModuleRuntimeAccess",
            roles=[self.module_runtime.role],
            statements=[
                *function_url_access(agent_tools),
                iam.PolicyStatement(
                    actions=[
                        "states:SendTaskSuccess",
                        "states:SendTaskFailure",
                        "states:SendTaskHeartbeat",
                    ],
                    resources=[state_machine.state_machine_arn],
                ),
                iam.PolicyStatement(
                    actions=CODE_INTERPRETER_ACTIONS,
                    resources=[
                        f"arn:{partition}:bedrock-agentcore:{region}:aws:code-interpreter/*"
                    ],
                ),
                iam.PolicyStatement(
                    actions=BROWSER_ACTIONS,
                    resources=[
                        f"arn:{partition}:bedrock-agentcore:{region}:aws:browser/*"
                    ],
                ),
            ],
        )
        if self.gateway:
            self.gateway.grant_invoke(self.module_runtime.role)
        iam.Policy(
            self,
            "ChatRuntimeAccess",
            roles=[self.chat_runtime.role],
            statements=function_url_access(chat_usage),
        )
        self.memory.grant_read_short_term_memory(self.chat_runtime.role)
        self.memory.grant_write(self.chat_runtime.role)
        # When the guardrail blocks a turn, Strands replaces the stored message with the redacted one, which
        # deletes the old event: without this the user saw "Something went wrong (SessionException)" after the
        # guardrail's own reply (a deployment, 2026-11-03).
        self.memory.grant_delete_short_term_memory(self.chat_runtime.role)
