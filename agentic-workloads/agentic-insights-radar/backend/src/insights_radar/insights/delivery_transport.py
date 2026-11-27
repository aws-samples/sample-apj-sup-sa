# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The ways out of a digest delivery: Slack (bot or incoming webhook), SES email, and the identity lookups a
Slack DM needs. Every client takes its HTTP or AWS client as an argument, so tests never touch the network.

Errors are sorted in two: ``PermanentDeliveryError`` (retrying cannot help: the bot is not in the channel, the
token is revoked, SES rejected the message) is recorded on the delivery and the message is acknowledged;
``TransientDeliveryError`` (a timeout, a 5xx, a rate limit, with Slack's ``Retry-After``) leaves the cursor where
it is and lets the queue try again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit

import httpcore
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError

from insights_radar.core.clock import Clock
from insights_radar.core.errors import DomainError, UnsubscribeUnavailableError, ValidationError

log = logging.getLogger(__name__)
# Well under the DigestDeploymentFaults alarm's 5-minute period, so a key that stays broken while unsubscribes keep
# coming has a line in every period and the alarm stays raised (scan cycles 44 and 45).
KEY_REPORT_SECONDS = 60
SECRET_LASTING_ERRORS = frozenset(
    {"AccessDeniedException", "ResourceNotFoundException", "DecryptionFailure", "InvalidRequestException"}
)


def _secret_error_code(err: DeliveryError) -> str:
    """The AWS error code behind a failed secret read, or "" when it was not refused by AWS (scan cycle 50)."""
    cause = err.__cause__
    return str(cause.response.get("Error", {}).get("Code", "")) if isinstance(cause, ClientError) else ""


def _lasting_secret_error(err: DeliveryError) -> bool:
    """Whether a failed secret read was refused for a reason that does not pass by itself (scan cycle 45)."""
    return _secret_error_code(err) in SECRET_LASTING_ERRORS


SLACK_API = "https://slack.com/api/"
WEBHOOK_PREFIX = "https://hooks.slack.com/"
HTTP_TIMEOUT_SECONDS = 10.0
MAX_RETRY_AFTER = 900
_CODE_RE = re.compile(r"[^a-z0-9_]+")
SLACK_USER_RE = re.compile(r"^[UW][A-Z0-9]{8,}$")
SLACK_DM_RE = re.compile(r"^D[A-Z0-9]{8,}$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
NOT_CONFIGURED = "Slack is not configured on this deployment"

# Slack answers these when retrying cannot help (the Tech Radar runner's list, plus what a DM can answer).
SLACK_PERMANENT = frozenset(
    {
        "channel_not_found",
        "msg_blocks_too_long",  # the same digest is rebuilt the same size, so a retry cannot help (scan cycle 3)
        "not_in_channel",
        "is_archived",
        "channel_is_archived",
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "token_expired",
        "account_inactive",
        "no_permission",
        "missing_scope",
        "restricted_action",
        "restricted_action_read_only_channel",
        "restricted_action_thread_only_channel",
        "restricted_action_non_threadable_channel",
        "ekm_access_denied",
        "team_access_not_granted",
        "msg_too_long",
        "no_text",
        "invalid_blocks",
        "invalid_blocks_format",
        "too_many_attachments",
        "users_not_found",
        "user_not_found",
        "user_disabled",
        "cannot_dm_bot",
        "user_is_bot",
        "invalid_arguments",
        "invalid_arg_name",
        "invalid_payload",
        "action_prohibited",
        "posting_to_general_channel_denied",
        "no_service",
        "user_not_visible",  # a Slack Connect or restricted user the bot cannot see (scan cycle 9)
        "not_allowed_token_type",
        "access_denied",
        "org_login_required",
        "no_service_id",
        "no_team",
        "no_active_hooks",
        "invalid_token",
        "slack_not_configured",
    }
)
# The person's own Slack account, not the deployment's: deactivated, or a bot. No setting produces these, so they
# count towards switching the delivery off (scan cycle 8).
SLACK_PERSONAL = frozenset({"user_disabled", "cannot_dm_bot"})  # not user_is_bot: it can mean the token (c9)
# No Slack user to send to: one person's, or everyone's when the bot is in the wrong workspace (scan cycle 10).
SLACK_LOOKUP = frozenset({"users_not_found", "user_not_found", "user_not_visible"})
# A rotated token takes effect without a cold start: the secret is read again once on these.
SLACK_AUTH = frozenset({"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive"})
SES_PERMANENT = frozenset(
    {
        "MessageRejected",
        "MailFromDomainNotVerifiedException",
        "AccountSuspendedException",
        "NotFoundException",
        "BadRequestException",
        "AccessDeniedException",
    }
)


class DeliveryError(Exception):
    def __init__(self, code: str, message: str = "", retry_after: int | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.retry_after = retry_after


# Why a send failed for good, set where the error is raised so the sender and the alarms read one field (c10).
OWNER = "owner"  # the delivery's own cause: its account gone or disabled, its address changed, its channel
# taken off the list, its Slack account deactivated. Counted: switched off after MAX_PERMANENT_FAILURES.
LOOKUP = "lookup"  # no Slack user for this email or alias: one person's, or everyone's if the lookup is broken.
# Counted, but switched off only after LOOKUP_MISS_LIMIT, and logged for the DigestLookupMisses alarm.
MODE = "mode"  # a mode the operator chose (a bot/webhook switch): shown, never counted, never alarmed.
DEPLOYMENT = "deployment"  # a token, scope, grant, SES identity or directory answer: shown, never counted, alarmed.
CATEGORIES = frozenset({OWNER, LOOKUP, MODE, DEPLOYMENT})


def slack_category(code: str) -> str:
    if code in SLACK_PERSONAL:
        return OWNER
    return LOOKUP if code in SLACK_LOOKUP else DEPLOYMENT


class PermanentDeliveryError(DeliveryError):
    """Retrying cannot help: recorded on the delivery and shown to its owner. ``category`` says whose fault it
    is (OWNER, LOOKUP, MODE or DEPLOYMENT, the default). A deployment fault or a mode is never counted, so it never
    switches anyone off and stays fixed once fixed (scan cycles 3 to 5, 10 and 11)."""

    def __init__(self, code: str, message: str = "", *, category: str = DEPLOYMENT) -> None:
        super().__init__(code, message)
        self.category = category
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")


class TransientDeliveryError(DeliveryError):
    """Worth another try: the queue redelivers the message."""


def slack_code(raw: Any) -> str:
    """Slack's error code, reduced to the characters codes use (it is stored and shown)."""
    return _CODE_RE.sub("_", str(raw or "unknown").lower()).strip("_")[:60] or "unknown"


def retry_after(headers: dict[str, str]) -> int:
    raw = headers.get("retry-after", "")
    return min(int(raw), MAX_RETRY_AFTER) if raw.isdigit() else 30


# ---- HTTP -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None


class HttpClient(Protocol):
    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse: ...


class HttpcoreClient:
    """HTTPS only, with a timeout on every phase. The URLs are fixed service endpoints or the operator's
    secret (a webhook), never anything a user or a web page supplied."""

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        if not url.startswith("https://"):
            raise PermanentDeliveryError("insecure_url", "only https endpoints are called")
        try:
            res = httpcore.request(
                method,
                url,
                headers=list(headers.items()),
                content=body,
                extensions={"timeout": {"connect": timeout, "read": timeout, "write": timeout, "pool": timeout}},
            )
        except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError) as err:
            raise TransientDeliveryError("network", f"network error: {type(err).__name__}") from err
        return HttpResponse(
            res.status, {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in res.headers}, res.content
        )


# ---- secrets ---------------------------------------------------------------------------------------------------


NOT_READY_RECHECK_SECONDS = 60.0
SECRET_MAX_AGE_SECONDS = 300.0


class SecretReader:
    """Secrets Manager values, cached; ``fresh`` reads one again (a rotated token).

    A value ``ready`` refuses (the stack's generated placeholder, before the operator puts the real token) is
    read again once ``NOT_READY_RECHECK_SECONDS`` have passed, so a token stored later is picked up by a warm
    container without a cold start (scan cycle 2). Every value is also read again once it is
    ``SECRET_MAX_AGE_SECONDS`` old, so any field added or rotated later (the directory lookup's API key) reaches warm
    containers within minutes; while that read fails, the value already held still serves (scan cycle 6)."""

    def __init__(self, client: Any, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._c = client
        self._now = monotonic
        self._cache: dict[str, tuple[str, float]] = {}

    def get(self, arn: str, fresh: bool = False, ready: Callable[[str], bool] | None = None) -> str:
        if not arn:
            raise PermanentDeliveryError("not_configured", "this deployment has no secret for it")
        cached = self._cache.get(arn)
        age = 0.0 if cached is None else self._now() - cached[1]
        not_ready = cached is not None and ready is not None and not ready(cached[0])
        refuse = not_ready and age >= NOT_READY_RECHECK_SECONDS
        if fresh or cached is None or refuse or age >= SECRET_MAX_AGE_SECONDS:
            try:
                res = self._c.get_secret_value(SecretId=arn)
            except (ClientError, BotoCoreError) as err:  # a timeout too, not only an AWS error (scan cycle 2)
                if cached is not None and not fresh and not not_ready:
                    # Only old, not wrong: keep sending with it, and try the read again in a minute rather than on
                    # every read while Secrets Manager is slow (scan cycle 7).
                    retry_at = self._now() - SECRET_MAX_AGE_SECONDS + NOT_READY_RECHECK_SECONDS
                    self._cache[arn] = (cached[0], retry_at)
                    return cached[0]
                raise TransientDeliveryError("secret_unavailable", "the secret could not be read") from err
            self._cache[arn] = (str(res.get("SecretString") or ""), self._now())
        return self._cache[arn][0]

    @staticmethod
    def field(raw: str, *names: str) -> str:
        """A secret is a plain string or JSON with one of ``names`` (``{"slack_bot_token": "xoxb-..."}``)."""
        text = raw.strip()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                return ""
            return next((str(data[n]).strip() for n in names if isinstance(data.get(n), str)), "")
        return text


# ---- Slack -----------------------------------------------------------------------------------------------------


def slack_ready(secret: str, webhook: bool) -> bool:
    """Whether a Slack secret holds a real value: the stack creates it with a generated placeholder, which is
    "not configured", never a token to try (and never logged)."""
    return secret.startswith(WEBHOOK_PREFIX if webhook else "xoxb-")


class SlackClient:
    """The Slack Web API with a bot token (``xoxb-``): post, open a DM, find a user by email.

    ``token(fresh)`` returns the token; a placeholder (the stack creates the secret with a generated value) is
    "not configured", a permanent error, never a call to Slack."""

    def __init__(self, token: Callable[[bool], str], http: HttpClient, timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self._token = token
        self._http = http
        self._timeout = timeout

    def call(self, method: str, payload: dict[str, Any], *, form: bool = False) -> dict[str, Any]:
        fresh = False
        while True:
            token = self._token(fresh)
            if not slack_ready(token, webhook=False):
                raise PermanentDeliveryError("slack_not_configured", NOT_CONFIGURED)
            if form:  # read methods such as users.lookupByEmail take form fields, not JSON
                body = urlencode(payload).encode("utf-8")
                kind = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(payload).encode("utf-8")
                kind = "application/json; charset=utf-8"
            headers = {"authorization": f"Bearer {token}", "content-type": kind}
            res = self._http.request("POST", SLACK_API + method, headers, body, self._timeout)
            if res.status == 429:
                raise TransientDeliveryError("ratelimited", "Slack rate limit", retry_after(res.headers))
            if res.status >= 500:
                raise TransientDeliveryError(f"http_{res.status}", f"Slack answered {res.status}")
            data = res.json()
            if not isinstance(data, dict):
                raise TransientDeliveryError("bad_response", f"Slack answered {res.status} without JSON")
            if data.get("ok") is True:
                return data
            code = slack_code(data.get("error"))
            if code in SLACK_AUTH and not fresh:
                fresh = True
                continue
            if code == "ratelimited":
                raise TransientDeliveryError(code, "Slack rate limit", retry_after(res.headers))
            if code in SLACK_PERMANENT:
                raise PermanentDeliveryError(code, f"Slack: {code}", category=slack_category(code))
            raise TransientDeliveryError(code, f"Slack: {code}")

    def post(self, channel: str, message: dict[str, Any]) -> str:
        data = self.call(
            "chat.postMessage",
            {
                "channel": channel,
                "text": message["text"],
                "blocks": message["blocks"],
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
        return str(data.get("ts", ""))

    def open_dm(self, user_id: str) -> str:
        if not SLACK_USER_RE.fullmatch(user_id):
            raise PermanentDeliveryError("slack_user_not_found", "no Slack user for you", category=LOOKUP)
        data = self.call("conversations.open", {"users": user_id})
        channel = data.get("channel")
        cid = channel.get("id") if isinstance(channel, dict) else None
        if not isinstance(cid, str) or not SLACK_DM_RE.fullmatch(cid):
            raise TransientDeliveryError("bad_response", "Slack opened no DM")
        return cid

    def user_by_email(self, email: str) -> str:
        try:
            data = self.call("users.lookupByEmail", {"email": email}, form=True)
        except PermanentDeliveryError as err:
            if err.code in ("users_not_found", "user_not_found"):
                raise PermanentDeliveryError(
                    "slack_user_not_found", "no Slack user has your email address", category=LOOKUP
                ) from err
            raise
        user = data.get("user")
        uid = user.get("id") if isinstance(user, dict) else None
        if not isinstance(uid, str) or not SLACK_USER_RE.fullmatch(uid):
            raise PermanentDeliveryError(
                "slack_user_not_found", "no Slack user has your email address", category=LOOKUP
            )
        return uid


class SlackWebhook:
    """An incoming webhook: one fixed channel, chosen when the webhook was made. The URL is a secret."""

    def __init__(self, url: Callable[[], str], http: HttpClient, timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self._url = url
        self._http = http
        self._timeout = timeout

    def post(self, message: dict[str, Any]) -> None:
        url = self._url()
        if not slack_ready(url, webhook=True):
            raise PermanentDeliveryError("slack_not_configured", NOT_CONFIGURED)
        body = json.dumps({"text": message["text"], "blocks": message["blocks"]}).encode("utf-8")
        res = self._http.request("POST", url, {"content-type": "application/json; charset=utf-8"}, body, self._timeout)
        if res.status == 200:
            return
        code = slack_code(res.body.decode("utf-8", "replace")[:100]) if res.body else f"http_{res.status}"
        if res.status == 429:
            raise TransientDeliveryError("ratelimited", "Slack rate limit", retry_after(res.headers))
        if res.status >= 500:
            raise TransientDeliveryError(f"http_{res.status}", f"Slack answered {res.status}")
        raise PermanentDeliveryError(code, f"Slack webhook: {code}")


# ---- who a DM goes to ------------------------------------------------------------------------------------------


class SlackIdResolver(Protocol):
    def slack_id(self, key: str) -> str: ...


class EmailSlackIds:
    """``dmResolver=email``: Slack's users.lookupByEmail with the person's own verified address (the customer's
    app holds users:read.email)."""

    def __init__(self, slack: SlackClient) -> None:
        self._slack = slack

    def slack_id(self, key: str) -> str:
        return self._slack.user_by_email(key)


class DirectorySlackIds:
    """``dmResolver=directory``: a directory lookup, an HTTPS endpoint you run (for example in front of your
    company directory) that maps an alias to a Slack member ID, called with SigV4 as an assumed role. Fails closed:
    with no endpoint or role every lookup is a permanent "not configured", never a guess."""

    SESSION_SECONDS = 900

    def __init__(
        self,
        endpoint: str,
        role_arn: str,
        sts: Any,
        http: HttpClient,
        region: str,
        *,
        api_key: str | Callable[[], str | None] | None = None,
        timeout: float = 5.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._endpoint = endpoint
        self._role = role_arn
        self._sts = sts
        self._http = http
        self._region = region
        self._api_key = api_key
        self._timeout = timeout
        self._now = now
        self._creds: tuple[float, Credentials] | None = None

    def _credentials(self) -> Credentials:
        if self._creds is None or self._creds[0] - 60 <= self._now():
            try:
                res = self._sts.assume_role(
                    RoleArn=self._role, RoleSessionName="digest-delivery", DurationSeconds=self.SESSION_SECONDS
                )
            except (ClientError, BotoCoreError) as err:
                raise TransientDeliveryError("directory_role", "could not assume the Slack lookup role") from err
            c = res["Credentials"]
            self._creds = (
                self._now() + self.SESSION_SECONDS,
                Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"]),
            )
        return self._creds[1]

    def _region_of(self, host: str) -> str:
        m = re.search(r"\.execute-api\.([a-z0-9-]+)\.amazonaws\.com$", host)
        return m.group(1) if m else self._region

    def slack_id(self, key: str) -> str:
        if not self._endpoint or not self._role:
            raise PermanentDeliveryError("directory_not_configured", "Slack DMs are not set up on this deployment yet")
        parts = urlsplit(self._endpoint)
        if parts.scheme != "https" or not parts.hostname:
            raise PermanentDeliveryError("directory_not_configured", "the Slack lookup endpoint must be https")
        if not ALIAS_RE.fullmatch(key):
            raise PermanentDeliveryError(
                "slack_user_not_found", "your sign-in has no alias to look up", category=LOOKUP
            )
        url = f"{self._endpoint.rstrip('/')}?alias={quote(key, safe='')}"
        req = AWSRequest(method="GET", url=url, headers={"accept": "application/json"})
        # A directory endpoint may also want an API key; it is read when needed (a key added or rotated later is
        # picked up within five minutes, SECRET_MAX_AGE_SECONDS).
        api_key = self._api_key() if callable(self._api_key) else self._api_key
        if api_key:
            req.headers["x-api-key"] = api_key
        SigV4Auth(self._credentials(), "execute-api", self._region_of(parts.hostname)).add_auth(req)
        res = self._http.request("GET", url, {k.lower(): str(v) for k, v in req.headers.items()}, None, self._timeout)
        if res.status == 429 or res.status >= 500:
            raise TransientDeliveryError(f"directory_{res.status}", f"the Slack lookup answered {res.status}")
        if res.status in (401, 403):
            raise PermanentDeliveryError("directory_denied", "the Slack lookup refused this deployment")
        if res.status == 404:
            raise PermanentDeliveryError("slack_user_not_found", "no Slack user for your alias", category=LOOKUP)
        if res.status != 200:  # a wrong path or query: every DM fails the same way (scan cycle 8)
            raise PermanentDeliveryError("directory_bad_answer", f"the Slack lookup answered {res.status}")
        data = res.json()
        if not isinstance(data, dict):  # a proxy or a login page: not the lookup's answer (scan cycle 9)
            raise PermanentDeliveryError("directory_bad_answer", "the Slack lookup did not answer with JSON")
        found = _find_slack_id(data, key)
        if not found:
            raise PermanentDeliveryError("slack_user_not_found", "no Slack user for your alias", category=LOOKUP)
        return found


_SLACK_ID_FIELDS = ("slackId", "slack_id", "slackUserId", "userId", "user_id", "id")
_ALIAS_FIELDS = ("alias", "login", "username")
_WRAPPERS = ("data", "result", "user")


def _find_slack_id(data: Any, alias: str) -> str | None:
    """The Slack user ID of ``alias`` in a lookup answer: from the answer itself, or from the one record it is
    wrapped in (``data``, ``result`` or ``user``). A record that names another alias is skipped, and nothing
    deeper is searched, so the ID of a manager or anyone else in the answer is never taken (scan cycle 3)."""
    if not isinstance(data, dict):
        return None
    records = [data] + [data[w] for w in _WRAPPERS if isinstance(data.get(w), dict)]
    for record in records:
        named = next((record[f] for f in _ALIAS_FIELDS if isinstance(record.get(f), str)), None)
        if named is not None and named.lower() != alias.lower():
            continue
        for name in _SLACK_ID_FIELDS:
            value = record.get(name)
            if isinstance(value, str) and SLACK_USER_RE.fullmatch(value):
                return value
    return None


# ---- email -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Email:
    to: str
    subject: str
    text: str
    html: str
    unsubscribe_url: str
    tags: dict[str, str]


class SesSender:
    """SES v2 SendEmail from the configured sender, through the configuration set whose bounce and complaint
    events come back to ``digest_feedback``, with RFC 8058 one-click unsubscribe headers."""

    def __init__(self, sesv2: Any, sender: str, config_set: str) -> None:
        self._ses = sesv2
        self._sender = sender
        self._config_set = config_set

    def send(self, mail: Email) -> str:
        if "\r" in mail.subject or "\n" in mail.subject or "\r" in mail.unsubscribe_url or "\n" in mail.unsubscribe_url:
            raise PermanentDeliveryError("bad_header", "a header carried a line break")  # never a header injection
        args: dict[str, Any] = {
            "FromEmailAddress": self._sender,
            "Destination": {"ToAddresses": [mail.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": mail.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": mail.text, "Charset": "UTF-8"},
                        "Html": {"Data": mail.html, "Charset": "UTF-8"},
                    },
                    "Headers": [
                        {"Name": "List-Unsubscribe", "Value": f"<{mail.unsubscribe_url}>"},
                        {"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"},
                    ],
                }
            },
            "EmailTags": [{"Name": k, "Value": v} for k, v in mail.tags.items()],
        }
        if self._config_set:
            args["ConfigurationSetName"] = self._config_set
        try:
            res = self._ses.send_email(**args)
        except ClientError as err:
            code = str(err.response.get("Error", {}).get("Code", "error"))
            if code in SES_PERMANENT:
                raise PermanentDeliveryError(code, f"SES: {code}") from err
            raise TransientDeliveryError(code, f"SES: {code}") from err
        except BotoCoreError as err:
            raise TransientDeliveryError("network", f"SES: {type(err).__name__}") from err
        return str(res.get("MessageId", ""))


# ---- unsubscribe links -----------------------------------------------------------------------------------------


class LinkExpiredError(DomainError):
    status = 410
    code = "link_expired"


class UnsubscribeTokens:
    """A link that turns off one delivery without signing in: HMAC-SHA256 over the subscriber and delivery id and
    an expiry, keyed by a generated secret. Compared in constant time; a token names one delivery and can do
    nothing else."""

    TTL_SECONDS = 180 * 86_400
    MAX_LEN = 600
    _DOMAIN = b"digest-unsubscribe|v1|"

    def __init__(self, key: Callable[[], str], clock: Clock) -> None:
        self._key = key
        self._clock = clock
        self._key_reported_at = 0.0

    def _secret(self) -> bytes:
        key = self._key().strip()
        if len(key) < 32:
            raise PermanentDeliveryError("unsubscribe_not_configured", "the unsubscribe key is not set")
        return key.encode("utf-8")

    def _mac(self, payload: bytes) -> bytes:
        return hmac.new(self._secret(), self._DOMAIN + payload, hashlib.sha256).digest()

    def issue(self, subject: str, delivery_id: str) -> str:
        expires = int(self._clock.now().timestamp()) + self.TTL_SECONDS
        payload = f"{subject}\n{delivery_id}\n{expires}".encode()
        return f"v1.{_b64(payload)}.{_b64(self._mac(payload))}"

    def verify(self, token: str) -> tuple[str, str]:
        """The (subject, delivery id) a token names; a 400 for anything forged or garbled, a 410 once expired."""
        bad = ValidationError("this unsubscribe link is not valid")
        if not isinstance(token, str) or len(token) > self.MAX_LEN or token.count(".") != 2:
            raise bad
        version, body, sig = token.split(".")
        payload, mac = _unb64(body), _unb64(sig)
        if version != "v1" or payload is None or mac is None:
            raise bad
        try:
            expected = self._mac(payload)
        except (PermanentDeliveryError, TransientDeliveryError) as err:
            # A throttled or failed read is worth another try: the route answers 503 with Retry-After. A key that is
            # missing or too short, or a read refused for a lasting reason (the secret deleted or scheduled for
            # deletion, the API role's grants narrowed, the data key disabled), is the deployment's fault, not the
            # link's: the key line is logged (at most once a minute per container, so no caller floods the log) and
            # DigestDeploymentFaults pages at the first one, judged across every container rather than in one
            # container's memory (scan cycles 42 to 48). A refused read is still answered "try again in a minute", since
            # IAM or KMS settling for a few seconds after a deploy looks the same; only a missing or short key, which
            # cannot pass by itself, sends no Retry-After. Only on the unsubscribe route: a send that issues a token
            # keeps every refused read transient, so the queue retries it.
            lasting = isinstance(err, PermanentDeliveryError) or _lasting_secret_error(err)
            if not lasting:
                raise
            now = self._clock.now().timestamp()
            if now - self._key_reported_at >= KEY_REPORT_SECONDS:
                self._key_reported_at = now
                # The reason tells a missing or short key (unsubscribe_not_configured) from a refused read (its AWS
                # code), so on-call need not wait for a repeat that sparse unsubscribes may never bring (c50).
                reason = err.code if isinstance(err, PermanentDeliveryError) else _secret_error_code(err)
                log.error(json.dumps({"event": "unsubscribe.key_unusable", "reason": reason}))
            if isinstance(err, TransientDeliveryError):
                raise
            raise UnsubscribeUnavailableError(
                "unsubscribing by link is not available right now: sign in and turn the delivery off under What's new"
            ) from err
        if not hmac.compare_digest(expected, mac):
            raise bad
        parts = payload.decode("utf-8", "replace").split("\n")
        if len(parts) != 3 or not parts[2].isdigit() or not parts[0] or not parts[1]:
            raise bad
        if int(parts[2]) < self._clock.now().timestamp():
            raise LinkExpiredError("this unsubscribe link has expired: sign in and turn the delivery off instead")
        return parts[0], parts[1]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,400}", text):
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:  # a length no base64 text has (one past a multiple of four)
        return None
