#!/usr/bin/env python3
"""Sample 01 evaluation as code: the ScopeAdherence judge plus online evaluation of live traffic.

The online evaluation config's execution role is created by the CDK construct, so no
hand-written IAM policy is needed. Settings come from the repo's .env, like step 0.
"""

import json
import os
import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from common.env_file import load_env


class AgentEvaluationStack(cdk.Stack):
    def __init__(self, scope, construct_id, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        ctx = lambda key, default: self.node.try_get_context(key) or default  # noqa: E731
        service = ctx("serviceName", os.environ.get("AGENT_SERVICE_NAME", "support-agent"))
        # Log groups that actually hold the agent's spans. Confirm with
        # ../find_span_log_groups.sql. A wrong group scores nothing, silently.
        log_groups = ctx("logGroupNames", "aws/spans").split(",")

        # The rubric lives in ../scope_adherence.json, shared with the CLI path.
        spec = json.loads((HERE.parent / "scope_adherence.json").read_text())["llmAsAJudge"]
        scope_adherence = agentcore.Evaluator(
            self, "ScopeAdherence",
            evaluator_name="ScopeAdherence",
            level=agentcore.EvaluationLevel.TRACE,
            description="Flags responses that answer requests outside AnyCompany support scope.",
            evaluator_config=agentcore.EvaluatorConfig.llm_as_a_judge(
                instructions=spec["instructions"],
                model_id=os.environ["JUDGE_MODEL_ID"],
                rating_scale=agentcore.EvaluatorRatingScale.numerical([
                    agentcore.NumericalRatingOption(label=o["label"], definition=o["definition"], value=o["value"])
                    for o in spec["ratingScale"]["numerical"]
                ]),
                inference_config=agentcore.EvaluatorInferenceConfig(max_tokens=512, temperature=0),
            ),
        )

        online = agentcore.OnlineEvaluationConfig(
            self, "OnlineEvaluation",
            online_evaluation_config_name="support_agent_quality",
            description=f"Scores live {service} traffic: helpfulness and scope adherence.",
            evaluators=[
                agentcore.EvaluatorSelector.builtin(agentcore.BuiltinEvaluator.HELPFULNESS),
                agentcore.EvaluatorSelector.custom(scope_adherence),
            ],
            data_source=agentcore.DataSourceConfig.from_cloud_watch_logs(
                log_group_names=log_groups, service_names=[service]),
            sampling_percentage=float(ctx("samplingPercentage", "100")),  # demo volume; use 10-20 for busy agents
            session_timeout=cdk.Duration.minutes(int(ctx("sessionTimeoutMinutes", "2"))),
            # The construct deploys the config DISABLED unless told otherwise. ACTIVE only means
            # provisioned; executionStatus is what switches scoring on.
            execution_status=agentcore.ExecutionStatus.ENABLED,
        )

        cdk.CfnOutput(self, "ScopeAdherenceEvaluatorId", value=scope_adherence.evaluator_id)
        cdk.CfnOutput(self, "OnlineEvaluationConfigId", value=online.online_evaluation_config_id)


load_env(ROOT / ".env")
app = cdk.App()
AgentEvaluationStack(
    app, "OmniSamplesAgentEvaluation",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION")),
    description="CloudWatch Omni samples, 01: ScopeAdherence evaluator + online evaluation",
)
app.synth()
