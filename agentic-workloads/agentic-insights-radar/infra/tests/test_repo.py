# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Repository hygiene: nothing a developer's machine wrote into a file ships in the release."""

from __future__ import annotations

import itertools
import json
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A home directory as a path: macOS /Users/<name>/, Linux /home/<name>/ or /local/home/<name>/ (file:// URLs
# included), Windows C:\Users\<name>\. Web URLs that merely contain /home/ or /Users/ are not paths.
LOCAL_PATH = re.compile(
    r"(?:(?<![\w.:/])|(?<=file://)|(?<=file://localhost))/(?:local/)?(?:Users|home)/[A-Za-z0-9._-]+(?![\w.-])"
    r"|[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)[^\\/\s\"']+"
)


def test_no_tracked_file_holds_a_local_home_path() -> None:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    hits = []
    for name in files:
        path = ROOT / name
        # This file quotes example paths to test the pattern.
        if (
            path.suffix in {".png", ".jpg", ".ico"}
            or not path.is_file()
            or path == Path(__file__).resolve()
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{name}: {m.group(0)}" for m in LOCAL_PATH.finditer(text)]
    assert not hits, hits


def test_the_path_pattern_catches_every_form_of_home_path() -> None:
    for text in (
        "/home/dev/x",
        "/local/home/dev/x",
        "/Users/dev/x",
        "file:///home/dev/x",
        "file://localhost/home/dev/",
        '"/home/dev"',
        "C:\\Users\\dev\\x",
        "C:\\\\Users\\\\dev\\\\x",
        "C:/Users/dev/x",
    ):
        assert LOCAL_PATH.search(text), text
    for text in (
        "https://w.example.com/bin/view/Users/dev/",
        "see ./home/dev/",
        "https://example.com/home/x/",
    ):
        assert not LOCAL_PATH.search(text), text


# An exactly pinned Python tool in a workflow or a deployment script: "uvx name==1.2.3",
# "--from name==1.2.3", "--with name==1.2.3". Lockfiles are not matched (they are Dependabot's).
PINNED_TOOL = re.compile(
    r"(?:uvx(?:\s+--from)?|--from|--with)\s+([a-z][a-z0-9_.-]*(?:\[[a-z,]+\])?)==([0-9][0-9a-z.+-]*)"
)
PINNED_FILES = (
    ".github/workflows/ci.yml",
    ".github/workflows/docs.yml",
    ".github/workflows/deploy-validate.yml",
    ".github/workflows/pinned-tools.yml",
    "deployment/validate.sh",
)


def _listed_pins() -> set[tuple[str, str]]:
    out = set()
    for line in (ROOT / "deployment/pinned-tools.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            out.add((name.strip().lower(), version.strip()))
    return out


def test_p2081_every_exactly_pinned_tool_is_audited_weekly() -> None:
    """The scanners CI runs are pinned outside every lockfile Dependabot reads, so they are listed
    in deployment/pinned-tools.txt and audited by .github/workflows/pinned-tools.yml. A new pin that
    is not listed would never be checked again (P2-081)."""
    listed = _listed_pins()
    assert listed, "deployment/pinned-tools.txt lists nothing"
    missing = []
    for name in PINNED_FILES:
        path = ROOT / name
        if not path.exists():
            continue
        for match in PINNED_TOOL.finditer(path.read_text()):
            tool = match.group(1).split("[")[0].lower()
            if (tool, match.group(2)) not in listed:
                missing.append(f"{name}: {match.group(1)}=={match.group(2)}")
    assert not missing, missing
    workflow = (ROOT / ".github/workflows/pinned-tools.yml").read_text()
    assert "deployment/pinned-tools.txt" in workflow and "pip-audit" in workflow


# ---- Shell commands: an unquoted version specifier is a redirection ------------------------------
# `uv run --python >=3.12 python app.py` in infra/cdk.json ran as `uv run --python python app.py`
# with its output sent to a new file named "=3.12" in infra/: the command still worked, so nothing
# failed and every synth left a stray file behind. shellcheck does not flag it (a redirection to a
# file named "=3.12" is valid shell), so this test reads every place the repository keeps a command
# that a shell runs.
_REDIRECT = {">", ">>", "<", ">|"}
# A prefix test, so no nested repetition (semgrep regex_dos): "=..." or a dotted number "1.2...".
_VERSION_LIKE = re.compile(r"=|\d+\.\d")


def version_redirects(command: str) -> list[str]:
    """The redirections in one shell command line whose target looks like a version specifier."""
    if "((" in command:  # arithmetic, where < and >= are comparisons
        return []
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []  # an unclosed quote: a line of a multi-line string, not a whole command
    return [
        f"{op}{target}"
        for op, target in itertools.pairwise(tokens)
        if op in _REDIRECT and _VERSION_LIKE.match(target)
    ]


def _shell_commands() -> list[tuple[str, str]]:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    out: list[tuple[str, str]] = []
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        if name.endswith("cdk.json"):
            out.append((name, json.loads(path.read_text())["app"]))
        elif name.endswith("package.json"):
            scripts = json.loads(path.read_text()).get("scripts", {})
            out += [(f"{name} {k}", v) for k, v in scripts.items()]
        elif name.endswith(".sh") or name.startswith(".github/workflows/"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                text = line.strip()
                if text.startswith("#"):
                    continue
                # A workflow step's `run: <command>` and `- run: <command>`.
                text = re.sub(r"^(?:-\s+)?run:\s*", "", text)
                out.append((f"{name}:{number}", text))
    return out


def test_no_shell_command_redirects_to_a_version_specifier() -> None:
    commands = _shell_commands()
    assert any(where == "infra/cdk.json" for where, _ in commands)
    hits = [
        f"{where}: {hit}"
        for where, command in commands
        for hit in version_redirects(command)
    ]
    assert not hits, hits


def test_the_redirect_check_tells_a_specifier_from_a_redirection() -> None:
    for bad in (
        "uv run --frozen --python >=3.12 python app.py",
        "pip install 'cdk-nag' boto3>=1.40",
        "uv pip install foo<=2.0 bar",
        "python -m pip install x >3.12",
    ):
        assert version_redirects(bad), bad
    for good in (
        'uv run --frozen --python ">=3.12" python app.py',
        "uv pip install 'boto3>=1.40'",
        "cmd > build.log 2>&1",
        "(( now >= 3 ))",
        'echo "unclosed',
        "uv run --python 3.12 pytest",
    ):
        assert not version_redirects(good), good


def test_p3_027_the_documented_test_command_is_the_coverage_gate() -> None:
    """`uv run pytest -q` measured no coverage in agents and mcp, so a contributor who dropped coverage
    was green locally and red in CI (P3-027); the backend needs the flags spelled out. The MCP server
    is Node now: its `npm test` is the coverage gate (vitest.config.ts sets the 100% thresholds)."""
    import json
    import tomllib

    scripts = json.loads((ROOT / "mcp" / "package.json").read_text())["scripts"]
    assert scripts["test"] == "vitest run --coverage"
    vitest = (ROOT / "mcp" / "vitest.config.ts").read_text()
    assert (
        "thresholds: { lines: 100, branches: 100, functions: 100, statements: 100 }"
        in vitest
    )
    for project in ("agents", "infra"):
        config = tomllib.loads((ROOT / project / "pyproject.toml").read_text())
        assert "--cov" in config["tool"]["pytest"]["ini_options"]["addopts"], project
        assert "--cov-branch" in config["tool"]["pytest"]["ini_options"]["addopts"], (
            project
        )
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    assert "cd backend && uv run pytest -q --cov --cov-branch" in contributing
    assert (
        "npm ci --ignore-scripts" in contributing and "npm ci &&" not in contributing
    )  # P3-058


def test_p3_053_the_web_app_build_runs_without_aws_credentials() -> None:
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location(
        "air_build", ROOT / "deployment" / "build.py"
    )
    assert spec and spec.loader
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    env = build.npm_env(
        {
            "PATH": "/usr/bin",
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "x",
            "AWS_SESSION_TOKEN": "y",
            "AWS_PROFILE": "deploy",
            "AMAZON_Q_TOKEN": "z",
        }
    )
    assert env["PATH"] == "/usr/bin"
    assert not {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AMAZON_Q_TOKEN",
    } & set(env)
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == env["AWS_CONFIG_FILE"] == os.devnull
    assert "env=npm_env()" in (ROOT / "deployment" / "build.py").read_text()


def test_p3_054_the_cdk_entry_point_warns_that_a_direct_deploy_deletes_chat_history() -> (
    None
):
    app = (ROOT / "infra" / "app.py").read_text()
    assert "Deploy with ``deployment/deploy.sh``, not ``cdk deploy`` directly" in app
    assert (
        "-c chatMemoryKey=aws-owned" in app
        and "deletes every user's chat history" in app
    )


def test_p3_082_and_p3_057_ci_builds_the_docs_and_pins_its_container_by_digest() -> (
    None
):
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    docs = (ROOT / ".github/workflows/docs.yml").read_text()
    assert "mkdocs build --strict" in ci and "mkdocs_hooks.py" in docs
    images = re.findall(r"docker run[^\n]*?\s(\S+/\S+:\S+)", ci)
    assert images and all(re.search(r"@sha256:[0-9a-f]{64}$", i) for i in images), (
        images
    )
