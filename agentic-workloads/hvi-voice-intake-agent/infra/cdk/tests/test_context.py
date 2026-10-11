"""Fail-closed context validation tests for ``stacks.context.HviContext``.

These exercise the documented defaults and the three required raises (unknown
``hvi:tts``, unknown ``hvi:agentCompute``, empty ``hvi:bedrockModelId``) purely
in-process — no synth of any stack, no AWS calls.
"""

from __future__ import annotations

import pytest

from stacks.context import (
    DEFAULT_ACCOUNT,
    DEFAULT_BEDROCK_MODEL_ID,
    DEFAULT_OPENSEARCH_ENGINE,
    DEFAULT_OPENSEARCH_INSTANCE_TYPE,
    DEFAULT_REGION,
    DEFAULT_WEB_CALLBACK_URL,
    HviContext,
)


def test_defaults_match_grounding(make_app):
    """With no context supplied, every accessor returns its documented default."""
    hvi = HviContext.from_scope(make_app())

    assert hvi.account == DEFAULT_ACCOUNT == "111122223333"
    assert hvi.region == DEFAULT_REGION == "ap-south-1"
    assert hvi.enable_neptune is False
    assert hvi.tts == "polly"
    assert hvi.enable_web_media is False
    assert hvi.skip_nat is False
    assert hvi.agent_compute == "lambda"
    assert hvi.bedrock_model_id == DEFAULT_BEDROCK_MODEL_ID
    assert hvi.opensearch_instance_type == DEFAULT_OPENSEARCH_INSTANCE_TYPE
    assert hvi.opensearch_engine == DEFAULT_OPENSEARCH_ENGINE
    assert hvi.web_callback_url == DEFAULT_WEB_CALLBACK_URL
    assert hvi.cloudfront_origin_prefix_list_id is None


def test_overrides_are_read_and_typed(make_app):
    """Context overrides flow through with correct types/coercion."""
    hvi = HviContext.from_scope(
        make_app(
            {
                "hvi:enableNeptune": True,
                "hvi:tts": "polly",
                "hvi:enableWebMedia": "true",  # CLI-style string coerces to bool
                "hvi:skipNat": "false",
                "hvi:agentCompute": "agentcore-cr",
                "hvi:bedrockModelId": "deepseek.v3.2",
                "hvi:opensearchInstanceType": "r6g.xlarge.search",
                "hvi:opensearchEngine": "OpenSearch_2.19",
                "hvi:webCallbackUrl": "https://demo.example.com/callback",
                "hvi:cloudfrontOriginPrefixListId": "pl-0123456789abcdef0",
                "hvi:account": "111122223333",
                "hvi:region": "ap-south-1",
            }
        )
    )

    assert hvi.enable_neptune is True
    assert hvi.tts == "polly"
    assert hvi.enable_web_media is True
    assert hvi.skip_nat is False
    assert hvi.agent_compute == "agentcore-cr"
    assert hvi.bedrock_model_id == "deepseek.v3.2"
    assert hvi.opensearch_instance_type == "r6g.xlarge.search"
    assert hvi.opensearch_engine == "OpenSearch_2.19"
    assert hvi.web_callback_url == "https://demo.example.com/callback"
    assert hvi.cloudfront_origin_prefix_list_id == "pl-0123456789abcdef0"
    assert hvi.account == "111122223333"


def test_unknown_tts_raises(make_app):
    with pytest.raises(ValueError, match="hvi:tts"):
        HviContext.from_scope(make_app({"hvi:tts": "elevenlabs"}))


def test_unknown_agent_compute_raises(make_app):
    with pytest.raises(ValueError, match="hvi:agentCompute"):
        HviContext.from_scope(make_app({"hvi:agentCompute": "fargate"}))


def test_empty_bedrock_model_id_raises(make_app):
    with pytest.raises(ValueError, match="hvi:bedrockModelId"):
        HviContext.from_scope(make_app({"hvi:bedrockModelId": "   "}))


def test_invalid_bool_raises(make_app):
    with pytest.raises(ValueError, match="hvi:enableNeptune"):
        HviContext.from_scope(make_app({"hvi:enableNeptune": "maybe"}))


# ---- Telephony (Phase 3, opt-in) ------------------------------------------
def test_telephony_defaults(make_app):
    """With no context, telephony is off and the pinned values carry through."""
    hvi = HviContext.from_scope(make_app())
    assert hvi.enable_telephony is False
    assert hvi.voice_connector_id == "abcdefghij0123456789"
    assert hvi.voice_connector_outbound_host == ""  # no default, not required off
    assert hvi.outbound_caller_id == "+12025550143"
    assert hvi.asterisk_instance_type == "t4g.medium"
    assert hvi.create_voice_connector is False
    assert hvi.tata_sip_hosts == ""
    assert hvi.tata_sip_did == ""


def test_enable_telephony_non_bool_raises(make_app):
    with pytest.raises(ValueError, match="hvi:enableTelephony"):
        HviContext.from_scope(make_app({"hvi:enableTelephony": "maybe"}))


def test_telephony_enabled_requires_outbound_host(make_app):
    with pytest.raises(ValueError, match="hvi:voiceConnectorOutboundHost"):
        HviContext.from_scope(make_app({"hvi:enableTelephony": True}))


def test_telephony_enabled_with_outbound_host_resolves(make_app):
    hvi = HviContext.from_scope(
        make_app(
            {
                "hvi:enableTelephony": True,
                "hvi:voiceConnectorOutboundHost": "example.voiceconnector.chime.aws",
            }
        )
    )
    assert hvi.enable_telephony is True
    assert (
        hvi.voice_connector_outbound_host
        == "example.voiceconnector.chime.aws"
    )


def test_empty_outbound_caller_id_override_raises(make_app):
    with pytest.raises(ValueError, match="hvi:outboundCallerId"):
        HviContext.from_scope(make_app({"hvi:outboundCallerId": ""}))


def test_empty_voice_connector_id_override_raises(make_app):
    with pytest.raises(ValueError, match="hvi:voiceConnectorId"):
        HviContext.from_scope(make_app({"hvi:voiceConnectorId": "  "}))
