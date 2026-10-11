"""Salesforce through an AgentCore Gateway, with the token held in the vault.

WHY THIS EXISTS ALONGSIDE hvi_salesforce.py RATHER THAN REPLACING IT
The direct client works and has been hardened by real failures - its comments are a
record of them. What it should not have to own is the OAuth client-credentials
grant, the in-process token cache, and the retry logic around a token endpoint that
has returned opaque 404s. AgentCore Identity's token vault owns exactly that, but it
is only reachable by something holding a workload identity, and the CRM lambdas have
none. A gateway does, and a gateway target takes credentialProviderType OAUTH.

So this speaks the same three operations over MCP to the gateway, which brokers the
token. It raises SalesforceError like the direct client does, so callers' except
blocks are unchanged, and it is selected by a flag - see salesforce_client() in
hvi_salesforce.py. Default is still the direct path.

urllib rather than requests, because the direct client is stdlib-only and the CRM
lambdas do not bundle requests. botocore comes with boto3 in the Lambda runtime.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from hvi_salesforce import SalesforceError

REGION = os.environ.get("DATA_REGION", "ap-south-1")
GATEWAY_NAME = os.environ.get("HVI_GATEWAY_NAME", "hvi-spike-gw")
TARGET_PREFIX = os.environ.get("HVI_SF_TARGET_PREFIX", "salesforce")
TIMEOUT = int(os.environ.get("HVI_GATEWAY_TIMEOUT", "30"))


def _discover_url() -> str:
    """Gateway MCP endpoint. Explicit env wins; otherwise look it up once."""
    url = os.environ.get("HVI_GATEWAY_URL", "").strip()
    if not url:
        control = boto3.client("bedrock-agentcore-control", region_name=REGION)
        gw = next((g for g in (control.list_gateways().get("items") or [])
                   if g.get("name") == GATEWAY_NAME), None)
        if not gw:
            raise SalesforceError(0, {"error": f"no gateway {GATEWAY_NAME}"},
                                  "gateway discovery")
        url = control.get_gateway(
            gatewayIdentifier=gw["gatewayId"]).get("gatewayUrl") or ""
    if not url:
        raise SalesforceError(0, {"error": "gateway has no url"},
                              "gateway discovery")
    return url if url.endswith("/mcp") else url.rstrip("/") + "/mcp"


class GatewaySalesforce:
    """The three operations the intake pipeline performs, over MCP."""

    def __init__(self) -> None:
        self._url: str | None = None
        self._session = boto3.Session()

    # ---- transport ---------------------------------------------------------
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
            raise SalesforceError(e.code, {"raw": e.read()[:400].decode(
                "utf-8", "replace")}, f"gateway {context}") from e
        except Exception as e:  # noqa: BLE001
            raise SalesforceError(0, {"error": str(e)},
                                  f"gateway {context}") from e

        try:
            envelope = json.loads(raw)
        except ValueError as e:
            raise SalesforceError(status, {"raw": raw[:400]},
                                  f"gateway {context}") from e

        # A JSON-RPC error, or a tool-level error, both mean the write did not
        # happen. Neither can be allowed to look like success to a caller that is
        # about to tell a customer their record is filed.
        if envelope.get("error"):
            raise SalesforceError(status, envelope["error"],
                                  f"gateway {context}")
        result = envelope.get("result") or {}
        parts = result.get("content") or []
        text = parts[0].get("text", "") if parts else ""
        if result.get("isError"):
            raise SalesforceError(status, {"toolError": text[:400]},
                                  f"gateway {context}")
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return {"raw": text}

    # ---- the operations ----------------------------------------------------
    def query(self, soql: str) -> list[dict]:
        """Run SOQL and return the records.

        The direct client follows nextRecordsUrl because a page is 2000 records and
        silently stopping at 2000 would read as "those customers do not need
        calling". The gateway exposes no queryMore operation, so rather than
        truncate quietly this refuses. If a caller ever needs more than one page,
        the fix is a queryMore operation on the target, not a silent short read.
        """
        doc = self._call("soqlQuery", {"q": soql}, "query")
        if not isinstance(doc, dict):
            return []
        records = doc.get("records") or []
        if doc.get("done") is False or doc.get("nextRecordsUrl"):
            raise SalesforceError(
                200, {"error": "paginated result, and the gateway target has no "
                               "queryMore operation",
                      "returned": len(records),
                      "totalSize": doc.get("totalSize")},
                "query pagination")
        return records

    def upsert(self, sobject: str, external_field: str, external_value: str,
               fields: dict) -> dict:
        body = self._call("upsertByExternalId", {
            "sobject": sobject,
            "externalField": external_field,
            "externalValue": str(external_value),
            **fields,
        }, f"upsert {sobject}")
        return body if isinstance(body, dict) else {"success": True}

    def update_contact(self, contact_id: str, fields: dict) -> None:
        self._call("updateContact", {"contactId": contact_id, **fields},
                   "update contact")
