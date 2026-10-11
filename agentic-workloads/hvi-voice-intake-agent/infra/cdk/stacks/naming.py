"""Deterministic resource naming for the HVI CDK app.

Resource names are *load-bearing* in this deploy: ``config/env.sh`` and the IAM
policies in ``scripts/15-iam.sh`` match resources by literal name (for example
``scripts/15-iam.sh`` grants the Lambda role ``s3:*`` on
``hvi-archive-<acct>-*``, so the archive bucket **must** be named
``hvi-archive-<acct>-<region>`` for that policy to apply). The CDK therefore
passes explicit names to every construct rather than letting CDK auto-naming
invent them.

Every helper here reproduces a name exactly as the reference shell scripts build
it (``${PREFIX}-...`` where ``PREFIX=hvi``). Account- and region-scoped names are
parameterised so the single source of truth for those two values stays in
``context.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Resource-name prefix, matching ``PREFIX=hvi`` in ``config/env.sh``.
PREFIX = "hvi"


@dataclass(frozen=True)
class Naming:
    """Produces every ``hvi-*`` resource name for a given account and region.

    All names are returned as plain strings so they can be handed straight to a
    construct's ``*_name`` prop. Account/region are injected (not read from the
    environment) so synthesis stays deterministic and offline.
    """

    account: str
    region: str

    # ---- DynamoDB tables (config/env.sh, scripts/10-dynamodb.sh) ----------
    @property
    def table_call_state(self) -> str:
        return f"{PREFIX}-call-state"

    @property
    def table_call_session(self) -> str:
        return f"{PREFIX}-call-session"

    @property
    def table_crm_cache(self) -> str:
        return f"{PREFIX}-crm-cache"

    @property
    def table_form_schema(self) -> str:
        return f"{PREFIX}-form-schema"

    @property
    def table_intake_responses(self) -> str:
        return f"{PREFIX}-intake-responses"

    @property
    def table_consent_ledger(self) -> str:
        return f"{PREFIX}-consent-ledger"

    @property
    def table_call_attempts(self) -> str:
        return f"{PREFIX}-call-attempts"

    @property
    def table_gate_audit(self) -> str:
        return f"{PREFIX}-gate-audit"

    @property
    def tables(self) -> tuple[str, ...]:
        """All eight table names, in ``config/env.sh`` declaration order."""
        return (
            self.table_call_state,
            self.table_call_session,
            self.table_crm_cache,
            self.table_form_schema,
            self.table_intake_responses,
            self.table_consent_ledger,
            self.table_call_attempts,
            self.table_gate_audit,
        )

    # ---- GSIs (scripts/10-dynamodb.sh) ------------------------------------
    @property
    def gsi_phone_index(self) -> str:
        return "phone-index"

    @property
    def gsi_status_index(self) -> str:
        return "status-index"

    # ---- S3 buckets (account+region scoped) -------------------------------
    @property
    def archive_bucket(self) -> str:
        # scripts/55-call-archive.sh / CALL_ARCHIVE_BUCKET in config/env.sh
        return f"{PREFIX}-archive-{self.account}-{self.region}"

    @property
    def artifacts_bucket(self) -> str:
        # scripts/52-omnivoice.sh (ModelDataUrl + prompt cache)
        return f"{PREFIX}-artifacts-{self.account}-{self.region}"

    @property
    def audio_bucket(self) -> str:
        # AUDIO_BUCKET in config/env.sh (never created; name only)
        return f"{PREFIX}-audio-{self.account}-{self.region}"

    # ---- IAM roles (scripts/15-iam.sh, 50/52, 35/36 adaptations) ----------
    @property
    def lambda_role(self) -> str:
        return f"{PREFIX}-lambda-role"

    @property
    def agentcore_role(self) -> str:
        return f"{PREFIX}-agentcore-role"

    @property
    def sagemaker_role(self) -> str:
        return f"{PREFIX}-sagemaker-role"

    @property
    def turn_role(self) -> str:
        # scripts/35-turn.sh: ROLE="${NAME}-role" where NAME="hvi-turn"
        return f"{PREFIX}-turn-role"

    @property
    def web_role(self) -> str:
        # DESIGN "Web media edge": the dedicated hvi-web box's instance role,
        # standing in for the Phase-3 hvi-asterisk-role.
        return f"{PREFIX}-web-role"

    # ---- VPC + networking (scripts/20-vpc.sh) -----------------------------
    @property
    def vpc(self) -> str:
        return f"{PREFIX}-vpc"

    @property
    def subnet_private_a(self) -> str:
        return f"{PREFIX}-private-a"

    @property
    def subnet_private_b(self) -> str:
        return f"{PREFIX}-private-b"

    @property
    def subnet_public_a(self) -> str:
        return f"{PREFIX}-public-a"

    @property
    def igw(self) -> str:
        return f"{PREFIX}-igw"

    @property
    def nat(self) -> str:
        return f"{PREFIX}-nat"

    @property
    def nat_eip(self) -> str:
        return f"{PREFIX}-nat-eip"

    @property
    def rt_private(self) -> str:
        return f"{PREFIX}-rt-private"

    @property
    def rt_public(self) -> str:
        return f"{PREFIX}-rt-public"

    @property
    def neptune_subnet_group(self) -> str:
        return f"{PREFIX}-neptune-subnets"

    # ---- Security groups (scripts/20-vpc.sh, 35-turn.sh, 36-web) ----------
    @property
    def sg_lambda(self) -> str:
        return f"{PREFIX}-lambda-sg"

    @property
    def sg_neptune(self) -> str:
        return f"{PREFIX}-neptune-sg"

    @property
    def sg_infer(self) -> str:
        return f"{PREFIX}-infer-sg"

    @property
    def sg_web(self) -> str:
        return f"{PREFIX}-web-sg"

    @property
    def sg_turn(self) -> str:
        return f"{PREFIX}-turn-sg"

    # ---- OpenSearch (scripts/26-opensearch.sh) ----------------------------
    @property
    def opensearch_domain(self) -> str:
        return f"{PREFIX}-logs"

    @property
    def index_icd_concepts(self) -> str:
        return f"{PREFIX}-icd-concepts"

    @property
    def index_call_transcripts(self) -> str:
        return f"{PREFIX}-call-transcripts"

    # ---- Graph Lambda + Neptune (scripts/30, 25) --------------------------
    @property
    def graph_fn(self) -> str:
        return f"{PREFIX}-graph"

    @property
    def neptune_cluster(self) -> str:
        return f"{PREFIX}-health-kg"

    @property
    def neptune_instance(self) -> str:
        # scripts/25-neptune.sh: INSTANCE_ID="${CLUSTER_ID}-1"
        return f"{PREFIX}-health-kg-1"

    # ---- Cognito (scripts/34-cognito.sh) ----------------------------------
    @property
    def cognito_pool(self) -> str:
        return f"{PREFIX}-web-pool"

    @property
    def cognito_spa_client(self) -> str:
        return f"{PREFIX}-web-spa"

    @property
    def cognito_domain(self) -> str:
        # Hosted-UI domain prefix: ${PREFIX}-web-<acct> (globally unique).
        return f"{PREFIX}-web-{self.account}"

    # ---- SageMaker endpoints (scripts/50-whisper.sh, 52-omnivoice.sh) -----
    @property
    def whisper_endpoint(self) -> str:
        return f"{PREFIX}-whisper"

    @property
    def whisper_model(self) -> str:
        return f"{PREFIX}-whisper-large-v3"

    @property
    def whisper_endpoint_config(self) -> str:
        return f"{PREFIX}-whisper-config"

    @property
    def omnivoice_endpoint(self) -> str:
        return f"{PREFIX}-omnivoice"

    @property
    def omnivoice_model(self) -> str:
        return f"{PREFIX}-omnivoice"

    @property
    def omnivoice_endpoint_config(self) -> str:
        return f"{PREFIX}-omnivoice-config"

    # ---- CRM / email + agent (scripts/32, 40) -----------------------------
    @property
    def crm_writeback_fn(self) -> str:
        return f"{PREFIX}-crm-writeback"

    @property
    def crm_sync_fn(self) -> str:
        return f"{PREFIX}-crm-sync"

    @property
    def send_confirmation_fn(self) -> str:
        return f"{PREFIX}-send-confirmation"

    @property
    def agent_fn(self) -> str:
        # scripts/40-agentcore.sh: RUNTIME_NAME="${PREFIX}_intake_agent"
        # (underscore, not hyphen — matches the AgentCore runtime name and the
        # /aws/lambda/hvi_intake_agent* log group the Lambda path uses).
        return f"{PREFIX}_intake_agent"

    @property
    def agent_ecr_repo(self) -> str:
        return f"{PREFIX}-agent"

    # ---- Media edge instances (scripts/35, 36) ----------------------------
    @property
    def turn_instance(self) -> str:
        return f"{PREFIX}-turn"

    @property
    def web_instance(self) -> str:
        return f"{PREFIX}-web"

    # ---- Telephony edge (scripts/70, 71, 72, 33) --------------------------
    @property
    def asterisk_instance(self) -> str:
        # scripts/70-asterisk.sh: NAME="${PREFIX}-asterisk"
        return f"{PREFIX}-asterisk"

    @property
    def asterisk_sg(self) -> str:
        # scripts/70-asterisk.sh: "${NAME}-sg"
        return f"{PREFIX}-asterisk-sg"

    @property
    def asterisk_role(self) -> str:
        # scripts/70-asterisk.sh: ROLE="${NAME}-role"; scripts/72 attaches its
        # inline policy to this exact name.
        return f"{PREFIX}-asterisk-role"

    @property
    def outbound_fn(self) -> str:
        # scripts/33-outbound-lambda.sh: FN="${PREFIX}-outbound"
        return f"{PREFIX}-outbound"

    @property
    def media_service(self) -> str:
        # scripts/72-media-deploy.sh: SERVICE="${PREFIX}-media"
        return f"{PREFIX}-media"

    @property
    def asterisk_config_doc(self) -> str:
        # SSM command document carrying the three Asterisk SIP config files.
        return f"{PREFIX}-asterisk-config"
