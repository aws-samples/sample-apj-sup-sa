"""CloudFormation custom resource handler for Bedrock AgentCore Runtimes.

Invoked by a CDK `custom_resources.Provider`. Handles Create / Update / Delete
events and maps them to the bedrock-agentcore-control boto3 API.

Event `ResourceProperties`:
    AgentRuntimeName (str)     — unique agent name
    ContainerUri (str)         — ECR image URI (ARM64)
    RoleArn (str)              — execution role ARN
    SubnetIds (list[str])      — VPC subnets to attach
    SecurityGroupIds (list[str]) — security groups
    Region (str)               — AWS region
    ModelId (str)              — Bedrock model ID, injected as BEDROCK_MODEL_ID env

Returns `Data.AgentRuntimeArn` so CloudFormation outputs can reference it.
"""
from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _client(region: str):
    return boto3.client("bedrock-agentcore-control", region_name=region)


def _network_config(props: dict) -> dict:
    return {
        "networkMode": "VPC",
        "networkModeConfig": {
            "subnets": props["SubnetIds"],
            "securityGroups": props["SecurityGroupIds"],
        },
    }


def _artifact(props: dict) -> dict:
    return {"containerConfiguration": {"containerUri": props["ContainerUri"]}}


def _environment(props: dict) -> dict:
    """Environment variables injected into the AgentCore Runtime container.

    Currently carries BEDROCK_MODEL_ID so the model can be changed via
    infra/.env without editing agent code. Empty dict if not provided.
    """
    env = {}
    model_id = props.get("ModelId")
    if model_id:
        env["BEDROCK_MODEL_ID"] = model_id
    return env


def _wait_ready(client, agent_id: str, timeout_s: int = 600) -> None:
    """Poll GetAgentRuntime until the runtime reaches a terminal state."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = client.get_agent_runtime(agentRuntimeId=agent_id)
        status = resp.get("status", "")
        logger.info("AgentRuntime %s status: %s", agent_id, status)
        if status in ("READY", "ACTIVE"):
            return
        if status in ("CREATE_FAILED", "UPDATE_FAILED", "FAILED"):
            raise RuntimeError(f"AgentRuntime {agent_id} entered {status}")
        time.sleep(10)
    raise TimeoutError(f"AgentRuntime {agent_id} did not become ready in {timeout_s}s")


def on_event(event, context):
    logger.info("Received event: %s", event)
    request_type = event["RequestType"]
    props = event["ResourceProperties"]
    region = props["Region"]
    client = _client(region)

    if request_type == "Create":
        return _on_create(client, props)
    if request_type == "Update":
        return _on_update(client, props, event)
    if request_type == "Delete":
        return _on_delete(client, event)
    raise ValueError(f"Unknown RequestType: {request_type}")


def _on_create(client, props: dict):
    name = props["AgentRuntimeName"]
    try:
        resp = client.create_agent_runtime(
            agentRuntimeName=name,
            agentRuntimeArtifact=_artifact(props),
            networkConfiguration=_network_config(props),
            roleArn=props["RoleArn"],
            environmentVariables=_environment(props),
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ConflictException", "AlreadyExistsException"):
            # Names carry a per-deployment suffix, so a collision means the
            # suffix was reused or someone else created a runtime with this
            # exact name. Refuse to adopt — adopting would let a future stack
            # delete a resource it never created.
            raise RuntimeError(
                f"Agent runtime name {name!r} already exists in this account/region. "
                f"Either delete the existing runtime, or change DEPLOYMENT_SUFFIX in infra/.env."
            ) from exc
        raise

    agent_id = resp["agentRuntimeId"]
    arn = resp["agentRuntimeArn"]
    try:
        _wait_ready(client, agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Waiting for runtime failed: %s", exc)
        # Still return — CloudFormation will surface the resource, and the
        # operator can check AgentCore console for the failure cause.

    return {
        "PhysicalResourceId": agent_id,
        "Data": {
            "AgentRuntimeId": agent_id,
            "AgentRuntimeArn": arn,
        },
    }


def _on_update(client, props: dict, event: dict):
    agent_id = event["PhysicalResourceId"]
    resp = client.update_agent_runtime(
        agentRuntimeId=agent_id,
        agentRuntimeArtifact=_artifact(props),
        networkConfiguration=_network_config(props),
        roleArn=props["RoleArn"],
        environmentVariables=_environment(props),
    )
    arn = resp.get("agentRuntimeArn") or client.get_agent_runtime(
        agentRuntimeId=agent_id
    )["agentRuntimeArn"]
    try:
        _wait_ready(client, agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Waiting for runtime failed: %s", exc)

    return {
        "PhysicalResourceId": agent_id,
        "Data": {
            "AgentRuntimeId": agent_id,
            "AgentRuntimeArn": arn,
        },
    }


def _on_delete(client, event: dict):
    agent_id = event["PhysicalResourceId"]
    if not agent_id or agent_id.startswith("failed-"):
        return {"PhysicalResourceId": agent_id or "no-op"}
    try:
        client.delete_agent_runtime(agentRuntimeId=agent_id)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            logger.warning("Agent runtime %s already gone", agent_id)
        else:
            raise
    return {"PhysicalResourceId": agent_id}
