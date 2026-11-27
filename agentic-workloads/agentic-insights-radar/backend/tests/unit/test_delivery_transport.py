# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery transports: Slack (bot, webhook), the DM lookups, SES, secrets, unsubscribe tokens. No network:
every client gets a fake HTTP or AWS client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpcore
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ValidationError
from insights_radar.insights import delivery_transport as dt
from insights_radar.insights.delivery_transport import (
    DirectorySlackIds,
    Email,
    EmailSlackIds,
    HttpcoreClient,
    HttpResponse,
    LinkExpiredError,
    PermanentDeliveryError,
    SecretReader,
    SesSender,
    SlackClient,
    SlackWebhook,
    TransientDeliveryError,
    UnsubscribeTokens,
    retry_after,
    slack_code,
)
from tests.integration.harness import DIGITS, SLACK_CHANNEL, SLACK_DM, FakeHttp, FakeSes

MSG = {"text": "t", "blocks": [{"type": "divider"}]}
CHANNEL, DM, BOT_USER = SLACK_CHANNEL, SLACK_DM, "B" + DIGITS  # made-up ids, built from parts (see harness)


def reply(status: int, body: Any, headers: dict[str, str] | None = None) -> HttpResponse:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResponse(status, headers or {}, raw)


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "m"}}, "Op")


# ---- Slack bot ------------------------------------------------------------------------------------------------


def test_post_sends_blocks_with_the_bot_token() -> None:
    http = FakeHttp()
    slack = SlackClient(lambda fresh: "xoxb-1", http, timeout=3)
    assert slack.post(CHANNEL, MSG) == "1.2"
    call = http.calls[0]
    assert call["url"] == "https://slack.com/api/chat.postMessage" and call["timeout"] == 3
    assert call["headers"]["authorization"] == "Bearer xoxb-1"
    assert call["body"] == {
        "channel": CHANNEL,
        "text": "t",
        "blocks": MSG["blocks"],
        "unfurl_links": False,
        "unfurl_media": False,
    }


@pytest.mark.parametrize("token", ["", "placeholderGeneratedByTheStack", "xoxp-user-token"])
def test_a_placeholder_token_is_not_configured(token: str) -> None:
    http = FakeHttp()
    with pytest.raises(PermanentDeliveryError) as err:
        SlackClient(lambda fresh: token, http).post(CHANNEL, MSG)
    assert err.value.code == "slack_not_configured" and not http.calls


@pytest.mark.parametrize(
    "code",
    ["channel_not_found", "not_in_channel", "is_archived", "msg_too_long", "msg_blocks_too_long", "missing_scope"],
)
def test_permanent_slack_errors(code: str) -> None:
    http = FakeHttp()
    http.replies = [reply(200, {"ok": False, "error": code})]
    with pytest.raises(PermanentDeliveryError) as err:
        SlackClient(lambda fresh: "xoxb-1", http).post(CHANNEL, MSG)
    assert err.value.code == code and "Slack" in err.value.message


def test_unknown_and_server_errors_are_transient() -> None:
    http = FakeHttp()
    http.replies = [
        reply(200, {"ok": False, "error": "internal_error"}),
        reply(503, b"down"),
        reply(200, b"<html>"),
        reply(200, ["not", "a", "dict"]),
    ]
    slack = SlackClient(lambda fresh: "xoxb-1", http)
    codes = []
    for _ in range(4):
        with pytest.raises(TransientDeliveryError) as err:
            slack.post(CHANNEL, MSG)
        codes.append(err.value.code)
    assert codes == ["internal_error", "http_503", "bad_response", "bad_response"]


def test_rate_limits_carry_retry_after() -> None:
    http = FakeHttp()
    http.replies = [
        reply(429, b"", {"retry-after": "42"}),
        reply(200, {"ok": False, "error": "ratelimited"}, {"retry-after": "99999"}),
        reply(429, b"", {"retry-after": "soon"}),
    ]
    slack = SlackClient(lambda fresh: "xoxb-1", http)
    got = []
    for _ in range(3):
        with pytest.raises(TransientDeliveryError) as err:
            slack.post(CHANNEL, MSG)
        got.append((err.value.code, err.value.retry_after))
    assert got == [("ratelimited", 42), ("ratelimited", 900), ("ratelimited", 30)]


def test_an_auth_error_reads_the_token_again_once() -> None:
    http = FakeHttp()
    asked: list[bool] = []

    def token(fresh: bool) -> str:
        asked.append(fresh)
        return "xoxb-new" if fresh else "xoxb-old"

    http.replies = [reply(200, {"ok": False, "error": "invalid_auth"})]
    assert SlackClient(token, http).post(CHANNEL, MSG) == "1.2"
    assert asked == [False, True] and http.calls[1]["headers"]["authorization"] == "Bearer xoxb-new"
    # Still refused with the fresh token: permanent, and not tried a third time.
    http.replies = [reply(200, {"ok": False, "error": "token_revoked"})] * 2
    asked.clear()
    with pytest.raises(PermanentDeliveryError) as err:
        SlackClient(token, http).post(CHANNEL, MSG)
    assert err.value.code == "token_revoked" and asked == [False, True]


def test_open_dm_and_lookup_by_email() -> None:
    http = FakeHttp()
    slack = SlackClient(lambda fresh: "xoxb-1", http)
    assert slack.open_dm("U0123456789") == DM
    assert http.calls[-1]["body"] == {"users": "U0123456789"}
    assert EmailSlackIds(slack).slack_id("a@example.com") == "U0123456789"
    lookup = http.calls[-1]
    assert lookup["headers"]["content-type"] == "application/x-www-form-urlencoded"
    assert lookup["body"] == "email=a%40example.com"  # form fields, not JSON
    with pytest.raises(PermanentDeliveryError):
        slack.open_dm(CHANNEL)  # only a user id
    http.replies = [reply(200, {"ok": True, "channel": {"id": CHANNEL}})]
    with pytest.raises(TransientDeliveryError):
        slack.open_dm("U0123456789")
    http.replies = [reply(200, {"ok": True, "channel": "x"})]
    with pytest.raises(TransientDeliveryError):
        slack.open_dm("U0123456789")


@pytest.mark.parametrize(
    "answer",
    [
        {"ok": False, "error": "users_not_found"},
        {"ok": True, "user": {"id": BOT_USER}},
        {"ok": True, "user": "U0123456789"},
    ],
)
def test_lookup_by_email_finds_nobody(answer: dict[str, Any]) -> None:
    http = FakeHttp()
    http.replies = [reply(200, answer)]
    with pytest.raises(PermanentDeliveryError) as err:
        SlackClient(lambda fresh: "xoxb-1", http).user_by_email("a@example.com")
    assert err.value.code == "slack_user_not_found"


def test_lookup_by_email_passes_other_errors_on() -> None:
    http = FakeHttp()
    http.replies = [reply(200, {"ok": False, "error": "missing_scope"})]
    with pytest.raises(PermanentDeliveryError) as err:
        SlackClient(lambda fresh: "xoxb-1", http).user_by_email("a@example.com")
    assert err.value.code == "missing_scope"


def test_slack_code_and_retry_after() -> None:
    assert slack_code("Channel Not-Found!") == "channel_not_found"
    assert slack_code(None) == "unknown" and slack_code("!!!") == "unknown"
    assert slack_code("x" * 100) == "x" * 60
    assert retry_after({}) == 30 and retry_after({"retry-after": "5"}) == 5


# ---- Slack webhook --------------------------------------------------------------------------------------------


def test_webhook_posts_to_its_secret_url() -> None:
    http = FakeHttp()
    SlackWebhook(lambda: "https://hooks.slack.com/services/T/B/x", http).post(MSG)
    assert http.calls[0]["url"] == "https://hooks.slack.com/services/T/B/x"
    assert http.calls[0]["body"] == {"text": "t", "blocks": MSG["blocks"]}


@pytest.mark.parametrize("url", ["", "https://evil.example/hook", "http://hooks.slack.com/x", "placeholder"])
def test_webhook_not_configured(url: str) -> None:
    http = FakeHttp()
    with pytest.raises(PermanentDeliveryError) as err:
        SlackWebhook(lambda: url, http).post(MSG)
    assert err.value.code == "slack_not_configured" and not http.calls


def test_webhook_errors() -> None:
    http = FakeHttp()
    hook = SlackWebhook(lambda: "https://hooks.slack.com/services/T/B/x", http)
    http.replies = [
        reply(404, b"channel_not_found"),
        reply(403, b""),
        reply(429, b"rate_limited", {"retry-after": "7"}),
        reply(500, b"oops"),
    ]
    with pytest.raises(PermanentDeliveryError) as e1:
        hook.post(MSG)
    with pytest.raises(PermanentDeliveryError) as e2:
        hook.post(MSG)
    with pytest.raises(TransientDeliveryError) as e3:
        hook.post(MSG)
    with pytest.raises(TransientDeliveryError) as e4:
        hook.post(MSG)
    assert (e1.value.code, e2.value.code, e3.value.retry_after, e4.value.code) == (
        "channel_not_found",
        "http_403",
        7,
        "http_500",
    )


# ---- HTTP -----------------------------------------------------------------------------------------------------


class _Res:
    def __init__(self) -> None:
        self.status = 201
        self.headers = [(b"Retry-After", b"3"), (b"Content-Type", b"application/json")]
        self.content = b'{"a": 1}'


def test_httpcore_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(method: str, url: str, **kw: Any) -> _Res:
        seen.update(kw, method=method, url=url)
        return _Res()

    monkeypatch.setattr(dt.httpcore, "request", fake)
    res = HttpcoreClient().request("POST", "https://slack.com/api/x", {"a": "b"}, b"{}", 2.5)
    assert res == HttpResponse(201, {"retry-after": "3", "content-type": "application/json"}, b'{"a": 1}')
    assert res.json() == {"a": 1} and HttpResponse(200, {}, b"").json() is None
    assert seen["headers"] == [("a", "b")] and seen["extensions"]["timeout"]["read"] == 2.5

    def broken(method: str, url: str, **kw: Any) -> _Res:
        raise httpcore.ConnectTimeout("slow")

    monkeypatch.setattr(dt.httpcore, "request", broken)
    with pytest.raises(TransientDeliveryError) as err:
        HttpcoreClient().request("GET", "https://slack.com/api/x", {}, None, 1)
    assert err.value.code == "network"
    with pytest.raises(PermanentDeliveryError):
        HttpcoreClient().request("GET", "http://slack.com/api/x", {}, None, 1)


# ---- secrets --------------------------------------------------------------------------------------------------


class FakeSecrets:
    def __init__(self, value: str) -> None:
        self.value = value
        self.reads = 0
        self.fail = False

    def get_secret_value(self, SecretId: str) -> dict[str, str]:
        self.reads += 1
        if self.fail:
            raise client_error("ResourceNotFoundException")
        return {"SecretString": self.value}


def test_secret_reader_turns_a_botocore_timeout_into_a_retry() -> None:
    """Scan cycle 2, finding 5: a timeout or a connection error is no ClientError, and was a 500."""

    class Slow:
        def __init__(self, err: Exception) -> None:
            self.err = err

        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            raise self.err

    for err in (ReadTimeoutError(endpoint_url="https://sm"), EndpointConnectionError(endpoint_url="https://sm")):
        with pytest.raises(TransientDeliveryError) as caught:
            SecretReader(Slow(err)).get("arn")
        assert caught.value.code == "secret_unavailable"


def test_secret_reader_reads_a_value_not_ready_again_after_a_minute() -> None:
    """Scan cycle 2, finding 1: a placeholder is read again (at most once a minute); a ready value is kept."""
    sm = FakeSecrets("GeneratedPlaceholder")
    now = [0.0]
    reader = SecretReader(sm, lambda: now[0])

    def ready(v: str) -> bool:
        return v.startswith("xoxb-")

    assert reader.get("arn", ready=ready) == "GeneratedPlaceholder"
    sm.value = "xoxb-9"
    now[0] = 59.0
    assert reader.get("arn", ready=ready) == "GeneratedPlaceholder" and sm.reads == 1
    now[0] = 60.0
    assert reader.get("arn", ready=ready) == "xoxb-9" and sm.reads == 2
    now[0] = 359.0
    assert reader.get("arn", ready=ready) == "xoxb-9" and sm.reads == 2  # a ready value is kept ...
    assert reader.get("arn") == "xoxb-9" and sm.reads == 2  # no check: kept as before
    now[0] = 10_000.0
    assert reader.get("arn", ready=ready) == "xoxb-9" and sm.reads == 3  # ... for five minutes (scan cycle 6)


def test_secret_reader_waits_a_minute_after_a_failed_age_read() -> None:
    """Scan cycle 7: while Secrets Manager fails, the held value serves and the read is tried once a minute, not
    on every read (each read can block for its whole retry budget)."""
    sm = FakeSecrets("xoxb-1")
    now = [0.0]
    reader = SecretReader(sm, lambda: now[0])
    assert reader.get("arn") == "xoxb-1" and sm.reads == 1
    sm.fail = True
    now[0] = 300.0
    assert reader.get("arn") == "xoxb-1" and sm.reads == 2
    now[0] = 359.0
    assert reader.get("arn") == "xoxb-1" and sm.reads == 2  # not again within the minute
    now[0] = 360.0
    assert reader.get("arn") == "xoxb-1" and sm.reads == 3
    sm.fail, sm.value = False, "xoxb-2"
    now[0] = 420.0
    assert reader.get("arn") == "xoxb-2" and sm.reads == 4


def test_secret_reader_caches_and_rereads() -> None:
    sm = FakeSecrets('{"slack_bot_token": " xoxb-1 "}')
    reader = SecretReader(sm)
    assert SecretReader.field(reader.get("arn"), "slack_bot_token") == "xoxb-1"
    reader.get("arn")
    assert sm.reads == 1
    sm.value = "xoxb-2"
    assert reader.get("arn", fresh=True) == "xoxb-2" and sm.reads == 2
    sm.fail = True
    with pytest.raises(TransientDeliveryError):
        reader.get("arn", fresh=True)
    with pytest.raises(PermanentDeliveryError):
        reader.get("")
    assert SecretReader.field("{broken", "a") == ""
    assert SecretReader.field('{"other": "x", "token": 5}', "token") == ""
    assert SecretReader.field('{"url": "https://hooks"}', "webhook_url", "url") == "https://hooks"
    assert SecretReader.field("  plain  ", "x") == "plain"


# ---- Directory lookup -----------------------------------------------------------------------------------------


class FakeSts:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def assume_role(self, **kw: Any) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise client_error("AccessDenied")
        assert kw["RoleArn"] == "arn:aws:iam::111122223333:role/directory" and kw["DurationSeconds"] == 900
        return {"Credentials": {"AccessKeyId": "AKIDEXAMPLE", "SecretAccessKey": "s", "SessionToken": "t"}}


ENDPOINT = "https://abc.execute-api.us-west-2.amazonaws.com/prod/users/slack-id"


def directory(http: FakeHttp, sts: FakeSts, now: list[float], **kw: Any) -> DirectorySlackIds:
    args: dict[str, Any] = {"endpoint": ENDPOINT, "role_arn": "arn:aws:iam::111122223333:role/directory"} | kw
    return DirectorySlackIds(
        args["endpoint"], args["role_arn"], sts, http, "us-east-1", api_key=args.get("api_key"), now=lambda: now[0]
    )


def test_directory_signs_with_the_assumed_role_and_caches_it() -> None:
    http, sts, now = FakeHttp(), FakeSts(), [1000.0]
    http.replies = [reply(200, {"slackId": "U0123456789"}), reply(200, {"data": {"user_id": "W0123456789"}})]
    resolver = directory(http, sts, now, api_key="key-1")
    assert resolver.slack_id("jdoe") == "U0123456789"
    call = http.calls[0]
    assert call["method"] == "GET" and call["url"] == f"{ENDPOINT}?alias=jdoe"
    assert call["headers"]["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-west-2/execute-api/aws4_request" in call["headers"]["authorization"]
    assert call["headers"]["x-amz-security-token"] == "t" and call["headers"]["x-api-key"] == "key-1"
    assert resolver.slack_id("jdoe") == "W0123456789"
    assert sts.calls == 1  # the session is reused until a minute before it ends
    now[0] += 900
    http.replies = [reply(200, {"id": "U0123456789"})]
    resolver.slack_id("jdoe")
    assert sts.calls == 2


def test_directory_region_falls_back_to_the_deployment() -> None:
    http, sts = FakeHttp(), FakeSts()
    http.replies = [reply(200, {"slack_id": "U0123456789"})]
    directory(http, sts, [0.0], endpoint="https://directory.example.com/x").slack_id("abc")
    assert "/us-east-1/execute-api/" in http.calls[0]["headers"]["authorization"]
    assert "x-api-key" not in http.calls[0]["headers"]


@pytest.mark.parametrize(
    ("endpoint", "role"),
    [("", "arn:r"), (ENDPOINT, ""), ("http://directory.example.com", "arn:r"), ("https://", "arn:r")],
)
def test_directory_fails_closed_when_not_configured(endpoint: str, role: str) -> None:
    http, sts = FakeHttp(), FakeSts()
    with pytest.raises(PermanentDeliveryError) as err:
        directory(http, sts, [0.0], endpoint=endpoint, role_arn=role).slack_id("abc")
    assert err.value.code == "directory_not_configured" and not http.calls and not sts.calls


@pytest.mark.parametrize(
    ("answer", "kind", "code"),
    [
        (reply(404, {"message": "not found"}), PermanentDeliveryError, "slack_user_not_found"),
        (reply(200, {"slackId": CHANNEL}), PermanentDeliveryError, "slack_user_not_found"),
        (reply(200, ["U0123456789"]), PermanentDeliveryError, "directory_bad_answer"),  # not a lookup answer (c9)
        (reply(200, {"a": {"b": "nope"}}), PermanentDeliveryError, "slack_user_not_found"),
        (reply(403, {}), PermanentDeliveryError, "directory_denied"),
        (reply(401, {}), PermanentDeliveryError, "directory_denied"),
        (reply(429, {}), TransientDeliveryError, "directory_429"),
        (reply(502, {}), TransientDeliveryError, "directory_502"),
    ],
)
def test_directory_answers(answer: HttpResponse, kind: type[Exception], code: str) -> None:
    http, sts = FakeHttp(), FakeSts()
    http.replies = [answer]
    with pytest.raises(kind) as err:
        directory(http, sts, [0.0]).slack_id("abc")
    assert getattr(err.value, "code", None) == code


def test_directory_refuses_a_bad_alias_and_role_failures() -> None:
    http, sts = FakeHttp(), FakeSts()
    for alias in ("", "A", "has space", "x@y", "a" * 70):
        with pytest.raises(PermanentDeliveryError):
            directory(http, sts, [0.0]).slack_id(alias)
    assert not http.calls
    sts.fail = True
    with pytest.raises(TransientDeliveryError) as err:
        directory(http, sts, [0.0]).slack_id("abc")
    assert err.value.code == "directory_role"


# ---- SES ------------------------------------------------------------------------------------------------------


def mail(**kw: Any) -> Email:
    base: dict[str, Any] = {
        "to": "a@example.com",
        "subject": "Radar: 2 new",
        "text": "text",
        "html": "<p>x</p>",
        "unsubscribe_url": "https://app.example.com/api/digest/unsubscribe?t=abc",
        "tags": {"delivery": "0123456789ab", "subscriber": "s-1"},
    }
    return Email(**(base | kw))


def test_ses_send_with_one_click_unsubscribe_headers() -> None:
    ses = FakeSes()
    assert SesSender(ses, "no-reply@example.com", "cfg").send(mail()) == "m-1"
    sent = ses.sent[0]
    assert sent["FromEmailAddress"] == "no-reply@example.com" and sent["ConfigurationSetName"] == "cfg"
    assert sent["Destination"] == {"ToAddresses": ["a@example.com"]}
    headers = {h["Name"]: h["Value"] for h in sent["Content"]["Simple"]["Headers"]}
    assert headers == {
        "List-Unsubscribe": "<https://app.example.com/api/digest/unsubscribe?t=abc>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert {"Name": "delivery", "Value": "0123456789ab"} in sent["EmailTags"]
    SesSender(ses, "no-reply@example.com", "").send(mail())
    assert "ConfigurationSetName" not in ses.sent[1]


@pytest.mark.parametrize("field", ["subject", "unsubscribe_url"])
def test_ses_refuses_a_line_break_in_a_header(field: str) -> None:
    ses = FakeSes()
    with pytest.raises(PermanentDeliveryError):
        SesSender(ses, "s@example.com", "c").send(mail(**{field: "x\r\nBcc: victim@example.com"}))
    assert not ses.sent


def test_ses_errors() -> None:
    ses = FakeSes()
    ses.errors = [
        client_error("MessageRejected"),
        client_error("TooManyRequestsException"),
        EndpointConnectionError(endpoint_url="https://email"),
    ]
    sender = SesSender(ses, "s@example.com", "c")
    with pytest.raises(PermanentDeliveryError) as e1:
        sender.send(mail())
    with pytest.raises(TransientDeliveryError) as e2:
        sender.send(mail())
    with pytest.raises(TransientDeliveryError) as e3:
        sender.send(mail())
    assert (e1.value.code, e2.value.code, e3.value.code) == ("MessageRejected", "TooManyRequestsException", "network")


# ---- unsubscribe tokens ---------------------------------------------------------------------------------------


def tokens(key: str = "k" * 40) -> tuple[UnsubscribeTokens, FixedClock]:
    clock = FixedClock(datetime(2026, 10, 2, tzinfo=UTC))
    return UnsubscribeTokens(lambda: key, clock), clock


def test_token_round_trip_and_expiry() -> None:
    t, clock = tokens()
    token = t.issue("s-1", "0123456789ab")
    assert token.startswith("v1.") and t.verify(token) == ("s-1", "0123456789ab")
    clock.advance(timedelta(days=179))
    assert t.verify(token) == ("s-1", "0123456789ab")
    clock.advance(timedelta(days=2))
    with pytest.raises(LinkExpiredError) as err:
        t.verify(token)
    assert err.value.status == 410


def test_tampered_tokens_are_refused() -> None:
    t, _ = tokens()
    token = t.issue("s-1", "0123456789ab")
    version, body, sig = token.split(".")
    other = t.issue("s-2", "0123456789ab").split(".")[1]
    forged = [
        f"{version}.{other}.{sig}",  # another subscriber's payload with this signature
        f"{version}.{body}.{sig[:-2]}AA",
        f"v2.{body}.{sig}",
        f"{version}.{body}",
        f"{version}.{body}.{sig}.x",
        f"{version}.{body}.{sig}" + "A" * 600,
        f"{version}.{body}!.{sig}",
        f"{version}.a.{sig}",  # not base64 (one past a multiple of four)
        "",
        "...",
    ]
    for bad in forged:
        with pytest.raises(ValidationError):
            t.verify(bad)
    with pytest.raises(ValidationError):
        t.verify(None)  # type: ignore[arg-type]
    # Signed with another key.
    other_key, _ = tokens("z" * 40)
    with pytest.raises(ValidationError):
        other_key.verify(token)


def test_a_well_signed_but_malformed_payload_is_refused() -> None:
    t, _ = tokens()
    for payload in (b"s-1\n0123456789ab", b"s-1\nid\nsoon", b"\nid\n1", b"s\n\n1"):
        token = f"v1.{dt._b64(payload)}.{dt._b64(t._mac(payload))}"
        with pytest.raises(ValidationError):
            t.verify(token)


def test_no_key_no_tokens(caplog: pytest.LogCaptureFixture) -> None:
    t, clock = tokens(key="short")
    with pytest.raises(PermanentDeliveryError):
        t.issue("s-1", "0123456789ab")
    good, _ = tokens()
    from insights_radar.api.http import error_response
    from insights_radar.core.errors import UnsubscribeUnavailableError

    def failures() -> int:
        return sum("unsubscribe.key_unusable" in r.getMessage() for r in caplog.records)

    for _ in range(2):  # c42, c43: the deployment's fault, with no Retry-After (only an operator can fix it)
        with pytest.raises(UnsubscribeUnavailableError) as err:
            t.verify(good.issue("s-1", "0123456789ab"))
        assert err.value.status == 503 and "Retry-After" not in error_response(err.value).headers
    assert failures() == 1  # c44, c45: at most once a minute, not once per caller
    assert any('"reason": "unsubscribe_not_configured"' in r.getMessage() for r in caplog.records)  # c50: the cause
    clock.advance(timedelta(seconds=61))
    with pytest.raises(UnsubscribeUnavailableError):
        t.verify(good.issue("s-1", "0123456789ab"))
    assert failures() == 2  # and again in the next period, so the alarm stays raised


# ---- scan cycle 3 ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "found"),
    [
        # Bob's own record has no Slack ID: the manager's is never taken (finding 3).
        ({"employee": {"alias": "bob"}, "manager": {"slackId": "U0MANAGER1"}}, None),
        ({"data": {"alias": "someone-else", "slackId": "U0OTHER001"}}, None),
        ({"alias": "someone-else", "slackId": "U0OTHER001"}, None),
        ({"user": {"login": "BOB", "id": "U0BOBBOB01"}}, "U0BOBBOB01"),
        ({"result": {"username": "bob", "slack_id": "W0BOBBOB01"}}, "W0BOBBOB01"),
        ({"alias": "bob", "data": {"slackId": "U0BOBBOB02"}}, "U0BOBBOB02"),
    ],
)
def test_c3_directory_takes_only_the_looked_up_alias_slack_id(answer: Any, found: str | None) -> None:
    http, sts = FakeHttp(), FakeSts()
    http.replies = [reply(200, answer)]
    resolver = directory(http, sts, [0.0])
    if found is None:
        with pytest.raises(PermanentDeliveryError) as err:
            resolver.slack_id("bob")
        assert err.value.code == "slack_user_not_found" and err.value.category == dt.LOOKUP
    else:
        assert resolver.slack_id("bob") == found


@pytest.mark.parametrize(
    "code", ["channel_not_found", "is_archived", "not_in_channel", "invalid_auth", "missing_scope", "user_not_found"]
)
def test_c4_slack_errors_never_count_as_the_subscribers_fault(code: str) -> None:
    """Scan cycle 4: a channel is one an admin listed, so even channel_not_found (a private channel the bot was
    never invited to) is the deployment's to fix; no Slack answer switches anyone off."""
    http = FakeHttp()
    http.replies = [reply(200, {"ok": False, "error": code})] * 2
    with pytest.raises(PermanentDeliveryError) as err:
        SlackClient(lambda fresh: "xoxb-1", http).post(CHANNEL, MSG)
    assert err.value.code == code and err.value.category != dt.OWNER  # never the owner's own cause


def test_c4_directory_sends_the_api_key_read_when_needed() -> None:
    http, sts, keys = FakeHttp(), FakeSts(), iter(["k-1", None])
    http.replies = [reply(200, {"slackId": "U0123456789"})] * 2
    resolver = directory(http, sts, [0.0], api_key=lambda: next(keys))
    resolver.slack_id("bob")
    resolver.slack_id("bob")
    assert http.calls[0]["headers"]["x-api-key"] == "k-1" and "x-api-key" not in http.calls[1]["headers"]


def test_c3_permanent_errors_default_to_the_deployments_fault() -> None:
    assert PermanentDeliveryError("x", "m").category == dt.DEPLOYMENT


def test_c45_c46_a_secret_refused_for_good_is_the_unusable_key_fault_only_when_unsubscribing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from botocore.exceptions import ClientError as _CE

    good, _ = tokens()
    token = good.issue("s-1", "0123456789ab")
    for code, lasting in (
        ("AccessDeniedException", True),
        ("ResourceNotFoundException", True),
        ("DecryptionFailure", True),
        ("InvalidRequestException", True),
        ("ThrottlingException", False),
    ):

        def unreadable(code: str = code) -> str:
            try:
                raise _CE({"Error": {"Code": code, "Message": "x"}}, "GetSecretValue")
            except _CE as err:
                raise TransientDeliveryError("secret_unavailable", "x") from err

        t = UnsubscribeTokens(unreadable, FixedClock(datetime(2026, 10, 2, tzinfo=UTC)))
        with pytest.raises(TransientDeliveryError):  # c48: answered "try again in a minute" either way
            t.verify(token)
        assert t._key_reported_at > 0 if lasting else t._key_reported_at == 0  # the key line (the alarm) if lasting
        named = any(f'"reason": "{code}"' in r.getMessage() for r in caplog.records)
        assert named is lasting  # c50: the line names the AWS code, so on-call need not wait for a repeat
        # c46: a send issuing a token keeps any refused read transient, so the queue retries it (an IAM delay
        # right after a deploy must not skip a scheduled digest).
        with pytest.raises(TransientDeliveryError):
            t.issue("s-1", "0123456789ab")


def test_c47_c48_a_refusal_through_the_real_secret_reader_logs_the_key_line() -> None:
    """The ClientError SecretReader attaches is what verify reads (c46 #4, c47 #3): a deleted secret writes the key
    line, which the alarm judges across containers, and is answered as worth another try (c48)."""
    from botocore.exceptions import ClientError as _CE

    from insights_radar.insights.delivery_transport import SecretReader

    class Client:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            raise _CE({"Error": {"Code": "ResourceNotFoundException", "Message": "x"}}, "GetSecretValue")

    reader = SecretReader(Client())
    t = UnsubscribeTokens(
        lambda: SecretReader.field(reader.get("arn:key"), "key", "unsubscribe_key"),
        FixedClock(datetime(2026, 10, 2, tzinfo=UTC)),
    )
    with pytest.raises(TransientDeliveryError):
        t.verify(tokens()[0].issue("s-1", "0123456789ab"))
    assert t._key_reported_at > 0
