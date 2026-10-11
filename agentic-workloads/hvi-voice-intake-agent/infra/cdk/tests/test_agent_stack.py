"""In-process synthesis tests for ``HviAgentStack`` (FEAT-005).

Each test builds an ``App(context={...})``, instantiates Data -> Knowledge ->
Inference -> Agent (consuming the upstream typed props), and asserts on
``Template.from_stack(agent)``. No ``cdk`` CLI, no Docker, no AWS calls — the
agent container is referenced from ECR (never built) and every Lambda asset
bundles locally.

The default ``hvi:tts=omnivoice`` path drags in the Inference OmniVoice subtree,
which needs a pre-built ``model.tar.gz`` at synth; the ``omnivoice_artifact``
fixture writes a throwaway tarball and the Inference stack is pointed at it, so
the tests never touch the repo tree.
"""

from __future__ import annotations

import json
import tarfile

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.agent_stack import HviAgentStack
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack
from stacks.knowledge_stack import HviKnowledgeStack

# A valid-looking CloudFront origin-facing managed prefix-list id for the
# enableWebMedia path (no AWS lookup — supplied via context).
_PREFIX_LIST_ID = "pl-0123456789abcdef0"


@pytest.fixture
def omnivoice_artifact(tmp_path):
    """A throwaway model.tar.gz so the Inference omnivoice check passes."""
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
    agent = HviAgentStack(
        app,
        "HviAgentStack",
        hvi=hvi,
        data=data,
        knowledge=knowledge,
        inference=inference,
        env=env,
    )
    return Template.from_stack(agent), agent


# ---- lookup helpers -------------------------------------------------------
def _image_functions(template):
    return {
        lid: r
        for lid, r in template.find_resources("AWS::Lambda::Function").items()
        if r["Properties"].get("PackageType") == "Image"
    }


def _function_by_name(template, name):
    for r in template.find_resources("AWS::Lambda::Function").values():
        if r["Properties"].get("FunctionName") == name:
            return r
    raise AssertionError(f"no Lambda function named {name}")


def _all_policy_statements(template):
    out = []
    for r in template.find_resources("AWS::IAM::Policy").values():
        out.extend(r["Properties"]["PolicyDocument"]["Statement"])
    return out


# ---- agent compute: default lambda path -----------------------------------
def test_agent_lambda_is_image_arm64(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    images = _image_functions(tmpl)
    assert len(images) == 1
    (fn,) = images.values()
    assert fn["Properties"]["Architectures"] == ["arm64"]
    assert fn["Properties"]["MemorySize"] == 1024
    assert fn["Properties"]["Timeout"] == 120


def test_agent_env_has_core_keys_and_nonempty_archive(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    (fn,) = _image_functions(tmpl).values()
    env = fn["Properties"]["Environment"]["Variables"]
    for key in (
        "BEDROCK_MODEL_ID",
        "OPENSEARCH_ENDPOINT",
        "GRAPH_FN",
        "TBL_CALL_STATE",
        "TBL_CALL_SESSION",
        "TBL_CRM",
        "TBL_FORM_SCHEMA",
        "TBL_RESPONSES",
        "CALL_ARCHIVE_BUCKET",
    ):
        assert key in env, key
    # BEDROCK_MODEL_ID + CALL_ARCHIVE_BUCKET are literals (context / Data prop);
    # OPENSEARCH_ENDPOINT and GRAPH_FN are cross-stack reference tokens, so only
    # their presence is asserted above.
    assert env["BEDROCK_MODEL_ID"] == "mistral.mistral-large-3-675b-instruct"
    assert env["CALL_ARCHIVE_BUCKET"] == "hvi-archive-111122223333-ap-south-1"
    assert env["CALL_ARCHIVE_BUCKET"]  # non-empty


def test_agent_env_lacks_speech_endpoints(make_app, omnivoice_artifact):
    # MEDIUM-1: STT/OMNIVOICE belong on the hvi-web box, never the agent.
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    (fn,) = _image_functions(tmpl).values()
    env = fn["Properties"]["Environment"]["Variables"]
    assert "STT_ENDPOINT" not in env
    assert "OMNIVOICE_ENDPOINT" not in env


# ---- agent compute: agentcore-cr path --------------------------------------
def test_agentcore_cr_has_no_image_fn_and_a_custom_resource(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(
        make_app, {"hvi:agentCompute": "agentcore-cr"}, artifact=omnivoice_artifact
    )
    # No agent Lambda (the only Image-package function) on this path.
    assert _image_functions(tmpl) == {}
    # The AgentCore control-plane custom resource stands in.
    tmpl.resource_count_is("Custom::HviAgentCoreRuntime", 1)


# ---- CRM / email Lambdas ---------------------------------------------------
def test_three_crm_lambdas_not_vpc_attached(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    for name in ("hvi-crm-writeback", "hvi-crm-sync", "hvi-send-confirmation"):
        fn = _function_by_name(tmpl, name)
        assert fn["Properties"].get("Runtime") == "python3.12"
        assert fn["Properties"].get("Architectures") == ["x86_64"]
        assert fn["Properties"].get("Handler") == "handler.lambda_handler"
        assert "VpcConfig" not in fn["Properties"], name


def test_crm_lambda_timeouts_and_memory(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    expected = {
        "hvi-crm-writeback": (120, 512),
        "hvi-crm-sync": (300, 512),
        "hvi-send-confirmation": (60, 256),
    }
    for name, (timeout, mem) in expected.items():
        fn = _function_by_name(tmpl, name)
        assert fn["Properties"]["Timeout"] == timeout, name
        assert fn["Properties"]["MemorySize"] == mem, name


def test_crm_lambda_env_contracts(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)

    writeback = _function_by_name(tmpl, "hvi-crm-writeback")[
        "Properties"
    ]["Environment"]["Variables"]
    assert writeback["SF_API_VERSION"] == "67.0"
    assert writeback["SF_SECRET_NAME"] == "hvi/salesforce"
    assert writeback["SF_MOCK"] == "false"
    assert writeback["HVI_SF_VIA_GATEWAY"] == "0"
    assert "SF_CLIENT_ID" not in writeback
    assert "SF_CLIENT_SECRET" not in writeback

    sync = _function_by_name(tmpl, "hvi-crm-sync")[
        "Properties"
    ]["Environment"]["Variables"]
    assert sync["FORM_ID"] == "arogya-sanjeevani-v1"
    assert sync["SLA_HOURS_FROM_PURCHASE"] == "48"
    assert sync["INSURER_NAME"] == "Meridian Health Cover"

    confirm = _function_by_name(tmpl, "hvi-send-confirmation")[
        "Properties"
    ]["Environment"]["Variables"]
    assert confirm["SES_FROM_EMAIL"]
    assert confirm["SES_REGION"] == "ap-south-1"
    assert confirm["DATA_REGION"] == "ap-south-1"


def test_send_confirmation_fails_when_ses_from_email_empty(make_app):
    with pytest.raises(ValueError) as excinfo:
        _synth(make_app, {"hvi:tts": "polly", "hvi:sesFromEmail": ""})
    assert "sesFromEmail" in str(excinfo.value)


# ---- Cognito ---------------------------------------------------------------
def test_cognito_user_pool(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    tmpl.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "UserPoolName": "hvi-web-pool",
            "AutoVerifiedAttributes": Match.array_with(["phone_number"]),
        },
    )


def test_cognito_spa_client_public_code_pkce(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    clients = tmpl.find_resources("AWS::Cognito::UserPoolClient")
    (client,) = clients.values()
    props = client["Properties"]
    assert props["ClientName"] == "hvi-web-spa"
    # No secret -> a public SPA client (GenerateSecret absent or false).
    assert not props.get("GenerateSecret", False)
    assert props["AllowedOAuthFlows"] == ["code"]
    assert set(props["AllowedOAuthScopes"]) == {"openid", "phone", "profile"}
    assert props["SupportedIdentityProviders"] == ["COGNITO"]


# ---- prompt cache (omnivoice only) -----------------------------------------
def test_prompt_cache_present_under_omnivoice(make_app, omnivoice_artifact):
    tmpl, _ = _synth(make_app, {"hvi:tts": "omnivoice"}, artifact=omnivoice_artifact)
    tmpl.resource_count_is("Custom::HviPromptCache", 1)


def test_prompt_cache_absent_under_polly(make_app):
    tmpl, _ = _synth(make_app, {"hvi:tts": "polly"})
    tmpl.resource_count_is("Custom::HviPromptCache", 0)


# ---- web media edge OFF (default) ------------------------------------------
def test_web_media_off_has_no_instances_sg_or_cloudfront(
    make_app, omnivoice_artifact
):
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    tmpl.resource_count_is("AWS::EC2::Instance", 0)
    tmpl.resource_count_is("AWS::CloudFront::Distribution", 0)
    # No coturn SG (no security groups created by the agent stack when off).
    for sg in tmpl.find_resources("AWS::EC2::SecurityGroup").values():
        assert sg["Properties"].get("GroupName") != "hvi-turn-sg"


def test_web_media_off_has_no_prompt_cache_grant(make_app, omnivoice_artifact):
    # The ReadPromptCache statement lives only on hvi-web-role, which is absent.
    tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    sids = {s.get("Sid") for s in _all_policy_statements(tmpl)}
    assert "ReadPromptCache" not in sids


# ---- web media edge ON ------------------------------------------------------
def _synth_web_on(make_app, artifact, extra=None):
    context = {"hvi:enableWebMedia": True, "hvi:cloudfrontOriginPrefixListId": _PREFIX_LIST_ID}
    context.update(extra or {})
    return _synth(make_app, context, artifact=artifact)


def test_web_media_on_creates_turn_and_web_instances(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact)
    tmpl.resource_count_is("AWS::EC2::Instance", 2)
    names = set()
    for inst in tmpl.find_resources("AWS::EC2::Instance").values():
        for tag in inst["Properties"].get("Tags", []):
            if tag["Key"] == "Name":
                names.add(tag["Value"])
    assert names == {"hvi-turn", "hvi-web"}


def test_web_media_on_cloudfront_present(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact)
    tmpl.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_web_sg_ingress_8095_from_prefix_list(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact)
    # A prefix-list peer renders as a standalone SecurityGroupIngress resource.
    ingress_rules = tmpl.find_resources("AWS::EC2::SecurityGroupIngress")
    assert any(
        p["Properties"].get("FromPort") == 8095
        and p["Properties"].get("ToPort") == 8095
        and p["Properties"].get("IpProtocol") == "tcp"
        and p["Properties"].get("SourcePrefixListId") == _PREFIX_LIST_ID
        for p in ingress_rules.values()
    ), ingress_rules


def test_turn_sg_opens_coturn_ports(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact)
    turn_sg = None
    for sg in tmpl.find_resources("AWS::EC2::SecurityGroup").values():
        if sg["Properties"].get("GroupName") == "hvi-turn-sg":
            turn_sg = sg
    assert turn_sg is not None
    ports = {
        (r.get("IpProtocol"), r.get("FromPort"), r.get("ToPort"))
        for r in turn_sg["Properties"]["SecurityGroupIngress"]
    }
    assert ("udp", 3478, 3478) in ports
    assert ("tcp", 3478, 3478) in ports
    assert ("udp", 49160, 49200) in ports
    assert ("tcp", 5349, 5349) in ports


def test_web_role_has_three_prompt_cache_statements(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact, {"hvi:tts": "omnivoice"})
    sids = {s.get("Sid") for s in _all_policy_statements(tmpl)}
    assert {"ReadPromptCache", "ListPromptCache", "WriteBackOnMiss"} <= sids


def test_web_role_retains_polly_deny(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact)
    deny = [
        s
        for s in _all_policy_statements(tmpl)
        if s.get("Sid") == "DenyOtherTTSVendors"
    ]
    assert deny, "Polly Deny statement missing"
    (stmt,) = deny
    assert stmt["Effect"] == "Deny"
    assert stmt["Action"] == "polly:*"


def test_web_instance_env_carries_the_full_contract(make_app, omnivoice_artifact):
    tmpl, _ = _synth_web_on(make_app, omnivoice_artifact, {"hvi:tts": "omnivoice"})
    web = None
    for inst in tmpl.find_resources("AWS::EC2::Instance").values():
        if any(
            t["Key"] == "Name" and t["Value"] == "hvi-web"
            for t in inst["Properties"].get("Tags", [])
        ):
            web = inst
    assert web is not None
    rendered = json.dumps(web)
    for fragment in (
        "HVI_WEB_AUTH=cognito",
        "HVI_COGNITO_USER_POOL_ID=",
        "HVI_COGNITO_APP_CLIENT_ID=",
        "HVI_WEB_ICE_SERVERS=",
        "HVI_WEB_PORT=8095",
        "STT_ENDPOINT=hvi-whisper",
        "STT_ENGINE=whisper",
        "HVI_TTS=omnivoice",
        "OMNIVOICE_ENDPOINT=hvi-omnivoice",
        "PROMPT_CACHE_BUCKET=hvi-artifacts-111122223333-ap-south-1",
    ):
        assert fragment in rendered, fragment
    # AudioSocket + PROMPT_CACHE_VOICE are deliberately unset.
    assert "AUDIOSOCKET_HOST" not in rendered
    assert "PROMPT_CACHE_VOICE" not in rendered


def test_web_media_on_requires_prefix_list_id(make_app, omnivoice_artifact):
    with pytest.raises(ValueError) as excinfo:
        _synth(make_app, {"hvi:enableWebMedia": True}, artifact=omnivoice_artifact)
    assert "cloudfrontOriginPrefixListId" in str(excinfo.value)


# ---- hvi-asterisk-role is NEVER referenced ---------------------------------
@pytest.mark.parametrize("web_on", [False, True])
def test_asterisk_role_never_referenced(make_app, omnivoice_artifact, web_on):
    if web_on:
        tmpl, _ = _synth_web_on(make_app, omnivoice_artifact)
    else:
        tmpl, _ = _synth(make_app, artifact=omnivoice_artifact)
    assert "hvi-asterisk-role" not in json.dumps(tmpl.to_json())
