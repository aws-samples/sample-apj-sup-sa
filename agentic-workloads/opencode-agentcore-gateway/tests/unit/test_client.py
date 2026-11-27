# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""client/ocgw.py: config precedence, token freshness and refresh, the lock, the local proxy and opencode-config."""
import base64
import importlib.util
import io
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ACCESS_KIND = "access_token"  # the token_kind setting under test
STALE_VALUE = "old-access"  # a stand-in for an expired access token

SRC = Path(__file__).resolve().parents[2] / "client" / "ocgw.py"


def jwt(**c):
    p = base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip("=")
    return f"e30.{p}.sig"


@pytest.fixture()
def m(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ocgw_under_test", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "ocgw"
    monkeypatch.setattr(mod, "HOME_DIR", home)
    monkeypatch.setattr(mod, "TOKENS", home / "tokens.json")
    monkeypatch.setattr(mod, "LOCAL_KEY", home / "local.key")
    monkeypatch.setattr(mod, "CONFIG_PATHS", [tmp_path / "mdm.json", home / "config.json"])
    for k in ("OCGW_OIDC_ISSUER", "OCGW_OIDC_CLIENT_ID", "OCGW_TOKEN_KIND", "OCGW_GATEWAY_URL"):
        monkeypatch.delenv(k, raising=False)
    mod.mdm = tmp_path / "mdm.json"
    return mod


def write_user(m, **cfg):
    m.HOME_DIR.mkdir(parents=True, exist_ok=True)
    (m.HOME_DIR / "config.json").write_text(json.dumps(cfg))


BASE = {"issuer": "https://idp.example.com/", "client_id": "cid", "gateway_url": "https://gw.example.com"}


# ---------------- config ----------------

@pytest.mark.parametrize("mdm,user,env,want", [
    ({"issuer": "https://corp/", "client_id": "c1"}, None, {"OCGW_OIDC_ISSUER": "https://evil/"}, ("https://corp/", "c1")),
    ({"issuer": "https://corp/", "client_id": "c1"}, {"issuer": "https://user/"}, {}, ("https://corp/", "c1")),
    ({"issuer": "https://corp/", "client_id": "c1"}, None, {"OCGW_OIDC_CLIENT_ID": "x"}, ("https://corp/", "c1")),
    ({"client_id": "c1"}, None, {"OCGW_OIDC_ISSUER": "https://other/"}, ("https://other/", "c1")),
    (None, {"issuer": "https://user/", "client_id": "u"}, {}, ("https://user/", "u")),
    (None, {"issuer": "https://user/", "client_id": "u"}, {"OCGW_OIDC_ISSUER": "https://env/"}, ("https://env/", "u")),
    (None, None, {"OCGW_OIDC_ISSUER": "https://env/", "OCGW_OIDC_CLIENT_ID": "e"}, ("https://env/", "e")),
])
def test_precedence(m, monkeypatch, mdm, user, env, want):
    if mdm:
        m.mdm.write_text(json.dumps(mdm))
    if user:
        write_user(m, **user)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    c = m.config()
    assert (c["issuer"], c["client_id"]) == want


@pytest.mark.parametrize("cfg", [{}, {"issuer": "https://x/"}, {"client_id": "c"}, {**BASE, "token_kind": "refresh_token"}])
def test_incomplete_config_exits(m, cfg):
    write_user(m, **cfg)
    with pytest.raises(SystemExit):
        m.config()


@pytest.mark.parametrize("url", ["http://idp/", "file:///etc/passwd", "ftp://x", "javascript:alert(1)", ""])
def test_https_only(m, url):
    with pytest.raises(SystemExit):
        m.https(url)


# ---------------- token freshness ----------------

def save_tok(m, life_left, lifetime=3600, refresh=True):
    now = int(time.time())
    m.save({"id_token": jwt(sub="u", exp=now + life_left, iat=now + life_left - lifetime), **({"refresh_token": "r1"} if refresh else {})})


@pytest.mark.parametrize("left,lifetime,refreshes", [(3600, 3600, False), (600, 3600, False), (301, 3600, False), (299, 3600, True),
                                                    (-5, 3600, True), (100, 120, False), (50, 120, True), (200, 600, True), (400, 600, False)])
def test_refresh_follows_exp(m, monkeypatch, left, lifetime, refreshes):
    write_user(m, **BASE)
    save_tok(m, left, lifetime)
    calls = []
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: calls.append(data) or {"id_token": jwt(sub="u", exp=int(time.time()) + 3600, iat=int(time.time()))})
    t = m.get_token(m.config())
    assert bool(calls) is refreshes and t.count(".") == 2
    if refreshes:
        assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "r1"
        assert json.loads(m.TOKENS.read_text())["refresh_token"] == "r1"  # kept when the IdP does not rotate it


def test_rotated_refresh_token_saved(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, -1)
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"id_token": jwt(exp=int(time.time()) + 3600), "refresh_token": "r2"})
    m.get_token(m.config())
    assert json.loads(m.TOKENS.read_text())["refresh_token"] == "r2"


def test_expired_without_refresh_token_asks_login(m):
    write_user(m, **BASE)
    save_tok(m, -1, refresh=False)
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())


def test_not_signed_in(m):
    write_user(m, **BASE)
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())


def test_refresh_without_id_token_keeps_new_refresh(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, -1)
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"access_token": "opaque", "refresh_token": "r3"})
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())
    assert json.loads(m.TOKENS.read_text())["refresh_token"] == "r3"


@pytest.mark.parametrize("expires_in,refreshes", [(0, True), (60, False), (3600, False)])
def test_opaque_token_uses_expires_in(m, monkeypatch, expires_in, refreshes):
    write_user(m, **BASE, token_kind=ACCESS_KIND)
    m.save({"access_token": "opaque", "expires_in": expires_in, "refresh_token": "r"})
    calls = []
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: calls.append(1) or {"access_token": "new", "expires_in": 3600})
    assert m.get_token(m.config()) == ("new" if refreshes else "opaque")


def test_tokens_file_is_0600_and_atomic(m):
    m.save({"id_token": "x"})
    assert (m.TOKENS.stat().st_mode & 0o777) == 0o600
    assert not list(m.HOME_DIR.glob(".tokens.*.tmp"))


def test_concurrent_refresh_spends_refresh_token_once(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, -1)
    spent = []
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})

    def post(url, data):
        spent.append(data["refresh_token"])
        time.sleep(0.05)
        return {"id_token": jwt(exp=int(time.time()) + 3600, iat=int(time.time())), "refresh_token": f"r{len(spent) + 1}"}
    monkeypatch.setattr(m, "post_form", post)
    cfg = m.config()
    ts = [threading.Thread(target=m.get_token, args=(cfg,)) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert spent == ["r1"]


# ---------------- local proxy ----------------

class FakeResp:
    length = None  # as http.client for a chunked or close-delimited body; a Content-Length body counts down

    def __init__(self, status, chunks, headers):
        import http.client
        self.status, self._chunks, self._headers = status, list(chunks), headers
        self.msg = http.client.HTTPMessage()
        for k, v in headers:
            self.msg[k] = v

    def getheaders(self):
        return self._headers

    def read1(self, n):
        return self._chunks.pop(0) if self._chunks else b""


@pytest.fixture()
def prox(m, monkeypatch):
    write_user(m, **BASE, proxy_port=0, websearch_url="https://ws.example.com/mcp")
    monkeypatch.setattr(m, "get_token", lambda cfg: "IDP-TOKEN")
    seen = []

    class Conn:
        debuglevel = 0

        def __init__(self, host, port, timeout):
            seen.append({"host": host, "port": port})

        def request(self, method, path, body=None, headers=None):
            seen[-1].update(method=method, path=path, body=body, headers=headers)

        def getresponse(self):
            return FakeResp(200, [b"data: 1\n\n", b"data: 2\n\n"], [("Content-Type", "text/event-stream"), ("Content-Length", "99")])

        def close(self):
            pass

    import http.server
    monkeypatch.setattr(m, "UPSTREAM", Conn)
    holder = {}
    real = m._ProxyServer  # r196: the proxy binds through its own server class (exclusive on Windows)

    class Capture(real):
        def __init__(self, addr, h):
            super().__init__(addr, h)
            holder["s"] = self
    monkeypatch.setattr(m, "_ProxyServer", Capture)
    t = threading.Thread(target=m.proxy, daemon=True)
    t.start()
    for _ in range(100):
        if "s" in holder:
            break
        time.sleep(0.02)
    s = holder["s"]
    yield m, f"http://127.0.0.1:{s.server_address[1]}", seen, m.local_key()
    s.shutdown()


def req(url, path, key=None, body=b'{"model":"ow/kimi-k3"}', method="POST", extra=None):
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {}), **(extra or {})}
    r = urllib.request.Request(url + path, data=body if method == "POST" else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:  # nosec B310 - test client calling the local proxy under test on 127.0.0.1
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_proxy_binds_loopback_only(prox):
    m, url, seen, key = prox
    assert url.startswith("http://127.0.0.1:")


@pytest.mark.parametrize("bad", [None, "wrong", "ocgw-", "Basic x"])
def test_proxy_rejects_wrong_key(prox, bad):
    m, url, seen, key = prox
    code, _ = req(url, "/inference/v1/chat/completions", key=bad)
    assert code == 401 and not seen


@pytest.mark.parametrize("path", ["/inference/v1/embeddings", "/v1/chat/completions", "/admin", "/inference/v1/chat/completions/../x", "/"])
def test_proxy_rejects_other_paths(prox, path):
    m, url, seen, key = prox
    code, _ = req(url, path, key=key)
    assert code == 404 and not seen


@pytest.mark.parametrize("path,method", [("/inference/v1/chat/completions", "POST"), ("/inference/v1/responses", "POST"),
                                         ("/inference/v1/models", "GET"), ("/inference/websearch", "POST")])
def test_proxy_forwards_with_fresh_token(prox, path, method):
    m, url, seen, key = prox
    code, body = req(url, path, key=key, method=method, extra={"X-Custom": "1", "Host": "evil", "Accept-Encoding": "gzip"})
    assert code == 200 and body == b"data: 1\n\ndata: 2\n\n"
    s = seen[-1]
    assert s["host"] == "gw.example.com" and s["path"] == path and s["method"] == method
    assert s["headers"]["Authorization"] == "Bearer IDP-TOKEN"
    assert key not in json.dumps(s["headers"])
    # r174: an allow-list (FORWARD_HEADERS): Content-Type goes on, a custom header stays on this machine.
    assert "Host" not in s["headers"] and "Accept-Encoding" not in s["headers"] and "X-Custom" not in s["headers"]
    assert s["headers"]["Content-Type"] == "application/json"


def test_proxy_sign_in_needed(prox, monkeypatch):
    m, url, seen, key = prox
    monkeypatch.setattr(m, "get_token", lambda cfg: (_ for _ in ()).throw(SystemExit("ocgw: not signed in, run `ocgw login`")))
    code, body = req(url, "/inference/v1/chat/completions", key=key)
    assert code == 401 and b"ocgw login" in body and not seen


def test_proxy_body_cap(prox):
    m, url, seen, key = prox
    code, _ = req(url, "/inference/v1/chat/completions", key=key, extra={"Content-Length": str(40 * 1024 * 1024)}, body=b"{}")
    assert code in (413, 400) and not seen


def test_local_key_is_private_and_stable(m):
    k1, k2 = m.local_key(), m.local_key()
    assert k1 == k2 and k1.startswith("ocgw-") and len(k1) > 40
    assert (m.LOCAL_KEY.stat().st_mode & 0o777) == 0o600


def test_proxy_needs_https_gateway(m):
    write_user(m, **{**BASE, "gateway_url": "http://gw.example.com"})
    with pytest.raises(SystemExit):
        m.proxy()


# ---------------- opencode-config ----------------

class Listing(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_opencode_config_merges(m, monkeypatch, tmp_path):
    write_user(m, **BASE)
    monkeypatch.setattr(m, "get_token", lambda cfg: "T")
    seen = {}

    def fake_open(r, timeout):
        seen["url"], seen["auth"] = r.full_url, r.headers.get("Authorization")
        return Listing(json.dumps({"data": [{"id": "ow/kimi-k3", "name": "kimi-k3"}, {"id": "ow/glm-5.3"}]}).encode())
    monkeypatch.setattr(m, "_urlopen", fake_open)
    p = tmp_path / "opencode.json"
    p.write_text(json.dumps({"$schema": "https://opencode.ai/config.json", "theme": "x", "provider": {"other": {"npm": "y"}}}))
    m.opencode_config(p)
    doc = json.loads(p.read_text())
    assert doc["theme"] == "x" and doc["provider"]["other"] == {"npm": "y"}
    oc = doc["provider"]["ocgw"]
    assert oc["npm"] == "@ai-sdk/openai-compatible"
    assert oc["options"]["baseURL"].startswith("http://127.0.0.1:") and oc["options"]["apiKey"] == "{file:" + str(m.LOCAL_KEY) + "}"
    assert set(oc["models"]) == {"ow/kimi-k3", "ow/glm-5.3"} and oc["models"]["ow/glm-5.3"]["name"] == "ow/glm-5.3"
    assert seen == {"url": "https://gw.example.com/inference/v1/models", "auth": "Bearer T"}
    assert m.LOCAL_KEY.exists()


def test_opencode_config_creates_file(m, monkeypatch, tmp_path):
    write_user(m, **BASE)
    monkeypatch.setattr(m, "get_token", lambda cfg: "T")
    monkeypatch.setattr(m, "_urlopen", lambda r, timeout: Listing(b'{"data": []}'))
    p = tmp_path / "sub" / "opencode.json"
    m.opencode_config(p)
    assert json.loads(p.read_text())["provider"]["ocgw"]["models"] == {}


def test_opencode_config_gateway_error(m, monkeypatch, tmp_path):
    write_user(m, **BASE)
    monkeypatch.setattr(m, "get_token", lambda cfg: "T")

    def boom(r, timeout):
        raise m.urllib.error.URLError("down")
    monkeypatch.setattr(m, "_urlopen", boom)
    with pytest.raises(SystemExit, match="could not list models"):
        m.opencode_config(tmp_path / "o.json")


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_proxy_forwards_mcp_to_websearch_gateway(prox, method):
    m, url, seen, key = prox
    code, _ = req(url, "/mcp", key=key, method=method, body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    assert code == 200 and seen[-1]["host"] == "ws.example.com" and seen[-1]["path"] == "/mcp"
    assert seen[-1]["headers"]["Authorization"] == "Bearer IDP-TOKEN"


def test_proxy_mcp_needs_key(prox):
    m, url, seen, key = prox
    assert req(url, "/mcp", key="nope")[0] == 401 and not seen


def test_proxy_mcp_404_without_websearch(m, monkeypatch):
    write_user(m, **BASE)
    assert m.config()["websearch_url"] == ""


def test_default_scope_needs_no_custom_groups_scope(m):
    write_user(m, **BASE)
    # Okta's default authorization server has no "groups" scope; asking for one fails sign-in with invalid_scope.
    assert m.config()["scope"].split() == ["openid", "email", "profile", "offline_access"]


def test_proxy_error_closes_keepalive_so_unread_body_is_not_parsed_as_next_request(prox):
    import http.client
    m, url, seen, key = prox
    port = int(url.rsplit(":", 1)[1])
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", "/inference/v1/chat/completions", body=b'{"x": "' + b"a" * 50_000 + b'"}',
              headers={"Content-Type": "application/json", "Authorization": "Bearer wrong"})
    r = c.getresponse()
    r.read()
    assert r.status == 401 and r.getheader("Connection", "").lower() == "close" and r.will_close
    c.close()


def test_proxy_returns_json_502_when_gateway_unreachable(prox, monkeypatch):
    m, url, seen, key = prox

    class Down:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise ConnectionResetError("reset by peer")

        def close(self):
            pass
    monkeypatch.setattr(m, "UPSTREAM", Down)
    code, body = req(url, "/inference/v1/chat/completions", key=key)
    assert code == 502 and json.loads(body)["error"]["type"] == "ocgw_proxy_error"


def test_proxy_returns_json_502_when_idp_unreachable(prox, monkeypatch):
    m, url, seen, key = prox

    def boom(cfg):
        raise OSError("discovery timed out")
    monkeypatch.setattr(m, "get_token", boom)
    code, body = req(url, "/inference/v1/chat/completions", key=key)
    assert code == 502 and "refresh" in json.loads(body)["error"]["message"] and not seen


@pytest.mark.parametrize("hdrs,want", [({"Transfer-Encoding": "chunked"}, 411), ({"Content-Length": "-1"}, 400), ({"Content-Length": "x"}, 400)])
def test_proxy_refuses_bodies_it_cannot_frame(prox, hdrs, want):
    import http.client
    m, url, seen, key = prox
    c = http.client.HTTPConnection("127.0.0.1", int(url.rsplit(":", 1)[1]), timeout=5)
    c.putrequest("POST", "/inference/v1/chat/completions")
    c.putheader("Authorization", f"Bearer {key}")
    for k, v in hdrs.items():
        c.putheader(k, v)
    c.endheaders()
    r = c.getresponse()
    r.read()
    assert r.status == want and not seen
    c.close()


def test_proxy_midstream_failure_is_not_a_complete_response(prox, monkeypatch):
    import http.client
    m, url, seen, key = prox
    closed = []

    class Breaks(FakeResp):
        def read1(self, n):
            if self._chunks:
                return self._chunks.pop(0)
            raise ConnectionResetError("reset mid-stream")

    class Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Breaks(200, [b"data: 1\n\n"], [("Content-Type", "text/event-stream")])

        def close(self):
            closed.append(True)
    monkeypatch.setattr(m, "UPSTREAM", Conn)
    c = http.client.HTTPConnection("127.0.0.1", int(url.rsplit(":", 1)[1]), timeout=5)
    c.request("POST", "/inference/v1/chat/completions", body=b"{}", headers={"Authorization": f"Bearer {key}"})
    r = c.getresponse()
    with pytest.raises(http.client.IncompleteRead):
        r.read()
    assert closed == [True]


def test_proxy_content_length_body_cut_short_is_not_a_complete_response(prox, monkeypatch):
    # r168 /code-review: read1() returns b"" at early EOF on a Content-Length body, so the proxy terminated a short
    # reply as if it were whole. It now leaves it unterminated, as for any failed stream.
    import http.client
    m, url, seen, key = prox

    class Short(FakeResp):
        length = 40  # bytes still owed when the upstream closed

    class Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Short(200, [b'{"choices": ['], [("Content-Type", "application/json")])

        def close(self):
            pass
    monkeypatch.setattr(m, "UPSTREAM", Conn)
    c = http.client.HTTPConnection("127.0.0.1", int(url.rsplit(":", 1)[1]), timeout=5)
    c.request("POST", "/inference/v1/chat/completions", body=b"{}", headers={"Authorization": f"Bearer {key}"})
    r = c.getresponse()
    with pytest.raises(http.client.IncompleteRead):
        r.read()


def test_token_lock_serialises_threads(m):
    import threading as th
    inside, peak, lock = [0], [0], th.Lock()

    def worker():
        with m.token_lock():
            with lock:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.02)
            with lock:
                inside[0] -= 1
    ts = [th.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1


class _FakeMsvcrt:
    LK_LOCK, LK_UNLCK = 1, 0

    def __init__(self, errs):
        self.errs, self.calls = list(errs), []

    def locking(self, fd, mode, n):
        self.calls.append(mode)
        if mode == self.LK_LOCK and self.errs:
            raise OSError(self.errs.pop(0), "lock")


def _windows(monkeypatch, fake):
    import sys
    monkeypatch.setitem(sys.modules, "fcntl", None)  # import fcntl -> ImportError, as on Windows
    monkeypatch.setitem(sys.modules, "msvcrt", fake)


def test_windows_lock_retries_contention_then_locks(m, monkeypatch):
    import errno
    fake = _FakeMsvcrt([errno.EDEADLK, errno.EACCES])
    _windows(monkeypatch, fake)
    with m.token_lock():
        pass
    assert fake.calls == [1, 1, 1, 0]


@pytest.mark.parametrize("errs", [["EBADF"], ["EDEADLK"] * 6])
def test_windows_lock_gives_up_instead_of_spinning(m, monkeypatch, errs):
    import errno
    fake = _FakeMsvcrt([getattr(errno, e) for e in errs])
    _windows(monkeypatch, fake)
    with pytest.raises(SystemExit):
        with m.token_lock():
            pass
    assert len(fake.calls) == len(errs)
    assert m._THREAD_LOCK.acquire(blocking=False)  # released on the way out
    m._THREAD_LOCK.release()


def test_proxy_forwards_mcp_session_delete(prox):
    m, url, seen, key = prox
    code, _ = req(url, "/mcp", key=key, method="DELETE")
    assert code == 200 and seen[-1]["method"] == "DELETE" and seen[-1]["host"] == "ws.example.com"


@pytest.mark.parametrize("method,path", [("DELETE", "/inference/v1/chat/completions"), ("PUT", "/mcp"), ("PATCH", "/inference/v1/models")])
def test_proxy_other_methods_get_json_405(prox, method, path):
    m, url, seen, key = prox
    code, body = req(url, path, key=key, method=method)
    assert code == 405 and json.loads(body)["error"]["type"] == "ocgw_proxy_error" and not seen


def test_proxy_204_has_no_body_and_keepalive_survives(prox, monkeypatch):
    import http.client
    m, url, seen, key = prox
    real = m.UPSTREAM

    class NoContent(real):
        def getresponse(self):
            return FakeResp(204, [], [("Mcp-Session-Id", "s1")]) if seen[-1]["method"] == "DELETE" else super().getresponse()
    monkeypatch.setattr(m, "UPSTREAM", NoContent)
    port = int(url.rsplit(":", 1)[1])
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("DELETE", "/mcp", headers={"Authorization": f"Bearer {key}"})
    r = c.getresponse()
    assert r.status == 204 and r.read() == b""
    c.request("GET", "/inference/v1/models", headers={"Authorization": f"Bearer {key}"})  # same connection
    r = c.getresponse()
    assert r.status == 200 and r.read() == b"data: 1\n\ndata: 2\n\n"
    c.close()


@pytest.mark.parametrize("path", ["/inference/v1/models?x=y", "/mcp?session=1"])
def test_proxy_refuses_query_strings_in_the_open(prox, path):
    m, url, seen, key = prox
    code, body = req(url, path, key=key, method="GET")
    assert code == 400 and "query" in json.loads(body)["error"]["message"] and not seen


def test_bad_discovery_document_is_not_cached(m, monkeypatch):
    docs = [{"issuer": "x"}, {"token_endpoint": "https://idp/t", "authorization_endpoint": "https://idp/a"}]
    monkeypatch.setattr(m, "_fetch_discovery", lambda issuer: docs.pop(0))
    m._DISCOVERY.clear()
    with pytest.raises(ValueError):
        m.discovery("https://idp")
    assert m.discovery("https://idp")["token_endpoint"] == "https://idp/t"  # fetched again, then cached


@pytest.mark.parametrize("saved", ["{not json", "[]", "{}"])
def test_bad_saved_session_asks_for_login(m, saved):
    write_user(m, **BASE)
    m.TOKENS.parent.mkdir(parents=True, exist_ok=True)
    m.TOKENS.write_text(saved)
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())


def test_non_object_token_reply_is_a_value_error(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, life_left=-10)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp/t"})
    monkeypatch.setattr(m, "post_form", lambda url, data: ["not", "a", "dict"])
    with pytest.raises(ValueError):
        m.get_token(m.config())


def _p(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.mark.parametrize("tok", [
    {"id_token": "opaque-token"},                                         # no exp, no obtained_at
    {"id_token": "opaque-token", "expires_in": None, "obtained_at": 1},
    {"id_token": "opaque-token", "expires_in": 60, "obtained_at": "x"},
    {"id_token": "opaque-token", "expires_in": True, "obtained_at": 1},
    {"id_token": f"e30.{_p(b'[1, 2]')}.sig"},                               # payload is a list
    {"id_token": f"e30.{_p(b'not json')}.sig"},
    {"id_token": "e30.%%%.sig"},
    {"id_token": jwt(exp="soon")},
])
def test_unreadable_expiry_counts_as_expired(m, tok):
    assert m.expiry(tok, "id_token") == (0.0, None)


def test_readable_expiry(m):
    assert m.expiry({"id_token": jwt(exp=2000, iat=1000)}, "id_token") == (2000.0, 1000.0)
    assert m.expiry({"id_token": "opaque", "expires_in": 300, "obtained_at": 100}, "id_token") == (400.0, 300)


def test_a_fresh_short_lived_token_without_iat_is_not_due(m):
    now = int(time.time())
    # expires_in is the access token's (3600), not this 5-minute id_token's: the lifetime comes from obtained_at
    tok = {"id_token": jwt(exp=now + 300), "expires_in": 3600, "obtained_at": now}
    assert m.expiry(tok, "id_token") == (float(now + 300), 300.0)
    assert not m._due(tok, "id_token")  # the margin is capped below its 300 s life, so it is not refreshed at once
    assert m.expiry({"id_token": jwt(exp=now + 300)}, "id_token") == (float(now + 300), None)  # no obtained_at either


def test_a_fast_local_clock_falls_back_to_expires_in(m):
    now = int(time.time())
    # laptop clock 400 s ahead: the token is timed on the local clock (obtained_at + expires_in), so it is not due at once
    tok = {"id_token": jwt(exp=now + 300), "expires_in": 300, "obtained_at": now + 400}
    assert m.expiry(tok, "id_token") == (float(now + 700), 300.0)
    real = time.time
    try:
        m.time.time = lambda: real() + 400  # the same fast clock for the due check
        assert not m._due(tok, "id_token")
    finally:
        m.time.time = real
    assert m.expiry({"id_token": jwt(exp=now + 300), "expires_in": True, "obtained_at": now + 400}, "id_token") == (float(now + 300), None)


@pytest.mark.parametrize("code,exc", [(503, OSError), (500, OSError), (429, OSError), (400, SystemExit), (401, SystemExit)])
def test_token_endpoint_5xx_is_retryable_and_4xx_asks_for_a_sign_in(m, monkeypatch, code, exc):
    import urllib.error  # noqa: PLC0415

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b"err"))
    monkeypatch.setattr(m, "_urlopen", boom)
    with pytest.raises(exc):
        m.post_form("https://idp.example.com/token", {"grant_type": "refresh_token"})


def test_a_valid_token_is_returned_without_the_lock(m, monkeypatch):
    write_user(m, **BASE)
    good = jwt(exp=int(time.time()) + 3600, iat=int(time.time()))
    m.TOKENS.write_text(json.dumps({"id_token": good}))
    monkeypatch.setattr(m, "token_lock", lambda: (_ for _ in ()).throw(AssertionError("took the lock")))
    assert m.get_token(m.config()) == good  # another request's slow refresh cannot hold this one up


def test_a_token_due_for_refresh_still_takes_the_lock(m, monkeypatch):
    write_user(m, **BASE)
    m.TOKENS.write_text(json.dumps({"id_token": jwt(exp=int(time.time()) + 5, iat=int(time.time()) - 3595)}))
    taken = []
    monkeypatch.setattr(m, "token_lock", lambda: (taken.append(1), (_ for _ in ()).throw(SystemExit("locked")))[1])
    with pytest.raises(SystemExit):
        m.get_token(m.config())
    assert taken == [1]


def test_unreadable_expiry_without_refresh_asks_for_a_sign_in(m):
    write_user(m, **BASE)
    m.TOKENS.write_text(json.dumps({"id_token": f"e30.{_p(b'[]')}.sig"}))
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())


def test_local_key_written_by_many_threads_at_once_is_never_empty(m):
    """Two first runs racing: both get the same, complete key and no stray temp file is left."""
    import threading
    keys, errs = [], []

    def run():
        try:
            keys.append(m.local_key())
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and len(set(keys)) == 1 and keys[0].startswith("ocgw-") and len(keys[0]) > 20
    assert [x.name for x in m.LOCAL_KEY.parent.iterdir() if x.name.startswith(".local.key")] == []


def test_login_gives_up_when_no_sign_in_comes_back(m, monkeypatch):
    # r62: an abandoned browser sign-in left `ocgw login` blocked forever, holding the callback port.
    import http.server
    monkeypatch.setattr(m, "LOGIN_WAIT_S", 2)
    monkeypatch.setattr(m, "PORT", 0)
    monkeypatch.setattr(m.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(m, "config", lambda: {"issuer": "https://idp.example", "client_id": "c", "scope": "openid"})
    monkeypatch.setattr(m, "discovery", lambda iss: {"authorization_endpoint": "https://idp.example/auth"})
    closed = []
    real_close = http.server.HTTPServer.server_close
    monkeypatch.setattr(http.server.HTTPServer, "server_close", lambda self: closed.append(1) or real_close(self))
    with pytest.raises(SystemExit, match="no sign-in came back"):
        m.login(open_browser=False)
    assert closed


def test_a_refresh_without_id_token_drops_the_old_one_instead_of_re_dating_it(m, monkeypatch):
    # r80: the old id_token (exp, no iat) was saved again under a new obtained_at, so expiry() timed it afresh from
    # expires_in and the proxy forwarded an expired token until then.
    write_user(m, **BASE)
    m.save({"id_token": jwt(sub="u", exp=int(time.time()) - 10), "refresh_token": "r1", "expires_in": 0})  # due
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"access_token": "opaque", "refresh_token": "r3", "expires_in": 3600})
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())
    saved = json.loads(m.TOKENS.read_text())
    assert "id_token" not in saved and saved["refresh_token"] == "r3"
    with pytest.raises(SystemExit, match="ocgw login"):  # the next request asks for a sign-in, never the stale token
        m.get_token(m.config())


def test_a_session_left_without_an_id_token_refreshes_again(m, monkeypatch):
    # r81: after a refresh that returned no id_token, every later request exited "no id_token" and the saved
    # refresh token was never used; it is now tried again, and a refresh that returns one signs the user back in.
    write_user(m, **BASE)
    m.save({"refresh_token": "r3"})
    stale = json.loads(m.TOKENS.read_text())
    m.TOKENS.write_text(json.dumps({**stale, "obtained_at": stale["obtained_at"] - m.MISSING_RETRY_S - 1}))
    calls = []
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: calls.append(data["refresh_token"]) or {
        "id_token": jwt(sub="u", exp=int(time.time()) + 3600, iat=int(time.time()))})
    assert m.get_token(m.config()).count(".") == 2 and calls == ["r3"]


def test_a_session_without_its_token_is_not_refreshed_on_every_request(m, monkeypatch):
    # r82: an IdP that never returns an id_token on refresh got one refresh grant per proxied model call.
    write_user(m, **BASE)
    m.save({"refresh_token": "r3"})  # just written by a refresh that returned no id_token
    calls = []
    monkeypatch.setattr(m, "discovery", lambda iss: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: calls.append(1) or {})
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())
    assert calls == []


def test_login_refuses_a_reply_without_the_token(m, monkeypatch):
    # r82: a sign-in reply without an id_token was saved ("Signed in as None") and left a session that cannot work.
    import socket
    import threading
    import urllib.parse
    import urllib.request
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(m, "PORT", port)
    monkeypatch.setattr(m, "LOGIN_WAIT_S", 10)
    monkeypatch.setattr(m, "config", lambda: {"issuer": "https://idp.example", "client_id": "c", "scope": "openid",
                                              "token_kind": "id_token"})
    monkeypatch.setattr(m, "discovery", lambda iss: {"authorization_endpoint": "https://idp.example/auth",
                                                     "token_endpoint": "https://idp.example/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"access_token": "opaque"})

    def browser(url):  # the IdP redirects back to the loopback callback with our state and a code
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        threading.Thread(target=lambda: opener.open(f"http://127.0.0.1:{port}/cb?state={state}&code=x", timeout=5).read(),
                         daemon=True).start()
    monkeypatch.setattr(m.webbrowser, "open", browser)
    with pytest.raises(SystemExit, match="returned no id_token"):
        m.login(open_browser=True)
    assert not m.TOKENS.exists()


@pytest.mark.parametrize("existing", ['{"provider": null}', '{"provider": []}', '[1, 2]'])
def test_opencode_config_refuses_a_file_it_cannot_merge_into(m, monkeypatch, tmp_path, existing):
    # r110: a message, not a TypeError traceback, and the file is left as it was.
    write_user(m, **BASE)
    monkeypatch.setattr(m, "get_token", lambda cfg: "T")
    monkeypatch.setattr(m, "_urlopen", lambda r, timeout: Listing(b'{"data": []}'))
    p = tmp_path / "opencode.json"
    p.write_text(existing)
    with pytest.raises(SystemExit, match="must be a JSON object"):
        m.opencode_config(p)
    assert p.read_text() == existing


def test_proxy_refuses_a_chunked_body_before_refreshing_the_sign_in(prox, monkeypatch):
    # r110: framing is checked first, so a request that will be refused never spends an IdP refresh.
    import http.client
    m, url, seen, key = prox
    calls = []
    monkeypatch.setattr(m, "get_token", lambda cfg: calls.append(1) or "T")
    c = http.client.HTTPConnection("127.0.0.1", int(url.rsplit(":", 1)[1]), timeout=5)
    c.putrequest("POST", "/inference/v1/chat/completions")
    c.putheader("Authorization", f"Bearer {key}")
    c.putheader("Transfer-Encoding", "chunked")
    c.endheaders()
    r = c.getresponse()
    r.read()
    assert r.status == 411 and not calls and not seen
    c.close()


def test_save_retries_a_rename_windows_refuses(m, monkeypatch):
    # r111: PermissionError while a reader has tokens.json open is retried, not raised.
    real, calls = m.os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        return real(src, dst)
    monkeypatch.setattr(m.os, "replace", flaky)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    m.save({"access_token": "a", "refresh_token": "r"})
    assert len(calls) == 3 and json.loads(m.TOKENS.read_text())["refresh_token"] == "r"


def test_opencode_config_explains_a_jsonc_file(m, monkeypatch, tmp_path):
    write_user(m, **BASE)
    monkeypatch.setattr(m, "get_token", lambda cfg: "T")
    monkeypatch.setattr(m, "_urlopen", lambda r, timeout: Listing(b'{"data": []}'))
    p = tmp_path / "opencode.json"
    p.write_text('{\n  // mine\n  "theme": "x"\n}\n')
    with pytest.raises(SystemExit, match="JSONC"):
        m.opencode_config(p)
    assert "// mine" in p.read_text()


def test_opencode_config_keeps_permissions_and_symlinks(m, monkeypatch, tmp_path):
    # r112: the rewrite goes through a symlink and keeps a 0600 file at 0600; no temp file is left behind.
    write_user(m, **BASE)
    monkeypatch.setattr(m, "get_token", lambda cfg: "T")
    monkeypatch.setattr(m, "_urlopen", lambda r, timeout: Listing(b'{"data": []}'))
    real = tmp_path / "dotfiles" / "opencode.json"
    real.parent.mkdir()
    real.write_text('{"theme": "x"}')
    import os
    os.chmod(real, 0o600)
    link = tmp_path / "opencode.json"
    link.symlink_to(real)
    m.opencode_config(link)
    assert link.is_symlink() and json.loads(real.read_text())["theme"] == "x"
    assert (real.stat().st_mode & 0o777) == 0o600
    assert not [p for p in real.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_rejected_refresh_drops_the_refresh_token(m, monkeypatch):
    # r112: invalid_grant once, then "run ocgw login" without asking the IdP again.
    write_user(m, **BASE)
    save_tok(m, -1)  # expired, refresh token r1
    calls = []
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})

    def refused(url, data):
        calls.append(data)
        raise m.TokenRefused("ocgw: token endpoint 400: invalid_grant", "invalid_grant")
    monkeypatch.setattr(m, "post_form", refused)
    with pytest.raises(SystemExit, match="session expired, run `ocgw login`"):  # r175: the fix, not the IdP's body
        m.get_token(m.config())
    with pytest.raises(SystemExit, match="ocgw login"):
        m.get_token(m.config())
    assert len(calls) == 1 and "refresh_token" not in json.loads(m.TOKENS.read_text())


@pytest.mark.parametrize("error", ["invalid_client", "", "unauthorized_client"])
def test_a_refusal_other_than_invalid_grant_keeps_the_refresh_token(m, monkeypatch, error):
    # r113: a mistyped client_id or a proxy 403 must not cost every user their session.
    write_user(m, **BASE)
    save_tok(m, -1)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(m.TokenRefused("ocgw: 400", error)))
    with pytest.raises(SystemExit):
        m.get_token(m.config())
    assert json.loads(m.TOKENS.read_text())["refresh_token"] == "r1"


def test_dropping_a_dead_refresh_token_keeps_obtained_at(m, monkeypatch):
    # r113: an opaque token timed by obtained_at + expires_in must not look fresh after the rewrite.
    write_user(m, **BASE)
    m.save({"id_token": "opaque", "refresh_token": "r1", "expires_in": 3600})
    tok = json.loads(m.TOKENS.read_text())
    tok["obtained_at"] = 1000
    m.TOKENS.write_text(json.dumps(tok))
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(m.TokenRefused("ocgw: 400", "invalid_grant")))
    with pytest.raises(SystemExit):
        m.get_token(m.config())
    after = json.loads(m.TOKENS.read_text())
    assert after["obtained_at"] == 1000 and "refresh_token" not in after


def test_post_form_reports_the_oauth_error_code(m, monkeypatch):
    import io
    import urllib.error

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error": "invalid_grant"}'))
    monkeypatch.setattr(m, "_urlopen", refuse)
    with pytest.raises(m.TokenRefused) as e:
        m.post_form("https://idp.example.com/token", {})
    assert e.value.error == "invalid_grant" and "400" in str(e.value)


def test_post_form_reads_a_long_invalid_grant_body(m, monkeypatch):
    # r114: Ory Hydra (JumpCloud) sends a long error_description; the code must still be read.
    import io
    import urllib.error
    body = json.dumps({"error": "invalid_grant", "error_description": "x" * 2000}).encode()
    monkeypatch.setattr(m, "_urlopen", lambda req, timeout: (_ for _ in ()).throw(
        urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))))
    with pytest.raises(m.TokenRefused) as e:
        m.post_form("https://idp.example.com/token", {})
    assert e.value.error == "invalid_grant" and len(str(e.value)) < 400


@pytest.mark.parametrize("failure", ["refused", "down"])
def test_an_early_refresh_that_fails_keeps_the_valid_token(m, monkeypatch, failure):
    # r114: inside MARGIN the token still works; a failed refresh must not fail the request.
    write_user(m, **BASE)
    save_tok(m, 120)  # 2 minutes left: inside MARGIN, so a refresh is tried
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    err = m.TokenRefused("ocgw: 403", "") if failure == "refused" else OSError("token endpoint 503")
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(err))
    assert m.get_token(m.config()).count(".") == 2


@pytest.mark.parametrize("err", [ValueError("not JSON"), "invalid_grant"])
def test_any_failed_early_refresh_keeps_the_valid_token_and_backs_off(m, monkeypatch, err):
    # r115: a captive portal's HTML (ValueError) or invalid_grant inside MARGIN; one IdP call, then the cached token.
    write_user(m, **BASE)
    save_tok(m, 120)
    calls = []
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})

    def fail(url, data):
        calls.append(1)
        raise m.TokenRefused("ocgw: 400", "invalid_grant") if err == "invalid_grant" else err
    monkeypatch.setattr(m, "post_form", fail)
    for _ in range(3):
        assert m.get_token(m.config()).count(".") == 2
    assert len(calls) == 1


def test_ocgw_never_follows_a_redirect(m):
    # r173 /code-review: refusal holds for every caller, main() or not, and the process-wide opener is untouched.
    import urllib.request
    handlers = [type(h) for h in m._OPENER.handlers]
    assert m._NoRedirect in handlers and urllib.request.HTTPRedirectHandler not in handlers
    assert urllib.request._opener is None or m._NoRedirect not in [type(h) for h in urllib.request._opener.handlers]


def test_the_refresh_backoff_is_shared_through_tokens_json(m, monkeypatch):
    # r116: a new process (`ocgw token`) sees the backoff the proxy recorded.
    write_user(m, **BASE)
    save_tok(m, 120)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(OSError("down")))
    m.get_token(m.config())
    assert json.loads(m.TOKENS.read_text())["refresh_failed_at"] > 0
    monkeypatch.setattr(m, "post_form", lambda url, data: pytest.fail("asked the IdP inside the backoff"))
    assert m.get_token(m.config()).count(".") == 2


def test_an_early_refresh_without_an_id_token_keeps_the_valid_one(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, 120)
    before = json.loads(m.TOKENS.read_text())
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"access_token": "a", "refresh_token": "r2"})
    assert m.get_token(m.config()) == before["id_token"]
    after = json.loads(m.TOKENS.read_text())
    assert after["refresh_token"] == "r2" and after["obtained_at"] == before["obtained_at"]


def test_a_refresh_that_outlives_the_token_does_not_hand_it_out(m, monkeypatch):
    # r116: expiry is checked again after the attempt.
    write_user(m, **BASE)
    save_tok(m, 120)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    real = m.time.time

    def slow(url, data):
        monkeypatch.setattr(m.time, "time", lambda: real() + 600)  # the attempt took longer than the token's life
        raise OSError("timed out")
    monkeypatch.setattr(m, "post_form", slow)
    with pytest.raises(OSError):
        m.get_token(m.config())


def test_an_empty_key_file_is_refused(m, monkeypatch):
    m.HOME_DIR.mkdir(parents=True, exist_ok=True)
    m.LOCAL_KEY.write_text("")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="is empty"):
        m.local_key()


def test_the_refresh_backoff_holds_when_tokens_json_cannot_be_written(m, monkeypatch):
    # r117: the in-memory copy keeps the backoff when _store fails.
    write_user(m, **BASE)
    save_tok(m, 120)
    monkeypatch.setattr(m, "_REFRESH_FAILED_AT", [0.0])
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(OSError("down")))

    def unwritable(tok):
        raise OSError("read-only")
    monkeypatch.setattr(m, "_store", unwritable)
    m.get_token(m.config())
    monkeypatch.setattr(m, "post_form", lambda url, data: pytest.fail("asked the IdP inside the backoff"))
    assert m.get_token(m.config()).count(".") == 2


def test_a_key_file_that_disappears_while_waiting_is_refused_cleanly(m, monkeypatch):
    m.HOME_DIR.mkdir(parents=True, exist_ok=True)
    m.LOCAL_KEY.write_text("")
    monkeypatch.setattr(m.time, "sleep", lambda s: m.LOCAL_KEY.unlink(missing_ok=True))
    with pytest.raises(SystemExit, match="incomplete or missing"):
        m.local_key()


def test_an_expired_refresh_without_an_id_token_drops_the_old_access_token(m, monkeypatch):
    # r118: under the new obtained_at an old access_token would look fresh.
    write_user(m, **BASE)
    save_tok(m, -60)
    tok = json.loads(m.TOKENS.read_text())
    tok["access_token"] = STALE_VALUE
    m.TOKENS.write_text(json.dumps(tok))
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"refresh_token": "r2"})
    with pytest.raises(SystemExit):
        m.get_token(m.config())
    after = json.loads(m.TOKENS.read_text())
    assert "access_token" not in after and "id_token" not in after and after["refresh_token"] == "r2"


def test_an_expired_token_backs_off_after_a_failed_refresh(m, monkeypatch):
    # r119: expired + IdP down: the second request fails fast instead of asking the IdP again.
    write_user(m, **BASE)
    save_tok(m, -60)
    monkeypatch.setattr(m, "_REFRESH_FAILED_AT", [0.0])
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(OSError):
        m.get_token(m.config())
    monkeypatch.setattr(m, "post_form", lambda url, data: pytest.fail("asked the IdP inside the backoff"))
    with pytest.raises(OSError, match="try again"):
        m.get_token(m.config())


def test_a_null_refresh_token_from_the_idp_keeps_the_old_one(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, -60)
    monkeypatch.setattr(m, "_REFRESH_FAILED_AT", [0.0])
    now = int(time.time())
    fresh = jwt(sub="u", exp=now + 3600, iat=now)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"id_token": fresh, "refresh_token": None})
    assert m.get_token(m.config()) == fresh
    assert json.loads(m.TOKENS.read_text())["refresh_token"] == "r1"


def test_a_half_written_key_is_never_used(m, monkeypatch):
    m.HOME_DIR.mkdir(parents=True, exist_ok=True)
    m.LOCAL_KEY.write_text("ocgw-abc")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="incomplete"):
        m.local_key()


def test_ocgw_token_reports_a_failed_refresh_without_a_traceback(m, monkeypatch, capsys):
    write_user(m, **BASE)
    save_tok(m, -60)
    monkeypatch.setattr(m, "_REFRESH_FAILED_AT", [0.0])
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(m.sys, "argv", ["ocgw", "token"])
    with pytest.raises(SystemExit, match="could not refresh the sign-in token: down"):
        m.main()


def test_a_longer_key_is_accepted(m):
    m.HOME_DIR.mkdir(parents=True, exist_ok=True)
    m.LOCAL_KEY.write_text("ocgw-" + "a" * 64)
    assert m.local_key() == "ocgw-" + "a" * 64


def test_proxy_body_cut_short_by_a_clean_close_is_a_400(prox):
    import socket
    from urllib.parse import urlparse
    m, url, seen, key = prox
    u = urlparse(url)
    s = socket.create_connection((u.hostname, u.port), timeout=10)
    s.sendall(b"POST /inference/v1/chat/completions HTTP/1.1\r\nHost: " + u.netloc.encode() + b"\r\n"
              b"Authorization: Bearer " + key.encode() + b"\r\nContent-Length: 10\r\n\r\nabc")
    s.shutdown(socket.SHUT_WR)
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    s.close()
    assert data.startswith(b"HTTP/1.1 400") and b"shorter than its Content-Length" in data and not seen


def test_an_oversized_key_file_is_refused(m, monkeypatch):
    m.HOME_DIR.mkdir(parents=True, exist_ok=True)
    m.LOCAL_KEY.write_text("ocgw-" + "a" * 300)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="incomplete or missing"):
        m.local_key()


def test_proxy_body_read_reset_closes_without_a_reply_or_a_forward(prox):
    import socket
    import struct
    from urllib.parse import urlparse
    m, url, seen, key = prox
    u = urlparse(url)
    s = socket.create_connection((u.hostname, u.port), timeout=10)
    s.sendall(b"POST /inference/v1/chat/completions HTTP/1.1\r\nHost: " + u.netloc.encode() + b"\r\n"
              b"Authorization: Bearer " + key.encode() + b"\r\nContent-Length: 100000\r\n\r\nabc")
    time.sleep(0.3)  # the proxy is now reading the body
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()  # a reset mid-body: the read raises, and the proxy closes quietly
    time.sleep(0.3)
    code, _ = req(url, "/inference/v1/models", key=key, method="GET")
    assert code == 200 and all(x["path"] != "/inference/v1/chat/completions" for x in seen)


def test_a_valid_token_is_served_while_another_thread_refreshes(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, 120)  # inside MARGIN: due for a refresh, still valid
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(AssertionError("no second refresh")))
    m._REFRESHING.set()
    try:
        assert m.get_token(m.config()).count(".") == 2
    finally:
        m._REFRESHING.clear()


def test_a_failed_save_after_a_refresh_still_returns_the_new_token(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, -60)
    new = jwt(sub="u", exp=int(time.time()) + 3600, iat=int(time.time()))
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: {"id_token": new, "refresh_token": "r2"})
    monkeypatch.setattr(m, "_store", lambda tok: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(m, "_UNSAVED", [None, None])
    assert m.get_token(m.config()) == new


def test_a_write_after_an_unsaved_refresh_replaces_the_in_memory_copy(m, monkeypatch):
    """r127 review: once a save failed, a later refusal (invalid_grant) wrote tokens.json only and the stale in-memory
    copy, with its dead refresh token, kept winning. Every write now goes through one writer that updates both."""
    write_user(m, **BASE)
    save_tok(m, -60)
    monkeypatch.setattr(m, "_UNSAVED", [None, None])
    stale = {**json.loads(m.TOKENS.read_text()), "refresh_token": "r-dead"}
    m._UNSAVED[:] = [stale, m._file_stamp()]  # an earlier refresh could not be saved
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    calls = []

    def refuse(url, data):
        calls.append(data["refresh_token"])
        raise m.TokenRefused("ocgw: token endpoint 400: invalid_grant", "invalid_grant")
    monkeypatch.setattr(m, "post_form", refuse)
    with pytest.raises(SystemExit, match="session expired"):  # r175: an expired token's dead session names the fix
        m.get_token(m.config())
    assert calls == ["r-dead"] and m._UNSAVED == [None, None]  # the disk recovered, so the file is current again
    assert "refresh_token" not in json.loads(m.TOKENS.read_text())
    with pytest.raises(SystemExit, match="session expired"):  # the dead refresh token is not tried again
        m.get_token(m.config())
    assert calls == ["r-dead"]


def test_a_session_written_by_another_process_wins_over_the_unsaved_copy(m, monkeypatch):
    """r127 review: `ocgw login` in another shell writes tokens.json; a proxy holding an unsaved copy must use it."""
    import os  # noqa: PLC0415
    write_user(m, **BASE)
    now = int(time.time())
    old = jwt(sub="old", exp=now + 3600, iat=now)
    m._store({"id_token": old, "refresh_token": "r-old", "obtained_at": now})
    monkeypatch.setattr(m, "_UNSAVED", [None, None])
    m._UNSAVED[:] = [{"id_token": old, "refresh_token": "r-old", "obtained_at": now}, m._file_stamp()]
    new = jwt(sub="new", exp=now + 3600, iat=now)
    m._store({"id_token": new, "refresh_token": "r-new", "obtained_at": now})  # the other process's login
    st = m.TOKENS.stat()
    os.utime(m.TOKENS, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # a distinct stamp even on a coarse clock
    assert m.get_token(m.config()) == new


def test_proxy_refuses_an_invalid_gateway_port(m):
    write_user(m, **{**BASE, "gateway_url": "https://gw.example.com:44x3/"})
    with pytest.raises(SystemExit, match="invalid port"):
        m.proxy()


def test_tokens_that_could_not_be_saved_are_used_by_the_next_call(m, monkeypatch):
    write_user(m, **BASE)
    save_tok(m, -60)
    new = jwt(sub="u", exp=int(time.time()) + 3600, iat=int(time.time()))
    calls = []
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})
    monkeypatch.setattr(m, "post_form", lambda url, data: calls.append(data) or {"id_token": new, "refresh_token": "r2"})
    monkeypatch.setattr(m, "_store", lambda tok: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(m, "_UNSAVED", [None, None])
    assert m.get_token(m.config()) == new and m.get_token(m.config()) == new
    assert len(calls) == 1  # the second call used the in-memory tokens, not the spent refresh token


@pytest.mark.parametrize("headers", [[("Content-Length", "2"), ("Content-Length", "2")], [("Content-Length", "+2")],
                                     [("Content-Length", "1_0")]])
def test_proxy_refuses_non_rfc_content_length(prox, headers):
    import socket
    from urllib.parse import urlparse
    m, url, seen, key = prox
    u = urlparse(url)
    s = socket.create_connection((u.hostname, u.port), timeout=10)
    s.sendall(b"POST /inference/v1/chat/completions HTTP/1.1\r\nHost: " + u.netloc.encode() + b"\r\nAuthorization: Bearer "
              + key.encode() + b"\r\n" + b"".join(f"{k}: {v}\r\n".encode() for k, v in headers) + b"\r\n{}")
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    s.close()
    assert data.startswith(b"HTTP/1.1 400") and b"bad Content-Length" in data and not seen


@pytest.mark.parametrize("port", [70000, -1, "80x", 1.5])
def test_config_refuses_a_bad_proxy_port(m, port):
    write_user(m, **BASE, proxy_port=port)
    with pytest.raises(SystemExit, match="proxy_port"):
        m.config()


def test_a_null_refresh_token_means_the_session_ends_with_the_token(m, monkeypatch):
    write_user(m, **BASE)
    now = int(time.time())
    m.save({"id_token": jwt(sub="u", exp=now - 60, iat=now - 3660), "refresh_token": None})
    monkeypatch.setattr(m, "post_form", lambda url, data: (_ for _ in ()).throw(AssertionError("no refresh with None")))
    with pytest.raises(SystemExit, match="session expired"):
        m.get_token(m.config())


@pytest.mark.parametrize("tok, want", [
    ({"id_token": jwt(sub="s1", email="dev@example.com"), "access_token": jwt(sub="s1", username="dev")}, "dev@example.com"),
    ({"access_token": jwt(sub="s1", username="dev")}, "dev"),  # Cognito access token alone
    ({"access_token": jwt(sub="s1")}, "s1"),
    ({"access_token": "not-a-jwt"}, "unknown user"),
])
def test_sign_in_message_names_the_person(m, tok, want):
    """r128: on Cognito the handed-out token is the access token, which has no email; the message reads the ID token."""
    assert m._who(tok, "access_token") == want


def _raw_jwt(payload: bytes) -> str:
    b = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"e30.{b}.sig"


@pytest.mark.parametrize("payload", [b"[" * 100000 + b"]" * 100000, b"not json", b"[1, 2]", b"\xff\xfe"])
def test_claims_or_empty_never_raises(m, payload):
    """r129: a payload nested past the JSON parser's limit raises RecursionError, which neither expiry() nor the
    proxy caught; the one reader now returns {} for it, as for any unreadable or non-object payload."""
    tok = {"access_token": _raw_jwt(payload)}
    assert m._claims_or_empty(tok, "access_token") == {}
    assert m.expiry(tok, "access_token")[0] <= time.time()  # unreadable counts as expired
    assert m._who(tok, "access_token") == "unknown user"


def test_claims_command_shows_the_readable_tokens_only(m, monkeypatch, capsys):
    """r129: one unreadable token no longer stops `ocgw claims` from showing the other."""
    write_user(m, **BASE)
    m.save({"id_token": jwt(sub="u1", email="dev@example.com"), "access_token": _raw_jwt(b"not json")})
    monkeypatch.setattr(m.sys, "argv", ["ocgw", "claims"])
    m.main()
    out = json.loads(capsys.readouterr().out)
    assert list(out) == ["id_token"] and out["id_token"]["email"] == "dev@example.com"


@pytest.mark.parametrize("raw", ['["a"]', '"x"', "{not json", "null"])
def test_claims_command_on_an_unreadable_session_says_so(m, monkeypatch, raw):
    """r130 /code-review: a tokens.json that is not a JSON object gave a raw traceback."""
    write_user(m, **BASE)
    m.TOKENS.parent.mkdir(parents=True, exist_ok=True)
    m.TOKENS.write_text(raw)
    monkeypatch.setattr(m.sys, "argv", ["ocgw", "claims"])
    with pytest.raises(SystemExit) as e:
        m.main()
    assert "unreadable" in str(e.value)


def _plain_upstream(m, monkeypatch, body=b'{"ok": true}'):
    class Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return FakeResp(200, [body], [("Content-Type", "application/json")])

        def close(self):
            pass
    monkeypatch.setattr(m, "UPSTREAM", Conn)


def test_proxy_answers_a_malformed_request_line(prox, monkeypatch):
    import http.client
    # r169 /code-review: log_message read self.command and self.path, which the stdlib has not set for a bad request
    # line, so the logger raised and the client got nothing. It now gets the stdlib's 400.
    import socket
    m, url, seen, key = prox
    port = int(url.rsplit(":", 1)[1])
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"NOT A REQUEST LINE AT ALL\r\n\r\n")
    data = b""
    while chunk := s.recv(4096):  # the stdlib answers an unparsable line as HTTP/0.9 (no status line) and closes
        data += chunk
    s.close()
    assert b"400" in data  # its error page: the logger did not raise before the answer went out
    _plain_upstream(m, monkeypatch)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)  # and the proxy still serves the next client
    c.request("POST", "/inference/v1/chat/completions", body=b"{}", headers={"Authorization": f"Bearer {key}"})
    assert c.getresponse().read() == b'{"ok": true}'


def test_proxy_sends_an_http10_client_a_close_delimited_body(prox, monkeypatch):
    # r169 /code-review: chunked framing does not exist in HTTP/1.0.
    import socket
    m, url, seen, key = prox
    _plain_upstream(m, monkeypatch)
    s = socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])), timeout=5)
    s.sendall(f"POST /inference/v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer {key}\r\nContent-Length: 2\r\n\r\n{{}}".encode())
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    assert b"transfer-encoding" not in head.lower() and body == b'{"ok": true}'


def test_proxy_gives_an_http10_client_the_upstream_length(prox, monkeypatch):
    # r170 /code-review: a close-delimited body cut short looks whole; with the upstream's Content-Length it does not.
    import socket
    m, url, seen, key = prox

    class Sized(FakeResp):
        length = 12

        def read1(self, n):
            b = super().read1(n)
            self.length -= len(b)
            return b
    class Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Sized(200, [b'{"ok": true}'], [("Content-Type", "application/json")])

        def close(self):
            pass
    monkeypatch.setattr(m, "UPSTREAM", Conn)
    s = socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])), timeout=5)
    s.sendall(f"POST /inference/v1/chat/completions HTTP/1.0\r\nAuthorization: Bearer {key}\r\nContent-Length: 2\r\n\r\n{{}}".encode())
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    assert b"content-length: 12" in head.lower() and body == b'{"ok": true}'


def test_relay_reports_a_client_that_hung_up(m):
    # r170 /code-review: a client gone at any write (headers, a chunk, the terminator) is not an upstream failure.
    class Gone:
        def write(self, b):
            raise BrokenPipeError

        def flush(self):
            pass

    class H:
        request_version = "HTTP/1.1"
        close_connection = False
        wfile = Gone()

        def send_response(self, s):
            pass

        def send_header(self, k, v):
            pass

        def end_headers(self):
            raise BrokenPipeError
    assert m.relay_reply(H(), FakeResp(200, [b"x"], [])) == "client gone"
    h = H()
    h.end_headers = lambda: None
    assert m.relay_reply(h, FakeResp(200, [b"x"], [])) == "client gone" and h.close_connection


class _Rec:
    """A handler double that records what relay_reply writes."""
    request_version = "HTTP/1.1"
    close_connection = False

    def __init__(self):
        self.headers, self.body = [], b""
        rec = self

        class W:
            def write(self, b):
                rec.body += b

            def flush(self):
                pass
        self.wfile = W()

    def send_response(self, s):
        self.status = s

    def send_header(self, k, v):
        self.headers.append((k.lower(), v))

    def end_headers(self):
        pass


def test_relay_owns_framing_and_server_headers_whatever_the_skip_list(m):
    # r171 /code-review: the upstream's Server, Date, Content-Length and Connection never pass.
    h = _Rec()
    up = [("Server", "up"), ("Date", "x"), ("Content-Length", "1"), ("Connection", "keep-alive"), ("X-Keep", "1")]
    assert m.relay_reply(h, FakeResp(200, [b"x"], up)) == "ok"
    names = [k for k, _ in h.headers]
    assert names == ["x-keep", "transfer-encoding"] and h.body == b"1\r\nx\r\n0\r\n\r\n"


def test_relay_lets_a_failing_tee_reach_its_caller(m):
    # r171 /code-review: a tee error is the caller's, not reported as "upstream failed".
    def tee(_):
        raise OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        m.relay_reply(_Rec(), FakeResp(200, [b"x"], []), tee=tee)


def test_relay_refuses_an_interim_reply(m):
    # r172 /code-review: a 103 is not the answer; the client gets the proxy's 502 and the connection closes.
    h = _Rec()
    h.command = "POST"
    assert m.relay_reply(h, FakeResp(103, [b""], [("Link", "x")])) == "upstream failed: interim reply 103"
    assert h.status == 502 and h.close_connection and b"interim reply (103)" in h.body


def test_relay_says_connection_close_when_it_closes(m):
    # r172 /code-review: an HTTP/1.0 client's body ends at the close, and the reply says so.
    h = _Rec()
    h.request_version = "HTTP/1.0"
    assert m.relay_reply(h, FakeResp(200, [b"x"], [])) == "ok"
    assert ("connection", "close") in h.headers and h.close_connection


def test_relay_drops_headers_the_upstream_connection_header_names(m):
    # r173 /code-review: a header the upstream's Connection names is hop-by-hop for that reply (RFC 9110 7.6.1).
    h = _Rec()
    assert m.relay_reply(h, FakeResp(200, [b"x"], [("Connection", "X-Hop, close"), ("X-Hop", "1"), ("X-End", "2")])) == "ok"
    assert [k for k, _ in h.headers] == ["x-end", "transfer-encoding"]


def test_final_response_skips_interim_replies(m):
    # r173 /code-review: a 103 then a 200 reaches the proxy as the 200, so a model call that ran is never lost.
    import io
    raw = b"HTTP/1.1 103 Early Hints\r\nLink: </a>\r\n\r\nHTTP/1.1 102 Processing\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

    class Sock:
        def makefile(self, mode):
            return io.BytesIO(raw)
    r = m.FinalResponse(Sock())
    r.begin()
    assert r.status == 200 and r.read() == b"ok"


def _raw(url, data):
    import socket
    s = socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])), timeout=5)
    s.sendall(data)
    out = b""
    while chunk := s.recv(4096):
        out += chunk
    s.close()
    return out


def test_proxy_forwards_only_the_allow_listed_headers_each_once(prox):
    # r174 /code-review: cookies, Expect and Connection-named headers never reach the gateway; a repeated one is a 400.
    _, url, seen, key = prox
    head = f"POST /inference/v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\n"
    _raw(url, (head + "Accept: a\r\nCookie: c=1\r\nCookie: d=2\r\nConnection: X-Secret, close\r\nX-Secret: s\r\n"
               "Mcp-Session-Id: S1\r\nContent-Length: 2\r\n\r\n{}").encode())
    assert seen[-1]["headers"] == {"Accept": "a", "Mcp-Session-Id": "S1", "Authorization": "Bearer IDP-TOKEN"}
    n = len(seen)
    out = _raw(url, (head + "Accept: a\r\nAccept: b\r\nContent-Length: 2\r\n\r\n{}").encode())
    assert b" 400 " in out.split(b"\r\n")[0] and b"send the Accept header once" in out and len(seen) == n


def test_proxy_refuses_any_transfer_encoding_line_and_expect(prox):
    # r174 /code-review: a chunked body on a second Transfer-Encoding line is refused; Expect: 100-continue gets a 417
    # before any 100 Continue, so nothing is read or forwarded.
    _, url, seen, key = prox
    head = f"POST /inference/v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\n"
    out = _raw(url, (head + "Transfer-Encoding: identity\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n").encode())
    assert b" 411 " in out.split(b"\r\n")[0]
    out = _raw(url, (head + "Expect: 100-continue\r\nContent-Length: 2\r\n\r\n").encode())
    assert b" 417 " in out.split(b"\r\n")[0] and b"100 Continue" not in out and not seen


def test_proxy_answers_http09_by_closing(prox):
    # r174 /code-review: HTTP/0.9 has no status line or headers to answer with; nothing is sent, nothing forwarded.
    _, url, seen, _ = prox
    assert _raw(url, b"GET /inference/v1/models\r\n") == b"" and not seen


def test_an_expired_session_says_run_ocgw_login(m, monkeypatch):
    # r175 /code-review: invalid_grant with the cached token already expired names the fix, not the IdP's raw body.
    write_user(m, **BASE)
    save_tok(m, -10)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})

    def refused(url, data):
        raise m.TokenRefused('ocgw: token endpoint said 400: {"error":"invalid_grant"}', "invalid_grant")
    monkeypatch.setattr(m, "post_form", refused)
    with pytest.raises(SystemExit, match="run `ocgw login`") as e:
        m.get_token(m.config())
    assert str(e.value).startswith("ocgw: session expired, run `ocgw login`")


def test_a_refused_header_costs_no_sign_in_refresh_and_a_connection_named_one_stays(prox, monkeypatch):
    # r176 /code-review: framing and header checks come before get_token; an allowed header Connection names stays here.
    m, url, seen, key = prox
    calls = []
    monkeypatch.setattr(m, "get_token", lambda cfg: calls.append(1) or "IDP-TOKEN")
    head = f"POST /inference/v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {key}\r\n"
    out = _raw(url, (head + "Accept: a\r\nAccept: b\r\nContent-Length: 2\r\n\r\n{}").encode())
    assert b" 400 " in out.split(b"\r\n")[0] and not calls
    _raw(url, (head + "Connection: Mcp-Session-Id, close\r\nMcp-Session-Id: S1\r\nAccept: a\r\nContent-Length: 2\r\n\r\n{}").encode())
    assert "Mcp-Session-Id" not in seen[-1]["headers"] and seen[-1]["headers"]["Accept"] == "a"


def test_an_expired_session_keeps_the_idps_words(m, monkeypatch):
    # r176 /code-review: the fix first, and the IdP's refusal after it.
    write_user(m, **BASE)
    save_tok(m, -10)
    monkeypatch.setattr(m, "discovery", lambda issuer: {"token_endpoint": "https://idp.example.com/token"})

    def refused(url, data):
        raise m.TokenRefused("ocgw: token endpoint said 400: The refresh token is invalid or expired.", "invalid_grant")
    monkeypatch.setattr(m, "post_form", refused)
    with pytest.raises(SystemExit, match=r"session expired, run `ocgw login` \(token endpoint said 400: The refresh token"):
        m.get_token(m.config())



def test_the_windows_mdm_path_never_comes_from_the_environment(m, monkeypatch, tmp_path):
    """r192 threat model T-060: on Windows the MDM file path used to follow the ProgramData environment variable, so a
    repository's .env or the user's shell could choose which file counts as managed config. program_data() asks the
    shell's known-folder API and otherwise falls back to the fixed C:\\ProgramData; the environment is never read."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "chosen-by-a-repo"))
    assert m.program_data() == Path(r"C:\ProgramData")  # no shell32 off Windows: the fixed fallback, not the variable
    assert "chosen-by-a-repo" not in str(m.program_data())


# --- r196 (threat model cycle 12) ---

def test_the_lock_file_is_private_even_when_an_older_version_left_it_open(m):
    """r196: another user who could open a 0644 lock file could hold a lock on it and stop every refresh."""
    import os
    import stat
    m.HOME_DIR.mkdir(mode=0o700, parents=True)
    (m.HOME_DIR / ".lock").write_bytes(b"")
    os.chmod(m.HOME_DIR / ".lock", 0o644)
    with m.token_lock():
        pass
    assert stat.S_IMODE(os.stat(m.HOME_DIR / ".lock").st_mode) == 0o600


def test_a_lock_held_by_another_process_ends_in_a_message_not_a_hang(m, monkeypatch):
    """r196: the POSIX wait was unbounded; it now gives up after about a minute, as the Windows branch does."""
    import fcntl
    import os
    m.HOME_DIR.mkdir(mode=0o700, parents=True)
    (m.HOME_DIR / ".lock").write_bytes(b"")
    holder = os.open(m.HOME_DIR / ".lock", os.O_RDONLY)
    fcntl.flock(holder, fcntl.LOCK_SH)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    try:
        with pytest.raises(SystemExit, match="locked by another process"):
            with m.token_lock():
                pass
        assert m._THREAD_LOCK.acquire(blocking=False)
        m._THREAD_LOCK.release()
    finally:
        os.close(holder)


def test_windows_binds_exclusively_so_no_other_socket_shares_the_port(m, monkeypatch):
    """r196: http.server sets SO_REUSEADDR, which on Windows lets another account bind the same 127.0.0.1 port."""
    import socket
    monkeypatch.setattr(m.os, "name", "nt")
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), raising=False)
    seen = []

    class Sock:
        def setsockopt(self, *a):
            seen.append(a)

    class Base:
        def server_bind(self):
            seen.append("bound")

    class S(m._Exclusive, Base):
        pass
    s = S()
    s.socket = Sock()
    s.server_bind()
    assert seen == [(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1), "bound"]
    assert all(a[1] != socket.SO_REUSEADDR for a in seen if isinstance(a, tuple))


@pytest.mark.parametrize("owned,ok", [(True, True), (False, False)])
def test_windows_reads_the_programdata_file_only_when_an_administrator_owns_it(m, monkeypatch, owned, ok):
    """r196: every local user may create files under C:\\ProgramData, so a file MDM did not write must not count."""
    m.mdm.write_text(json.dumps({"issuer": "https://evil.example", "client_id": "x"}))
    monkeypatch.setattr(m.os, "name", "nt")
    monkeypatch.setattr(m, "admin_owned", lambda p: owned)
    if ok:
        assert m.config()["issuer"] == "https://evil.example"
    else:
        with pytest.raises(SystemExit, match="not owned by an administrator"):
            m.config()
