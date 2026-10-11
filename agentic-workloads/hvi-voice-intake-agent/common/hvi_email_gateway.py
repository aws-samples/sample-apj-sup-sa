"""The confirmation email through an AgentCore Gateway, over MCP.

WHY THIS EXISTS ALONGSIDE the direct hvi-send-confirmation invoke.
This is the first step of Option A (docs/ARCHITECTURE_OPTION_A_GATEWAY.md): move
the intake pipeline's integration calls behind an AgentCore Gateway as MCP tools,
so they gain managed ingress/egress auth and audit-grade observability, WITHOUT
handing any control-flow decision to a model. The deterministic state machine
still decides when to send; this only changes HOW the send is dispatched.

It mirrors common/hvi_salesforce_gateway.py deliberately - same SigV4-signed MCP
transport to the same gateway - because that path is already proven against this
account. The only operation the intake pipeline needs here is send_confirmation.

Selected by a flag; default is the direct Lambda invoke - see email_client() in
this module and the call site in agent/tools.send_confirmation_email. Set
EMAIL_VIA_GATEWAY=1 to use it; unset it to roll back with no code change.

urllib rather than requests, to match the Salesforce module and keep the Lambda
runtimes stdlib-only; botocore ships with boto3.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = os.environ.get("DATA_REGION", "ap-south-1")
GATEWAY_NAME = os.environ.get("HVI_GATEWAY_NAME", "hvi-spike-gw")
TARGET_PREFIX = os.environ.get("HVI_EMAIL_TARGET_PREFIX", "email")
TIMEOUT = int(os.environ.get("HVI_GATEWAY_TIMEOUT", "30"))


class EmailGatewayError(RuntimeError):
    """A gateway/tool failure. Raised so send_confirmation_email's except block
    behaves exactly as it does for the direct path - a failed send must never
    look like success to a caller about to tell a customer their record is
    filed."""

    def __init__(self, status: int, detail: object, context: str) -> None:
        super().__init__(f"email gateway {context}: {detail}")
        self.status = status
        self.detail = detail
        self.context = context


def _discover_url() -> str:
    """Gateway MCP endpoint. Explicit env wins; otherwise look it up once."""
    url = os.environ.get("HVI_GATEWAY_URL", "").strip()
    if not url:
        control = boto3.client("bedrock-agentcore-control", region_name=REGION)
        gw = next((g for g in (control.list_gateways().get("items") or [])
                   if g.get("name") == GATEWAY_NAME), None)
        if not gw:
            raise EmailGatewayError(0, f"no gateway {GATEWAY_NAME}",
                                    "gateway discovery")
        url = control.get_gateway(
            gatewayIdentifier=gw["gatewayId"]).get("gatewayUrl") or ""
    if not url:
        raise EmailGatewayError(0, "gateway has no url", "gateway discovery")
    return url if url.endswith("/mcp") else url.rstrip("/") + "/mcp"


class GatewayEmail:
    """The one operation the intake pipeline performs, over MCP."""

    def __init__(self) -> None:
        self._url: str | None = None
        self._session = boto3.Session()

    @property
    def url(self) -> str:
        if self._url is None:
            self._url = _discover_url()
        return self._url

    def _call(self, operation: str, arguments: dict, context: str) -> object:
        tool = f"{TARGET_PREFIX}___{operation}"
        body = json.dumps({
            "jsonrpc": "2.0", "id": operation, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }).encode()

        signed = AWSRequest(method="POST", url=self.url, data=body,
                            headers={"Content-Type": "application/json"})
        creds = self._session.get_credentials().get_frozen_credentials()
        SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(signed)

        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers=dict(signed.headers))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise EmailGatewayError(
                e.code, e.read()[:400].decode("utf-8", "replace"),
                context) from e
        except Exception as e:  # noqa: BLE001
            raise EmailGatewayError(0, str(e), context) from e

        try:
            envelope = json.loads(raw)
        except ValueError as e:
            raise EmailGatewayError(status, raw[:400], context) from e

        if envelope.get("error"):
            raise EmailGatewayError(status, envelope["error"], context)
        result = envelope.get("result") or {}
        parts = result.get("content") or []
        text = parts[0].get("text", "") if parts else ""
        if result.get("isError"):
            raise EmailGatewayError(status, text[:400], context)
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return {"raw": text}

    def send_confirmation(self, call_id: str) -> dict:
        """Send the post-call summary email for a call. Returns the tool's body,
        which must carry sent=True; anything else is raised, exactly as the direct
        path treats a Lambda that did not set sent."""
        body = self._call("send_confirmation", {"callId": call_id},
                          "send confirmation")
        if not isinstance(body, dict):
            raise EmailGatewayError(200, {"raw": body}, "send confirmation")
        if not body.get("sent"):
            raise EmailGatewayError(200, body.get("error")
                                    or "confirmation email was not sent",
                                    "send confirmation")
        return body


def email_client(force: str | None = None):
    """Return the direct or gateway email sender.

      direct   agent/tools.send_confirmation_email's own Lambda invoke.
      gateway  this module's GatewayEmail over MCP; auth is brokered by the
               AgentCore Gateway / Identity vault rather than the Lambda's own IAM.

    Default is direct - this is how a medical-record confirmation reaches the
    customer, so the gateway path is opt-in per deployment rather than a swap. Set
    EMAIL_VIA_GATEWAY=1 to use it; unset it to roll back with no code change.

    Import is lazy so a deployment that never enables the flag does not need this
    module present at all. Returns None for the direct path so the caller keeps
    its existing, hardened Lambda-invoke code.
    """
    choice = (force or os.environ.get("EMAIL_VIA_GATEWAY", "0")).strip().lower()
    if choice in ("1", "true", "yes", "gateway"):
        return GatewayEmail()
    return None
