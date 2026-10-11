"""SageMaker-endpoint custom-resource handler (Inference stack).

Creates the Model / EndpointConfig(InstancePools) / Endpoint trio with the same
``create-model`` / ``create-endpoint-config`` / ``create-endpoint`` API calls
as ``scripts/50-whisper.sh`` and ``scripts/52-omnivoice.sh`` — including the
``InstancePools`` priority ladder (which L1 ``CfnEndpointConfig`` cannot
reliably express, the whole reason this is a custom resource) and the
**Failed-endpoint rebuild-before-update** logic: a Failed endpoint cannot be
updated into health, so it is torn down and rebuilt from the new config.

Idempotent, mirroring the scripts: an existing Model / EndpointConfig is
deleted and recreated so current settings apply; an existing healthy Endpoint
is updated in place; a Failed Endpoint is deleted (and waited out) then
recreated. Like the scripts, it issues the create/update and returns — it does
**not** block on ``InService`` (that is the 15-25 min provisioning the scripts
leave to a status poll).

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType``
and ``ResourceProperties``: ``Region``, ``RoleArn``, ``Image``, ``ModelName``,
``EndpointConfigName``, ``EndpointName``, ``Environment`` (a dict), optional
``ModelDataUrl``, ``InstancePools`` (comma-separated, the preflight-filtered
ladder) and the three endpoint-config timeouts.
"""

from __future__ import annotations

import os
import time

import boto3

_DEFAULT_REGION = os.environ.get("DATA_REGION", "ap-south-1")
_TAGS = [{"Key": "Project", "Value": "health-voice-intake"}]


def _physical_id(event: dict) -> str:
    return (event.get("ResourceProperties") or {}).get("EndpointName") or "hvi-endpoint"


def _exists(describe, **kwargs) -> bool:
    """True if a describe call succeeds; a not-found error reads as absent."""
    try:
        describe(**kwargs)
        return True
    except Exception:  # noqa: BLE001 — any describe failure means "treat as absent"
        return False


def _endpoint_status(sm, endpoint_name: str) -> str:  # noqa: ANN001
    try:
        return sm.describe_endpoint(EndpointName=endpoint_name)["EndpointStatus"]
    except Exception:  # noqa: BLE001
        return "NONE"


def _wait_endpoint_gone(sm, endpoint_name: str) -> None:  # noqa: ANN001
    # Bounded poll mirroring the scripts' `while describe-endpoint; sleep 5` loop.
    for _ in range(240):  # ~20 min ceiling
        if not _exists(sm.describe_endpoint, EndpointName=endpoint_name):
            return
        time.sleep(5)


def _instance_pools(props: dict) -> list[dict]:
    """Comma-separated ladder -> ``[{InstanceType, Priority}]`` (1-based)."""
    pools = [p for p in (props.get("InstancePools") or "").split(",") if p]
    return [
        {"InstanceType": itype, "Priority": index + 1}
        for index, itype in enumerate(pools)
    ]


def _apply(sm, props: dict) -> None:  # noqa: ANN001
    model_name = props["ModelName"]
    config_name = props["EndpointConfigName"]
    endpoint_name = props["EndpointName"]

    # ---- Model (delete + recreate so current settings apply) --------------
    if _exists(sm.describe_model, ModelName=model_name):
        sm.delete_model(ModelName=model_name)
    container = {
        "Image": props["Image"],
        "Environment": dict(props.get("Environment") or {}),
    }
    model_data_url = props.get("ModelDataUrl")
    if model_data_url:
        container["ModelDataUrl"] = model_data_url
    sm.create_model(
        ModelName=model_name,
        ExecutionRoleArn=props["RoleArn"],
        PrimaryContainer=container,
        Tags=_TAGS,
    )

    # ---- Endpoint config (delete + recreate) ------------------------------
    if _exists(sm.describe_endpoint_config, EndpointConfigName=config_name):
        sm.delete_endpoint_config(EndpointConfigName=config_name)
    variant = {
        "VariantName": "AllTraffic",
        "ModelName": model_name,
        "InitialInstanceCount": 1,
        "InitialVariantWeight": 1.0,
        "ContainerStartupHealthCheckTimeoutInSeconds": int(
            props["ContainerStartupHealthCheckTimeoutInSeconds"]
        ),
        "ModelDataDownloadTimeoutInSeconds": int(
            props["ModelDataDownloadTimeoutInSeconds"]
        ),
        "VariantInstanceProvisionTimeoutInSeconds": int(
            props["VariantInstanceProvisionTimeoutInSeconds"]
        ),
        "InstancePools": _instance_pools(props),
    }
    sm.create_endpoint_config(
        EndpointConfigName=config_name,
        ProductionVariants=[variant],
        Tags=_TAGS,
    )

    # ---- Endpoint (Failed -> rebuild; healthy -> update; else create) -----
    status = _endpoint_status(sm, endpoint_name)
    if status == "Failed":
        sm.delete_endpoint(EndpointName=endpoint_name)
        _wait_endpoint_gone(sm, endpoint_name)
        status = "NONE"
    if status != "NONE":
        sm.update_endpoint(
            EndpointName=endpoint_name, EndpointConfigName=config_name
        )
    else:
        sm.create_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=config_name,
            Tags=_TAGS,
        )


def _teardown(sm, props: dict) -> None:  # noqa: ANN001
    endpoint_name = props.get("EndpointName")
    config_name = props.get("EndpointConfigName")
    model_name = props.get("ModelName")
    if endpoint_name and _exists(sm.describe_endpoint, EndpointName=endpoint_name):
        sm.delete_endpoint(EndpointName=endpoint_name)
        _wait_endpoint_gone(sm, endpoint_name)
    if config_name and _exists(
        sm.describe_endpoint_config, EndpointConfigName=config_name
    ):
        sm.delete_endpoint_config(EndpointConfigName=config_name)
    if model_name and _exists(sm.describe_model, ModelName=model_name):
        sm.delete_model(ModelName=model_name)


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})
    region = props.get("Region") or _DEFAULT_REGION
    sm = boto3.client("sagemaker", region_name=region)

    if request_type == "Delete":
        _teardown(sm, props)
        return {"PhysicalResourceId": _physical_id(event)}

    _apply(sm, props)
    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"EndpointName": props["EndpointName"]},
    }
