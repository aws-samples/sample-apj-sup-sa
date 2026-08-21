#!/usr/bin/env python3
"""Embed the workshop assets into infra/template.yaml as gzip+base64 blobs.

The stack is fully self-contained: the tools Lambda handler, the gateway
handler, the tool contract, and the wildlife datasets are all embedded in the
template and written out at deploy time by the Provisioner custom resource. This
removes any dependency on an external repo or a pre-upload step - the stack
deploys with a single `aws cloudformation deploy`.

Run this whenever any of the source files below change:

    python3 infra/bundle_assets.py

It rewrites the four generated placeholders in template.yaml in place:
    __TOOLS_CODE_B64__   <- infra/lambda/tools/index.py
    __GATEWAY_CODE_B64__ <- infra/gateway_provisioner/index.py
    __TOOL_DEF_B64__     <- agent/tool_definition.json
    __DATA_FILES__       <- every agent/data/*.json (map keyed by filename)

Idempotent: it re-reads sources and replaces the values each run, so it also
works to refresh an already-populated template.
"""

import base64
import gzip
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # .../infra
ROOT = os.path.dirname(HERE)                               # .../github-samples
TEMPLATE = os.path.join(HERE, "template.yaml")

TOOLS_CODE = os.path.join(HERE, "lambda", "tools", "index.py")
GATEWAY_CODE = os.path.join(HERE, "gateway_provisioner", "index.py")
TOOL_DEF = os.path.join(ROOT, "agent", "tool_definition.json")
DATA_GLOB = os.path.join(ROOT, "agent", "data", "*.json")

# CloudFormation template hard limit when passed inline to create-change-set.
# Above this the CLI requires --s3-bucket, which would break the single-command
# deploy, so we guard against it here.
TEMPLATE_INLINE_LIMIT = 51200


def blob(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(gzip.compress(f.read(), 9)).decode("ascii")


def main() -> int:
    for p in (TEMPLATE, TOOLS_CODE, GATEWAY_CODE, TOOL_DEF):
        if not os.path.exists(p):
            print(f"ERROR: missing {p}", file=sys.stderr)
            return 1

    text = open(TEMPLATE).read()

    # (YAML property key in template.yaml, source file to embed)
    scalar_assets = [
        ("ToolsCodeB64", TOOLS_CODE),
        ("GatewayCodeB64", GATEWAY_CODE),
        ("ToolDefB64", TOOL_DEF),
    ]

    # Build the DataFiles map (keys at 8-space indent under the 6-space key).
    data_files = sorted(glob.glob(DATA_GLOB))
    if not data_files:
        print(f"ERROR: no dataset files matched {DATA_GLOB}", file=sys.stderr)
        return 1
    lines = ["      DataFiles:"]
    for f in data_files:
        lines.append('        %s: "%s"' % (os.path.basename(f), blob(f)))
    data_block = "\n".join(lines)

    # Replace each scalar blob (works whether the template still has the raw
    # token or a previously-generated value).
    for yaml_key, path in scalar_assets:
        value_quoted = '"%s"' % blob(path)
        # Match:  <yaml_key>: "<anything>"  keeping the leading indentation.
        pat = re.compile(r'^(\s*%s:\s*)"[^"]*"\s*$' % re.escape(yaml_key), re.M)
        new = pat.sub(lambda m: m.group(1) + value_quoted, text, count=1)
        if new == text:
            print(f"ERROR: could not find line for {yaml_key}", file=sys.stderr)
            return 1
        text = new

    # Replace the DataFiles block: either the raw token line or a prior map.
    if "DataFiles: __DATA_FILES__" in text:
        text = text.replace("      DataFiles: __DATA_FILES__", data_block, 1)
    else:
        # Refresh an existing generated map: from 'DataFiles:' up to 'Version:'.
        pat = re.compile(r'^      DataFiles:\n(?:        .*\n)+', re.M)
        new = pat.sub(data_block + "\n", text, count=1)
        if new == text:
            print("ERROR: could not locate DataFiles block to refresh", file=sys.stderr)
            return 1
        text = new

    open(TEMPLATE, "w").write(text)

    size = len(text.encode())
    print(f"Embedded {len(data_files)} datasets + 3 code/contract blobs.")
    print(f"template.yaml is now {size} bytes "
          f"({'OK' if size < TEMPLATE_INLINE_LIMIT else 'OVER'} the "
          f"{TEMPLATE_INLINE_LIMIT}-byte inline limit).")
    if size >= TEMPLATE_INLINE_LIMIT:
        print("WARNING: template exceeds the inline limit; `deploy` will need "
              "--s3-bucket.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
