# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("render", ROOT / "mdm" / "render.py")
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)
BASE = ["--issuer", "https://x.okta.com/oauth2/default", "--client-id", "abcd1234", "--gateway-url", "https://gw.example.com"]


def test_renders_without_websearch(tmp_path):
    render.main(BASE + ["--out", str(tmp_path)])
    oc = json.loads((tmp_path / "opencode.json").read_text())
    assert oc["mcp"]["web-search"]["enabled"] is False
    assert json.loads((tmp_path / "ocgw.json").read_text())["websearch_url"] == ""


def test_renders_with_websearch(tmp_path):
    render.main(BASE + ["--websearch-url", "https://ws.example.com/mcp", "--out", str(tmp_path)])
    oc = json.loads((tmp_path / "opencode.json").read_text())
    assert oc["mcp"]["web-search"]["enabled"] is True
    assert oc["enabled_providers"] == ["ocgw"]


@pytest.mark.parametrize("bad", [["--websearch-url", "http://ws.example.com"], ["--client-id", "a b"], ["--proxy-port", "80"],
                                 ["--default-model", "gpt-4o"]])
def test_rejects_bad_input(tmp_path, bad):
    with pytest.raises(SystemExit):
        render.main(BASE + bad + ["--out", str(tmp_path)])


def test_default_and_small_model_must_be_listed(tmp_path):
    with pytest.raises(SystemExit, match="not in --models"):
        render.main(BASE + ["--models", "ow/glm-5.3,ow/glm-5", "--out", str(tmp_path)])
    render.main(BASE + ["--models", "ow/glm-5.3,ow/glm-5", "--default-model", "ow/glm-5", "--small-model", "ow/glm-5.3", "--out", str(tmp_path)])


@pytest.mark.parametrize("bad", ['https://x.okta.com/oauth2/default","token_kind":"access_token', "https://x.okta.com/a b", "https://x.okta.com/\\\\x"])
def test_urls_cannot_inject_json(tmp_path, bad):
    with pytest.raises(SystemExit):
        render.main(["--issuer", bad, "--client-id", "abcd1234", "--gateway-url", "https://gw.example.com", "--out", str(tmp_path)])


@pytest.mark.parametrize("extra,kind,scope", [
    ([], "id_token", "openid email profile offline_access"),                                    # Okta, JumpCloud
    (["--token-kind", "access_token", "--scope", "openid email profile"], "access_token", "openid email profile"),  # Cognito
])
def test_token_kind_and_scope_reach_the_mdm_file(tmp_path, extra, kind, scope):
    render.main(BASE + extra + ["--out", str(tmp_path)])
    cfg = json.loads((tmp_path / "ocgw.json").read_text())
    assert cfg["token_kind"] == kind and cfg["scope"] == scope


@pytest.mark.parametrize("bad", [["--token-kind", "refresh_token"], ["--scope", 'openid", "issuer": "https://evil'],
                                 ["--scope", ""], ["--scope", "openid  email"]])
def test_bad_token_kind_or_scope_is_refused(tmp_path, bad):
    with pytest.raises(SystemExit):
        render.main(BASE + bad + ["--out", str(tmp_path)])


def test_the_managed_config_asks_before_edits_shell_commands_and_fetches(tmp_path):
    """r194: README Security asks deployers to set opencode's permissions; the managed config the sample renders does."""
    render.main(BASE + ["--out", str(tmp_path)])
    oc = json.loads((tmp_path / "opencode.json").read_text())
    assert oc["permission"] == {"edit": "ask", "bash": "ask", "webfetch": "ask"}
