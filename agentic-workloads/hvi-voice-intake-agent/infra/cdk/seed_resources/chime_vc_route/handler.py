"""Chime Voice-Connector routing custom-resource handler (Telephony stack).

Reproduces ``scripts/70-asterisk.sh``'s two imperative ``chime-sdk-voice``
calls that repoint the **existing** Voice Connector at the CDK-created Asterisk
Elastic IP — there is no CloudFormation resource for VC termination/origination
(``aws-cdk-lib`` only ships a bare ``CfnVoiceConnector``), so this is a custom
resource, same shape as ``seed_resources/agentcore_runtime`` /
``seed_resources/sagemaker_endpoint``: a ``handler(event, _context)`` entry
dispatching on ``event["RequestType"]`` with ``boto3`` imported lazily so synth
never needs the SDK.

The VC lives in ``us-east-1`` (``TELEPHONY_REGION``) while this resource lives
in the ap-south-1 telephony stack (so the EIP is a local token); the handler
reaches across with ``region_name=us-east-1``, exactly as ``scripts/70`` runs
in the data region and targets ``--region "$TELEPHONY_REGION"``.

HARD SAFETY: ``Delete`` is a deliberate **no-op** — it never clears the live
production trunk's termination/origination route (removing it would break any
real inbound path). This is paired with ``RemovalPolicy.RETAIN`` on the Asterisk
EIP so the allow-listed address is never re-handed out (MEDIUM-6). This file is
only *staged* at synth; it executes exclusively at deploy time with real
credentials. The design and the test suite make no AWS calls.
"""

from __future__ import annotations

import os


def _client(region: str):
    import boto3  # imported lazily so staging never needs the SDK

    return boto3.client("chime-sdk-voice", region_name=region)


def _physical_id(event: dict) -> str:
    props = event.get("ResourceProperties") or {}
    vc_id = props.get("VoiceConnectorId") or "hvi-vc"
    return event.get("PhysicalResourceId") or f"hvi-chime-vc-route-{vc_id}"


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})

    if request_type == "Delete":
        # Deliberate no-op: never clear a live production trunk's route on a
        # stack delete (MEDIUM-6). The EIP RETAIN keeps the trusted address.
        print(
            "Delete: leaving the Voice Connector termination/origination "
            "intact (live production trunk; no-op by design)"
        )
        return {"PhysicalResourceId": _physical_id(event)}

    region = props.get("TelephonyRegion") or os.environ.get(
        "TELEPHONY_REGION", "us-east-1"
    )
    vc_id = props["VoiceConnectorId"]
    eip = props["AsteriskEip"]
    calling_regions = props.get("CallingRegions") or ["US", "IN"]

    client = _client(region)

    # scripts/70: put-voice-connector-termination (allow-list the Asterisk EIP).
    client.put_voice_connector_termination(
        VoiceConnectorId=vc_id,
        Termination={
            "CpsLimit": 1,
            "CidrAllowedList": [f"{eip}/32"],
            "CallingRegions": calling_regions,
            "Disabled": False,
        },
    )

    # scripts/70: put-voice-connector-origination (route inbound SIP -> EIP).
    client.put_voice_connector_origination(
        VoiceConnectorId=vc_id,
        Origination={
            "Routes": [
                {
                    "Host": eip,
                    "Port": 5060,
                    "Protocol": "UDP",
                    "Priority": 1,
                    "Weight": 1,
                }
            ],
            "Disabled": False,
        },
    )

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"VoiceConnectorId": vc_id, "AsteriskEip": eip},
    }
