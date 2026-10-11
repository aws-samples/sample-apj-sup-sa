#!/usr/bin/env python3
"""CDK app entrypoint for the HVI web-only deploy.

Single app, single account (placeholder ``111122223333``), single region
(``ap-south-1``) — the grounding values from ``config/env.sh``, overridable via
context. Each stack feature adds its stack instantiation and cross-stack wiring
in the marked section below.

Synthesis and the test suite make **no** AWS calls and need no credentials.
"""

from __future__ import annotations

import aws_cdk as cdk

from stacks.agent_stack import HviAgentStack
from stacks.chime_stack import HviChimeStack
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.inference_stack import HviInferenceStack
from stacks.knowledge_stack import HviKnowledgeStack
from stacks.telephony_stack import HviTelephonyStack


def build_app(context: dict | None = None) -> cdk.App:
    """Build the HVI CDK app, honouring the resolved ``hvi:*`` context.

    Factored into a function (rather than module-level) so test imports can
    drive the real ``if hvi.enable_telephony:`` conditional without triggering
    a synth — the ``cdk.json`` entrypoint still synths via ``__main__`` below.
    """
    app = cdk.App(context=dict(context) if context is not None else None)

    # Resolved, validated hvi:* configuration (fail-closed on bad input).
    hvi = HviContext.from_scope(app)

    # The Phase 2 stacks are pinned to the same account+region so a bare synth
    # reproduces the scripts' single-region target exactly (DPDP residency is
    # owned here, at the env level).
    env = cdk.Environment(account=hvi.account, region=hvi.region)

    # -----------------------------------------------------------------------
    #   HviDataStack       (FEAT-002)
    #   HviKnowledgeStack  (FEAT-003)
    #   HviInferenceStack  (FEAT-004)
    #   HviAgentStack      (FEAT-005) + final cross-stack wiring
    #   HviChimeStack / HviTelephonyStack  (Phase 3, behind hvi:enableTelephony)
    # -----------------------------------------------------------------------

    data = HviDataStack(app, "HviDataStack", hvi=hvi, env=env)

    # Knowledge consumes Data's typed prop handles (VPC/subnets/SGs/roles/Neptune
    # subnet group) — never name re-lookups.
    knowledge = HviKnowledgeStack(
        app, "HviKnowledgeStack", hvi=hvi, data=data, env=env
    )

    # Inference consumes Data's hvi-sagemaker-role (typed prop). The default
    # hvi:tts=polly needs no GPU artifact. Opt in with -c hvi:tts=omnivoice and
    # this stack fails synth if build/omnivoice/model.tar.gz is absent — the
    # intended fail-loud behaviour, never a silent fallback.
    inference = HviInferenceStack(
        app, "HviInferenceStack", hvi=hvi, data=data, env=env
    )

    # Agent consumes Data (roles/tables/archive bucket), Knowledge (OpenSearch +
    # graph fn), and Inference (artifacts bucket + endpoint names) as typed props
    # — CDK emits the cross-stack CfnOutput/Fn::ImportValue plumbing. The agent
    # env never carries STT_ENDPOINT/OMNIVOICE_ENDPOINT (those go on the hvi-web
    # box); BEDROCK_MODEL_ID comes from the single context value hvi:bedrockModelId.
    HviAgentStack(
        app,
        "HviAgentStack",
        hvi=hvi,
        data=data,
        knowledge=knowledge,
        inference=inference,
        env=env,
    )

    # Phase 3 — the telephony edge, filling the reserved Stack 3 slot. Default
    # off: a bare synth produces exactly the four Phase 2 stacks. When enabled
    # it adds the us-east-1 Chime anchor and the ap-south-1 Asterisk edge (two
    # region-scoped stacks — Chime SDK Voice is not available in ap-south-1).
    if hvi.enable_telephony:
        chime = HviChimeStack(
            app,
            "HviChimeStack",
            hvi=hvi,
            env=cdk.Environment(account=hvi.account, region="us-east-1"),
        )
        HviTelephonyStack(
            app,
            "HviTelephonyStack",
            hvi=hvi,
            data=data,
            inference=inference,
            chime=chime,
            env=cdk.Environment(account=hvi.account, region=hvi.region),
        )

    return app


if __name__ == "__main__":
    build_app().synth()
