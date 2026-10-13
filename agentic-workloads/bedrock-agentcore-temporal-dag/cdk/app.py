#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.agents_stack import AgentsStack
from stacks.orchestrator_stack import OrchestratorStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

network = NetworkStack(app, "DafNetwork", env=env)

agents = AgentsStack(app, "DafAgents", env=env)

orchestrator = OrchestratorStack(
    app, "DafOrchestrator",
    vpc=network.vpc,
    env=env,
)
orchestrator.add_dependency(network)
orchestrator.add_dependency(agents)

app.synth()
