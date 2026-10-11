"""``HviChimeStack`` — Stack 3b of the HVI telephony edge (``us-east-1``).

The reference/prerequisite anchor for the telephony edge, pinned to
``TELEPHONY_REGION=us-east-1`` because Amazon Chime SDK Voice Connector is not
available in ``ap-south-1`` (``config/env.sh`` records the confirmed region
list). It only ever instantiates behind ``hvi:enableTelephony=true``.

In the default (pre-existing-VC) case this stack **creates no Chime resources**
— the Voice Connector ``abcdefghij0123456789`` (placeholder) and its phone
numbers are provisioned out of band and can ring real phones, so they are
**referenced** as manual prerequisites, never recreated. It simply carries the referenced ids as typed
attributes for :class:`HviTelephonyStack` to consume.

Only under the opt-in ``hvi:createVoiceConnector=true`` (default off, **non-prod
only**) does it create a single bare ``CfnVoiceConnector`` and emit its id as a
``CfnOutput`` **on this stack only** — never handed to the ap-south-1 telephony
stack as a construct reference (that would be a forbidden cross-region
CloudFormation token requiring ``crossRegionReferences``; MEDIUM-5). The
operator re-supplies the new id via ``-c hvi:voiceConnectorId=<new-id>`` on the
next synth, after which the route resource consumes it as a plain context
constant exactly like the default path.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_chime as chime,
)
from constructs import Construct

from stacks.context import HviContext
from stacks.naming import PREFIX, Naming


class HviChimeStack(Stack):
    """The us-east-1 Chime anchor: references the live VC, or (non-prod) one."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hvi: HviContext,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hvi = hvi
        self.naming = Naming(account=hvi.account, region=hvi.region)

        # Referenced, manually-provisioned ids carried as typed attributes for
        # HviTelephonyStack — never a name re-lookup, never a cross-region token.
        self.voice_connector_id = hvi.voice_connector_id
        self.voice_connector_outbound_host = hvi.voice_connector_outbound_host
        self.outbound_caller_id = hvi.outbound_caller_id
        self.tata_sip_hosts = hvi.tata_sip_hosts
        self.tata_sip_did = hvi.tata_sip_did

        # Typed handle (None on the default live-VC path).
        self.voice_connector: chime.CfnVoiceConnector | None = None

        if hvi.create_voice_connector:
            self._build_fresh_voice_connector()
        # Default path: no Chime resources — the live VC is referenced only.

    def _build_fresh_voice_connector(self) -> None:
        """Non-prod bootstrap: create a bare VC and surface its id as an output.

        The id is emitted as a ``CfnOutput`` on THIS stack only; the operator
        re-supplies it via ``-c hvi:voiceConnectorId=`` on the next synth, so no
        cross-region CloudFormation reference is ever created (MEDIUM-5).
        """
        vc = chime.CfnVoiceConnector(
            self,
            "VoiceConnector",
            name=f"{PREFIX}-voice-connector",
            require_encryption=True,
            aws_region="us-east-1",
        )
        self.voice_connector = vc
        cdk.CfnOutput(
            self,
            "VoiceConnectorIdOutput",
            value=vc.ref,
            description=(
                "Fresh (non-prod) Voice Connector id — re-supply via "
                "-c hvi:voiceConnectorId=<this-id> on the next synth; it is "
                "deliberately NOT handed cross-region to HviTelephonyStack."
            ),
        )
