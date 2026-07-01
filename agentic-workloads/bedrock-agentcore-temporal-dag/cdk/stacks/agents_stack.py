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
    """Creates ECR repositories and SSM parameters for AgentCore Runtimes.

    AgentCore Runtime itself has no CDK L2 support, so it must be deployed
    separately via CfnRuntime (L1) or the bedrock-agentcore-starter-toolkit.
    This stack provisions the prerequisite resources for that deployment.
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

            # Placeholder — update with the actual ARN after AgentCore deployment
            ssm.StringParameter(
                self, f"Param-{name}",
                parameter_name=f"/agents/{name}/arn",
                string_value=f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/PLACEHOLDER-{name}",
                description=f"AgentCore Runtime ARN for {name} agent",
            )

        # IAM role for Worker Agents (AgentCore Execution Role)
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
