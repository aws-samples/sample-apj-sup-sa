from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_ecr as ecr,
    aws_ssm as ssm,
    aws_iam as iam,
    CfnOutput,
)
from constructs import Construct


AGENT_NAMES = ["gather", "analyze", "evaluate", "synthesize"]


class AgentsStack(Stack):
    """AgentCore Runtimes用のECRリポジトリとSSMパラメータを作成する。

    AgentCore Runtime自体はCDK L2未対応のため、CfnRuntime (L1) または
    bedrock-agentcore-starter-toolkit で別途デプロイする想定。
    このStackはそのための事前リソースを作成する。
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.repositories: dict[str, ecr.Repository] = {}

        for name in AGENT_NAMES:
            repo = ecr.Repository(
                self, f"Repo-{name}",
                repository_name=f"daf-agent-{name}",
                removal_policy=RemovalPolicy.DESTROY,
                empty_on_delete=True,
            )
            self.repositories[name] = repo

            # プレースホルダ — 実際のARNはAgentCoreデプロイ後に更新する
            ssm.StringParameter(
                self, f"Param-{name}",
                parameter_name=f"/agents/{name}/arn",
                string_value=f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/PLACEHOLDER-{name}",
                description=f"AgentCore Runtime ARN for {name} agent",
            )

        # Worker Agent用IAMロール（AgentCore Execution Role）
        self.agent_role = iam.Role(
            self, "AgentExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Execution role for DAF Worker Agents",
        )
        self.agent_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                "arn:aws:bedrock:*::foundation-model/*",
                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                "arn:aws:bedrock:*:*:inference-profile/*",
            ],
        ))
        self.agent_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ecr:GetAuthorizationToken",
            ],
            resources=["*"],
        ))
        self.agent_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability",
            ],
            resources=[f"arn:aws:ecr:{self.region}:{self.account}:repository/daf-agent-*"],
        ))

        CfnOutput(self, "AgentRoleArn", value=self.agent_role.role_arn)
