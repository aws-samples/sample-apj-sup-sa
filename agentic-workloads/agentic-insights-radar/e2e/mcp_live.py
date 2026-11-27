# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Live check of the MCP server: PKCE sign-in through the hosted UI, then a stdio MCP session."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from playwright.sync_api import sync_playwright

from session import Deployment, fresh_totp, outputs

# The server is started through e2e/mcp-server.sh with literal arguments only, so no command is ever built from
# data: the script finds node and this repository's mcp/dist/index.js itself (Holmes r52). Build the server with
# `cd mcp && npm ci --ignore-scripts && npm run build`.
LAUNCHER = Path(__file__).resolve().parent / "mcp-server.sh"
E2E_DIR = str(LAUNCHER.parent)


def hosted_login(url: str, email: str, password: str, secret: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url)
        page.locator('input[name="username"]:visible').fill(email)
        page.locator('input[name="password"]:visible').fill(password)
        page.locator('input[name="signInSubmitButton"]:visible').click()
        box = page.locator('input[name="totpCode"]:visible, input#totpCodeInput:visible, input[name="code"]:visible').first
        box.wait_for(timeout=30000)
        box.fill(fresh_totp(secret))
        page.keyboard.press("Enter")
        page.wait_for_url("http://127.0.0.1:8765/callback**", timeout=30000)
        browser.close()


class HostedLogin:
    """Runs hosted_login in a thread while `login` waits for the redirect, and keeps its failure, so a
    Playwright error is reported as itself rather than as a sign-in timeout minutes later."""

    def __init__(self, email: str, password: str, secret: str) -> None:
        self._args = (email, password, secret)
        self.error: Exception | None = None

    def _run(self, url: str) -> None:
        try:
            hosted_login(url, *self._args)
        except Exception as err:  # noqa: BLE001 - reported by check() on the waiting thread
            self.error = err

    def open_browser(self, url: str) -> bool:
        threading.Thread(target=self._run, args=(url,), daemon=True).start()
        return True

    def check(self) -> None:
        if self.error is not None:
            raise RuntimeError(f"hosted sign-in failed: {self.error}") from self.error


def result_text(res: Any) -> str:
    """The text of a tool result's first content block ("" for none or a non-text block)."""
    return str(getattr(res.content[0], "text", "")) if res.content else ""


def login(env: dict[str, str], browser: HostedLogin, timeout: float) -> None:
    """`login --no-browser` prints the sign-in address instead of opening a browser: hand it to
    Playwright, then wait for the server to receive the redirect and save the tokens."""
    proc = subprocess.Popen(  # nosec B603 - literal arguments, no shell
        ["bash", "mcp-server.sh", "login", "--no-browser"],
        cwd=E2E_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("https://") and "/oauth2/authorize?" in line:
                browser.open_browser(line.strip())
                break
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        browser.check()
        raise RuntimeError(f"sign-in did not finish within {timeout:.0f} s") from None
    if proc.returncode != 0:
        browser.check()
        raise RuntimeError(f"login failed (exit {proc.returncode}): {err.strip()}")


@contextlib.contextmanager
def mcp_sign_in(
    d: Deployment, api_url: str, client_id: str, role: str = "editor", timeout: float = 180
) -> Iterator[tuple[str, Path]]:
    """Signs a role in to the MCP server in a throwaway config home and yields the home and the token
    file. On exit the refresh token is revoked (`logout`) and the home, tokens included, is deleted."""
    d.sign_in(d.user(role))  # ensures TOTP is enrolled
    user = d.user(role)
    with tempfile.TemporaryDirectory() as home:
        # Bound to api_url, as `insights-radar-mcp login` is: the server refuses tokens from another deployment.
        # Signed in at the pinned domain, the stack output, which /api/config must report too (P3-081).
        env = serve_env(home, api_url, client_id, pinned_domain(d))
        browser = HostedLogin(user.email, user.password, user.totp_secret or "")
        try:
            login(env, browser, timeout)
            yield home, Path(home) / "insights-radar" / "tokens.json"
        finally:
            with contextlib.suppress(Exception):
                subprocess.run(  # nosec B603 - literal arguments, no shell
                    ["bash", "mcp-server.sh", "logout"], cwd=E2E_DIR, env=env, capture_output=True, timeout=60, check=False
                )


def pinned_domain(d: Deployment) -> str:
    """The CognitoDomain stack output, checked against what the deployment's /api/config reports."""
    domain = str(d.outputs.get("CognitoDomain") or d.config["cognito_domain"]).lower()
    assert domain == str(d.config["cognito_domain"]).lower(), (domain, d.config["cognito_domain"])
    return domain


def serve_env(home: str, api_url: str, client_id: str, domain: str) -> dict[str, str]:
    """The server's environment: pinned to this deployment's domain, whatever the operator's shell sets
    (P3-081), and the generic server (an empty deployment.json), whatever deployment.json mcp/ carries."""
    generic = Path(home) / "deployment.json"
    generic.write_text(json.dumps({}))
    return {
        **os.environ,
        "XDG_CONFIG_HOME": home,
        "AIR_MCP_CONFIG": str(generic),
        "AIR_API_URL": api_url,
        "AIR_MCP_CLIENT_ID": client_id,
        "AIR_COGNITO_DOMAIN": domain,
    }


def server_params(home: str, api_url: str, client_id: str, domain: str) -> StdioServerParameters:
    """`serve` over stdio, as an MCP client starts it."""
    command, args = "bash", [str(LAUNCHER), "serve"]  # bash: no reliance on the executable bit
    return StdioServerParameters(command=command, args=args, env=serve_env(home, api_url, client_id, domain))


async def session(home: str, api_url: str, client_id: str, domain: str) -> None:
    params = server_params(home, api_url, client_id, domain)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("tools:", len(tools.tools))
        calls: list[tuple[str, dict[str, Any]]] = [
            ("list_navigation", {}),
            ("search", {"query": "agent sandboxing", "limit": 3}),
            ("list_insights", {"kind": "gaps"}),
            ("export_radar", {"slug": "agent-platforms", "format": "csv"}),
            ("run_module", {"slug": "signal", "confirm": True}),
        ]
        for name, args in calls:
            res = await s.call_tool(name, args)
            text = result_text(res)
            error = getattr(res, "is_error", getattr(res, "isError", None))
            # Metadata only: results carry deployment data (entries, insights, radar rows).
            print(f"{name}: {len(text)} chars, error={error}, fenced={'untrusted-tool-output' in text}")


def main() -> None:
    out = outputs()
    d = Deployment()
    with mcp_sign_in(d, out["AppUrl"], out["McpClientId"], timeout=120) as (home, tokens):
        print("signed in; token mode", oct(tokens.stat().st_mode & 0o777))
        asyncio.run(session(home, out["AppUrl"], out["McpClientId"], pinned_domain(d)))


if __name__ == "__main__":
    main()
