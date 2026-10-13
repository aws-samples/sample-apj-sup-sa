"""Offline unit tests for the AgentCore WebRTC entrypoint in bot.py.

No AWS, no network: we set VOICE_RUNTIME_MODE=agentcore, import bot.py (which builds
the BedrockAgentCoreApp + registers @app.entrypoint), then drive the entrypoint with
the KVS fetch, the SmallWebRTC request handler, and the pipeline run all stubbed.

Asserts the behaviours that matter for the migration:
1. An "offer" yields the SSE sentinels ANSWER:START / {"answer":...} / ANSWER:END.
2. The per-user JWT (gateway_token) + shared runtimeSessionId from the offer body
   reach the pipeline (run_bot), so RBAC/RLS + the shared Memory thread are preserved.
3. "ice-candidates" are dispatched to the live request handler.
4. An unknown request type yields an error (never hangs).
5. get_kvs_ice_servers() shapes KVS GetIceServerConfig into IceServer objects and
   keeps only turn: URIs.
"""

import asyncio
import os
import sys

import pytest

# Select the AgentCore code path BEFORE importing bot, and satisfy the autouse
# require_env fixture (these are dummy values; nothing here touches AWS).
os.environ["VOICE_RUNTIME_MODE"] = "agentcore"
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")
os.environ.setdefault("DEMO_USERNAME", "test@example.com")
os.environ.setdefault("DEMO_PASSWORD", "test-pass")
os.environ.setdefault("AWS_AGENT_ARN", "arn:aws:bedrock-agentcore:us-west-2:1:runtime/x")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot  # noqa: E402  (imported after env is set so the agentcore block runs)


async def _drain(agen):
    """Collect all items yielded by an async generator."""
    out = []
    async for item in agen:
        out.append(item)
    return out


class _FakeHandler:
    """Stand-in for SmallWebRTCRequestHandler.

    On handle_web_request it invokes the connection callback (like the real one does
    once an offer is negotiated) and returns a canned SDP answer. Records patch calls.
    """

    def __init__(self, *a, **k):
        self.patches = []
        self.connection_cb = None

    async def handle_web_request(self, request, webrtc_connection_callback):
        self.connection_cb = webrtc_connection_callback
        await webrtc_connection_callback(object())  # fake SmallWebRTCConnection
        return {"sdp": "v=0...", "type": "answer"}

    async def handle_patch_request(self, request):
        self.patches.append(request)


def _strict_from_dict(d):
    """Mimic the REAL SmallWebRTCRequest.from_dict, which only accepts the SDP fields
    and raises TypeError on any unexpected key (sdp/type/pc_id/restart_pc). This is
    what catches the runtimeSessionId/gateway_token leak regression."""
    allowed = {"sdp", "type", "pc_id", "restart_pc"}
    extra = set(d) - allowed
    if extra:
        raise TypeError(
            f"SmallWebRTCRequest.__init__() got an unexpected keyword argument "
            f"{sorted(extra)[0]!r}")
    return d


def _install_common_stubs(monkeypatch):
    """Stub KVS fetch, the request handler, the request parser, and the pipeline run."""
    monkeypatch.setattr(bot, "get_kvs_ice_servers", lambda: [])
    # Use a STRICT from_dict so a stray runtimeSessionId in the offer data fails the
    # test (it would 502 the live signaling proxy with a TypeError otherwise).
    monkeypatch.setattr(bot, "SmallWebRTCRequest", type(
        "FakeReq", (), {"from_dict": staticmethod(_strict_from_dict)}
    ))
    # Capture what reaches the pipeline.
    captured = {}

    async def _fake_session(connection, user_token, runtime_session_id):
        captured["user_token"] = user_token
        captured["runtime_session_id"] = runtime_session_id

    monkeypatch.setattr(bot, "_run_webrtc_session", _fake_session)
    return captured


def test_offer_yields_sse_answer_sentinels(monkeypatch):
    _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "SmallWebRTCRequestHandler", _FakeHandler)

    # The offer data carries the shared runtimeSessionId (the proxy adds it). The
    # entrypoint MUST strip it before SmallWebRTCRequest.from_dict — otherwise the
    # strict parser raises TypeError and the live signaling proxy returns 502.
    payload = {"type": "offer", "data": {"sdp": "off", "type": "offer",
                                         "runtimeSessionId": "sess-123"}}
    out = asyncio.run(_drain(bot.agentcore_entrypoint(payload, None)))

    assert out[0] == {"status": "ANSWER:START"}
    assert "answer" in out[1] and out[1]["answer"]["type"] == "answer"
    assert out[-1] == {"status": "ANSWER:END"}


def test_offer_returns_answer_without_blocking_on_pipeline(monkeypatch):
    """Regression: the SDP answer MUST be yielded even if the pipeline session never
    finishes. SmallWebRTCRequestHandler awaits the connection callback BEFORE it
    returns the answer, so the callback must launch the pipeline in the BACKGROUND.
    If it awaited the (here: never-ending) session, the answer would never reach the
    browser, ICE would time out, and the offer would 502 (the live bug)."""
    _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "SmallWebRTCRequestHandler", _FakeHandler)

    # A pipeline session that never returns — the offer must still answer promptly.
    async def _never_ending(connection, user_token, runtime_session_id):
        await asyncio.sleep(3600)

    monkeypatch.setattr(bot, "_run_webrtc_session", _never_ending)

    async def _run():
        payload = {"type": "offer", "data": {"sdp": "v=0", "type": "offer",
                                             "runtimeSessionId": "s"}}
        # Bound the whole drain so a regression (blocking callback) fails fast.
        return await asyncio.wait_for(_drain(bot.agentcore_entrypoint(payload, None)), timeout=5)

    out = asyncio.run(_run())
    assert out[0] == {"status": "ANSWER:START"}
    assert "answer" in out[1]
    assert out[-1] == {"status": "ANSWER:END"}


def test_offer_strips_runtime_session_id_before_webrtc_parse(monkeypatch):
    """Regression: runtimeSessionId in the offer data must NOT reach
    SmallWebRTCRequest.from_dict (it only accepts sdp/type/pc_id/restart_pc). The
    live bug was a 502 'no answer from runtime' caused by
    'SmallWebRTCRequest.__init__() got an unexpected keyword argument runtimeSessionId'."""
    captured = _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "SmallWebRTCRequestHandler", _FakeHandler)

    payload = {"type": "offer", "data": {"sdp": "v=0", "type": "offer",
                                         "runtimeSessionId": "shared-sess-xyz"}}
    # Must NOT raise; must yield the answer sentinels and still propagate the session id.
    out = asyncio.run(_drain(bot.agentcore_entrypoint(payload, None)))
    assert out[0] == {"status": "ANSWER:START"}
    assert out[-1] == {"status": "ANSWER:END"}
    assert captured["runtime_session_id"] == "shared-sess-xyz"


class _FakeContext:
    """Stand-in for the AgentCore runtime context (exposes request_headers)."""

    def __init__(self, headers):
        self.request_headers = headers


def test_offer_reads_token_from_headers(monkeypatch):
    """The per-user JWT now comes from the Authorization header (the signaling proxy
    no longer duplicates it in the body), and the shared session id from the body."""
    captured = _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "SmallWebRTCRequestHandler", _FakeHandler)

    ctx = _FakeContext({"Authorization": "Bearer user-jwt-xyz"})
    payload = {"type": "offer", "data": {"runtimeSessionId": "shared-sess-abc"}}
    asyncio.run(_drain(bot.agentcore_entrypoint(payload, ctx)))

    assert captured["user_token"] == "user-jwt-xyz"
    assert captured["runtime_session_id"] == "shared-sess-abc"


def test_offer_no_header_yields_no_token(monkeypatch):
    """The header is the ONLY identity channel: with no Authorization header the
    token is None, even if a (now-unused) gateway_token sits in the body. The body
    copy was retired — a stray body token must NOT be honoured."""
    captured = _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "SmallWebRTCRequestHandler", _FakeHandler)

    payload = {"type": "offer", "data": {"gateway_token": "stray-body-jwt",
                                         "runtimeSessionId": "shared-sess-abc"}}
    asyncio.run(_drain(bot.agentcore_entrypoint(payload, None)))

    assert captured["user_token"] is None
    assert captured["runtime_session_id"] == "shared-sess-abc"


def test_offer_ignores_body_token(monkeypatch):
    """A body gateway_token is ignored entirely; only the header is read."""
    captured = _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "SmallWebRTCRequestHandler", _FakeHandler)

    ctx = _FakeContext({"authorization": "Bearer header-token"})
    payload = {"type": "offer", "data": {"gateway_token": "body-ignored",
                                         "runtimeSessionId": "s"}}
    asyncio.run(_drain(bot.agentcore_entrypoint(payload, ctx)))

    assert captured["user_token"] == "header-token"


def test_ice_candidates_dispatch(monkeypatch):
    _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "IceCandidate", lambda **c: c)
    monkeypatch.setattr(bot, "SmallWebRTCPatchRequest", lambda **d: d)
    fake = _FakeHandler()
    monkeypatch.setattr(bot, "_request_handler", fake)

    payload = {"type": "ice-candidates", "data": {"pc_id": "p1", "candidates": [{"candidate": "c"}]}}
    out = asyncio.run(_drain(bot.agentcore_entrypoint(payload, None)))

    assert out == [{"status": "ok"}]
    assert len(fake.patches) == 1


def test_ice_candidates_without_connection_errors(monkeypatch):
    _install_common_stubs(monkeypatch)
    monkeypatch.setattr(bot, "_request_handler", None)
    out = asyncio.run(_drain(bot.agentcore_entrypoint(
        {"type": "ice-candidates", "data": {"candidates": []}}, None)))
    assert out and "error" in out[0]


def test_unknown_type_yields_error(monkeypatch):
    _install_common_stubs(monkeypatch)
    out = asyncio.run(_drain(bot.agentcore_entrypoint({"type": "bogus", "data": {}}, None)))
    assert out and "error" in out[0]


def test_kvs_ice_shaping(monkeypatch):
    """get_kvs_ice_servers maps GetIceServerConfig → IceServer and keeps only turn: URIs."""
    class _FakeKvs:
        class exceptions:
            class ResourceNotFoundException(Exception):
                pass

        def describe_signaling_channel(self, ChannelName):
            return {"ChannelInfo": {"ChannelARN": "arn:kvs:chan"}}

        def get_signaling_channel_endpoint(self, ChannelARN, SingleMasterChannelEndpointConfiguration):
            return {"ResourceEndpointList": [{"Protocol": "HTTPS", "ResourceEndpoint": "https://kvs.example"}]}

    class _FakeSignaling:
        def get_ice_server_config(self, ChannelARN, Service):
            return {"IceServerList": [
                {"Uris": ["turn:1.2.3.4:443?transport=tcp", "stun:1.2.3.4:443"],
                 "Username": "u", "Password": "p"},
                {"Uris": ["stun:only.example"], "Username": "x", "Password": "y"},  # dropped
            ]}

    def _fake_client(service, **kwargs):
        return _FakeKvs() if service == "kinesisvideo" else _FakeSignaling()

    monkeypatch.setattr(bot.boto3, "client", _fake_client)

    servers = bot.get_kvs_ice_servers()
    assert len(servers) == 1  # the stun-only entry is dropped
    s = servers[0]
    assert s.urls == ["turn:1.2.3.4:443?transport=tcp"]
    assert s.username == "u" and s.credential == "p"
