"""AgentCore-runtime custom-resource handler (Agent stack, agentcore-cr path).

Stands in for the agent Lambda when ``hvi:agentCompute=agentcore-cr``. AgentCore
Runtime has **no** CloudFormation resource type (``scripts/40-agentcore.sh`` uses
the imperative ``bedrock-agentcore-control`` API), so this custom resource
reproduces that control-plane call: it ``create``/``update``-s the
``hvi_intake_agent`` runtime from the pre-built ECR image with the same
environment map ``scripts/40`` passes, and ``delete``-s it on stack teardown.

Offline note: this handler is only ever *staged* at synth (a local file copy);
it is executed exclusively at deploy time with real credentials. The design and
the test suite make no AWS calls.

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType`` and
``ResourceProperties`` (``RuntimeName``, ``ImageUri``, ``RoleArn``,
``EnvironmentVariables`` and ``Region``).
"""

from __future__ import annotations

import json
import os


def _client(region: str):
    import boto3  # imported lazily so staging never needs the SDK

    return boto3.client("bedrock-agentcore-control", region_name=region)


def _physical_id(event: dict) -> str:
    return event.get("PhysicalResourceId") or "hvi-agentcore-runtime"


def _find_runtime_id(client, runtime_name: str) -> str | None:
    runtimes = client.list_agent_runtimes().get("agentRuntimes", [])
    for runtime in runtimes:
        if runtime.get("agentRuntimeName") == runtime_name:
            return runtime.get("agentRuntimeId")
    return None


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})

    region = props.get("Region") or os.environ.get("DATA_REGION", "ap-south-1")
    runtime_name = props["RuntimeName"]
    client = _client(region)

    if request_type == "Delete":
        runtime_id = _find_runtime_id(client, runtime_name)
        if runtime_id:
            client.delete_agent_runtime(agentRuntimeId=runtime_id)
        return {"PhysicalResourceId": _physical_id(event)}

    image_uri = props["ImageUri"]
    role_arn = props["RoleArn"]
    env_vars = props.get("EnvironmentVariables", {})
    artifact = json.dumps(
        {"containerConfiguration": {"containerUri": image_uri}}
    )
    network = json.dumps({"networkMode": "PUBLIC"})

    runtime_id = _find_runtime_id(client, runtime_name)
    if runtime_id:
        client.update_agent_runtime(
            agentRuntimeId=runtime_id,
            agentRuntimeArtifact=artifact,
            networkConfiguration=network,
            roleArn=role_arn,
            environmentVariables=env_vars,
        )
    else:
        resp = client.create_agent_runtime(
            agentRuntimeName=runtime_name,
            agentRuntimeArtifact=artifact,
            networkConfiguration=network,
            roleArn=role_arn,
            environmentVariables=env_vars,
        )
        runtime_id = resp.get("agentRuntimeId", runtime_name)

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"AgentRuntimeId": runtime_id},
    }
