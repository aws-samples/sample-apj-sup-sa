"""In-process synthesis tests for ``HviTelephonyStack`` (Phase 3, ap-south-1).

Builds Data -> Inference -> Chime -> Telephony in-process (mirroring
``test_agent_stack._synth``) and asserts on ``Template.from_stack(telephony)``.
No ``cdk`` CLI, no Docker, no AWS calls — every Lambda/custom-resource asset
bundles locally and nothing is executed.
"""

from __future__ import annotations

import json
import tarfile

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from stacks.chime_stack import HviChimeStack
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack
from stacks.telephony_stack import HviTelephonyStack

_OUTBOUND_HOST = "example.voiceconnector.chime.aws"
_MEDIA_CIDRS = {
    "3.80.16.0/23",
    "52.55.62.128/25",
    "52.55.63.0/25",
    "34.212.95.128/25",
    "34.223.21.0/25",
}


@pytest.fixture
def omnivoice_artifact(tmp_path):
    path = tmp_path / "model.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        marker = tmp_path / "ref.txt"
        marker.write_text("reference transcript", encoding="utf-8")
        tar.add(marker, arcname="ref.txt")
    return str(path)


def _synth(make_app, context=None, *, artifact=None):
    base = {
        "hvi:enableTelephony": True,
        "hvi:voiceConnectorOutboundHost": _OUTBOUND_HOST,
    }
    base.update(context or {})
    app = make_app(base)
    hvi = HviContext.from_scope(app)
    ap_south = cdk.Environment(account=hvi.account, region=hvi.region)
    us_east = cdk.Environment(account=hvi.account, region="us-east-1")
    data = HviDataStack(app, "HviDataStack", hvi=hvi, env=ap_south)
    inference = HviInferenceStack(
        app,
        "HviInferenceStack",
        hvi=hvi,
        data=data,
        omnivoice_artifact_path=artifact,
        env=ap_south,
    )
    chime = HviChimeStack(app, "HviChimeStack", hvi=hvi, env=us_east)
    telephony = HviTelephonyStack(
        app,
        "HviTelephonyStack",
        hvi=hvi,
        data=data,
        inference=inference,
        chime=chime,
        env=ap_south,
    )
    return Template.from_stack(telephony), telephony


# ---- lookup helpers -------------------------------------------------------
def _all_policy_statements(template):
    out = []
    for r in template.find_resources("AWS::IAM::Policy").values():
        out.extend(r["Properties"]["PolicyDocument"]["Statement"])
    return out


def _statement_by_sid(template, sid):
    for stmt in _all_policy_statements(template):
        if stmt.get("Sid") == sid:
            return stmt
    return None


def _function_by_name(template, name):
    for r in template.find_resources("AWS::Lambda::Function").values():
        if r["Properties"].get("FunctionName") == name:
            return r
    raise AssertionError(f"no Lambda function named {name}")


def _asterisk_sg(template):
    for sg in template.find_resources("AWS::EC2::SecurityGroup").values():
        if sg["Properties"].get("GroupName") == "hvi-asterisk-sg":
            return sg
    raise AssertionError("hvi-asterisk-sg not found")


# ---- Asterisk instance + EIP + SG -----------------------------------------
def test_single_asterisk_instance_tagged_and_typed(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    instances = tmpl.find_resources("AWS::EC2::Instance")
    assert len(instances) == 1
    (inst,) = instances.values()
    assert inst["Properties"]["InstanceType"] == "t4g.medium"
    names = {
        t["Value"]
        for t in inst["Properties"].get("Tags", [])
        if t["Key"] == "Name"
    }
    assert names == {"hvi-asterisk"}


def test_instance_requires_imdsv2(make_app, omnivoice_artifact):
    # require_imdsv2=True renders a LaunchTemplate with HttpTokens=required.
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    lts = tmpl.find_resources("AWS::EC2::LaunchTemplate")
    assert any(
        lt["Properties"]["LaunchTemplateData"]
        .get("MetadataOptions", {})
        .get("HttpTokens")
        == "required"
        for lt in lts.values()
    ), lts


def test_asterisk_instance_type_overridable(make_app, omnivoice_artifact):
    tmpl, _ = _synth(
        make_app,
        {"hvi:asteriskInstanceType": "c6g.medium"},
        artifact=omnivoice_artifact,
    )
    (inst,) = tmpl.find_resources("AWS::EC2::Instance").values()
    assert inst["Properties"]["InstanceType"] == "c6g.medium"


def test_eip_present_and_retained(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    eips = tmpl.find_resources("AWS::EC2::EIP")
    assert len(eips) == 1
    (eip,) = eips.values()
    assert eip["DeletionPolicy"] == "Retain"
    assert eip["UpdateReplacePolicy"] == "Retain"


def test_asterisk_sg_scoped_ingress(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    sg = _asterisk_sg(tmpl)
    ingress = sg["Properties"]["SecurityGroupIngress"]
    # SIP signalling: UDP 5060 from the Chime SIP CIDR.
    assert any(
        r.get("IpProtocol") == "udp"
        and r.get("FromPort") == 5060
        and r.get("ToPort") == 5060
        and r.get("CidrIp") == "3.80.16.0/23"
        for r in ingress
    ), ingress
    # RTP media: UDP 10000-10500 from each of the five media CIDRs.
    media_sources = {
        r.get("CidrIp")
        for r in ingress
        if r.get("IpProtocol") == "udp"
        and r.get("FromPort") == 10000
        and r.get("ToPort") == 10500
    }
    assert media_sources == _MEDIA_CIDRS, media_sources


def test_asterisk_sg_has_no_open_ingress(make_app, omnivoice_artifact):
    # Anti-toll-fraud invariant: no 0.0.0.0/0 anywhere on the SG.
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    sg = _asterisk_sg(tmpl)
    for r in sg["Properties"]["SecurityGroupIngress"]:
        assert r.get("CidrIp") != "0.0.0.0/0", r
        assert r.get("CidrIpv6") != "::/0", r


# ---- SSM config delivery ---------------------------------------------------
def test_ssm_document_and_association(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    tmpl.resource_count_is("AWS::SSM::Document", 1)
    tmpl.resource_count_is("AWS::SSM::Association", 1)
    tmpl.has_resource_properties(
        "AWS::SSM::Document", {"Name": "hvi-asterisk-config"}
    )
    # The association targets the instance by InstanceIds.
    (assoc,) = tmpl.find_resources("AWS::SSM::Association").values()
    targets = assoc["Properties"]["Targets"]
    assert any(t["Key"] == "InstanceIds" for t in targets), targets


def test_ssm_document_carries_nat_trap_and_loopback(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    rendered = json.dumps(tmpl.to_json())
    for fragment in (
        "external_signaling_address",
        "external_media_address",
        "local_net",
        "127.0.0.1:8090",
        "match=3.80.16.0/23",
        "_[+0-9].",
    ):
        assert fragment in rendered, fragment
    # The VPC CIDR literal (10.42.0.0/16) is supplied via the association params.
    assert "10.42.0.0/16" in rendered


# ---- instance role: hvi-asterisk-role --------------------------------------
def test_role_invoke_orchestrator_lambda_default(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    stmt = _statement_by_sid(tmpl, "InvokeOrchestrator")
    assert stmt is not None
    assert stmt["Action"] == "lambda:InvokeFunction"
    resource = stmt["Resource"]
    resource = resource if isinstance(resource, list) else [resource]
    assert any("function:hvi_intake_agent" in str(r) for r in resource), resource


def test_role_invoke_orchestrator_agentcore_cr(make_app, omnivoice_artifact):
    tmpl, _ = _synth(
        make_app, {"hvi:agentCompute": "agentcore-cr"}, artifact=omnivoice_artifact
    )
    stmt = _statement_by_sid(tmpl, "InvokeOrchestrator")
    assert stmt is not None
    assert stmt["Action"] == "bedrock-agentcore:InvokeAgentRuntime"


def test_role_prompt_cache_statements_under_omnivoice(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact)
    sids = {s.get("Sid") for s in _all_policy_statements(tmpl)}
    assert {"ReadPromptCache", "ListPromptCache", "WriteBackOnMiss"} <= sids


def test_role_prompt_cache_statements_absent_under_polly(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    sids = {s.get("Sid") for s in _all_policy_statements(tmpl)}
    assert not ({"ReadPromptCache", "ListPromptCache", "WriteBackOnMiss"} & sids)


def test_role_has_speech_store_and_polly_deny(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    speech = _statement_by_sid(tmpl, "InvokeSpeechEndpoints")
    assert speech is not None
    assert speech["Action"] == "sagemaker:InvokeEndpoint"
    store = _statement_by_sid(tmpl, "StoreCallRecording")
    assert store is not None and store["Action"] == "s3:PutObject"
    deny = _statement_by_sid(tmpl, "DenyOtherTTSVendors")
    assert deny is not None
    assert deny["Effect"] == "Deny"
    assert deny["Action"] == "polly:*"


def test_role_has_no_dynamodb_or_bedrock_invoke_model(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    # Only inspect the hvi-asterisk-role's statements (exclude provider-framework
    # and lambda-role policies, which are owned by other stacks/constructs).
    rendered = json.dumps(
        [
            s
            for s in _all_policy_statements(tmpl)
            if s.get("Sid")
            in {
                "InvokeOrchestrator",
                "InvokeSpeechEndpoints",
                "StoreCallRecording",
                "DenyOtherTTSVendors",
                "ReadPromptCache",
                "ListPromptCache",
                "WriteBackOnMiss",
            }
        ]
    )
    assert "dynamodb:" not in rendered
    assert "bedrock:InvokeModel" not in rendered


# ---- outbound dialler Lambda -----------------------------------------------
def test_outbound_lambda_shape(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    fn = _function_by_name(tmpl, "hvi-outbound")
    props = fn["Properties"]
    assert props["Runtime"] == "python3.12"
    assert props["Architectures"] == ["x86_64"]
    assert props["Timeout"] == 120
    assert props["MemorySize"] == 512
    assert "VpcConfig" not in props
    env = props["Environment"]["Variables"]
    assert env["ALLOW_WINDOW_OVERRIDE"] == "false"
    assert env["TRAI_CALL_START_HOUR"] == "9"
    assert env["TRAI_CALL_END_HOUR"] == "21"
    assert env["TRAI_MAX_ATTEMPTS_PER_DAY"] == "3"
    assert env["TELEPHONY_REGION"] == "us-east-1"


def test_no_schedule_resources(make_app, omnivoice_artifact):
    # scripts/33: nothing dials on its own — no EventBridge rule or scheduler.
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    tmpl.resource_count_is("AWS::Events::Rule", 0)
    tmpl.resource_count_is("AWS::Scheduler::Schedule", 0)


# ---- Custom::HviChimeVcRoute -----------------------------------------------
def test_vc_route_custom_resource(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    resources = tmpl.find_resources("Custom::HviChimeVcRoute")
    assert len(resources) == 1
    (resource,) = resources.values()
    props = resource["Properties"]
    assert props["VoiceConnectorId"] == "abcdefghij0123456789"
    assert props["TelephonyRegion"] == "us-east-1"
    assert props["CallingRegions"] == ["US", "IN"]
    # The EIP arrives as an unresolved token (a Ref/GetAtt), not a literal.
    assert "AsteriskEip" in props


def test_vc_route_role_scoped_to_the_vc(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    stmt = _statement_by_sid(tmpl, "RepointVoiceConnector")
    assert stmt is not None
    actions = stmt["Action"]
    assert "chime-sdk-voice:PutVoiceConnectorTermination" in actions
    assert "chime-sdk-voice:PutVoiceConnectorOrigination" in actions
    resource = stmt["Resource"]
    assert "vc/abcdefghij0123456789" in str(resource)
