"""Typed, validated, fail-closed accessors for the ``hvi:*`` CDK context flags.

Every external input the stacks accept arrives as CDK context (``cdk.json``
defaults, overridable with ``-c hvi:flag=value``). This module is the single
place those values are read, type-coerced, defaulted, and validated. Validation
is **fail-closed**: an unknown enum value or an empty required string raises at
synth time rather than silently deploying a misconfigured stack — matching the
repo's "fail loud, no silent fallback" invariant.
"""

from __future__ import annotations

from dataclasses import dataclass

from constructs import Construct

# ---- Grounding defaults (config/env.sh) -----------------------------------
DEFAULT_ACCOUNT = "111122223333"
DEFAULT_REGION = "ap-south-1"
#: In-region, DPDP-clean reasoning model (BEDROCK_MODEL_ID in config/env.sh).
DEFAULT_BEDROCK_MODEL_ID = "mistral.mistral-large-3-675b-instruct"
#: scripts/26-opensearch.sh reasoning: r6g (not burstable t3) for the live path.
DEFAULT_OPENSEARCH_INSTANCE_TYPE = "r6g.large.search"
#: Pinned, known-good engine version; CDK must not query list-versions at synth
#: (that would be an AWS call), so the "newest" lookup is replaced by a pin.
DEFAULT_OPENSEARCH_ENGINE = "OpenSearch_2.17"
#: Local dev callback for the public SPA client when none is supplied.
DEFAULT_WEB_CALLBACK_URL = "http://localhost:8095"
#: Pinned live Voice Connector in the demo account (config/env.sh). Enabling
#: telephony in any other account REQUIRES -c hvi:voiceConnectorId=<that VC>.
DEFAULT_VOICE_CONNECTOR_ID = "abcdefghij0123456789"
#: Local DID assigned to the Voice Connector (config/env.sh OUTBOUND_CALLER_ID).
DEFAULT_OUTBOUND_CALLER_ID = "+12025550143"
#: Single pinned Asterisk instance type (CFN cannot iterate the script's ladder).
DEFAULT_ASTERISK_INSTANCE_TYPE = "t4g.medium"

# ---- Enum domains ---------------------------------------------------------
TTS_CHOICES = ("omnivoice", "polly")
AGENT_COMPUTE_CHOICES = ("lambda", "agentcore-cr")

# ---- Context keys ---------------------------------------------------------
KEY_ENABLE_NEPTUNE = "hvi:enableNeptune"
KEY_TTS = "hvi:tts"
KEY_ENABLE_WEB_MEDIA = "hvi:enableWebMedia"
KEY_SKIP_NAT = "hvi:skipNat"
KEY_AGENT_COMPUTE = "hvi:agentCompute"
KEY_BEDROCK_MODEL_ID = "hvi:bedrockModelId"
KEY_OPENSEARCH_INSTANCE_TYPE = "hvi:opensearchInstanceType"
KEY_OPENSEARCH_ENGINE = "hvi:opensearchEngine"
KEY_WEB_CALLBACK_URL = "hvi:webCallbackUrl"
KEY_CLOUDFRONT_PREFIX_LIST_ID = "hvi:cloudfrontOriginPrefixListId"
KEY_ACCOUNT = "hvi:account"
KEY_REGION = "hvi:region"
# ---- Telephony (Phase 3, opt-in) ------------------------------------------
KEY_ENABLE_TELEPHONY = "hvi:enableTelephony"
KEY_VOICE_CONNECTOR_ID = "hvi:voiceConnectorId"
KEY_VOICE_CONNECTOR_OUTBOUND_HOST = "hvi:voiceConnectorOutboundHost"
KEY_OUTBOUND_CALLER_ID = "hvi:outboundCallerId"
KEY_ASTERISK_INSTANCE_TYPE = "hvi:asteriskInstanceType"
KEY_CREATE_VOICE_CONNECTOR = "hvi:createVoiceConnector"
KEY_TATA_SIP_HOSTS = "hvi:tataSipHosts"
KEY_TATA_SIP_DID = "hvi:tataSipDid"


def _as_bool(value: object, *, key: str) -> bool:
    """Coerce a context value to bool, accepting JSON bools and CLI strings.

    ``-c hvi:flag=true`` yields the string ``"true"``; a ``cdk.json`` default
    yields a real bool. Anything else fails closed.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    raise ValueError(
        f"{key} must be a boolean (true/false), got {value!r}"
    )


def _as_enum(value: object, choices: tuple[str, ...], *, key: str) -> str:
    """Validate an enum string against ``choices``; fail closed on unknowns."""
    if not isinstance(value, str) or value not in choices:
        raise ValueError(
            f"{key} must be one of {choices}, got {value!r}"
        )
    return value


@dataclass(frozen=True)
class HviContext:
    """Resolved, validated ``hvi:*`` configuration for a synth."""

    account: str
    region: str
    enable_neptune: bool
    tts: str
    enable_web_media: bool
    skip_nat: bool
    agent_compute: str
    bedrock_model_id: str
    opensearch_instance_type: str
    opensearch_engine: str
    web_callback_url: str
    cloudfront_origin_prefix_list_id: str | None
    # ---- Telephony (Phase 3, opt-in) --------------------------------------
    enable_telephony: bool
    voice_connector_id: str
    voice_connector_outbound_host: str
    outbound_caller_id: str
    asterisk_instance_type: str
    create_voice_connector: bool
    tata_sip_hosts: str
    tata_sip_did: str

    @classmethod
    def from_scope(cls, scope: Construct) -> "HviContext":
        """Read and validate the context from any construct's node.

        Pass the ``App`` (or any stack) — context resolves through the node
        tree, so ``App(context={...})`` in tests and the ``cdk.json`` block in
        production both feed this.
        """
        node = scope.node

        def ctx(key: str, default: object) -> object:
            value = node.try_get_context(key)
            return default if value is None else value

        bedrock_model_id = str(ctx(KEY_BEDROCK_MODEL_ID, DEFAULT_BEDROCK_MODEL_ID))
        if not bedrock_model_id.strip():
            raise ValueError(f"{KEY_BEDROCK_MODEL_ID} must not be empty")

        opensearch_engine = str(ctx(KEY_OPENSEARCH_ENGINE, DEFAULT_OPENSEARCH_ENGINE))
        if not opensearch_engine.strip():
            raise ValueError(f"{KEY_OPENSEARCH_ENGINE} must not be empty")

        prefix_list = node.try_get_context(KEY_CLOUDFRONT_PREFIX_LIST_ID)

        # ---- Telephony (Phase 3, opt-in) ----------------------------------
        enable_telephony = _as_bool(
            ctx(KEY_ENABLE_TELEPHONY, False), key=KEY_ENABLE_TELEPHONY
        )

        voice_connector_id = str(
            ctx(KEY_VOICE_CONNECTOR_ID, DEFAULT_VOICE_CONNECTOR_ID)
        )
        if not voice_connector_id.strip():
            raise ValueError(f"{KEY_VOICE_CONNECTOR_ID} must not be empty")

        # Required-non-empty ONLY when telephony is enabled: the Asterisk SIP
        # config cannot be rendered without the VC's OutboundHostName, and
        # reading it from the VC at synth would be a forbidden AWS call. No
        # cdk.json default on purpose — fail closed rather than ship a wrong one.
        voice_connector_outbound_host = str(
            ctx(KEY_VOICE_CONNECTOR_OUTBOUND_HOST, "")
        )
        if enable_telephony and not voice_connector_outbound_host.strip():
            raise ValueError(
                f"{KEY_VOICE_CONNECTOR_OUTBOUND_HOST} must be set (non-empty) "
                "when hvi:enableTelephony=true — supply the pinned Voice "
                "Connector's OutboundHostName (a get-voice-connector read at "
                "synth is a forbidden AWS call)"
            )

        # Defaulted to the live DID; reject an explicitly-empty override, which
        # scripts/71/33 hard-refuse (an empty From makes Chime answer 486).
        outbound_caller_id = str(
            ctx(KEY_OUTBOUND_CALLER_ID, DEFAULT_OUTBOUND_CALLER_ID)
        )
        if not outbound_caller_id.strip():
            raise ValueError(
                f"{KEY_OUTBOUND_CALLER_ID} must not be empty — an empty From "
                "makes Chime answer every outbound INVITE with 486 Busy Here"
            )

        return cls(
            account=str(ctx(KEY_ACCOUNT, DEFAULT_ACCOUNT)),
            region=str(ctx(KEY_REGION, DEFAULT_REGION)),
            enable_neptune=_as_bool(
                ctx(KEY_ENABLE_NEPTUNE, False), key=KEY_ENABLE_NEPTUNE
            ),
            tts=_as_enum(ctx(KEY_TTS, "polly"), TTS_CHOICES, key=KEY_TTS),
            enable_web_media=_as_bool(
                ctx(KEY_ENABLE_WEB_MEDIA, False), key=KEY_ENABLE_WEB_MEDIA
            ),
            skip_nat=_as_bool(ctx(KEY_SKIP_NAT, False), key=KEY_SKIP_NAT),
            agent_compute=_as_enum(
                ctx(KEY_AGENT_COMPUTE, "lambda"),
                AGENT_COMPUTE_CHOICES,
                key=KEY_AGENT_COMPUTE,
            ),
            bedrock_model_id=bedrock_model_id,
            opensearch_instance_type=str(
                ctx(KEY_OPENSEARCH_INSTANCE_TYPE, DEFAULT_OPENSEARCH_INSTANCE_TYPE)
            ),
            opensearch_engine=opensearch_engine,
            web_callback_url=str(ctx(KEY_WEB_CALLBACK_URL, DEFAULT_WEB_CALLBACK_URL)),
            cloudfront_origin_prefix_list_id=(
                None if prefix_list is None else str(prefix_list)
            ),
            enable_telephony=enable_telephony,
            voice_connector_id=voice_connector_id,
            voice_connector_outbound_host=voice_connector_outbound_host,
            outbound_caller_id=outbound_caller_id,
            asterisk_instance_type=str(
                ctx(KEY_ASTERISK_INSTANCE_TYPE, DEFAULT_ASTERISK_INSTANCE_TYPE)
            ),
            create_voice_connector=_as_bool(
                ctx(KEY_CREATE_VOICE_CONNECTOR, False),
                key=KEY_CREATE_VOICE_CONNECTOR,
            ),
            tata_sip_hosts=str(ctx(KEY_TATA_SIP_HOSTS, "")),
            tata_sip_did=str(ctx(KEY_TATA_SIP_DID, "")),
        )
