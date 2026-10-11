"""Salesforce client for the CRM port.

Salesforce is the SYSTEM OF RECORD. DynamoDB holds a read projection so that the
pre-call gate and the in-call agent never take a runtime dependency on Salesforce
availability - an outage or an exhausted API limit part-way through a call would
otherwise break a call in progress, and Salesforce limits are per-org per-24h, so
per-turn reads would burn them for nothing.

Design notes that are decisions, not incidentals:

  The instance_url comes from the TOKEN RESPONSE, not from configuration. The My
  Domain URL you log in with is often not the canonical API host, and using the
  configured one works right up until it silently does not.

  The API version is DISCOVERED once, not pinned. Hardcoding a version that an
  org has not been upgraded to yet fails with a 404 that reads exactly like a
  missing object.

  Writes are UPSERTS on an External Id, never inserts. A retry after a network
  blip must not create a second medical-history record for one call.

  Tokens are cached in memory for the process lifetime. Client-credentials tokens
  are cheap but not free, and a Lambda handling a completion should not spend a
  round trip on auth it already has.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("hvi.salesforce")

SECRET_NAME = os.environ.get("SF_SECRET_NAME", "hvi/salesforce")
REGION = os.environ.get("DATA_REGION", "ap-south-1")
TIMEOUT = int(os.environ.get("SF_TIMEOUT_SECS", "30"))

# THE TOKEN REQUEST GETS RETRIED; A REJECTED CREDENTIAL DOES NOT.
#
# A CRM sync failed outright with `token HTTP 404 : {"raw": ""}` - the token
# endpoint returning Not Found with an empty body, which is not a documented
# OAuth error and was gone on the next attempt. Every other transport failure
# here already survives one blip (a 401 mid-run re-authenticates and retries),
# but authentication itself had no retry at all, so one bad second took down the
# whole projection.
#
# Only classes that can plausibly succeed on a second attempt are retried:
# 5xx, an opaque 404, and network/timeout errors. A 400 or 403 from the token
# endpoint means the client id, secret or connected-app policy is wrong; those
# are permanent, and retrying them would replace a clear error message with a
# slow one - and can trip an org's login lockout.
#
# THE BACKOFF IS CAPPED, NOT DOUBLING, BECAUSE THE FAILURE IS A BURST.
#
# Measured, rather than assumed. Polling the token endpoint every 20s caught the
# failure twice, and the response headers settle what it is:
#
#     HTTP 404, Content-Length: 0
#     Strict-Transport-Security: max-age=63072000; includeSubDomains
#     Cache-Control: must-revalidate,no-cache,no-store
#
# That max-age and that Cache-Control are Salesforce's own, so the empty-body 404
# comes from the org - not from a proxy, and not from the wrong host (aiming the
# same credentials at login.salesforce.com gives a 400 "request not supported on
# this domain", which is a different failure entirely).
#
# Two facts fix the shape of the retry. Fourteen token requests back to back all
# returned 200, so the trigger is NOT load - which is the only thing doubling
# backoff is good for; it exists to shed load during congestion, and there is no
# congestion to shed. And a 20s poll interval saw isolated failures while three
# consecutive suites each burned five rapid attempts, which is only consistent
# with the endpoint failing in bursts of roughly 15-25s. A 1-2-4-8 chain spans
# 15s and therefore fits INSIDE a burst - it was sampling the same outage five
# times and calling it five failures.
#
# So: back off quickly to a 5s ceiling and keep sampling - ten attempts spanning
# ~37s, which outlives the burst instead of nesting inside it. The wall-clock
# budget is what actually bounds this; attempts alone would not, because a slow
# request can take SF_TIMEOUT_SECS on its own.
#
# Affordable because NOTHING ON A LIVE CALL WAITS FOR THIS. Salesforce is the
# system of record, read through a DynamoDB projection precisely so an outage
# cannot stall a call in progress; the callers here are the batch sync (300s
# Lambda timeout), the post-call writeback (120s) and the schema tool, all of
# which would rather take forty seconds than fail.
AUTH_ATTEMPTS = int(os.environ.get("SF_AUTH_ATTEMPTS", "10"))
AUTH_BACKOFF_SECS = float(os.environ.get("SF_AUTH_BACKOFF_SECS", "1.0"))
AUTH_BACKOFF_CAP_SECS = float(os.environ.get("SF_AUTH_BACKOFF_CAP_SECS", "5.0"))
AUTH_BUDGET_SECS = float(os.environ.get("SF_AUTH_BUDGET_SECS", "45.0"))
_RETRYABLE_AUTH_STATUS = frozenset({404, 500, 502, 503, 504})


class SalesforceError(RuntimeError):
    """Raised for any non-2xx from Salesforce, with the body attached.

    Salesforce error bodies are structured and genuinely useful
    (errorCode + message + fields), so they are preserved rather than flattened
    into a string - callers branch on errorCode.
    """

    def __init__(self, status: int, body, context: str = ""):
        self.status = status
        self.body = body
        self.errorCode = ""
        if isinstance(body, list) and body and isinstance(body[0], dict):
            self.errorCode = body[0].get("errorCode", "")
        elif isinstance(body, dict):
            self.errorCode = body.get("errorCode") or body.get("error") or ""
        super().__init__(f"{context} HTTP {status} {self.errorCode}: "
                         f"{json.dumps(body)[:400]}")


def load_credentials() -> dict:
    """Secrets Manager first, environment second.

    Environment support exists for local development only. Nothing writes these
    to disk, and the secret is the path used in every deployed component.
    """
    if os.environ.get("SF_CLIENT_ID") and os.environ.get("SF_CLIENT_SECRET"):
        return {
            "instance_url": (os.environ.get("SF_LOGIN_URL") or "").rstrip("/"),
            "client_id": os.environ["SF_CLIENT_ID"],
            "client_secret": os.environ["SF_CLIENT_SECRET"],
        }
    import boto3
    sm = boto3.client("secretsmanager", region_name=REGION)
    doc = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    return {
        "instance_url": doc["instance_url"].rstrip("/"),
        "client_id": doc["client_id"],
        "client_secret": doc["client_secret"],
    }


class Salesforce:
    """Thin REST client: OAuth client-credentials, SOQL, upsert, Tooling API."""

    def __init__(self, credentials: dict | None = None):
        self._creds = credentials or load_credentials()
        self._token: str | None = None
        self._instance: str | None = None
        self._version: str | None = None
        self._lock = threading.Lock()

    # ---- auth --------------------------------------------------------------
    def _authenticate(self) -> None:
        base = self._creds["instance_url"]
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self._creds["client_id"],
            "client_secret": self._creds["client_secret"],
        }).encode()

        # The URL goes in the error context because it is the one fact that
        # actually distinguishes the failure modes and it was missing when it was
        # needed: an empty-body 404 was diagnosed three times over without ever
        # establishing which host had been asked. The client id and secret travel
        # in the POST body, not the URL, so there is nothing here to leak.
        token_url = f"{base}/services/oauth2/token"

        # See AUTH_ATTEMPTS: transient classes only, and the last failure is
        # re-raised unchanged so the caller still sees the real Salesforce error.
        started = time.monotonic()
        doc = None
        for attempt in range(1, AUTH_ATTEMPTS + 1):
            delay = min(AUTH_BACKOFF_SECS * (2 ** (attempt - 1)),
                        AUTH_BACKOFF_CAP_SECS)

            # Evaluated after a failure, not before the request, so the elapsed
            # time includes however long the request itself took.
            def spent(_a=attempt, _d=delay) -> bool:
                """Out of attempts, or another wait would overrun the budget."""
                return (_a == AUTH_ATTEMPTS
                        or (time.monotonic() - started) + _d >= AUTH_BUDGET_SECS)

            req = urllib.request.Request(token_url, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
                    doc = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code not in _RETRYABLE_AUTH_STATUS or spent():
                    raise SalesforceError(e.code, _safe_json(e.read()),
                                          f"token {token_url} (attempt "
                                          f"{attempt}/{AUTH_ATTEMPTS}, "
                                          f"{time.monotonic() - started:.0f}s)") from e
                log.warning("salesforce token HTTP %s from %s on attempt %d/%d; "
                            "retrying in %.0fs",
                            e.code, token_url, attempt, AUTH_ATTEMPTS, delay)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if spent():
                    raise SalesforceError(0, {"raw": str(e)},
                                          f"token {token_url} (attempt "
                                          f"{attempt}/{AUTH_ATTEMPTS}, "
                                          f"{time.monotonic() - started:.0f}s)") from e
                log.warning("salesforce token transport error from %s on attempt "
                            "%d/%d (%s); retrying in %.0fs",
                            token_url, attempt, AUTH_ATTEMPTS, e, delay)
            time.sleep(delay)

        if not isinstance(doc, dict) or not doc.get("access_token"):
            raise SalesforceError(0, {"raw": "no access_token in token response"},
                                  f"token {token_url}")

        self._token = doc["access_token"]
        # Canonical host, which can differ from the My Domain URL.
        self._instance = (doc.get("instance_url") or base).rstrip("/")
        log.info("salesforce authenticated: %s", self._instance)

    def _ensure_auth(self) -> None:
        with self._lock:
            if not self._token:
                self._authenticate()

    @property
    def instance_url(self) -> str:
        self._ensure_auth()
        return self._instance or ""

    @property
    def version(self) -> str:
        """Newest API version the org actually has."""
        if self._version:
            return self._version
        self._ensure_auth()
        status, doc = self._raw("GET", "/services/data/")
        if status != 200 or not isinstance(doc, list) or not doc:
            raise SalesforceError(status, doc, "version discovery")
        self._version = max(doc, key=lambda v: float(v["version"]))["version"]
        return self._version

    # ---- transport ---------------------------------------------------------
    def _raw(self, method: str, path: str, payload=None,
             retry_auth: bool = True) -> tuple[int, object]:
        self._ensure_auth()
        url = path if path.startswith("http") else f"{self._instance}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            # A 401 mid-run means the token expired. Re-authenticate once and
            # retry, rather than failing a completion writeback over it.
            if e.code == 401 and retry_auth:
                log.info("salesforce token rejected; re-authenticating")
                with self._lock:
                    self._token = None
                self._ensure_auth()
                return self._raw(method, path, payload, retry_auth=False)
            return e.code, _safe_json(e.read())

    def request(self, method: str, path: str, payload=None, context: str = "") -> object:
        status, body = self._raw(method, path, payload)
        if status >= 300:
            raise SalesforceError(status, body, context or f"{method} {path}")
        return body

    # ---- data --------------------------------------------------------------
    def query(self, soql: str) -> list[dict]:
        """Run SOQL, following pagination.

        nextRecordsUrl is followed because a page is 2000 records and a silent
        truncation at 2000 would look like "those customers do not need calling".
        """
        path = (f"/services/data/v{self.version}/query/?q="
                f"{urllib.parse.quote(soql)}")
        records: list[dict] = []
        while path:
            doc = self.request("GET", path, context="query")
            if not isinstance(doc, dict):
                break
            records.extend(doc.get("records", []))
            path = doc.get("nextRecordsUrl") or ""
        return records

    def upsert(self, sobject: str, external_field: str, external_value: str,
               fields: dict) -> dict:
        """Upsert by External Id. Idempotent by construction.

        This is why Call_Id__c is marked External Id + Unique: a retry after a
        timeout finds the existing row and updates it, instead of writing a
        duplicate medical history for the same call.
        """
        path = (f"/services/data/v{self.version}/sobjects/{sobject}/"
                f"{external_field}/{urllib.parse.quote(str(external_value), safe='')}")
        status, body = self._raw("PATCH", path, fields)
        if status not in (200, 201, 204):
            raise SalesforceError(status, body, f"upsert {sobject}")
        return body if isinstance(body, dict) else {"success": True}

    def update_contact(self, contact_id: str, fields: dict) -> None:
        """Patch fields on one Contact.

        Named rather than left as a raw request() call so both transports expose
        the same three operations - see salesforce_client(). A path-shaped
        interface cannot be satisfied by an MCP tool call without the caller
        knowing about REST paths, which defeats the point of moving the transport.
        """
        self.request("PATCH",
                     f"/services/data/v{self.version}/sobjects/Contact/{contact_id}",
                     fields, context="update contact")

    def describe(self, sobject: str) -> dict | None:
        status, body = self._raw(
            "GET", f"/services/data/v{self.version}/sobjects/{sobject}/describe")
        if status == 404:
            return None
        if status >= 300:
            raise SalesforceError(status, body, f"describe {sobject}")
        return body if isinstance(body, dict) else None

    def current_user_id(self) -> str:
        """The user the client-credentials flow runs as.

        Needed to assign a permission set. It comes from the token's identity
        rather than configuration, because 'Run As' is set in Salesforce and
        nothing on our side knows who it is.
        """
        doc = self.request("GET", "/services/oauth2/userinfo", context="userinfo")
        if not isinstance(doc, dict) or not doc.get("user_id"):
            raise SalesforceError(200, doc, "userinfo returned no user_id")
        return doc["user_id"]

    def create(self, sobject: str, fields: dict) -> str:
        """Insert one record, returning its Id."""
        doc = self.request("POST", f"/services/data/v{self.version}/sobjects/{sobject}/",
                           fields, context=f"create {sobject}")
        if not isinstance(doc, dict) or not doc.get("id"):
            raise SalesforceError(200, doc, f"create {sobject} returned no id")
        return doc["id"]

    # ---- metadata (Tooling API) -------------------------------------------
    def tooling_create(self, kind: str, full_name: str, metadata: dict) -> dict:
        """Create a CustomObject or CustomField through the Tooling API.

        Used instead of the SOAP Metadata API because it is plain REST with the
        same auth as everything else here. The point is that the schema is
        created by a reviewable script rather than clicked into existence, so it
        is reproducible in a second org and a mistyped API name cannot silently
        break the writeback at runtime.
        """
        path = f"/services/data/v{self.version}/tooling/sobjects/{kind}/"
        return self.request("POST", path,
                            {"FullName": full_name, "Metadata": metadata},
                            context=f"create {kind} {full_name}")  # type: ignore[return-value]


    # ---- metadata (SOAP, for things Tooling cannot create) -----------------
    def soap_create_custom_object(self, api_name: str, label: str,
                                  plural_label: str,
                                  display_format: str = "MH-{00000}",
                                  description: str = "") -> None:
        """Create a custom object through the SOAP Metadata API.

        WHY SOAP HERE AND REST EVERYWHERE ELSE
        Not a preference. The Tooling API reports CustomObject as createable:
        false - it can read custom objects but not create them - while it does
        accept CustomField. Verified against the org rather than assumed
        (tests/diag_sf_tooling.py). So the object comes in over SOAP and every
        field over REST, which is the smallest amount of SOAP that does the job.

        The alternative was a metadata zip deploy through
        /metadata/deployRequest, which handles objects and fields together but
        needs zip assembly and deploy-status polling for no benefit at this size.
        """
        session = _xml_escape(self._token or "")
        self._ensure_auth()
        session = _xml_escape(self._token or "")
        desc = f"<met:description>{_xml_escape(description)}</met:description>" if description else ""

        envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <soapenv:Header>
    <met:SessionHeader><met:sessionId>{session}</met:sessionId></met:SessionHeader>
  </soapenv:Header>
  <soapenv:Body>
    <met:createMetadata>
      <met:metadata xsi:type="met:CustomObject">
        <met:fullName>{_xml_escape(api_name)}</met:fullName>
        <met:label>{_xml_escape(label)}</met:label>
        <met:pluralLabel>{_xml_escape(plural_label)}</met:pluralLabel>
        <met:nameField>
          <met:type>AutoNumber</met:type>
          <met:label>Record Number</met:label>
          <met:displayFormat>{_xml_escape(display_format)}</met:displayFormat>
        </met:nameField>
        <met:deploymentStatus>Deployed</met:deploymentStatus>
        <met:sharingModel>ReadWrite</met:sharingModel>
        <met:enableReports>true</met:enableReports>
        <met:enableHistory>true</met:enableHistory>
        {desc}
      </met:metadata>
    </met:createMetadata>
  </soapenv:Body>
</soapenv:Envelope>"""

        url = f"{self._instance}/services/Soap/m/{self.version}"
        req = urllib.request.Request(url, data=envelope.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "text/xml; charset=UTF-8")
        req.add_header("SOAPAction", "createMetadata")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
                body = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise SalesforceError(e.code, {"soap": body[:800]},
                                  f"createMetadata {api_name}") from e

        # The SOAP API returns HTTP 200 even for a failed create, so the body has
        # to be inspected. Trusting the status code here would report success on
        # every failure.
        if "<success>true</success>" not in body:
            raise SalesforceError(200, {"soap": body[:800]},
                                  f"createMetadata {api_name} did not succeed")


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _safe_json(raw: bytes):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"raw": raw.decode(errors="replace")[:400]}


# ─────────────────────────────────────────────────────────────────────────── #
# transport selection
# ─────────────────────────────────────────────────────────────────────────── #
def salesforce_client(force: str | None = None):
    """The Salesforce client this process should use.

    Two transports satisfy the same three operations - query, upsert,
    update_contact - and both raise SalesforceError, so callers do not change:

      direct   this module. Owns the OAuth client-credentials grant, an in-process
               token cache, and retry logic around a token endpoint that has failed
               in ways the comments above still record.
      gateway  hvi_salesforce_gateway. Calls an AgentCore Gateway over MCP; the
               token comes from the AgentCore Identity vault, so none of that OAuth
               code is in the path.

    Default is direct. The gateway path is real and proven against this org - a SOQL
    read and both writes - but it is how a medical record reaches the insurer, so it
    is opt-in per deployment rather than a swap. Set HVI_SF_VIA_GATEWAY=1 to use it;
    unset it to roll back with no code change.

    Import is lazy so a deployment that never enables the flag does not need the
    gateway module present at all.
    """
    choice = (force or os.environ.get("HVI_SF_VIA_GATEWAY", "0")).strip().lower()
    if choice in ("1", "true", "yes", "gateway"):
        from hvi_salesforce_gateway import GatewaySalesforce

        return GatewaySalesforce()
    return Salesforce()
