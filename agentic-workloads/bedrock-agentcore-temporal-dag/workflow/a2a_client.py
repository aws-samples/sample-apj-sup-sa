import json
import os
from urllib.parse import quote
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

_region = os.environ.get("AWS_REGION", "us-east-1")

# ローカルモード: AGENT_ENDPOINTS 環境変数で Agent URL を直接指定
# 例: {"gather":"http://localhost:9001","analyze":"http://localhost:9002",...}
_local_endpoints: dict[str, str] = {}
if os.environ.get("AGENT_ENDPOINTS"):
    _local_endpoints = json.loads(os.environ["AGENT_ENDPOINTS"])

_is_local = bool(_local_endpoints)


class SigV4HTTPXAuth(httpx.Auth):
    """AgentCore向けSigV4署名。リクエストごとにcredentialsを再取得しローテーションに対応する。"""

    def __init__(self, credentials, service: str, region: str):
        self._credentials = credentials
        self._service = service
        self._region = region

    def auth_flow(self, request: httpx.Request):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        headers = dict(request.headers)
        headers.pop("connection", None)

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=headers,
        )
        frozen_creds = self._credentials.get_frozen_credentials()
        SigV4Auth(frozen_creds, self._service, self._region).add_auth(aws_request)
        request.headers.update(dict(aws_request.headers))
        yield request


# --- Service Discovery ---

_arn_cache: dict[str, str] = {}


def _get_agent_arn(agent_name: str) -> str:
    import boto3

    if not hasattr(_get_agent_arn, "_ssm"):
        _get_agent_arn._ssm = boto3.client("ssm", region_name=_region)

    if agent_name not in _arn_cache:
        param = _get_agent_arn._ssm.get_parameter(Name=f"/agents/{agent_name}/arn")
        _arn_cache[agent_name] = param["Parameter"]["Value"]

    return _arn_cache[agent_name]


def _get_invoke_url(arn: str) -> str:
    return f"https://bedrock-agentcore.{_region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations"


# --- HTTP Client ---

_card_cache: dict[str, object] = {}


def _create_httpx_client(signed: bool = False) -> httpx.AsyncClient:
    if not signed:
        return httpx.AsyncClient(timeout=600)

    import boto3

    session = boto3.Session(region_name=_region)
    credentials = session.get_credentials()
    auth = SigV4HTTPXAuth(
        credentials=credentials,
        service="bedrock-agentcore",
        region=_region,
    )
    session_id = str(uuid4())
    headers = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id}
    return httpx.AsyncClient(timeout=600, auth=auth, headers=headers)


# --- Public API ---


async def invoke_agent(agent_name: str, input_data: dict) -> dict:
    """A2AプロトコルでAgentを呼び出す。

    ローカルモード (AGENT_ENDPOINTS設定時):
      - SigV4署名なし、a2a-sdk ClientFactory経由

    AWSモード (デフォルト):
      - SSM から ARN → invoke URL 構築
      - SigV4署名付き JSON-RPC POST を直接送信
    """
    if _is_local:
        return await _invoke_local(agent_name, input_data)
    else:
        return await _invoke_aws(agent_name, input_data)


async def _invoke_local(agent_name: str, input_data: dict) -> dict:
    url = _local_endpoints.get(agent_name)
    if not url:
        raise ValueError(f"Agent '{agent_name}' not found in AGENT_ENDPOINTS")

    httpx_client = httpx.AsyncClient(timeout=600)
    try:
        if agent_name not in _card_cache:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=url)
            _card_cache[agent_name] = await resolver.get_agent_card()

        card = _card_cache[agent_name]
        config = ClientConfig(httpx_client=httpx_client, streaming=False)
        a2a_client = ClientFactory(config).create(card)

        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text=json.dumps(input_data))],
            message_id=uuid4().hex,
        )
        request = SendMessageRequest(message=message)

        async for event in a2a_client.send_message(request):
            if hasattr(event, "task"):
                task = event.task
                if task.status.state == TaskState.TASK_STATE_FAILED:
                    error_msg = task.status.message.parts[0].text if task.status.message.parts else "Unknown error"
                    raise RuntimeError(f"Agent '{agent_name}' failed: {error_msg}")
                if task.artifacts:
                    text = task.artifacts[0].parts[0].text
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"result": text}
                if task.status.message and task.status.message.parts:
                    text = task.status.message.parts[0].text
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"result": text}
            elif isinstance(event, Message):
                text = event.parts[0].text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"result": text}
    finally:
        await httpx_client.aclose()

    raise RuntimeError(f"No response from agent: {agent_name}")


async def _invoke_aws(agent_name: str, input_data: dict) -> dict:
    arn = _get_agent_arn(agent_name)
    url = _get_invoke_url(arn)

    httpx_client = _create_httpx_client(signed=True)
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": uuid4().hex,
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": json.dumps(input_data)}],
                    "messageId": uuid4().hex,
                }
            },
        }

        response = await httpx_client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            raise RuntimeError(f"Agent '{agent_name}' JSON-RPC error: {result['error']}")

        task = result.get("result", {})

        # Check for failed state
        status = task.get("status", {})
        if status.get("state") == "failed":
            error_parts = status.get("message", {}).get("parts", [])
            error_msg = error_parts[0].get("text", "Unknown error") if error_parts else "Unknown error"
            raise RuntimeError(f"Agent '{agent_name}' failed: {error_msg}")

        # Extract text from artifacts
        artifacts = task.get("artifacts", [])
        if artifacts:
            parts = artifacts[0].get("parts", [])
            if parts:
                text = parts[0].get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"result": text}

        # Fallback: extract from status message
        msg_parts = status.get("message", {}).get("parts", [])
        if msg_parts:
            text = msg_parts[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"result": text}

    finally:
        await httpx_client.aclose()

    raise RuntimeError(f"No response from agent: {agent_name}")
