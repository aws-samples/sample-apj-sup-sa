"""``HviInferenceStack`` — Stack 4 of the HVI Phase 2 CDK app.

Reproduces ``scripts/50-whisper.sh`` and ``scripts/52-omnivoice.sh`` plus the
GPU preflight of ``scripts/51-gpu-quota.py`` as a single CDK stack:

* the **Whisper** SageMaker endpoint (``hvi-whisper``) — created on **both**
  TTS paths, because Whisper STT is unaffected by ``hvi:tts``;
* the **OmniVoice** endpoint (``hvi-omnivoice``) + the ``hvi-artifacts`` bucket
  + the ``model.tar.gz`` asset — created **only** under ``hvi:tts=omnivoice``;
* a **per-endpoint GPU-preflight** custom resource that runs before each
  endpoint's custom resource (the endpoint ``DependsOn`` its preflight);
* the **SageMaker-endpoint** custom resource that creates
  Model/EndpointConfig(InstancePools)/Endpoint — a custom resource because
  ``InstancePools`` is not reliably expressible on L1 ``CfnEndpointConfig``.

Both endpoints consume ``hvi-sagemaker-role`` from :class:`HviDataStack` as a
typed prop (never a name re-lookup). The role's read of ``hvi-artifacts`` is a
static-ARN statement owned by Data, so this stack must **not** call
``bucket.grant_read`` on it (that would create a Data<->Inference cycle).

Fail-loud (``DESIGN`` "Stack 4 — Inference"):

* The per-endpoint ``InstancePools`` ladders are verbatim from the scripts and
  MUST NOT grow or reorder — Whisper's 5-entry ladder and OmniVoice's 2-entry
  ladder are passed to the preflight as fixed candidate ladders; the preflight
  only ever drops headroom-less entries and raises ``NO_USABLE_GPU`` on empty.
* Under ``hvi:tts=omnivoice`` the stack **fails synth** if the pre-built
  ``build/omnivoice/model.tar.gz`` is absent (pointing at ``scripts/52``) — the
  "no silent robot-cloning-a-robot" prerequisite. Under ``hvi:tts=polly`` none
  of the OmniVoice subtree is created, so a bare/CI synth succeeds with no
  artifact present.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_assets as s3_assets,
    custom_resources as cr,
)
from constructs import Construct

from stacks.assets import vendored_asset_code
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.naming import Naming

# infra/cdk/stacks/inference_stack.py -> parents: [stacks, cdk, infra, <root>]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CDK_ROOT = Path(__file__).resolve().parents[1]
_SEED_RESOURCES = _CDK_ROOT / "seed_resources"

# HuggingFace account id is the same across every commercial region.
_DLC_ACCOUNT = "763104351884"

# Fixed per-endpoint candidate ladders — VERBATIM from the scripts, MUST NOT
# grow or reorder. Whisper caps at 5 (SageMaker's InstancePools maximum);
# OmniVoice is deliberately capped at 2 (a long ladder reserves scarce quota
# breadth and breaks blue/green updates on limit-1 types).
_WHISPER_LADDER = (
    "ml.g4dn.xlarge",
    "ml.g5.xlarge",
    "ml.g6.xlarge",
    "ml.g4dn.2xlarge",
    "ml.g5.2xlarge",
)
_WHISPER_CAP = 5
_OMNIVOICE_LADDER = ("ml.g5.xlarge", "ml.g4dn.xlarge")
_OMNIVOICE_CAP = 2

# Endpoint-config timeouts (startup health-check / model-download / provision).
_WHISPER_TIMEOUTS = (900, 900, 600)
# OmniVoice-specific and LOAD-BEARING: pip install (transformers 5, gradio,
# librosa, accelerate) + a 3.3 GB checkpoint download before warmup generate().
_OMNIVOICE_TIMEOUTS = (3600, 1800, 600)


class HviInferenceStack(Stack):
    """Whisper (always) + OmniVoice (omnivoice-only) SageMaker endpoints."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hvi: HviContext,
        data: HviDataStack,
        omnivoice_artifact_path: str | Path | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hvi = hvi
        self.data = data
        self.naming = Naming(account=hvi.account, region=hvi.region)
        # Overridable so offline tests can point at a temp tarball; defaults to
        # the pre-built artifact scripts/52 produces.
        self._artifact_path = (
            Path(omnivoice_artifact_path)
            if omnivoice_artifact_path is not None
            else _REPO_ROOT / "build" / "omnivoice" / "model.tar.gz"
        )

        # Typed cross-stack handles (None until/unless created).
        self.artifacts_bucket: s3.Bucket | None = None
        self.whisper_endpoint_name = self.naming.whisper_endpoint
        self.omnivoice_endpoint_name: str | None = None

        self._build_providers()
        self._build_whisper()  # always
        if hvi.tts == "omnivoice":
            self._build_omnivoice()
        # hvi:tts=polly -> no OmniVoice endpoint, no artifacts bucket, no asset.

    # ------------------------------------------------------------- providers
    def _build_providers(self) -> None:
        region = self.hvi.region

        # GPU preflight: read-only service-quotas + sagemaker describe/list.
        preflight_fn = lambda_.Function(
            self,
            "GpuPreflightFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(_SEED_RESOURCES / "gpu_preflight"),
            timeout=Duration.minutes(5),
            memory_size=256,
            environment={"DATA_REGION": region},
        )
        preflight_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="GpuPreflightReadQuota",
                actions=[
                    "servicequotas:ListServiceQuotas",
                    "servicequotas:GetServiceQuota",
                    "sagemaker:ListEndpoints",
                    "sagemaker:DescribeEndpoint",
                    "sagemaker:DescribeEndpointConfig",
                ],
                resources=["*"],
            )
        )
        self._preflight_provider = cr.Provider(
            self, "GpuPreflightProvider", on_event_handler=preflight_fn
        )

        # SageMaker endpoint manager: create/update/delete the model trio and
        # pass the execution role to SageMaker.
        endpoint_fn = lambda_.Function(
            self,
            "SageMakerEndpointFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(_SEED_RESOURCES / "sagemaker_endpoint"),
            timeout=Duration.minutes(15),
            memory_size=256,
            environment={"DATA_REGION": region},
        )
        endpoint_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="SageMakerEndpointManage",
                actions=[
                    "sagemaker:CreateModel",
                    "sagemaker:DeleteModel",
                    "sagemaker:DescribeModel",
                    "sagemaker:CreateEndpointConfig",
                    "sagemaker:DeleteEndpointConfig",
                    "sagemaker:DescribeEndpointConfig",
                    "sagemaker:CreateEndpoint",
                    "sagemaker:UpdateEndpoint",
                    "sagemaker:DeleteEndpoint",
                    "sagemaker:DescribeEndpoint",
                    "sagemaker:AddTags",
                ],
                resources=["*"],
            )
        )
        endpoint_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PassSageMakerExecutionRole",
                actions=["iam:PassRole"],
                resources=[self.data.sagemaker_role.role_arn],
            )
        )
        self._endpoint_provider = cr.Provider(
            self, "SageMakerEndpointProvider", on_event_handler=endpoint_fn
        )

    # --------------------------------------------------------------- helpers
    def _endpoint(
        self,
        logical: str,
        *,
        endpoint_name: str,
        model_name: str,
        config_name: str,
        image: str,
        environment: dict,
        candidate_ladder: tuple[str, ...],
        cap: int,
        timeouts: tuple[int, int, int],
        model_data_url: str | None = None,
    ) -> cdk.CustomResource:
        """A per-endpoint GPU preflight CR + the endpoint CR that DependsOn it."""
        preflight = cdk.CustomResource(
            self,
            f"{logical}Preflight",
            service_token=self._preflight_provider.service_token,
            resource_type="Custom::HviGpuPreflight",
            properties={
                "Region": self.hvi.region,
                "EndpointName": endpoint_name,
                # Literal, ordered ladder — asserted by the synth tests.
                "CandidateLadder": ",".join(candidate_ladder),
                "Cap": cap,
            },
        )

        properties: dict = {
            "Region": self.hvi.region,
            "RoleArn": self.data.sagemaker_role.role_arn,
            "Image": image,
            "ModelName": model_name,
            "EndpointConfigName": config_name,
            "EndpointName": endpoint_name,
            "Environment": environment,
            # The endpoint runs the preflight-FILTERED ladder, not the raw one.
            "InstancePools": preflight.get_att_string("Ladder"),
            "ContainerStartupHealthCheckTimeoutInSeconds": timeouts[0],
            "ModelDataDownloadTimeoutInSeconds": timeouts[1],
            "VariantInstanceProvisionTimeoutInSeconds": timeouts[2],
        }
        if model_data_url is not None:
            properties["ModelDataUrl"] = model_data_url

        endpoint = cdk.CustomResource(
            self,
            f"{logical}Endpoint",
            service_token=self._endpoint_provider.service_token,
            resource_type="Custom::HviSageMakerEndpoint",
            properties=properties,
        )
        # Fail early, never hang: the endpoint CR runs only after its preflight.
        endpoint.node.add_dependency(preflight)
        return endpoint

    # --------------------------------------------------------------- whisper
    def _build_whisper(self) -> None:
        n = self.naming
        image = (
            f"{_DLC_ACCOUNT}.dkr.ecr.{self.hvi.region}.amazonaws.com/"
            "huggingface-pytorch-inference:2.6.0-transformers4.51.3-gpu-py312-"
            "cu124-ubuntu22.04-v2.3"
        )
        environment = {
            "HF_MODEL_ID": "openai/whisper-large-v3",
            "HF_TASK": "automatic-speech-recognition",
            # Deliberately 0: hf_transfer against the Xet CDN 403'd the weight
            # shards and the container never came up (scripts/50).
            "HF_HUB_ENABLE_HF_TRANSFER": "0",
            "SAGEMAKER_CONTAINER_LOG_LEVEL": "20",
            "SAGEMAKER_REGION": "ap-south-1",
            "MMS_DEFAULT_RESPONSE_TIMEOUT": "300",
        }
        self._endpoint(
            "Whisper",
            endpoint_name=n.whisper_endpoint,
            model_name=n.whisper_model,
            config_name=n.whisper_endpoint_config,
            image=image,
            environment=environment,
            candidate_ladder=_WHISPER_LADDER,
            cap=_WHISPER_CAP,
            timeouts=_WHISPER_TIMEOUTS,
        )

    # ------------------------------------------------------------- omnivoice
    def _build_omnivoice(self) -> None:
        n = self.naming

        # Fail-loud synth-time presence check (DESIGN: no silent robot-cloning-
        # a-robot). Reached ONLY on the omnivoice path, so it never blocks the
        # publishable polly sample.
        if not self._artifact_path.is_file():
            raise FileNotFoundError(
                "hvi:tts=omnivoice selected but the OmniVoice model artifact is "
                f"absent at {self._artifact_path}. Build it first with "
                "scripts/52-omnivoice.sh (which needs a human reference "
                "recording at build/omnivoice/ref.wav + ref.txt), or synth with "
                "-c hvi:tts=polly for a bare/CI run that needs no artifact."
            )

        # hvi-artifacts bucket — created only under omnivoice (NIT-6): holds the
        # ModelDataUrl object (and, later, the prompt cache). Public access
        # blocked, SSE AES256.
        self.artifacts_bucket = s3.Bucket(
            self,
            "ArtifactsBucket",
            bucket_name=n.artifacts_bucket,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,  # AES256
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Model the pre-built tarball as an asset so the bytes are part of the
        # CDK app and the local artifact is validated at synth. The ModelDataUrl
        # the endpoint reads is the deterministic hvi-artifacts key, exactly as
        # scripts/52 writes it (the operator's scripts/52 upload / a deploy step
        # populates that key; naming.py makes it deterministic).
        s3_assets.Asset(
            self,
            "OmniVoiceModelAsset",
            path=str(self._artifact_path),
        )
        model_data_url = f"s3://{n.artifacts_bucket}/omnivoice/model.tar.gz"

        image = (
            f"{_DLC_ACCOUNT}.dkr.ecr.{self.hvi.region}.amazonaws.com/"
            "pytorch-inference:2.6.0-gpu-py312-cu124-ubuntu22.04-sagemaker"
        )
        environment = {
            "SAGEMAKER_PROGRAM": "inference.py",
            "SAGEMAKER_SUBMIT_DIRECTORY": "/opt/ml/model/code",
            "SAGEMAKER_CONTAINER_LOG_LEVEL": "20",
            "SAGEMAKER_REGION": "ap-south-1",
            "OMNIVOICE_MODEL_ID": "k2-fsa/OmniVoice",
            "OMNIVOICE_SPEED": "0.85",
            "OMNIVOICE_SPEED_EN": "0.78",
            "OMNIVOICE_SPEED_HI": "1.06",
            "OMNIVOICE_SPEED_HINGLISH": "0.78",
            "OMNIVOICE_NUM_STEP": "16",
            "OMNIVOICE_NORMALIZE_TEXT": "1",
            "HF_HUB_ENABLE_HF_TRANSFER": "0",
            "HF_HOME": "/tmp/hf",
            "TS_DEFAULT_RESPONSE_TIMEOUT": "300",
        }
        self._endpoint(
            "OmniVoice",
            endpoint_name=n.omnivoice_endpoint,
            model_name=n.omnivoice_model,
            config_name=n.omnivoice_endpoint_config,
            image=image,
            environment=environment,
            candidate_ladder=_OMNIVOICE_LADDER,
            cap=_OMNIVOICE_CAP,
            timeouts=_OMNIVOICE_TIMEOUTS,
            model_data_url=model_data_url,
        )
        self.omnivoice_endpoint_name = n.omnivoice_endpoint
