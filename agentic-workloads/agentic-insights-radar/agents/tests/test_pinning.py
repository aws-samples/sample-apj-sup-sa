# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The pinned HTTP client for admin-attached MCP servers connects only where SafeResolver approved."""

from __future__ import annotations

import asyncio
import http.server
import threading
from collections.abc import Iterator

import httpcore2
import httpx2
import pytest
from insights_radar.core.urls import SafeResolver

from insights_radar_agents.connectors import PinnedAsyncBackend, PinnedStreamableHttp, PinnedTransport, pinned_client


class Fixed(SafeResolver):
    """Resolves every host to the given addresses, or refuses like a rebinding target would."""

    def __init__(self, *addresses: str, refuse: bool = False) -> None:
        super().__init__()
        self.addresses, self.refuse = list(addresses), refuse
        self.asked: list[str] = []

    def check(self, host: str, port: int = 443) -> list[str]:
        self.asked.append(host)
        if self.refuse:
            raise ValueError(f"{host} resolves to a non-public address")
        return self.addresses


@pytest.fixture
def server() -> Iterator[int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/hop":
                self.send_response(307)
                self.send_header("location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pinned")

        def log_message(self, *a: object) -> None:
            return

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def get(resolver: SafeResolver, url: str) -> httpx2.Response:
    async def go() -> httpx2.Response:
        async with pinned_client(resolver) as client:
            return await client.get(url)

    return asyncio.run(go())


def test_connects_to_the_vetted_address_not_what_the_name_resolves_to(server: int) -> None:
    resolver = Fixed("127.0.0.1")
    res = get(resolver, f"http://mcp.example.invalid:{server}/")
    assert res.status_code == 200 and res.text == "pinned" and resolver.asked == ["mcp.example.invalid"]


def test_tries_the_next_vetted_address_when_one_fails(server: int) -> None:
    res = get(Fixed("127.0.0.2", "127.0.0.1"), f"http://mcp.example.invalid:{server}/")
    assert res.status_code == 200


def test_refused_resolution_or_no_reachable_address_is_a_connect_error(server: int) -> None:
    with pytest.raises(httpx2.ConnectError, match="non-public"):
        get(Fixed(refuse=True), f"http://mcp.example.invalid:{server}/")
    with pytest.raises(httpx2.ConnectError, match="could not connect"):
        get(Fixed("127.0.0.2"), f"http://mcp.example.invalid:{server}/")


def test_redirects_are_not_followed(server: int) -> None:
    res = get(Fixed("127.0.0.1"), f"http://mcp.example.invalid:{server}/hop")
    assert res.status_code == 307  # returned to the caller, never fetched


def test_client_ignores_proxy_settings_and_uses_the_pinned_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:3128")
    client = pinned_client(Fixed("127.0.0.1"))
    transport = client._transport  # type: ignore[attr-defined]
    assert isinstance(transport, PinnedTransport) and not client.follow_redirects
    assert isinstance(transport._pool._network_backend, PinnedAsyncBackend)  # type: ignore[attr-defined]
    assert isinstance(transport._pool, httpcore2.AsyncConnectionPool)  # type: ignore[attr-defined]


def test_streamable_http_transport_targets_its_server() -> None:
    factory = PinnedStreamableHttp("https://mcp.example.com/mcp", Fixed("203.0.113.10"))
    assert factory.url == "https://mcp.example.com/mcp"
    assert hasattr(factory(), "__aenter__")  # an async context manager, as MCPClient expects
