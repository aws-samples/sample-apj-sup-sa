"""In-process synthesis tests for ``HviInferenceStack`` (FEAT-004).

Each test builds an ``App(context={...})``, instantiates Data then Inference
(consuming Data's ``hvi-sagemaker-role`` prop), and asserts on
``Template.from_stack``. No ``cdk`` CLI, no Docker, no AWS calls.

The OmniVoice subtree needs a pre-built ``model.tar.gz`` present at synth; the
``omnivoice_artifact`` fixture writes a throwaway tarball to a tmp path and the
stack is pointed at it via ``omnivoice_artifact_path`` so the tests never touch
the repo tree. The absent-artifact fail-loud behaviour is asserted separately
with no override (the worktree has no ``build/omnivoice/model.tar.gz``).
"""

from __future__ import annotations

import tarfile

import aws_cdk as cdk
import pytest

from aws_cdk.assertions import Template

from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack

_WHISPER_LADDER = "ml.g4dn.xlarge,ml.g5.xlarge,ml.g6.xlarge,ml.g4dn.2xlarge,ml.g5.2xlarge"
_OMNIVOICE_LADDER = "ml.g5.xlarge,ml.g4dn.xlarge"


@pytest.fixture
def omnivoice_artifact(tmp_path):
    """A throwaway model.tar.gz so the omnivoice synth-time check passes."""
    path = tmp_path / "model.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        marker = tmp_path / "ref.txt"
        marker.write_text("reference transcript", encoding="utf-8")
        tar.add(marker, arcname="ref.txt")
    return str(path)


def _synth(make_app, context=None, *, artifact=None):
    app = make_app(context or {})
    hvi = HviContext.from_scope(app)
    env = cdk.Environment(account=hvi.account, region=hvi.region)
    data = HviDataStack(app, "HviDataStack", hvi=hvi, env=env)
    inference = HviInferenceStack(
        app,
        "HviInferenceStack",
        hvi=hvi,
        data=data,
        omnivoice_artifact_path=artifact,
        env=env,
    )
    return Template.from_stack(inference), inference


# ---- resource lookup helpers ---------------------------------------------
def _resources_of_type(template, cfn_type):
    return template.find_resources(cfn_type)


def _by_endpoint(resources, endpoint_name):
    for logical_id, resource in resources.items():
        if resource["Properties"].get("EndpointName") == endpoint_name:
            return logical_id, resource
    raise AssertionError(f"no resource with EndpointName={endpoint_name}")


# ---- Whisper (present on BOTH tts paths) ----------------------------------
@pytest.mark.parametrize("tts", ["omnivoice", "polly"])
def test_whisper_endpoint_and_preflight_present_on_both_paths(
    make_app, omnivoice_artifact, tts
):
    tmpl, _ = _synth(make_app, {"hvi:tts": tts}, artifact=omnivoice_artifact)
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    # Whisper's preflight + endpoint exist regardless of the TTS engine.
    _by_endpoint(preflights, "hvi-whisper")
    _by_endpoint(endpoints, "hvi-whisper")


def test_whisper_preflight_ladder_is_exact_five_entry_order(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    _, resource = _by_endpoint(preflights, "hvi-whisper")
    assert resource["Properties"]["CandidateLadder"] == _WHISPER_LADDER
    assert resource["Properties"]["Cap"] == 5


def test_whisper_endpoint_timeouts_are_900_900_600(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    _, resource = _by_endpoint(endpoints, "hvi-whisper")
    props = resource["Properties"]
    assert props["ContainerStartupHealthCheckTimeoutInSeconds"] == 900
    assert props["ModelDataDownloadTimeoutInSeconds"] == 900
    assert props["VariantInstanceProvisionTimeoutInSeconds"] == 600


def test_whisper_model_env_and_image(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    _, resource = _by_endpoint(endpoints, "hvi-whisper")
    props = resource["Properties"]
    env = props["Environment"]
    assert env["HF_MODEL_ID"] == "openai/whisper-large-v3"
    assert env["HF_TASK"] == "automatic-speech-recognition"
    assert env["HF_HUB_ENABLE_HF_TRANSFER"] == "0"
    assert env["SAGEMAKER_CONTAINER_LOG_LEVEL"] == "20"
    assert env["SAGEMAKER_REGION"] == "ap-south-1"
    assert env["MMS_DEFAULT_RESPONSE_TIMEOUT"] == "300"
    assert "huggingface-pytorch-inference" in props["Image"]
    assert "ap-south-1" in props["Image"]
    # Whisper is HF_MODEL_ID driven — no model artifact.
    assert "ModelDataUrl" not in props


def test_whisper_endpoint_depends_on_its_preflight(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    preflight_id, _ = _by_endpoint(preflights, "hvi-whisper")
    _, endpoint = _by_endpoint(endpoints, "hvi-whisper")
    assert preflight_id in endpoint.get("DependsOn", [])


# ---- OmniVoice (omnivoice path only) --------------------------------------
def test_omnivoice_endpoint_and_preflight_present_under_omnivoice(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(
        make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact
    )
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    _by_endpoint(preflights, "hvi-omnivoice")
    _by_endpoint(endpoints, "hvi-omnivoice")


def test_omnivoice_preflight_ladder_is_two_entries_no_g6(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(
        make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact
    )
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    _, resource = _by_endpoint(preflights, "hvi-omnivoice")
    ladder = resource["Properties"]["CandidateLadder"]
    assert ladder == _OMNIVOICE_LADDER
    assert "ml.g6.xlarge" not in ladder
    assert resource["Properties"]["Cap"] == 2
    # At most the two expected pools, in order.
    assert ladder.split(",") == ["ml.g5.xlarge", "ml.g4dn.xlarge"]


def test_omnivoice_endpoint_timeouts_are_3600_1800_600(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(
        make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact
    )
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    _, resource = _by_endpoint(endpoints, "hvi-omnivoice")
    props = resource["Properties"]
    # OmniVoice-specific — must NOT inherit Whisper's 900/900/600.
    assert props["ContainerStartupHealthCheckTimeoutInSeconds"] == 3600
    assert props["ModelDataDownloadTimeoutInSeconds"] == 1800
    assert props["VariantInstanceProvisionTimeoutInSeconds"] == 600


def test_omnivoice_model_env_and_data_url(make_app, omnivoice_artifact):
    tmpl, _ = _synth(
        make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact
    )
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    _, resource = _by_endpoint(endpoints, "hvi-omnivoice")
    props = resource["Properties"]
    env = props["Environment"]
    assert env["OMNIVOICE_SPEED"] == "0.85"
    assert env["OMNIVOICE_SPEED_EN"] == "0.78"
    assert env["OMNIVOICE_SPEED_HI"] == "1.06"
    assert env["OMNIVOICE_SPEED_HINGLISH"] == "0.78"
    assert env["OMNIVOICE_NUM_STEP"] == "16"
    assert env["OMNIVOICE_NORMALIZE_TEXT"] == "1"
    assert env["HF_HOME"] == "/tmp/hf"
    assert env["TS_DEFAULT_RESPONSE_TIMEOUT"] == "300"
    assert (
        props["ModelDataUrl"]
        == "s3://hvi-artifacts-111122223333-ap-south-1/omnivoice/model.tar.gz"
    )
    assert "pytorch-inference" in props["Image"]


def test_omnivoice_endpoint_depends_on_its_preflight(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(
        make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact
    )
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    preflight_id, _ = _by_endpoint(preflights, "hvi-omnivoice")
    _, endpoint = _by_endpoint(endpoints, "hvi-omnivoice")
    assert preflight_id in endpoint.get("DependsOn", [])


def test_artifacts_bucket_present_and_secure_under_omnivoice(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(
        make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact
    )
    tmpl.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": "hvi-artifacts-111122223333-ap-south-1",
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            },
        },
    )


# ---- polly path (publishable sample; no OmniVoice subtree) ----------------
def test_polly_has_no_omnivoice_endpoint_or_preflight(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    preflights = _resources_of_type(tmpl, "Custom::HviGpuPreflight")
    endpoints = _resources_of_type(tmpl, "Custom::HviSageMakerEndpoint")
    # Exactly one of each — Whisper only.
    assert len(preflights) == 1
    assert len(endpoints) == 1
    names = {
        r["Properties"]["EndpointName"]
        for r in {**preflights, **endpoints}.values()
    }
    assert names == {"hvi-whisper"}


def test_polly_has_no_artifacts_bucket(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    tmpl.resource_count_is("AWS::S3::Bucket", 0)


def test_polly_synth_succeeds_with_no_artifact_present(make_app):
    # No artifact override and the worktree has no build/omnivoice/model.tar.gz;
    # the polly path must synth without raising.
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    assert tmpl is not None


# ---- fail-loud: omnivoice without the artifact ----------------------------
def test_omnivoice_synth_fails_when_artifact_absent(make_app):
    with pytest.raises(FileNotFoundError) as excinfo:
        _synth(make_app, {"hvi:tts": "omnivoice"})  # no artifact override
    message = str(excinfo.value)
    assert "scripts/52" in message
    assert "hvi:tts=polly" in message
