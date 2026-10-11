"""``HviAgentStack`` — Stack 5 of the HVI Phase 2 CDK app.

Reproduces the **web channel** of ``scripts/40-agentcore.sh``,
``41-agent-model.sh``, ``32-crm-lambdas.sh``, ``34-cognito.sh``,
``36-web-cloudfront.sh``, ``54-prompt-cache.sh``, ``35-turn.sh`` and
``72-media-deploy.sh`` as a single CDK stack:

* the **agent** compute — a container ``lambda.DockerImageFunction`` on the
  default ``hvi:agentCompute=lambda`` path (AgentCore Runtime has no CloudFormation
  coverage), or a ``bedrock-agentcore-control`` custom resource under
  ``hvi:agentCompute=agentcore-cr``;
* the three non-VPC **CRM/email Lambdas** (``hvi-crm-writeback``/``hvi-crm-sync``/
  ``hvi-send-confirmation``) with their exact per-function timeout/memory/env;
* **Cognito** — the ``hvi-web-pool`` user pool, hosted-UI domain, and the public
  ``hvi-web-spa`` SPA client (no secret, code + PKCE);
* the **prompt-cache** custom resource (``hvi:tts=omnivoice`` only);
* the flagged **web-media edge** behind ``hvi:enableWebMedia`` (default off): the
  ``hvi-turn`` coturn relay, the ``hvi-web`` media box + its SG and instance role
  (which reads/writes the prompt cache), and the CloudFront distribution.

Everything upstream is consumed through **typed props** carrying construct
objects (``HviDataStack`` roles/tables/archive bucket, ``HviKnowledgeStack``
OpenSearch + graph fn, ``HviInferenceStack`` artifacts bucket + endpoint names) —
never name re-lookups, never hard-coded ARNs.

Hard rules honoured (``DESIGN`` "Stack 5 — Agent"):

* the **agent** env never carries ``STT_ENDPOINT``/``OMNIVOICE_ENDPOINT`` — those
  belong on the ``hvi-web`` box, which is the real consumer (MEDIUM-1);
* the Phase-3 ``hvi-asterisk-role`` is **never** referenced — the prompt-cache
  reader/writer is the dedicated ``hvi-web-role`` (MEDIUM-2);
* the agent container is referenced from ECR (``from_ecr``), never built — so
  synthesis stays Docker-free.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cf_origins,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    custom_resources as cr,
)
from constructs import Construct

from stacks.assets import vendored_asset_code
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack
from stacks.knowledge_stack import HviKnowledgeStack
from stacks.naming import Naming

# infra/cdk/stacks/agent_stack.py -> parents: [stacks, cdk, infra, <repo root>]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CDK_ROOT = Path(__file__).resolve().parents[1]
_SEED_DIR = _REPO_ROOT / "seed"
_SEED_RESOURCES = _CDK_ROOT / "seed_resources"
_LAMBDAS_DIR = _REPO_ROOT / "lambdas"
_COMMON_DIR = _REPO_ROOT / "common"
_TOOLS_DIR = _REPO_ROOT / "tools"

# config/env.sh defaults; overridable via context (fail-loud on empty SES).
_SF_LOGIN_URL_DEFAULT = "https://login.salesforce.com"
_SES_FROM_EMAIL_DEFAULT = "sender@example.com"

# scripts/35-turn.sh ports + relay window + the referenced SecureString.
_TURN_PORT = 3478
_TURN_TLS_PORT = 5349
_TURN_RELAY_MIN = 49160
_TURN_RELAY_MAX = 49200
_TURN_SECRET_PARAM = "/hvi/turn/shared-secret"
# scripts/36-web-cloudfront.sh: bot_web binds 0.0.0.0:8095.
_WEB_PORT = 8095
# arm64 Ubuntu 24.04 — the media host runs aarch64 wheels (scripts/72).
_UBUNTU_ARM64_SSM = (
    "/aws/service/canonical/ubuntu/server/24.04/stable/current/"
    "arm64/hvm/ebs-gp3/ami-id"
)
_TURN_INSTANCE_TYPE = "t4g.small"
_WEB_INSTANCE_TYPE = "t4g.small"

_PROJECT_TAG = ("Project", "health-voice-intake")


def _requests_package_dir() -> str:
    """Absolute path to the ``requests`` package in the synth interpreter.

    ``requests`` is pure-Python, so copying the installed package into a Lambda
    asset reproduces ``scripts/32``'s ``pip install --target`` vendoring with no
    Docker and no network (a local filesystem copy).
    """
    spec = importlib.util.find_spec("requests")
    if spec is None or not spec.origin:
        raise RuntimeError(
            "requests must be importable in the synth venv to vendor it into "
            "the CRM/email Lambda assets"
        )
    return str(Path(spec.origin).parent)


class HviAgentStack(Stack):
    """Agent compute + CRM/email + Cognito + prompt cache + (gated) web media."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hvi: HviContext,
        data: HviDataStack,
        knowledge: HviKnowledgeStack,
        inference: HviInferenceStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hvi = hvi
        self.data = data
        self.knowledge = knowledge
        self.inference = inference
        self.naming = Naming(account=hvi.account, region=hvi.region)

        # Typed handles (None until/unless created).
        self.agent_function: lambda_.DockerImageFunction | None = None
        self.agentcore_runtime: cdk.CustomResource | None = None
        self.prompt_cache: cdk.CustomResource | None = None
        self.turn_instance: ec2.Instance | None = None
        self.web_instance: ec2.Instance | None = None
        self.web_distribution: cloudfront.Distribution | None = None

        self._agent_env = self._build_agent_env()

        self._build_cognito()
        self._build_crm_lambdas()
        self._build_agent_compute()
        if hvi.tts == "omnivoice":
            self._build_prompt_cache()
        if hvi.enable_web_media:
            self._build_web_media_edge()

    # ----------------------------------------------------------- agent env
    def _build_agent_env(self) -> dict:
        """The full ``scripts/40`` ENV_JSON map — NO speech endpoints (MEDIUM-1)."""
        n = self.naming
        return {
            "DATA_REGION": self.hvi.region,
            "BEDROCK_MODEL_ID": self.hvi.bedrock_model_id,
            "OPENSEARCH_ENDPOINT": self.knowledge.opensearch_domain.domain_endpoint,
            "GRAPH_FN": self.knowledge.graph_fn.function_name,
            "CRM_FN": n.crm_writeback_fn,
            "EMAIL_FN": n.send_confirmation_fn,
            "TBL_CALL_STATE": n.table_call_state,
            "TBL_CALL_SESSION": n.table_call_session,
            "TBL_CRM": n.table_crm_cache,
            "TBL_FORM_SCHEMA": n.table_form_schema,
            "TBL_RESPONSES": n.table_intake_responses,
            # scripts/40 reads back the deployed value; absent -> resume ON (0).
            "HVI_ALWAYS_FRESH": "0",
            # Non-empty, or archiving silently disables (scripts/40 fail-loud note).
            "CALL_ARCHIVE_BUCKET": self.data.archive_bucket_name,
            "CALL_ARCHIVE_PREFIX": "calls",
            "AUDIO_BUCKET": n.audio_bucket,
        }

    # -------------------------------------------------------------- cognito
    def _build_cognito(self) -> None:
        n = self.naming

        self.cognito_pool = cognito.UserPool(
            self,
            "WebPool",
            user_pool_name=n.cognito_pool,
            # The verified phone_number claim is the whole point: the media host
            # binds the caller to THEIR record from this claim, not client input.
            sign_in_aliases=cognito.SignInAliases(phone=True),
            auto_verify=cognito.AutoVerifiedAttrs(phone=True),
            standard_attributes=cognito.StandardAttributes(
                phone_number=cognito.StandardAttribute(required=True, mutable=True)
            ),
            # Holds real identity data in the production account — never dropped.
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.cognito_pool.add_domain(
            "WebPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=n.cognito_domain
            ),
        )
        # Public SPA client: NO secret, authorization-code + PKCE, scopes
        # openid/phone/profile. A browser cannot keep a secret.
        self.cognito_spa_client = self.cognito_pool.add_client(
            "WebSpaClient",
            user_pool_client_name=n.cognito_spa_client,
            generate_secret=False,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.PHONE,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[self.hvi.web_callback_url],
                logout_urls=[self.hvi.web_callback_url],
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
        )

    # ---------------------------------------------------------- crm lambdas
    def _sf_env(self) -> dict:
        """Shared Salesforce config (scripts/32) — no SF_CLIENT_ID/SECRET."""
        sf_login_url = str(
            self.node.try_get_context("hvi:sfLoginUrl") or _SF_LOGIN_URL_DEFAULT
        )
        return {
            "SF_LOGIN_URL": sf_login_url,
            "SF_SECRET_NAME": "hvi/salesforce",
            "SF_MOCK": "false",
            "SF_API_VERSION": "67.0",
            "DATA_REGION": self.hvi.region,
            "HVI_SF_VIA_GATEWAY": "0",
            "HVI_GATEWAY_NAME": "hvi-spike-gw",
        }

    def _crm_lambda(
        self,
        logical: str,
        *,
        function_name: str,
        source_dir: Path,
        common_modules: tuple[str, ...],
        environment: dict,
        timeout_seconds: int,
        memory_size: int,
    ) -> lambda_.Function:
        extra_files: dict[str, str] = {
            f"{mod}.py": str(_COMMON_DIR / f"{mod}.py") for mod in common_modules
        }
        extra_files["requests"] = _requests_package_dir()
        return lambda_.Function(
            self,
            logical,
            function_name=function_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.lambda_handler",
            code=vendored_asset_code(source_dir, extra_files),
            timeout=Duration.seconds(timeout_seconds),
            memory_size=memory_size,
            role=self.data.lambda_role,  # scripts/32: hvi-lambda-role
            # NOT in the VPC: Salesforce/SES are public (scripts/32).
            environment=environment,
        )

    def _build_crm_lambdas(self) -> None:
        n = self.naming
        sf_env = self._sf_env()

        self.crm_writeback_fn = self._crm_lambda(
            "CrmWritebackFn",
            function_name=n.crm_writeback_fn,
            source_dir=_LAMBDAS_DIR / "crm_writeback",
            common_modules=(
                "hvi_state",
                "hvi_salesforce",
                "hvi_salesforce_gateway",
                "hvi_recording",
            ),
            environment=dict(sf_env),
            timeout_seconds=120,
            memory_size=512,
        )

        self.crm_sync_fn = self._crm_lambda(
            "CrmSyncFn",
            function_name=n.crm_sync_fn,
            source_dir=_LAMBDAS_DIR / "crm_sync",
            common_modules=(
                "hvi_state",
                "hvi_salesforce",
                "hvi_salesforce_gateway",
            ),
            environment={
                **sf_env,
                "FORM_ID": "arogya-sanjeevani-v1",
                "SLA_HOURS_FROM_PURCHASE": "48",
                "INSURER_NAME": "Meridian Health Cover",
            },
            timeout_seconds=300,
            memory_size=512,
        )

        # SES sender: synth FAILS if SES_FROM_EMAIL unset (scripts/32 refuses to
        # deploy a sender that cannot deliver). Default from config/env.sh.
        ses_from_email = self.node.try_get_context("hvi:sesFromEmail")
        if ses_from_email is None:
            ses_from_email = _SES_FROM_EMAIL_DEFAULT
        ses_from_email = str(ses_from_email)
        if not ses_from_email.strip():
            raise ValueError(
                "hvi:sesFromEmail must not be empty — scripts/32 refuses to "
                "deploy a confirmation sender that cannot deliver"
            )
        confirm_env = {
            "DATA_REGION": self.hvi.region,
            "SES_REGION": self.hvi.region,
            "SES_FROM_EMAIL": ses_from_email,
        }
        correction_reply_to = self.node.try_get_context("hvi:correctionReplyTo")
        if correction_reply_to:
            confirm_env["CORRECTION_REPLY_TO"] = str(correction_reply_to)

        self.send_confirmation_fn = self._crm_lambda(
            "SendConfirmationFn",
            function_name=n.send_confirmation_fn,
            source_dir=_LAMBDAS_DIR / "send_confirmation",
            common_modules=("hvi_state",),
            environment=confirm_env,
            timeout_seconds=60,
            memory_size=256,
        )

    # --------------------------------------------------------- agent compute
    def _agent_ecr_image_uri(self) -> tuple[ecr.IRepository, str]:
        repo = ecr.Repository.from_repository_name(
            self, "AgentEcrRepo", self.naming.agent_ecr_repo
        )
        return repo, f"{repo.repository_uri}:latest"

    def _build_agent_compute(self) -> None:
        if self.hvi.agent_compute == "lambda":
            self._build_agent_lambda()
        else:
            self._build_agentcore_runtime_cr()

    def _build_agent_lambda(self) -> None:
        """Default path: the agent container on Lambda (arm64), from ECR.

        The image is referenced from ECR (``from_ecr``) rather than built, so
        synthesis never invokes Docker. ``scripts/40`` is the tool that builds
        and pushes the arm64 image (now carrying the Lambda Web Adapter delta
        this feature adds to ``agent/Dockerfile``).
        """
        repo, _ = self._agent_ecr_image_uri()
        self.agent_function = lambda_.DockerImageFunction(
            self,
            "AgentFn",
            function_name=self.naming.agent_fn,
            code=lambda_.DockerImageCode.from_ecr(repo, tag_or_digest="latest"),
            architecture=lambda_.Architecture.ARM_64,
            memory_size=1024,
            timeout=Duration.seconds(120),
            # hvi-agentcore-role: on this path Data gives it the lambda trust +
            # the /aws/lambda/* logs capability (MEDIUM-1).
            role=self.data.agentcore_role,
            environment=self._agent_env,
        )

    def _build_agentcore_runtime_cr(self) -> None:
        """``agentcore-cr`` path: no agent Lambda; a control-plane CR stands in."""
        repo, image_uri = self._agent_ecr_image_uri()

        fn = lambda_.Function(
            self,
            "AgentCoreRuntimeFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(_SEED_RESOURCES / "agentcore_runtime"),
            timeout=Duration.minutes(15),
            memory_size=256,
            environment={"DATA_REGION": self.hvi.region},
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ManageAgentCoreRuntime",
                actions=[
                    "bedrock-agentcore:CreateAgentRuntime",
                    "bedrock-agentcore:UpdateAgentRuntime",
                    "bedrock-agentcore:DeleteAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime",
                    "bedrock-agentcore:ListAgentRuntimes",
                ],
                resources=["*"],
            )
        )
        # The runtime assumes hvi-agentcore-role, so the CR must be able to pass it.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PassAgentCoreRole",
                actions=["iam:PassRole"],
                resources=[self.data.agentcore_role.role_arn],
            )
        )
        repo.grant_pull(fn)

        provider = cr.Provider(
            self, "AgentCoreRuntimeProvider", on_event_handler=fn
        )
        self.agentcore_runtime = cdk.CustomResource(
            self,
            "AgentCoreRuntime",
            service_token=provider.service_token,
            resource_type="Custom::HviAgentCoreRuntime",
            properties={
                "Region": self.hvi.region,
                "RuntimeName": self.naming.agent_fn,
                "ImageUri": image_uri,
                "RoleArn": self.data.agentcore_role.role_arn,
                "EnvironmentVariables": self._agent_env,
            },
        )

    # ---------------------------------------------------------- prompt cache
    def _build_prompt_cache(self) -> None:
        """Omnivoice-only CR: reuse tools/build_prompt_cache.py (scripts/54)."""
        n = self.naming
        bucket = self.inference.artifacts_bucket
        assert bucket is not None, "artifacts bucket must exist under omnivoice"

        fn = lambda_.Function(
            self,
            "PromptCacheFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(
                _SEED_RESOURCES / "prompt_cache",
                extra_files={
                    "build_prompt_cache.py": str(
                        _TOOLS_DIR / "build_prompt_cache.py"
                    ),
                    "hvi_prompts.py": str(_COMMON_DIR / "hvi_prompts.py"),
                    "hvi_prompt_cache.py": str(
                        _COMMON_DIR / "hvi_prompt_cache.py"
                    ),
                    "hvi_state.py": str(_COMMON_DIR / "hvi_state.py"),
                    "health-kg.json": str(_SEED_DIR / "health-kg.json"),
                },
            ),
            timeout=Duration.minutes(15),
            memory_size=512,
            environment={
                "PROMPT_CACHE_BUCKET": bucket.bucket_name,
                "OMNIVOICE_ENDPOINT": self.inference.omnivoice_endpoint_name,
                "FORM_ID": "arogya-sanjeevani-v1",
                "DATA_REGION": self.hvi.region,
            },
        )
        # Least-privilege: write the cache, invoke OmniVoice, read the form schema
        # + CRM projection the builder walks.
        bucket.grant_read_write(fn, "prompt-cache/*")
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PromptCacheListBucket",
                actions=["s3:ListBucket"],
                resources=[bucket.bucket_arn],
                conditions={"StringLike": {"s3:prefix": ["prompt-cache/*"]}},
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PromptCacheInvokeOmniVoice",
                actions=["sagemaker:InvokeEndpoint"],
                resources=[
                    f"arn:aws:sagemaker:{self.hvi.region}:{self.hvi.account}:"
                    f"endpoint/{self.naming.omnivoice_endpoint}"
                ],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PromptCacheReadTables",
                actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
                resources=[
                    f"arn:aws:dynamodb:*:{self.hvi.account}:table/hvi-*",
                    f"arn:aws:dynamodb:*:{self.hvi.account}:table/hvi-*/index/*",
                ],
            )
        )

        provider = cr.Provider(self, "PromptCacheProvider", on_event_handler=fn)
        resource = cdk.CustomResource(
            self,
            "PromptCacheResource",
            service_token=provider.service_token,
            resource_type="Custom::HviPromptCache",
            properties={
                "PromptCacheBucket": bucket.bucket_name,
                "OmniVoiceEndpoint": self.inference.omnivoice_endpoint_name,
                "FormId": "arogya-sanjeevani-v1",
                "DataRegion": self.hvi.region,
                "SeedHash": self._hash_file(_SEED_DIR / "health-kg.json"),
            },
        )
        # Sequence after the OmniVoice endpoint (InService) and the form-schema
        # seed. Referencing the Inference-owned artifacts bucket promotes to a
        # stack-level dependency (all of Inference — including the OmniVoice
        # endpoint custom resource — completes before any of Agent); the
        # form-schema seed lives in Data and is depended on directly.
        resource.node.add_dependency(bucket)
        resource.node.add_dependency(self.data.form_schema_seed)
        self.prompt_cache = resource

    @staticmethod
    def _hash_file(path: Path) -> str:
        import hashlib

        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    # ------------------------------------------------------ web media edge
    def _build_web_media_edge(self) -> None:
        """hvi:enableWebMedia=true: coturn relay + hvi-web box + CloudFront."""
        self._build_turn_relay()
        self._build_web_instance()
        self._build_web_cloudfront()

    def _build_turn_relay(self) -> None:
        n = self.naming
        region = self.hvi.region

        turn_sg = ec2.SecurityGroup(
            self,
            "TurnSg",
            vpc=self.data.vpc,
            security_group_name=n.sg_turn,
            description="coturn TURN relay: STUN/TURN + relay media range",
            allow_all_outbound=True,
        )
        # A public TURN relay is a rendezvous point; the open ports are safe only
        # because coturn requires a short-lived HMAC credential (scripts/35).
        turn_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.udp(_TURN_PORT), "stun-turn"
        )
        turn_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(_TURN_PORT), "stun-turn-tcp"
        )
        turn_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.udp_range(_TURN_RELAY_MIN, _TURN_RELAY_MAX),
            "turn-relay-media",
        )
        turn_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(_TURN_TLS_PORT), "turns-tls"
        )

        turn_role = iam.Role(
            self,
            "TurnRole",
            role_name=n.turn_role,
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="coturn TURN relay: SSM management, no SSH",
        )
        turn_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )

        ubuntu_arm64 = ec2.MachineImage.from_ssm_parameter(
            _UBUNTU_ARM64_SSM, os=ec2.OperatingSystemType.LINUX
        )
        turn = ec2.Instance(
            self,
            "TurnInstance",
            vpc=self.data.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=[self.data.public_subnet]),
            instance_type=ec2.InstanceType(_TURN_INSTANCE_TYPE),
            machine_image=ubuntu_arm64,
            security_group=turn_sg,
            role=turn_role,
            require_imdsv2=True,
        )
        cdk.Tags.of(turn).add("Name", n.turn_instance)
        cdk.Tags.of(turn).add(*_PROJECT_TAG)

        # The relay reads the referenced SecureString at boot and advertises its
        # own public IP as the relay address (coturn use-auth-secret model).
        turn.user_data.add_commands(
            "set -euxo pipefail",
            "export DEBIAN_FRONTEND=noninteractive",
            "apt-get update -y",
            "apt-get install -y coturn awscli jq",
            "PUB=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)",
            "SECRET=$(aws ssm get-parameter --region "
            f"{region} --name {_TURN_SECRET_PARAM} "
            "--with-decryption --query 'Parameter.Value' --output text)",
            "cat > /etc/turnserver.conf <<CONF",
            f"listening-port={_TURN_PORT}",
            f"tls-listening-port={_TURN_TLS_PORT}",
            f"min-port={_TURN_RELAY_MIN}",
            f"max-port={_TURN_RELAY_MAX}",
            "fingerprint",
            "use-auth-secret",
            "static-auth-secret=${SECRET}",
            "realm=hvi.turn",
            "external-ip=${PUB}",
            "relay-ip=${PUB}",
            "no-tcp-relay",
            "no-multicast-peers",
            "no-cli",
            "CONF",
            "unset SECRET",
            "echo 'TURNSERVER_ENABLED=1' > /etc/default/coturn",
            "systemctl enable coturn",
            "systemctl restart coturn",
        )
        # Read the shared secret the operator generated out-of-band (never created
        # here with a literal) — scoped to that one parameter.
        turn_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadTurnSecret",
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{region}:{self.hvi.account}:"
                    f"parameter{_TURN_SECRET_PARAM}"
                ],
            )
        )

        eip = ec2.CfnEIP(
            self,
            "TurnEip",
            domain="vpc",
            instance_id=turn.instance_id,
            tags=[cdk.CfnTag(key="Name", value=n.turn_instance)],
        )
        self.turn_instance = turn
        self.turn_eip = eip

    def _build_web_instance(self) -> None:
        n = self.naming
        hvi = self.hvi

        prefix_list_id = hvi.cloudfront_origin_prefix_list_id
        if not prefix_list_id:
            raise ValueError(
                "hvi:enableWebMedia=true requires hvi:cloudfrontOriginPrefixListId "
                "(the com.amazonaws.global.cloudfront.origin-facing managed "
                "prefix-list id) so hvi-web-sg admits only CloudFront on 8095"
            )

        # hvi-web-sg: a single ingress — TCP 8095 from the CloudFront origin-
        # facing prefix list ONLY (scripts/36). /api/offer is also Cognito-gated.
        web_sg = ec2.SecurityGroup(
            self,
            "WebSg",
            vpc=self.data.vpc,
            security_group_name=n.sg_web,
            description="CloudFront origin to bot_web WebRTC signalling",
            allow_all_outbound=True,
        )
        web_sg.add_ingress_rule(
            ec2.Peer.prefix_list(prefix_list_id),
            ec2.Port.tcp(_WEB_PORT),
            "cloudfront-web",
        )

        # hvi-web-role: SSM core + the prompt-cache reader/writer (standing in for
        # the Phase-3 hvi-asterisk-role) + agent invoke + speech-endpoint invoke +
        # archive write, with the Polly Deny retained (scripts/72).
        web_role = iam.Role(
            self,
            "WebRole",
            role_name=n.web_role,
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="hvi-web media box: SSM + prompt cache + agent/STT/TTS",
        )
        web_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        self._attach_web_role_policies(web_role)
        self.web_role = web_role

        ubuntu_arm64 = ec2.MachineImage.from_ssm_parameter(
            _UBUNTU_ARM64_SSM, os=ec2.OperatingSystemType.LINUX
        )
        web = ec2.Instance(
            self,
            "WebInstance",
            vpc=self.data.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=[self.data.public_subnet]),
            instance_type=ec2.InstanceType(_WEB_INSTANCE_TYPE),
            machine_image=ubuntu_arm64,
            security_group=web_sg,
            role=web_role,
            require_imdsv2=True,
        )
        cdk.Tags.of(web).add("Name", n.web_instance)
        cdk.Tags.of(web).add(*_PROJECT_TAG)

        self._write_web_env_files(web)
        self.web_sg = web_sg
        self.web_instance = web

    def _attach_web_role_policies(self, role: iam.Role) -> None:
        n = self.naming
        acc = self.hvi.account
        region = self.hvi.region

        # The THREE scripts/54 statements, retargeted from hvi-asterisk-role to
        # hvi-web-role (NIT-1). Only under omnivoice is there a cache to read.
        if self.hvi.tts == "omnivoice":
            cache_arn = f"arn:aws:s3:::{n.artifacts_bucket}"
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="ReadPromptCache",
                    actions=["s3:GetObject"],
                    resources=[f"{cache_arn}/prompt-cache/*"],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="ListPromptCache",
                    actions=["s3:ListBucket"],
                    resources=[cache_arn],
                    conditions={
                        "StringLike": {"s3:prefix": ["prompt-cache/*"]}
                    },
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="WriteBackOnMiss",
                    actions=["s3:PutObject"],
                    resources=[f"{cache_arn}/prompt-cache/*"],
                )
            )

        # Agent invoke target depends on the compute choice.
        if self.hvi.agent_compute == "lambda":
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="InvokeOrchestrator",
                    actions=["lambda:InvokeFunction"],
                    resources=[
                        f"arn:aws:lambda:*:{acc}:function:{n.agent_fn}"
                    ],
                )
            )
        else:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="InvokeOrchestrator",
                    actions=["bedrock-agentcore:InvokeAgentRuntime"],
                    resources=["*"],
                )
            )

        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeSpeechEndpoints",
                actions=["sagemaker:InvokeEndpoint"],
                resources=[f"arn:aws:sagemaker:{region}:{acc}:endpoint/hvi-*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="StoreCallRecording",
                actions=["s3:PutObject"],
                resources=[f"arn:aws:s3:::hvi-archive-{acc}-*/*"],
            )
        )
        # Retain the Polly Deny so a stray Polly call cannot ship even under a
        # misconfiguration (scripts/72).
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DenyOtherTTSVendors",
                effect=iam.Effect.DENY,
                actions=["polly:*"],
                resources=["*"],
            )
        )

    def _write_web_env_files(self, web: ec2.Instance) -> None:
        """Write /etc/hvi/web.env + /etc/hvi/media.env via user-data (scripts/36/72).

        The static keys land as literal fragments of the rendered UserData, so the
        synth tests can assert the contract; the dynamic values (Cognito ids, the
        relay EIP) interpolate as cross-stack/same-stack tokens.
        """
        n = self.naming
        region = self.hvi.region
        eip = self.turn_eip.ref  # the relay's public IP (TURN relay address)

        web_env = [
            "HVI_WEB_AUTH=cognito",
            f"HVI_COGNITO_REGION={region}",
            f"HVI_COGNITO_USER_POOL_ID={self.cognito_pool.user_pool_id}",
            f"HVI_COGNITO_APP_CLIENT_ID="
            f"{self.cognito_spa_client.user_pool_client_id}",
            # STUN (no credential) + TURN (per-session credential minted at
            # runtime from the shared secret) — only the relay URL is static.
            f"HVI_WEB_ICE_SERVERS=stun:{eip}:{_TURN_PORT},turn:{eip}:{_TURN_PORT}",
            f"HVI_WEB_PORT={_WEB_PORT}",
        ]

        media_env = [
            f"DATA_REGION={region}",
            f"AWS_REGION={region}",
            f"AWS_DEFAULT_REGION={region}",
            # Phase-1 STT is Whisper-only; the real consumer is common/hvi_stt.py.
            f"STT_ENDPOINT={self.inference.whisper_endpoint_name}",
            "STT_ENGINE=whisper",
            f"HVI_TTS={self.hvi.tts}",
            f"CALL_ARCHIVE_BUCKET={self.data.archive_bucket_name}",
            "CALL_ARCHIVE_PREFIX=calls",
            "HVI_RECORD_CALLS=1",
            "HVI_LOG_LEVEL=INFO",
            "VAD_MIN_VOLUME=0.35",
            "VAD_CONFIDENCE=0.6",
            "VAD_START_SECS=0.2",
            "VAD_STOP_SECS=1.5",
        ]
        # The agent invoke target (scripts/72 AGENT_RUNTIME_ARN); on the Lambda
        # path it is the agent function name (media host switches to InvokeFunction).
        if self.hvi.agent_compute == "lambda" and self.agent_function is not None:
            media_env.append(f"AGENT_FUNCTION_NAME={self.agent_function.function_name}")
        else:
            media_env.append(f"AGENT_RUNTIME_NAME={n.agent_fn}")

        # OmniVoice-only speech wiring (omitted under polly). The endpoint and
        # bucket NAMES are deterministic (naming.py), so the env file carries the
        # literal names scripts/72 writes — the dependency on the Inference stack
        # is carried by the prompt-cache grant, not by these strings.
        if self.hvi.tts == "omnivoice":
            media_env.append(
                f"OMNIVOICE_ENDPOINT={self.inference.omnivoice_endpoint_name}"
            )
            media_env.append(f"PROMPT_CACHE_BUCKET={n.artifacts_bucket}")
        # AUDIOSOCKET_* and PROMPT_CACHE_VOICE are DELIBERATELY unset (scripts/72):
        # AudioSocket is the telephony leg; the cache voice default is authoritative.

        web.user_data.add_commands("mkdir -p /etc/hvi")
        for line in web_env:
            web.user_data.add_commands(
                f"printf '%s\\n' '{line}' >> /etc/hvi/web.env"
            )
        for line in media_env:
            web.user_data.add_commands(
                f"printf '%s\\n' '{line}' >> /etc/hvi/media.env"
            )
        web.user_data.add_commands("chmod 600 /etc/hvi/web.env /etc/hvi/media.env")

    def _build_web_cloudfront(self) -> None:
        """CloudFront fronting the hvi-web origin on 8095 (scripts/36)."""
        assert self.web_instance is not None

        origin = cf_origins.HttpOrigin(
            self.web_instance.instance_public_dns_name,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            http_port=_WEB_PORT,
        )
        self.web_distribution = cloudfront.Distribution(
            self,
            "WebDistribution",
            comment="hvi-web",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=(
                    cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS
                ),
                # All viewer methods so POST /api/offer reaches the origin.
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                # CachingDisabled on the DEFAULT behaviour (everything) so
                # /api/offer and the RTVI responses are never cached (NIT-7).
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                # AllViewer so the POST body + headers reach the origin.
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
            ),
        )
