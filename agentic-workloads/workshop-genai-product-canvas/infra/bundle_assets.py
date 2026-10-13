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
import glob
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # .../infra
ROOT = os.path.dirname(HERE)                               # the sample root
TEMPLATE = os.path.join(HERE, "template.yaml")

CODE_EDITOR = os.path.join(HERE, "code-editor.yaml")

TOOLS_CODE = os.path.join(HERE, "lambda", "tools", "index.py")
GATEWAY_CODE = os.path.join(HERE, "gateway_provisioner", "index.py")
TOOL_DEF = os.path.join(ROOT, "agent", "tool_definition.json")
DATA_GLOB = os.path.join(ROOT, "agent", "data", "*.json")
AGENT_DIR = os.path.join(ROOT, "agent")

# Directories synced into the participant workspace at /workshop/repo/, in
# addition to the datasets. Keep this in step with what the content tells
# participants to open - see the AgentFiles comment in main().
WORKSPACE_DIRS = ("agent", "canvas", "skills")

# Files under agent/ that must NOT ship to participants. package-lock.json is
# 180 KB of transitive pins that `npm install` regenerates; the rest is build or
# editor residue.
AGENT_EXCLUDE_NAMES = {"package-lock.json", ".DS_Store"}
AGENT_EXCLUDE_DIRS = {"node_modules", "__pycache__", ".venv", "cdk.out", "data"}

# CloudFormation limits: 51,200 bytes when a template body is passed inline, and
# 1 MB when it is staged in S3. Embedding the agent source deliberately crosses
# the inline limit, so a standalone deploy needs --s3-bucket. Workshop Studio
# always stages to S3, so the 1 MB ceiling is the one that matters there.
TEMPLATE_INLINE_LIMIT = 51200
TEMPLATE_S3_LIMIT = 1024 * 1024

# EC2 caps UserData at 16 KB. code-editor.yaml's bootstrap sits close to it, and
# going over is a hard CREATE_FAILED at deploy time - the worst place to find out.
# Checked on every run because this script runs on every sync.
USERDATA_LIMIT = 16384
USERDATA_WARN_AT = 15000


def check_userdata() -> int:
    """Report the rendered size of code-editor.yaml's UserData block."""
    if not os.path.exists(CODE_EDITOR):
        return 0
    lines = open(CODE_EDITOR).read().split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l == "        UserData:") + 2
    except StopIteration:
        print("WARN: could not find the UserData block in code-editor.yaml",
              file=sys.stderr)
        return 0
    body = []
    for line in lines[start:]:
        if not line.strip():
            body.append("")
        elif line.startswith("            "):
            body.append(line[12:])
        else:
            break
    size = len("\n".join(body).rstrip("\n").encode()) + 1
    if size >= USERDATA_LIMIT:
        print(f"ERROR: code-editor.yaml UserData is {size} bytes, over EC2's "
              f"{USERDATA_LIMIT}-byte limit. The instance will fail to launch.",
              file=sys.stderr)
        return 1
    if size >= USERDATA_WARN_AT:
        print(f"NOTE: UserData is {size} bytes, {USERDATA_LIMIT - size} short of "
              f"EC2's {USERDATA_LIMIT}-byte limit - trim before adding more.")
    return 0


# A secret embedded here would be gzipped and base64-ed into template.yaml,
# where no secret scanner can see it, and then shipped to every participant and
# on to a public repository. That already happened once: agentcore.json carried a
# live Cognito client secret and gateway URL from a developer's own deployment
# where the content says placeholders belong. This is the guard, and it runs on
# every file before it is embedded.
SECRET_KEY_RE = re.compile(
    r'[A-Za-z_]*(?:client_secret|secret|password|passwd|api_?key)[A-Za-z_]*',
    re.I,
)
# The value does not always sit next to its key. agentcore.json - the file that
# leaked - uses {"name": "COGNITO_CLIENT_SECRET", "value": "..."}, so look ahead
# a short way for the first plausible value instead of requiring "key": "value".
SECRET_VALUE_RE = re.compile(r'"([^"\n]{16,})"|=\s*(\S{16,})')
LOOKAHEAD = 120

# Values that are meant to be there: placeholders, ARNs, paths, URLs, and
# ordinary identifiers with no high-entropy run.
SECRET_ALLOWED_RE = re.compile(
    r'^(?:REPLACE_FROM_|REPLACE_|<|\{\{|\$\{|!|arn:aws:|/|https?://)|'
    r'(?:example|placeholder|changeme|redacted|dummy|xxxx|your-)',
    re.I,
)
# A credential has a long unbroken run of mixed alphanumerics. Prose, action
# names like secretsmanager:GetSecretValue, and dotted identifiers do not.
SECRET_SHAPE_RE = re.compile(r'[A-Za-z0-9+/_-]{16,}')


def secret_findings(path: str, text: str) -> list:
    out = []
    for key in SECRET_KEY_RE.finditer(text):
        window = text[key.end():key.end() + LOOKAHEAD]
        value_match = SECRET_VALUE_RE.search(window)
        if not value_match:
            continue
        value = value_match.group(1) or value_match.group(2)
        if SECRET_ALLOWED_RE.search(value):
            continue
        run = SECRET_SHAPE_RE.search(value)
        if not run or not (any(c.isdigit() for c in run.group(0))
                           and any(c.isalpha() for c in run.group(0))):
            continue
        line = text.count("\n", 0, key.start()) + 1
        out.append((line, key.group(0), value[:6] + "..."))
    return out


def blob(path: str) -> str:
    # mtime=0 keeps the output byte-identical for unchanged input. Without it
    # gzip stamps the current time into every header, so re-running this rewrote
    # all 60-odd blobs and left the Workshop Studio copy permanently "different"
    # from this one.
    with open(path, "rb") as f:
        raw = f.read()
    try:
        found = secret_findings(path, raw.decode("utf-8"))
    except UnicodeDecodeError:
        found = []
    if found:
        rel = os.path.relpath(path, ROOT)
        print(f"ERROR: refusing to embed {rel} - it looks like it contains a "
              f"real credential:", file=sys.stderr)
        for line, key, preview in found:
            print(f"  {rel}:{line}  {key} = {preview}", file=sys.stderr)
        print("  Replace the value with a REPLACE_FROM_gateway.env placeholder, "
              "or fetch it at runtime.", file=sys.stderr)
        raise SystemExit(1)
    return base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode("ascii")


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

    # Build the AgentFiles map: every file a participant needs in their
    # workspace, keyed by its path relative to the sample root. The datasets are
    # excluded here because DataFiles already seeds them under agent/data/.
    #
    # agent/ alone is not enough. Part 2's build prompt tells the coding agent to
    # read ./canvas for the blank template and the worked reference, and the
    # deploy step uses skills/deploy-to-agentcore. Ship anything the content
    # points at, or the instruction cannot be followed.
    agent_files = []
    for top in WORKSPACE_DIRS:
        base = os.path.join(ROOT, top)
        if not os.path.isdir(base):
            print(f"ERROR: {top}/ is referenced by the content but missing",
                  file=sys.stderr)
            return 1
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in AGENT_EXCLUDE_DIRS]
            for fn in filenames:
                if fn in AGENT_EXCLUDE_NAMES:
                    continue
                full = os.path.join(dirpath, fn)
                agent_files.append((os.path.relpath(full, ROOT), full))
    agent_files.sort()
    if not agent_files:
        print(f"ERROR: no agent source files found under {AGENT_DIR}", file=sys.stderr)
        return 1
    agent_lines = ["      AgentFiles:"]
    for rel, full in agent_files:
        agent_lines.append('        %s: "%s"' % (json.dumps(rel), blob(full)))
    agent_block = "\n".join(agent_lines)

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
    #
    # Every replacement below checks that the pattern MATCHED, not that the text
    # changed. Now that blob() is deterministic, re-running with unchanged input
    # legitimately produces identical output, and a "did the text change" test
    # would report that as a missing key.
    for yaml_key, path in scalar_assets:
        value_quoted = '"%s"' % blob(path)
        # Match:  <yaml_key>: "<anything>"  keeping the leading indentation.
        pat = re.compile(r'^(\s*%s:\s*)"[^"]*"\s*$' % re.escape(yaml_key), re.M)
        text, n = pat.subn(lambda m: m.group(1) + value_quoted, text, count=1)
        if not n:
            print(f"ERROR: could not find line for {yaml_key}", file=sys.stderr)
            return 1

    # Replace the DataFiles block: either the raw token line or a prior map.
    if "DataFiles: __DATA_FILES__" in text:
        text = text.replace("      DataFiles: __DATA_FILES__", data_block, 1)
    else:
        # Refresh an existing generated map: from 'DataFiles:' up to 'Version:'.
        pat = re.compile(r'^      DataFiles:\n(?:        .*\n)+', re.M)
        text, n = pat.subn(data_block + "\n", text, count=1)
        if not n:
            print("ERROR: could not locate DataFiles block to refresh", file=sys.stderr)
            return 1

    # Replace the AgentFiles block: either the raw token line or a prior map.
    if "AgentFiles: __AGENT_FILES__" in text:
        text = text.replace("      AgentFiles: __AGENT_FILES__", agent_block, 1)
    else:
        pat = re.compile(r'^      AgentFiles:\n(?:        .*\n)+', re.M)
        text, n = pat.subn(agent_block + "\n", text, count=1)
        if not n:
            print("ERROR: could not locate AgentFiles block to refresh", file=sys.stderr)
            return 1

    open(TEMPLATE, "w").write(text)

    size = len(text.encode())
    print(f"Embedded {len(data_files)} datasets, {len(agent_files)} agent files, "
          f"and 3 code/contract blobs.")
    print(f"template.yaml is now {size} bytes "
          f"({size * 100 // TEMPLATE_S3_LIMIT}% of the 1 MB S3 limit).")
    if size >= TEMPLATE_S3_LIMIT:
        print("ERROR: template exceeds the 1 MB S3 limit.", file=sys.stderr)
        return 1
    if size >= TEMPLATE_INLINE_LIMIT:
        print("NOTE: over the 51,200-byte inline limit (expected - the agent "
              "source is embedded). A standalone `aws cloudformation deploy` "
              "needs --s3-bucket; Workshop Studio stages to S3 itself.")
    return check_userdata()


if __name__ == "__main__":
    sys.exit(main())
