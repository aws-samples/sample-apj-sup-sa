#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Render the MDM files for one deployment from Terraform outputs and your IdP settings.

  python3 mdm/render.py --issuer https://<org>.okta.com/oauth2/default --client-id <id> \
      --gateway-url <terraform output gateway_base_url> [--websearch-url <terraform output websearch_mcp_url>] \
      [--models ow/kimi-k3,ow/glm-5.3] [--default-model ow/kimi-k3] [--small-model ow/glm-5.3] [--out mdm/build]
      [--token-kind access_token --scope "openid email profile"]   (the Cognito stand-in: see terraform output ocgw_config)

Writes ocgw.json (install to /Library/Application Support/OpencodeGateway/ on macOS or /etc/opencode-gateway/ on Linux)
and opencode.json (install to the opencode system config directory: /Library/Application Support/opencode/ or
/etc/opencode/), which pins opencode to the gateway provider only. Values are checked here, so a bad URL fails once at
build time rather than on every laptop.
"""
import argparse
import json
import re
import string
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = {"kimi-k3": "Kimi K3", "glm-5.3": "GLM 5.3", "deepseek-v3.2": "DeepSeek V3.2", "glm-5": "GLM 5"}


def https_url(v):
    u = urllib.parse.urlsplit(v)
    if u.scheme != "https" or not u.hostname or any(c in v for c in '"\\<>{}`\'') or any(ord(c) < 33 for c in v):
        raise argparse.ArgumentTypeError(f"{v!r} is not an https URL")
    return v.rstrip("/")


def model_id(v):
    if not re.fullmatch(r"ow/[a-z0-9][a-z0-9.-]*", v):
        raise argparse.ArgumentTypeError(f"{v!r} is not an ow/<alias> model ID")
    return v


def render(a):
    models = [model_id(m.strip()) for m in a.models.split(",") if m.strip()]
    for flag, m in (("--default-model", a.default_model), ("--small-model", a.small_model)):
        if m not in models:
            raise SystemExit(f"{flag} {m} is not in --models ({', '.join(models)})")
    vals = {"ISSUER": a.issuer, "CLIENT_ID": a.client_id, "GATEWAY_URL": a.gateway_url, "WEBSEARCH_URL": a.websearch_url or "",
            "PROXY_PORT": str(a.proxy_port), "TOKEN_KIND": a.token_kind, "SCOPE": a.scope, "DEFAULT_MODEL": a.default_model, "SMALL_MODEL": a.small_model,
            "WEB_SEARCH": "true" if a.websearch_url else "false",
            "MODELS_JSON": json.dumps({m: {"name": NAMES.get(m[3:], m)} for m in models})}
    # Every value lands inside a JSON string in the templates: escape it as one, so no value can add or change keys.
    vals = {k: (v if k == "MODELS_JSON" else json.dumps(v)[1:-1]) for k, v in vals.items()}
    out = {}
    for src, dst in (("ocgw.json.tmpl", "ocgw.json"), ("opencode.managed.json.tmpl", "opencode.json")):
        text = string.Template((HERE / src).read_text()).substitute(vals)
        json.loads(text)  # the rendered file must be valid JSON
        out[dst] = text
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--issuer", type=https_url, required=True)
    p.add_argument("--client-id", required=True)
    p.add_argument("--gateway-url", type=https_url, required=True)
    p.add_argument("--websearch-url", type=https_url, default=None)
    p.add_argument("--models", default="ow/kimi-k3,ow/glm-5.3,ow/deepseek-v3.2,ow/glm-5")
    p.add_argument("--default-model", type=model_id, default="ow/kimi-k3")
    p.add_argument("--small-model", type=model_id, default="ow/glm-5.3")
    # The MDM file wins over the user's config and the environment, so it must carry what the IdP needs: Okta and
    # JumpCloud send the id_token with offline_access; the Cognito stand-in needs the access_token (it has client_id
    # and username) and refuses offline_access (terraform output ocgw_config has both).
    p.add_argument("--token-kind", choices=["id_token", "access_token"], default="id_token")
    p.add_argument("--scope", default="openid email profile offline_access")
    p.add_argument("--proxy-port", type=int, default=8766)
    p.add_argument("--out", type=Path, default=HERE / "build")
    a = p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9._-]{4,128}", a.client_id):
        p.error("client ID has unexpected characters")
    if not re.fullmatch(r"[A-Za-z0-9:._/-]+( [A-Za-z0-9:._/-]+)*", a.scope):
        p.error("scope must be space-separated scope names")
    if not 1024 <= a.proxy_port <= 65535:
        p.error("proxy port must be 1024-65535")
    a.out.mkdir(parents=True, exist_ok=True)
    for name, text in render(a).items():
        (a.out / name).write_text(text)
        print(f"wrote {a.out / name}")


if __name__ == "__main__":
    main()
