# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Sign in to a deployed Agentic Insights Radar the way a user does (SRP + TOTP) and call its API.

The suite runs only against the stack named in ``AIR_E2E_TARGET``, which must match the stack in
``build/outputs.json``: an outputs file left over from another deployment can never point it at
production by accident. The stack must be deployed with ``AIR_E2E_SIGN_IN=true`` (SRP on the MCP
app client; the web client allows only the hosted UI).

Test users are created per run (``air-e2e-<role>-<run>@example.com``) with suppressed invitation
emails and a generated password, and deleted when the run ends. Users that a crashed run left
behind are deleted at the start of the next run once they are a few hours old. Their TOTP secrets
are kept outside the repository in ``~/.cache/insights-radar-e2e/`` (never committed) for the run.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import string
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import httpx
import pyotp
from pycognito.aws_srp import AWSSRP

CACHE = Path.home() / ".cache" / "insights-radar-e2e"
RUN = secrets.token_hex(3)  # one suffix per test run, so concurrent runs never share users
STALE_SECONDS = 6 * 3600  # a test user older than this is left over from a crashed run
# deploy.sh writes the stack outputs here; AIR_OUTPUTS points the tests at another deployment's copy.
OUTPUTS = Path(os.environ.get("AIR_OUTPUTS") or Path(__file__).resolve().parent.parent / "build" / "outputs.json")


def outputs() -> dict[str, str]:
    data = json.loads(OUTPUTS.read_text())
    return next(iter(data.values()))


def target_stack() -> str:
    """The stack ``build/outputs.json`` describes, if AIR_E2E_TARGET names it; otherwise stop."""
    stack = str(next(iter(json.loads(OUTPUTS.read_text()))))
    target = os.environ.get("AIR_E2E_TARGET", "")
    if target != stack:
        raise SystemExit(
            f"{OUTPUTS} describes stack {stack!r}. Set AIR_E2E_TARGET={stack} to run the e2e suite against it "
            "(it creates users, toggles the kill switch and spends tokens)."
        )
    return stack


def _password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20)) + "aA1!"


@dataclass
class TestUser:
    email: str
    password: str
    totp_secret: str | None = None


_USED_WINDOWS: dict[str, int] = {}


def fresh_totp(secret: str) -> str:
    """A TOTP code from a window this process has not used yet: Cognito accepts each code once."""
    window = int(time.time() // 30)
    if _USED_WINDOWS.get(secret, -1) >= window:
        time.sleep((window + 1) * 30 - time.time() + 1)
        window = int(time.time() // 30)
    _USED_WINDOWS[secret] = window
    return pyotp.TOTP(secret).now()


class Deployment:
    def __init__(self) -> None:
        self.stack = target_stack()
        out = outputs()
        self.outputs = out
        self.app_url = out["AppUrl"]
        self.api_url = out["AppUrl"]  # through CloudFront and the WAF, as users reach it
        config = httpx.get(f"{self.api_url}/api/config", timeout=20).json()
        self.config = config
        self.pool_id = config["user_pool_id"]
        # SRP runs on the MCP client (the web client allows only the hosted UI); its ID tokens carry an
        # audience the API and the chat runtime accept.
        self.client_id = out["McpClientId"]
        self.region = self.pool_id.split("_", 1)[0]  # pool ids are <region>_<id>
        self.idp = boto3.client("cognito-idp", region_name=self.region)
        CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
        CACHE.chmod(0o700)  # mkdir's mode does not apply to a directory that already exists
        self.remove_stale_test_users()

    # ---- users ----------------------------------------------------------------------------

    def _store(self) -> Path:
        return CACHE / f"{self.pool_id}.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        p = self._store()
        return json.loads(p.read_text()) if p.exists() else {}

    def _save(self, users: dict[str, dict[str, Any]]) -> None:
        p = self._store()
        _write_private(p, json.dumps(users, indent=1))

    def user(self, role: str) -> TestUser:
        """A signed-up test user in group ``role`` (``viewer`` means no group)."""
        users = self._load()
        email = f"air-e2e-{role}-{RUN}@example.com"
        if email in users:
            return TestUser(**users[email])
        password = _password()
        # Not cached, so its TOTP secret is lost: a user left over from an earlier run (MFA already set up
        # with that secret) could never sign in again. Start it over; only e2e test users are named so.
        try:
            self.idp.admin_delete_user(UserPoolId=self.pool_id, Username=email)
        except self.idp.exceptions.UserNotFoundException:
            pass
        (CACHE / f"tokens-{self.pool_id}-{email}.json").unlink(missing_ok=True)  # the old user's tokens
        self.idp.admin_create_user(
            UserPoolId=self.pool_id,
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        )
        self.idp.admin_set_user_password(UserPoolId=self.pool_id, Username=email, Password=password, Permanent=True)
        group = role.rstrip("0123456789")  # "editor2" is a second editor
        if group in ("admin", "editor"):
            self.idp.admin_add_user_to_group(UserPoolId=self.pool_id, Username=email, GroupName=group)
        user = TestUser(email, password)
        users[email] = user.__dict__
        self._save(users)
        return user

    def remove_stale_test_users(self, now: float | None = None) -> list[str]:
        """Delete e2e users a crashed or interrupted run left behind (they hold known passwords)."""
        now = time.time() if now is None else now
        removed: list[str] = []
        pages = self.idp.get_paginator("list_users").paginate(UserPoolId=self.pool_id, Filter='email ^= "air-e2e-"')
        for page in pages:
            for u in page["Users"]:
                if now - u["UserCreateDate"].timestamp() > STALE_SECONDS:
                    self.idp.admin_delete_user(UserPoolId=self.pool_id, Username=u["Username"])
                    removed.append(u["Username"])
        return removed

    def stack_resource(self, resource_type: str, logical_prefix: str) -> str:
        """The physical id of the stack's resource of this type whose logical id starts with the prefix."""
        cfn = boto3.client("cloudformation", region_name=self.region)
        for page in cfn.get_paginator("list_stack_resources").paginate(StackName=self.stack):
            for r in page["StackResourceSummaries"]:
                if r["ResourceType"] == resource_type and r["LogicalResourceId"].startswith(logical_prefix):
                    return str(r["PhysicalResourceId"])
        raise LookupError(f"{self.stack} has no {resource_type} named {logical_prefix}*")

    def function_url(self, logical_prefix: str) -> str:
        name = self.stack_resource("AWS::Lambda::Function", logical_prefix)
        url = boto3.client("lambda", region_name=self.region).get_function_url_config(FunctionName=name)
        return str(url["FunctionUrl"]).rstrip("/")

    def remove_test_users(self) -> None:
        """Delete every user this suite created, and forget their passwords, secrets and tokens."""
        for email in self._load():
            try:
                self.idp.admin_delete_user(UserPoolId=self.pool_id, Username=email)
            except self.idp.exceptions.UserNotFoundException:
                pass
            (CACHE / f"tokens-{self.pool_id}-{email}.json").unlink(missing_ok=True)
        self._store().unlink(missing_ok=True)

    def _remember(self, user: TestUser) -> None:
        users = self._load()
        users[user.email] = user.__dict__
        self._save(users)

    # ---- sign-in ----------------------------------------------------------------------------

    def sign_in(self, user: TestUser) -> dict[str, str]:
        """Tokens for ``user``, reused from the cache while they have 45+ minutes left: session fixtures
        hold them for the whole run, browser and MCP tests included."""
        cache = CACHE / f"tokens-{self.pool_id}-{user.email}.json"
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached["expires"] - time.time() > 2700:
                return dict(cached["tokens"])
        tokens = self._sign_in(user)
        _write_private(cache, json.dumps({"tokens": tokens, "expires": time.time() + 3600}))
        return tokens

    def _sign_in(self, user: TestUser, retried: bool = False) -> dict[str, str]:
        srp = AWSSRP(username=user.email, password=user.password, pool_id=self.pool_id, client_id=self.client_id,
                     client=self.idp)
        auth = self.idp.initiate_auth(AuthFlow="USER_SRP_AUTH", AuthParameters=srp.get_auth_params(),
                                      ClientId=self.client_id)
        if auth.get("ChallengeName") == "PASSWORD_VERIFIER":
            auth = self.idp.respond_to_auth_challenge(
                ClientId=self.client_id, ChallengeName="PASSWORD_VERIFIER",
                ChallengeResponses=srp.process_challenge(auth["ChallengeParameters"], srp.get_auth_params()),
            )
        for _ in range(3):
            name = auth.get("ChallengeName")
            if not name:
                break
            if name == "MFA_SETUP":
                assoc = self.idp.associate_software_token(Session=auth["Session"])
                user.totp_secret = assoc["SecretCode"]
                verify = self.idp.verify_software_token(Session=assoc["Session"],
                                                        UserCode=fresh_totp(user.totp_secret))
                self._remember(user)
                auth = self.idp.respond_to_auth_challenge(
                    ClientId=self.client_id, ChallengeName="MFA_SETUP", Session=verify["Session"],
                    ChallengeResponses={"USERNAME": user.email},
                )
            elif name == "SOFTWARE_TOKEN_MFA":
                if not user.totp_secret:
                    raise RuntimeError(f"{user.email} has MFA but no stored TOTP secret")
                code = fresh_totp(user.totp_secret)
                try:
                    auth = self.idp.respond_to_auth_challenge(
                        ClientId=self.client_id, ChallengeName="SOFTWARE_TOKEN_MFA", Session=auth["Session"],
                        ChallengeResponses={"USERNAME": user.email, "SOFTWARE_TOKEN_MFA_CODE": code},
                    )
                except (self.idp.exceptions.CodeMismatchException, self.idp.exceptions.ExpiredCodeException):
                    if retried:
                        raise  # twice in a row: the stored secret is wrong, not the timing
                    return self._sign_in(user, retried=True)
            else:
                raise RuntimeError(f"unexpected challenge {name}")
        result = auth["AuthenticationResult"]
        return {"id": result["IdToken"], "access": result["AccessToken"]}


def _write_private(path: Path, text: str) -> None:
    """Owner-only from the moment the file exists, replaced atomically (passwords and TOTP secrets)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def unsigned_jwt(claims: dict[str, Any]) -> str:
    """A token with ``alg: none`` and no signature: what an attacker forges. Built here rather than
    written out, so it is plainly not a credential."""

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}."


class Api:
    def __init__(
        self,
        base: str,
        id_token: str | None = None,
        token: Callable[[], str] | None = None,
    ) -> None:
        """``id_token`` fixes one token (an anonymous or a deliberately forged client). ``token`` is
        asked for one on every request, which is what the suite uses: ID tokens last an hour and a
        full run (two module runs of up to 30 minutes each, then browser, MCP and chat tests) outlasts
        them, so late tests used to fail with 401 (P2-083). ``Deployment.sign_in`` signs in again when
        fewer than 45 minutes are left, so this costs a cached file read per call."""
        headers = {"authorization": f"Bearer {id_token}"} if id_token else {}
        self._token = token
        self.client = httpx.Client(base_url=base, headers=headers, timeout=60)

    def _fresh(self) -> dict[str, str] | None:
        return {"authorization": f"Bearer {self._token()}"} if self._token else None

    def call(self, method: str, path: str, body: Any = None, **params: str) -> tuple[int, Any]:
        res = self.client.request(method, path, json=body, params=params or None, headers=self._fresh())
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, res.text

    def get(self, path: str, **params: str) -> httpx.Response:
        """A GET whose raw response the test reads (a CSV export, for one), signed with a current token
        like ``call``. ``self.client`` alone carries only a fixed ``id_token``: for the suite's
        per-request clients it sends no token at all, which the API answers with 401."""
        return self.client.get(path, params=params or None, headers=self._fresh())


def chat(deployment: Deployment, id_token: str, prompt: str, session_id: str) -> list[dict[str, Any]]:
    """Invoke the chat runtime like the web app: bearer ID token, streaming response."""
    from urllib.parse import quote

    arn = deployment.config["chat_runtime_arn"]
    url = f"https://bedrock-agentcore.{deployment.region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations"
    frames: list[dict[str, Any]] = []
    with httpx.stream(
        "POST",
        url,
        params={"qualifier": "DEFAULT"},
        headers={
            "authorization": f"Bearer {id_token}",
            "content-type": "application/json",
            "accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        json={"prompt": prompt},
        timeout=300,
    ) as res:
        if res.status_code >= 400:
            return [{"http_error": res.status_code, "body": res.read().decode()[:1000]}]
        for line in res.iter_lines():
            if line.startswith("data: "):
                try:
                    frames.append(json.loads(line[6:]))
                except ValueError:
                    frames.append({"raw": line[6:]})
    return frames
