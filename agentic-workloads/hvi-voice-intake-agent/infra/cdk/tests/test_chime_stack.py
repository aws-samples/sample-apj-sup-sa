"""In-process synthesis tests for ``HviChimeStack`` (Phase 3, us-east-1 anchor).

Each test builds an ``App(context={...})`` and asserts on
``Template.from_stack(chime)``. No ``cdk`` CLI, no Docker, no AWS calls. The
default (live-VC) path creates **zero** Chime resources — the safety assertion
that the live production Voice Connector is referenced, never recreated.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Template

from stacks.chime_stack import HviChimeStack
from stacks.context import HviContext

_OUTBOUND_HOST = "example.voiceconnector.chime.aws"


def _synth(make_app, context=None):
    base = {
        "hvi:enableTelephony": True,
        "hvi:voiceConnectorOutboundHost": _OUTBOUND_HOST,
    }
    base.update(context or {})
    app = make_app(base)
    hvi = HviContext.from_scope(app)
    chime = HviChimeStack(
        app,
        "HviChimeStack",
        hvi=hvi,
        env=cdk.Environment(account=hvi.account, region="us-east-1"),
    )
    return Template.from_stack(chime), chime


def test_region_is_us_east_1(make_app):
    _, chime = _synth(make_app)
    assert chime.region == "us-east-1"


def test_default_path_creates_no_voice_connector(make_app):
    # Safety: the LIVE production VC is referenced, never recreated.
    tmpl, chime = _synth(make_app)
    tmpl.resource_count_is("AWS::Chime::VoiceConnector", 0)
    assert chime.voice_connector is None


def test_default_path_exposes_referenced_ids(make_app):
    _, chime = _synth(make_app)
    assert chime.voice_connector_id == "abcdefghij0123456789"
    assert chime.voice_connector_outbound_host == _OUTBOUND_HOST
    assert chime.outbound_caller_id == "+12025550143"
    assert chime.tata_sip_hosts == ""
    assert chime.tata_sip_did == ""


def test_create_voice_connector_path(make_app):
    tmpl, chime = _synth(make_app, {"hvi:createVoiceConnector": True})
    tmpl.resource_count_is("AWS::Chime::VoiceConnector", 1)
    tmpl.has_resource_properties(
        "AWS::Chime::VoiceConnector",
        {"RequireEncryption": True, "AwsRegion": "us-east-1"},
    )
    assert chime.voice_connector is not None
    # The fresh id is surfaced as a CfnOutput on THIS stack (never cross-region).
    tmpl.find_outputs("VoiceConnectorIdOutput")


def test_tata_values_carry_when_supplied(make_app):
    _, chime = _synth(
        make_app,
        {
            "hvi:tataSipHosts": "203.0.113.10/32,203.0.113.11/32",
            "hvi:tataSipDid": "1600XXXXXXX",
        },
    )
    assert chime.tata_sip_hosts == "203.0.113.10/32,203.0.113.11/32"
    assert chime.tata_sip_did == "1600XXXXXXX"
