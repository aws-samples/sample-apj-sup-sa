"""The CRM writeback through an AgentCore Gateway, over MCP.

Option A, step 2 (docs/ARCHITECTURE_OPTION_A_GATEWAY.md). The orchestrator's call
to the hvi-crm-writeback Lambda moves behind an AgentCore Gateway as an MCP tool,
for managed auth + audit-grade observability, while the deterministic state machine
still decides WHEN to finalise. Only the transport of the writeback invoke changes.

NOTE this is a DIFFERENT gateway hop from the one already inside hvi-crm-writeback:
that Lambda has its own HVI_SF_VIA_GATEWAY selector for Salesforce<->Lambda. This
module is the orchestrator<->writeback-tool hop. They are independent and can be
enabled separately.

Mirrors common/hvi_email_gateway.py and common/hvi_salesforce_gateway.py - same
SigV4-signed MCP transport to the same gateway, stdlib urllib + botocore only.
Selected by CRM_VIA_GATEWAY (default 0); see crm_client() and the call site in
agent/tools.submit_to_crm. The state machine's own DynamoDB work (finalize_session,
mark_status) stays direct by design - only the writeback tool call is brokered here.
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
TARGET_PREFIX = os.environ.get("HVI_CRM_TARGET_PREFIX", "crm")
TIMEOUT = int(os.environ.get("HVI_GATEWAY_TIMEOUT", "30"))


class CRMGatewayError(RuntimeError):
    """A gateway/tool failure. Raised so submit_to_crm's except block behaves
    exactly as for the direct path - an unconfirmed writeback must never look like
    a completed underwriting record; the session must stay resumable."""

    def __init__(self, status: int, detail: object, context: str) -> None:
        super().__init__(f"crm gateway {context}: {detail}")
        self.status = status
        self.detail = detail
        self.context = context


def _discover_url() -> str:
    url = os.environ.get("HVI_GATEWAY_URL", "").strip()
    if not url:
        control = boto3.client("bedrock-agentcore-control", region_name=REGION)
        gw = next((g for g in (control.list_gateways().get("items") or [])
                   if g.get("name") == GATEWAY_NAME), None)
        if not gw:
            raise CRMGatewayError(0, f"no gateway {GATEWAY_NAME}",
                                  "gateway discovery")
        url = control.get_gateway(
            gatewayIdentifier=gw["gatewayId"]).get("gatewayUrl") or ""
    if not url:
        raise CRMGatewayError(0, "gateway has no url", "gateway discovery")
    return url if url.endswith("/mcp") else url.rstrip("/") + "/mcp"


class GatewayCRM:
    """The one writeback operation the intake pipeline performs, over MCP."""

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
            raise CRMGatewayError(
                e.code, e.read()[:400].decode("utf-8", "replace"),
                context) from e
        except Exception as e:  # noqa: BLE001
            raise CRMGatewayError(0, str(e), context) from e

        try:
            envelope = json.loads(raw)
        except ValueError as e:
            raise CRMGatewayError(status, raw[:400], context) from e

        if envelope.get("error"):
            raise CRMGatewayError(status, envelope["error"], context)
        result = envelope.get("result") or {}
        parts = result.get("content") or []
        text = parts[0].get("text", "") if parts else ""
        if result.get("isError"):
            raise CRMGatewayError(status, text[:400], context)
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return {"raw": text}

    def submit(self, call_id: str) -> dict:
        """Confirm the CRM writeback for a call. Returns the tool's body, which
        must carry written=True; anything else is raised, exactly as the direct
        path treats a Lambda that did not confirm the writeback."""
        body = self._call("submit", {"action": "submit", "callId": call_id},
                          "submit")
        if not isinstance(body, dict):
            raise CRMGatewayError(200, {"raw": body}, "submit")
        if not body.get("written"):
            raise CRMGatewayError(200, body.get("errorMessage")
                                  or body.get("error")
                                  or "CRM writeback was not confirmed", "submit")
        return body


def crm_client(force: str | None = None):
    """Return the direct or gateway CRM writeback transport.

      direct   agent/tools.submit_to_crm's own hvi-crm-writeback Lambda invoke.
      gateway  this module's GatewayCRM over MCP; auth is brokered by the
               AgentCore Gateway rather than the invoke's own IAM.

    Default is direct - this is how a completed medical record reaches the insurer,
    so the gateway path is opt-in per deployment rather than a swap. Set
    CRM_VIA_GATEWAY=1 to use it; unset it to roll back with no code change. Import is
    lazy so a deployment that never enables the flag does not need this module.
    Returns None for the direct path so the caller keeps its hardened invoke code.
    """
    choice = (force or os.environ.get("CRM_VIA_GATEWAY", "0")).strip().lower()
    if choice in ("1", "true", "yes", "gateway"):
        return GatewayCRM()
    return None
