# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""dashboard/proxy.py: every GET needs the per-run key (cookie set by the printed link); the Host check still applies."""
import http.client
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "dashboard" / "proxy.py"


PYTHON = sys.executable  # the interpreter running this file; children run it too


def py_env(env):
    """env with this interpreter's directory first on PATH, so the child's argv[0] "python3" resolves to it."""
    return {**env, "PATH": os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")}


@pytest.fixture()
def proxy(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_PROFILE": ""}
    p = subprocess.Popen(["python3", "-B", str(SRC), "--port", str(port), "--url", "https://abc123.lambda-url.us-east-1.on.aws", "--profile", "",
                          "--region", "us-east-1"],  # r113: the default now follows AWS_REGION, so pin it
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, executable=PYTHON, env=py_env(env))
    line = p.stdout.readline()
    key = re.search(r"\?k=(\S+)", line).group(1)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield port, key
    p.kill()


def get(port, path, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **({"Cookie": cookie} if cookie else {})})
    r = c.getresponse()
    out = (r.status, r.getheader("Set-Cookie"), r.getheader("Location"))
    c.close()
    return out


def test_no_key_is_refused(proxy):
    port, _ = proxy
    assert get(port, "/api/v2/me")[0] == 401
    assert get(port, "/api/v2/me", cookie=f"ocgw_dash_{port}=wrong")[0] == 401


def test_printed_link_sets_cookie_and_strips_key(proxy):
    port, key = proxy
    status, cookie, loc = get(port, f"/?k={key}")
    assert status == 302 and loc == "/" and f"ocgw_dash_{port}={key}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert get(port, "/?k=nope")[0] == 401



@pytest.mark.parametrize("url", ["https://usage.example.com", "https://dash.invalid", "https://x.lambda-url.eu-west-1.on.aws.evil.com"])
def test_a_url_that_is_not_a_function_url_is_refused(url):
    env = {**os.environ, "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_PROFILE": ""}
    r = subprocess.run(["python3", "-B", str(SRC), "--port", "1", "--url", url, "--profile", ""], capture_output=True, text=True, executable=PYTHON, env=py_env(env), timeout=30)
    assert r.returncode != 0 and "not a Lambda Function URL" in r.stderr


def test_the_region_comes_from_the_url_not_the_shell(tmp_path):
    # r114: AWS_REGION for another region in the shell must not break a us-east-1 deployment.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_PROFILE": "",
           "AWS_REGION": "ap-southeast-2", "AWS_DEFAULT_REGION": "ap-southeast-2"}
    p = subprocess.Popen(["python3", "-B", str(SRC), "--port", str(port), "--url", "https://abc123.lambda-url.us-east-1.on.aws",
                          "--profile", ""], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, executable=PYTHON, env=py_env(env))
    try:
        assert "?k=" in p.stdout.readline()
    finally:
        p.kill()


def send(port, method, path="/", host=None):
    """Status, content-length header and body of one request, so a HEAD reply can be checked for a body."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, headers={"Host": host if host is not None else f"127.0.0.1:{port}"})
    r = c.getresponse()
    out = (r.status, r.getheader("content-length"), r.read())
    c.close()
    return out


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_a_write_method_gets_the_read_only_json(proxy, method):
    port, _ = proxy
    status, length, body = send(port, method)
    assert status == 405
    assert b"read-only" in body
    assert int(length) == len(body)


def test_head_answers_without_a_body(proxy):
    # RFC 9110: a HEAD reply carries the headers its GET would and no body. A body here would be read as the start of
    # the next reply on a kept-alive connection.
    status, length, body = send(port := proxy[0], "HEAD")
    assert (status, body) == (405, b"")
    assert int(length) > 0  # the length the GET would have sent
    assert send(port, "GET", "/api/v2/me")[0] == 401  # the proxy is still serving


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "DELETE", "OPTIONS"])
def test_another_host_name_is_refused_on_every_method(proxy, method):
    # DNS rebinding: a page on an attacker's name that resolves to 127.0.0.1 must not reach any handler.
    port, _ = proxy
    assert send(port, method, host="evil.example:" + str(port))[0] == 403


def test_the_per_run_key_is_not_written_to_the_log(tmp_path):
    """r192 threat model T-061: the first request carries the key as ?k=, and the access log printed it. The proxy now
    logs request lines without their query string."""
    port = None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_PROFILE": ""}
    p = subprocess.Popen(["python3", "-B", str(SRC), "--port", str(port), "--url", "https://abc123.lambda-url.us-east-1.on.aws",
                          "--profile", "", "--region", "us-east-1"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, executable=PYTHON, env=py_env(env))
    try:
        key = re.search(r"\?k=(\S+)", p.stdout.readline()).group(1)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.05)
        assert get(port, f"/?k={key}")[0] == 302
        line = p.stdout.readline()
        assert line.startswith("proxy:") and key not in line and "GET /" in line, line
    finally:
        p.kill()


def test_windows_binds_the_proxy_port_exclusively():
    """r196 threat model T-065: http.server sets SO_REUSEADDR, which on Windows lets another account's socket bind the
    same 127.0.0.1 port while the proxy listens. proxy.py runs on import, so its mixin is taken from the source."""
    import ast
    tree = ast.parse(SRC.read_text())
    mixin = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Exclusive")
    server = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ProxyServer")
    assert [ast.unparse(b) for b in server.bases] == ["_Exclusive", "ThreadingHTTPServer"]
    assert "_ProxyServer((" in SRC.read_text() and "\nThreadingHTTPServer((" not in SRC.read_text()
    fake_os = type("os", (), {"name": "nt"})
    fake_socket = type("socket", (), {"SOL_SOCKET": socket.SOL_SOCKET, "SO_REUSEADDR": socket.SO_REUSEADDR,
                                      "SO_EXCLUSIVEADDRUSE": getattr(socket, "SO_EXCLUSIVEADDRUSE", -5)})
    import importlib.util
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    (tmp / "proxy_mixin.py").write_text(ast.unparse(mixin) + "\n")  # the file's own class, nothing else

    def load():
        # Load it as a module whose os and socket are the fakes (r197: no exec, which Bandit reports as B102).
        spec = importlib.util.spec_from_file_location("proxy_mixin", tmp / "proxy_mixin.py")
        mod = importlib.util.module_from_spec(spec)
        mod.os, mod.socket = fake_os, fake_socket
        spec.loader.exec_module(mod)
        return mod
    ns = vars(load())
    seen = []

    class Sock:
        def setsockopt(self, *a):
            seen.append(a)

    class Base:
        def server_bind(self):
            seen.append("bound")

    class S(ns["_Exclusive"], Base):
        pass
    s = S()
    s.socket = Sock()
    s.server_bind()
    assert seen == [(socket.SOL_SOCKET, fake_socket.SO_EXCLUSIVEADDRUSE, 1), "bound"]
    assert ns["_Exclusive"].allow_reuse_address is False
    fake_os.name = "posix"
    assert load()._Exclusive.allow_reuse_address is True
