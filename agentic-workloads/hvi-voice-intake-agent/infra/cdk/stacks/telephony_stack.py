"""``HviTelephonyStack`` — Stack 3a of the HVI telephony edge (``ap-south-1``).

Reproduces the active telephony edge from ``scripts/70-asterisk.sh``,
``71-asterisk-config.sh``, ``72-media-deploy.sh`` and ``33-outbound-lambda.sh``
as a single CDK stack, pinned to ``DATA_REGION=ap-south-1`` (barge-in loop
local, media in India for DPDP). It only instantiates behind
``hvi:enableTelephony=true``.

Builds:

* the **Asterisk SIP<->AudioSocket bridge** — ``hvi-asterisk`` EC2 (Ubuntu 24.04
  arm64, IMDSv2, SSM-managed, no SSH) in the existing Phase 2 public subnet, its
  Elastic IP (``RETAIN`` — MEDIUM-6), and the ``hvi-asterisk-sg`` scoped to
  Chime's published SIP/RTP ranges only (never ``0.0.0.0/0``);
* the **instance role** ``hvi-asterisk-role`` — modeled statement-for-statement
  on the Phase 2 ``hvi-web`` role (the explicit stand-in): compute-conditional
  ``InvokeOrchestrator``, ``InvokeSpeechEndpoints``, ``StoreCallRecording``, the
  ``polly:*`` Deny, and (omnivoice only) the three prompt-cache statements. No
  ``dynamodb:*``, no ``bedrock:InvokeModel`` (the dropped Voxtral grant);
* the **SSM config delivery** — an ``AWS::SSM::Document`` (command) carrying the
  three Asterisk SIP config files, applied by an ``AWS::SSM::Association``
  (declarative re-convergence, a documented deviation from the scripts' one-shot
  SendCommand). The media code + ``media.env`` are operator-delivered post-deploy
  via ``scripts/72`` (MEDIUM-3), not by the CDK;
* the **outbound dialler Lambda** ``hvi-outbound`` (``scripts/33``) — compliance
  gates + origination, with NO schedule (nothing dials on its own);
* the **``Custom::HviChimeVcRoute``** resource — the imperative
  termination/origination repoint of the (us-east-1) Voice Connector at the
  Asterisk EIP (no CFN coverage exists), living here so the EIP is a local token.

Consumes Phase 2 / anchor typed props: ``HviDataStack`` (VPC, public subnet,
VPC CIDR, lambda role, archive bucket name), ``HviInferenceStack`` (endpoint
names) and ``HviChimeStack`` (referenced VC id / outbound host / caller id).
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_ssm as ssm,
    custom_resources as cr,
)
from constructs import Construct

from stacks.assets import vendored_asset_code
from stacks.chime_stack import HviChimeStack
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack
from stacks.naming import Naming

# infra/cdk/stacks/telephony_stack.py -> parents: [stacks, cdk, infra, <root>]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CDK_ROOT = Path(__file__).resolve().parents[1]
_SEED_RESOURCES = _CDK_ROOT / "seed_resources"
_LAMBDAS_DIR = _REPO_ROOT / "lambdas"
_COMMON_DIR = _REPO_ROOT / "common"

# arm64 Ubuntu 24.04 — scripts/70 resolves this exact Canonical SSM parameter.
_UBUNTU_ARM64_SSM = (
    "/aws/service/canonical/ubuntu/server/24.04/stable/current/"
    "arm64/hvm/ebs-gp3/ami-id"
)

# Chime SDK Voice Connector network ranges for us-east-1 — VERBATIM from
# scripts/70-asterisk.sh (docs.aws.amazon.com/chime-sdk/.../network-config.html).
# These are static, service-regional published constants, NOT VC attributes.
_CHIME_SIP_CIDR = "3.80.16.0/23"
_CHIME_MEDIA_CIDRS = (
    "3.80.16.0/23",
    "52.55.62.128/25",
    "52.55.63.0/25",
    "34.212.95.128/25",
    "34.223.21.0/25",
)
_SIP_PORT = 5060
_RTP_START = 10000
_RTP_END = 10500  # narrow on purpose: one concurrent call needs 2 ports
_AUDIOSOCKET_HOST = "127.0.0.1"
_AUDIOSOCKET_PORT = 8090

_TELEPHONY_REGION = "us-east-1"
_CALLING_REGIONS = ["US", "IN"]

_PROJECT_TAG = ("Project", "health-voice-intake")


class HviTelephonyStack(Stack):
    """The Asterisk edge + outbound dialler + VC-route repoint (ap-south-1)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hvi: HviContext,
        data: HviDataStack,
        inference: HviInferenceStack,
        chime: HviChimeStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hvi = hvi
        self.data = data
        self.inference = inference
        self.chime = chime
        self.naming = Naming(account=hvi.account, region=hvi.region)

        self._build_asterisk()  # instance + EIP + SG + role
        self._build_ssm_config()  # SSM document + association
        self._build_outbound_lambda()  # hvi-outbound (no schedule)
        self._build_vc_route()  # Custom::HviChimeVcRoute

    # ------------------------------------------------------- asterisk bridge
    def _build_asterisk(self) -> None:
        n = self.naming

        # hvi-asterisk-sg: SIP signalling + RTP media from Chime's published
        # ranges ONLY — an open 0.0.0.0/0 5060 attracts toll-fraud in minutes.
        sg = ec2.SecurityGroup(
            self,
            "AsteriskSg",
            vpc=self.data.vpc,
            security_group_name=n.asterisk_sg,
            description="Asterisk SIP/RTP from Chime Voice Connector only",
            allow_all_outbound=True,
        )
        sg.add_ingress_rule(
            ec2.Peer.ipv4(_CHIME_SIP_CIDR),
            ec2.Port.udp(_SIP_PORT),
            "chime-sip-signalling",
        )
        for cidr in _CHIME_MEDIA_CIDRS:
            sg.add_ingress_rule(
                ec2.Peer.ipv4(cidr),
                ec2.Port.udp_range(_RTP_START, _RTP_END),
                "chime-media",
            )
        # No SSH, no 0.0.0.0/0, no 8090 rule (AudioSocket is loopback-internal).
        self.asterisk_sg = sg

        # hvi-asterisk-role: SSM-managed, no SSH. Mirrors the Phase 2 hvi-web
        # role (the explicit stand-in for this role) statement-for-statement.
        role = iam.Role(
            self,
            "AsteriskRole",
            role_name=n.asterisk_role,
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Asterisk SIP bridge + media runtime: SSM + agent/STT/TTS",
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        self._attach_asterisk_role_policies(role)
        self.asterisk_role = role

        ubuntu_arm64 = ec2.MachineImage.from_ssm_parameter(
            _UBUNTU_ARM64_SSM, os=ec2.OperatingSystemType.LINUX
        )
        instance = ec2.Instance(
            self,
            "AsteriskInstance",
            vpc=self.data.vpc,
            # The existing Phase 2 IGW-routed public subnet — this stack creates
            # no new subnet (MEDIUM-4). A hard capacity failure at deploy is a
            # loud operator retry via hvi:asteriskInstanceType.
            vpc_subnets=ec2.SubnetSelection(subnets=[self.data.public_subnet]),
            instance_type=ec2.InstanceType(self.hvi.asterisk_instance_type),
            machine_image=ubuntu_arm64,
            security_group=sg,
            role=role,
            require_imdsv2=True,
        )
        cdk.Tags.of(instance).add("Name", n.asterisk_instance)
        cdk.Tags.of(instance).add(*_PROJECT_TAG)
        self.asterisk_instance = instance

        # Stable EIP: the VC termination allow-list is CIDR-based, so a public
        # IP that changed on stop/start would silently break the trunk.
        eip = ec2.CfnEIP(
            self,
            "AsteriskEip",
            domain="vpc",
            instance_id=instance.instance_id,
            tags=[cdk.CfnTag(key="Name", value=n.asterisk_instance)],
        )
        # MEDIUM-6: RETAIN so the allow-listed /32 is never re-handed out by a
        # stack delete while the VC still trusts it (paired with the no-op
        # Delete on the VC-route custom resource).
        eip.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
        self.asterisk_eip = eip

    def _attach_asterisk_role_policies(self, role: iam.Role) -> None:
        n = self.naming
        acc = self.hvi.account
        region = self.hvi.region

        # The THREE scripts/54 prompt-cache statements, returned to their real
        # owner (the media box). Only under omnivoice is there a cache to read.
        if self.hvi.tts == "omnivoice":
            cache_arn = f"arn:aws:s3:::{n.artifacts_bucket}"
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="ReadPromptCache",
                    actions=["s3:GetObject"],
                    resources=[f"{cache_arn}/prompt-cache/*"],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="ListPromptCache",
                    actions=["s3:ListBucket"],
                    resources=[cache_arn],
                    conditions={
                        "StringLike": {"s3:prefix": ["prompt-cache/*"]}
                    },
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="WriteBackOnMiss",
                    actions=["s3:PutObject"],
                    resources=[f"{cache_arn}/prompt-cache/*"],
                )
            )

        # InvokeOrchestrator — compute-conditional (MEDIUM-1), so the role works
        # on the default Lambda path, not only agentcore-cr.
        if self.hvi.agent_compute == "lambda":
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="InvokeOrchestrator",
                    actions=["lambda:InvokeFunction"],
                    resources=[f"arn:aws:lambda:*:{acc}:function:{n.agent_fn}"],
                )
            )
        else:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="InvokeOrchestrator",
                    actions=["bedrock-agentcore:InvokeAgentRuntime"],
                    resources=["*"],
                )
            )

        # InvokeSpeechEndpoints — the whole STT path under Phase 1 (Whisper-only)
        # plus OmniVoice TTS. No bedrock:InvokeModel (the dropped Voxtral grant).
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeSpeechEndpoints",
                actions=["sagemaker:InvokeEndpoint"],
                resources=[f"arn:aws:sagemaker:{region}:{acc}:endpoint/hvi-*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="StoreCallRecording",
                actions=["s3:PutObject"],
                resources=[f"arn:aws:s3:::hvi-archive-{acc}-*/*"],
            )
        )
        # Retain the Polly Deny so the wrong voice cannot ship even by accident.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DenyOtherTTSVendors",
                effect=iam.Effect.DENY,
                actions=["polly:*"],
                resources=["*"],
            )
        )
        # Deliberately NO dynamodb:* — the one host with a public IP and an open
        # SIP port cannot read or write a single health record.

    # ---------------------------------------------------- ssm config delivery
    def _build_ssm_config(self) -> None:
        """Deliver the three Asterisk SIP config files over an SSM Association.

        A documented deviation from scripts/71's one-shot SendCommand (NIT-2):
        an Association re-converges declaratively when the document content
        changes, the CDK-native expression of "retune without replacing the
        instance". The media code + media.env are operator-delivered post-deploy
        via scripts/72 (MEDIUM-3) — not carried here.
        """
        n = self.naming

        document = ssm.CfnDocument(
            self,
            "AsteriskConfigDoc",
            name=n.asterisk_config_doc,
            document_type="Command",
            target_type="/AWS::EC2::Instance",
            content=self._asterisk_config_document(),
        )
        self.asterisk_config_doc = document

        association = ssm.CfnAssociation(
            self,
            "AsteriskConfigAssociation",
            name=document.ref,
            association_name=n.asterisk_config_doc,
            targets=[
                ssm.CfnAssociation.TargetProperty(
                    key="InstanceIds",
                    values=[self.asterisk_instance.instance_id],
                )
            ],
            parameters={
                # EIP + outbound host are deploy-time tokens; VPC CIDR is the
                # 10.42.0.0/16 literal from data.vpc_cidr_block (MEDIUM-1, never
                # data.vpc.vpc_cidr_block which raises at synth).
                "Eip": [self.asterisk_eip.ref],
                "VpcCidr": [self.data.vpc_cidr_block],
                "OutboundHost": [self.chime.voice_connector_outbound_host],
                "OutboundCallerId": [self.chime.outbound_caller_id],
            },
        )
        association.add_dependency(document)
        self.asterisk_config_association = association

    def _asterisk_config_document(self) -> dict:
        """The SSM command-document body: writes pjsip/extensions/rtp + verifies.

        Dynamic values arrive as SSM document parameters ({{ Eip }}, {{ VpcCidr }},
        {{ OutboundHost }}, {{ OutboundCallerId }}) resolved from the Association.
        The config reproduces scripts/71's load-bearing safety properties: the
        NAT-trap external_signaling/media_address + local_net, the two-endpoint
        from_user/callerid split, the IP-first identify matching Chime's CIDR, and
        the _[+0-9]. dialplan handing calls to AudioSocket on the loopback.
        """
        run_command = (
            self._pjsip_lines()
            + self._extensions_lines()
            + self._rtp_lines()
            + [
                "chown asterisk:asterisk /etc/asterisk/pjsip.conf "
                "/etc/asterisk/extensions.conf /etc/asterisk/rtp.conf",
                "systemctl restart asterisk",
                "sleep 4",
                "asterisk -rx 'pjsip show transports'",
                "asterisk -rx 'pjsip show endpoint chime' | head -20",
                "asterisk -rx 'dialplan show from-chime'",
            ]
        )
        return {
            "schemaVersion": "2.2",
            "description": (
                "Deliver the hvi-asterisk SIP trunk config (pjsip/extensions/rtp)"
                " and restart Asterisk."
            ),
            "parameters": {
                "Eip": {
                    "type": "String",
                    "description": "Asterisk Elastic IP (external SIP/media addr)",
                },
                "VpcCidr": {
                    "type": "String",
                    "description": "VPC CIDR for pjsip local_net (NAT trap)",
                },
                "OutboundHost": {
                    "type": "String",
                    "description": "Voice Connector OutboundHostName",
                },
                "OutboundCallerId": {
                    "type": "String",
                    "description": "DID presented on outbound calls",
                },
            },
            "mainSteps": [
                {
                    "action": "aws:runShellScript",
                    "name": "writeAsteriskConfig",
                    "inputs": {"runCommand": run_command},
                }
            ],
        }

    @staticmethod
    def _pjsip_lines() -> list[str]:
        # Quoted heredoc: the shell does not expand ${...}; SSM still substitutes
        # the {{ param }} placeholders at document-render time.
        return [
            "cat > /etc/asterisk/pjsip.conf <<'PJSIP'",
            "; Managed by HviTelephonyStack (SSM) - edits here are overwritten.",
            "[global]",
            "type=global",
            "; IP-based identification first: Chime trunks carry no credentials.",
            "endpoint_identifier_order=ip,username",
            "user_agent=hvi-intake",
            "",
            "[transport-udp]",
            "type=transport",
            "protocol=udp",
            "bind=0.0.0.0:5060",
            "; NAT trap: advertise the EIP, treat the VPC CIDR as internal.",
            "external_signaling_address={{ Eip }}",
            "external_media_address={{ Eip }}",
            "local_net={{ VpcCidr }}",
            "",
            "[chime-aor]",
            "type=aor",
            "contact=sip:{{ OutboundHost }}:5060",
            "qualify_frequency=60",
            "",
            "[chime]",
            "type=endpoint",
            "context=from-chime",
            "aors=chime-aor",
            "disallow=all",
            "allow=ulaw,alaw",
            "direct_media=no",
            "rtp_symmetric=yes",
            "force_rport=yes",
            "rewrite_contact=yes",
            "dtmf_mode=rfc4733",
            "from_domain={{ Eip }}",
            "; from_user ONLY (never callerid) — callerid applies both",
            "; directions and would overwrite inbound caller id (disclosure bug).",
            "from_user={{ OutboundCallerId }}",
            "",
            "[chime-identify]",
            "type=identify",
            "endpoint=chime",
            "; Chime SDK Voice Connector SIP signalling range for us-east-1.",
            "match=3.80.16.0/23",
            "",
            "; OUTBOUND-ONLY endpoint — carries callerid, no identify section so",
            "; no inbound call can ever select it.",
            "[chime-out-aor]",
            "type=aor",
            "contact=sip:{{ OutboundHost }}:5060",
            "qualify_frequency=60",
            "",
            "[chime-out]",
            "type=endpoint",
            "context=from-chime",
            "aors=chime-out-aor",
            "disallow=all",
            "allow=ulaw,alaw",
            "direct_media=no",
            "rtp_symmetric=yes",
            "force_rport=yes",
            "rewrite_contact=yes",
            "dtmf_mode=rfc4733",
            "from_domain={{ Eip }}",
            "from_user={{ OutboundCallerId }}",
            "callerid=Meridian Health Cover <{{ OutboundCallerId }}>",
            "PJSIP",
        ]

    @staticmethod
    def _extensions_lines() -> list[str]:
        return [
            "cat > /etc/asterisk/extensions.conf <<'DIALPLAN'",
            "; Managed by HviTelephonyStack (SSM) - edits here are overwritten.",
            "[globals]",
            f"AUDIOSOCKET_HOST={_AUDIOSOCKET_HOST}",
            f"AUDIOSOCKET_PORT={_AUDIOSOCKET_PORT}",
            "",
            "; _[+0-9]. (not _X.) so E.164 numbers with a leading + match.",
            "[from-chime]",
            "exten => _[+0-9].,1,NoOp(hvi inbound: ${CALLERID(num)} -> ${EXTEN})",
            " same => n,Set(CHANNEL(hangup_handler_push)=hvi-hangup,s,1)",
            " same => n,Answer()",
            " same => n,Set(CHANNEL(audioreadformat)=slin)",
            " same => n,Set(CHANNEL(audiowriteformat)=slin)",
            " same => n,Set(CALLUUID=${SHELL(uuidgen | tr -d '\\n')})",
            " same => n,System(printf '%s' \"${CALLERID(num)}\" >"
            " /var/lib/hvi/calls/${CALLUUID})",
            # Loopback AudioSocket target — co-located media loop, no SG rule.
            " same => n,AudioSocket(${CALLUUID},127.0.0.1:8090)",
            " same => n,Hangup()",
            "",
            "[hvi-outbound]",
            "exten => _[+0-9].,1,NoOp(hvi outbound answered by ${EXTEN})",
            " same => n,Set(CHANNEL(hangup_handler_push)=hvi-hangup,s,1)",
            " same => n,Answer()",
            " same => n,Set(CHANNEL(audioreadformat)=slin)",
            " same => n,Set(CHANNEL(audiowriteformat)=slin)",
            " same => n,Set(CALLUUID=${SHELL(uuidgen | tr -d '\\n')})",
            " same => n,System(mv /var/lib/hvi/pending/${EXTEN}"
            " /var/lib/hvi/calls/${CALLUUID})",
            " same => n,AudioSocket(${CALLUUID},127.0.0.1:8090)",
            " same => n,Hangup()",
            "",
            "[hvi-hangup]",
            "exten => s,1,NoOp(hvi hangup: ${CALLUUID} cause=${HANGUPCAUSE})",
            " same => n,System(rm -f /var/lib/hvi/calls/${CALLUUID})",
            " same => n,Return()",
            "DIALPLAN",
        ]

    @staticmethod
    def _rtp_lines() -> list[str]:
        # Port range must match the SG (narrow on purpose: attack surface).
        return [
            "cat > /etc/asterisk/rtp.conf <<'RTPCONF'",
            "; Managed by HviTelephonyStack (SSM) - edits here are overwritten.",
            "[general]",
            "rtpstart=10000",
            "rtpend=10500",
            "strictrtp=yes",
            "RTPCONF",
        ]

    # ------------------------------------------------------- outbound dialler
    def _build_outbound_lambda(self) -> None:
        """hvi-outbound (scripts/33): compliance gates + origination, NO schedule."""
        n = self.naming
        self.outbound_fn = lambda_.Function(
            self,
            "OutboundFn",
            function_name=n.outbound_fn,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.lambda_handler",
            code=vendored_asset_code(
                _LAMBDAS_DIR / "outbound",
                extra_files={"hvi_state.py": str(_COMMON_DIR / "hvi_state.py")},
            ),
            timeout=Duration.seconds(120),
            memory_size=512,
            # hvi-lambda-role (Data) already carries OriginateThroughAsterisk
            # (ssm:SendCommand) + OriginateReadResult. NOT VPC-attached.
            role=self.data.lambda_role,
            environment={
                "DATA_REGION": self.hvi.region,
                "TELEPHONY_REGION": _TELEPHONY_REGION,
                "PREFIX": "hvi",
                "OUTBOUND_CALLER_ID": self.chime.outbound_caller_id,
                # Typed prop — the Asterisk instance id, no name re-lookup.
                "ASTERISK_INSTANCE_ID": self.asterisk_instance.instance_id,
                "FORM_ID": "arogya-sanjeevani-v1",
                "TRAI_CALL_START_HOUR": "9",
                "TRAI_CALL_END_HOUR": "21",
                "TRAI_MAX_ATTEMPTS_PER_DAY": "3",
                "SLA_HOURS_FROM_PURCHASE": "48",
                # false ON PURPOSE: an out-of-window demo call requires the
                # audited ALLOW_WINDOW_OVERRIDE, written to hvi-gate-audit.
                "ALLOW_WINDOW_OVERRIDE": "false",
            },
        )
        # No AWS::Events::Rule / AWS::Scheduler::Schedule — nothing dials itself.

    # --------------------------------------------------------- VC-route CR
    def _build_vc_route(self) -> None:
        """Custom::HviChimeVcRoute — repoint the us-east-1 VC at the Asterisk EIP.

        Lives in this ap-south-1 stack so the EIP is a local token; the handler
        reaches us-east-1 via boto3 region_name — no cross-region CFN reference.
        """
        acc = self.hvi.account
        vc_id = self.chime.voice_connector_id
        vc_arn = f"arn:aws:chime:{_TELEPHONY_REGION}:{acc}:vc/{vc_id}"

        fn = lambda_.Function(
            self,
            "ChimeVcRouteFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(_SEED_RESOURCES / "chime_vc_route"),
            timeout=Duration.minutes(5),
            memory_size=256,
            environment={"TELEPHONY_REGION": _TELEPHONY_REGION},
        )
        # Least-privilege: the two writes + the get/list reads needed to be
        # idempotent, scoped to the one VC ARN. No phone-number / VC-create.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="RepointVoiceConnector",
                actions=[
                    "chime-sdk-voice:PutVoiceConnectorTermination",
                    "chime-sdk-voice:PutVoiceConnectorOrigination",
                    "chime-sdk-voice:GetVoiceConnector",
                    "chime-sdk-voice:GetVoiceConnectorTermination",
                    "chime-sdk-voice:GetVoiceConnectorOrigination",
                    "chime-sdk-voice:ListVoiceConnectors",
                ],
                resources=[vc_arn],
            )
        )

        provider = cr.Provider(
            self, "ChimeVcRouteProvider", on_event_handler=fn
        )
        self.vc_route = cdk.CustomResource(
            self,
            "ChimeVcRoute",
            service_token=provider.service_token,
            resource_type="Custom::HviChimeVcRoute",
            properties={
                "VoiceConnectorId": vc_id,
                "AsteriskEip": self.asterisk_eip.ref,
                "TelephonyRegion": _TELEPHONY_REGION,
                "CallingRegions": _CALLING_REGIONS,
            },
        )
