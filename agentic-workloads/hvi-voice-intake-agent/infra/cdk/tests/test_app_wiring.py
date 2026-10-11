"""Whole-App cross-stack wiring tests (FEAT-005).

Synthesises the entire four-stack App (Data -> Knowledge -> Inference -> Agent)
under the four required permutations and asserts each one synthesises with **no**
exception and that Stack 3 (telephony) is never instantiated. This exercises the
typed cross-stack prop wiring end to end (CDK emits the CfnOutput/Fn::ImportValue
plumbing during ``app.synth()``). No ``cdk`` CLI, no Docker, no AWS calls.

The default ``hvi:tts=omnivoice`` needs a pre-built ``model.tar.gz`` at synth, so
the Inference stack is pointed at a throwaway tarball; the enableWebMedia
permutation also supplies the CloudFront origin-facing prefix-list id.
"""

from __future__ import annotations

import tarfile

import aws_cdk as cdk
import pytest

from stacks.agent_stack import HviAgentStack
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack
from stacks.knowledge_stack import HviKnowledgeStack

_EXPECTED_STACKS = {
    "HviDataStack",
    "HviKnowledgeStack",
    "HviInferenceStack",
    "HviAgentStack",
}
_PREFIX_LIST_ID = "pl-0123456789abcdef0"


@pytest.fixture
def omnivoice_artifact(tmp_path):
    path = tmp_path / "model.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        marker = tmp_path / "ref.txt"
        marker.write_text("reference transcript", encoding="utf-8")
        tar.add(marker, arcname="ref.txt")
    return str(path)


def _build_app(context, outdir, artifact):
    app = cdk.App(context=dict(context), outdir=str(outdir))
    hvi = HviContext.from_scope(app)
    env = cdk.Environment(account=hvi.account, region=hvi.region)
    data = HviDataStack(app, "HviDataStack", hvi=hvi, env=env)
    knowledge = HviKnowledgeStack(
        app, "HviKnowledgeStack", hvi=hvi, data=data, env=env
    )
    inference = HviInferenceStack(
        app,
        "HviInferenceStack",
        hvi=hvi,
        data=data,
        omnivoice_artifact_path=artifact,
        env=env,
    )
    HviAgentStack(
        app,
        "HviAgentStack",
        hvi=hvi,
        data=data,
        knowledge=knowledge,
        inference=inference,
        env=env,
    )
    return app


_PERMUTATIONS = {
    "default": {},
    "enableNeptune": {"hvi:enableNeptune": True},
    "enableWebMedia": {
        "hvi:enableWebMedia": True,
        "hvi:cloudfrontOriginPrefixListId": _PREFIX_LIST_ID,
    },
    "agentcore-cr": {"hvi:agentCompute": "agentcore-cr"},
}


@pytest.mark.parametrize("name", list(_PERMUTATIONS))
def test_permutation_synthesises_and_stack3_absent(
    make_app, tmp_path, omnivoice_artifact, name
):
    app = _build_app(_PERMUTATIONS[name], tmp_path / name, omnivoice_artifact)
    assembly = app.synth()  # raises if any cross-stack reference fails to resolve
    stack_ids = {s.id for s in assembly.stacks}
    assert stack_ids == _EXPECTED_STACKS, stack_ids
    # Stack 3 (telephony) is reserved but never instantiated in Phase 2.
    assert not any("telephony" in sid.lower() for sid in stack_ids)
    assert not any("stack3" in sid.lower() for sid in stack_ids)


# ---- Phase 3 telephony on/off — driven through app.py's OWN conditional ----
# NIT-3: exercise build_app's `if hvi.enable_telephony:` block directly, not the
# test-local _build_app (which unconditionally builds the four Phase 2 stacks).
# polly so the app-driven synth needs no OmniVoice artifact.
_OUTBOUND_HOST = "example.voiceconnector.chime.aws"


def _assembly_stacks(context):
    from app import build_app

    assembly = build_app(context).synth()
    return {s.id: s for s in assembly.stacks}


def test_telephony_off_by_default_build_app():
    stacks = _assembly_stacks({"hvi:tts": "polly"})
    assert set(stacks) == _EXPECTED_STACKS
    assert not any("telephony" in sid.lower() for sid in stacks)
    assert not any("chime" in sid.lower() for sid in stacks)


def test_telephony_on_adds_both_region_scoped_stacks():
    stacks = _assembly_stacks(
        {
            "hvi:tts": "polly",
            "hvi:enableTelephony": True,
            "hvi:voiceConnectorOutboundHost": _OUTBOUND_HOST,
        }
    )
    assert set(stacks) == _EXPECTED_STACKS | {
        "HviTelephonyStack",
        "HviChimeStack",
    }
    assert stacks["HviTelephonyStack"].environment.region == "ap-south-1"
    assert stacks["HviChimeStack"].environment.region == "us-east-1"
