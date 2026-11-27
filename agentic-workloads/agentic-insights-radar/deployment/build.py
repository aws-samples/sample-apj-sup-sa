#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Build the deployable bundles without Docker.

    build/lambda/   backend package + pinned boto3, for the arm64 Python 3.14 Lambda functions
    build/agents/   agent runtimes + dependencies as Linux arm64 wheels, for AgentCore direct
                    code deployment (one bundle, two entry points: module_main.py, chat_main.py)
    build/site/     the single-page application (frontend/ must exist: a deployment without it has no app)

uv resolves wheels for the target platform from any host (``--python-platform``), so this runs
the same on macOS, Linux x86 (CodeBuild) and Linux arm64.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - runs uv and npm with fixed arguments, never a shell
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
# Lambda python3.14 and AgentCore run on Amazon Linux 2023 (glibc 2.34): manylinux_2_28 wheels load there.
PLATFORM = ["--python-version", "3.14", "--python-platform", "aarch64-manylinux_2_28", "--only-binary", ":all:"]
BUILD_CONSTRAINTS = Path(__file__).with_name("build-constraints.txt")
SHIMS = {
    "module_main.py": "from insights_radar_agents.module_app import main\n\nmain()\n",
    "chat_main.py": "from insights_radar_agents.chat_app import main\n\nmain()\n",
}


def _program(name: str) -> str:
    """The absolute path of a required build tool, so the build never runs a relative path."""
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"{name} is not installed (see the README prerequisites)")
    return found


def uv(*args: str, cwd: Path = ROOT) -> None:
    """Run uv: the program is fixed, the arguments are a list (never a shell string)."""
    print("+ uv", " ".join(args), flush=True)
    # argv[0] is the literal name; executable= is the absolute path found on PATH, so no relative
    # path is run and the command is never built from input.
    subprocess.run(["uv", *args], executable=_program("uv"), cwd=cwd, check=True)  # noqa: S603  # nosec B603 B607


def npm_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment npm runs in: no AWS credentials. ``npm run build`` executes code from the web app's
    dependency tree (tsc, vite and its plugins), and deploy.sh runs it with the operator's deployment
    credentials in the environment (P3-053). Credential files are pointed at nothing too, so an SDK in a
    build plugin cannot fall back to ~/.aws."""
    source = dict(os.environ if env is None else env)
    clean = {k: v for k, v in source.items() if not k.startswith(("AWS_", "AMAZON_"))}
    clean.update(AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull, AWS_EC2_METADATA_DISABLED="true")
    return clean


def npm(*args: str, cwd: Path = ROOT) -> None:
    """Run npm: the program is fixed, the arguments are a list (never a shell string)."""
    print("+ npm", " ".join(args), flush=True)
    # argv[0] is the literal name; executable= is the absolute path found on PATH, so no relative
    # path is run and the command is never built from input.
    subprocess.run(["npm", *args], executable=_program("npm"), cwd=cwd, check=True, env=npm_env())  # noqa: S603  # nosec B603 B607


def fresh(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def normalise(path: Path) -> None:
    """AgentCore and Lambda need world-readable files (644) and traversable directories (755)."""
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chmod(Path(root) / d, 0o755)  # noqa: S103  # nosec B103 - deployment package directories must be traversable
        for f in files:
            p = Path(root) / f
            os.chmod(p, 0o755 if os.access(p, os.X_OK) else 0o644)  # noqa: S103  # nosec B103 - executables in a deployment bundle stay executable
        for pyc in [d for d in dirs if d == "__pycache__"]:
            shutil.rmtree(Path(root) / pyc, ignore_errors=True)


def wheels() -> list[str]:
    """Wheels of the two local packages (pure Python), so dependencies can stay binary-only."""
    out = fresh(BUILD / "wheels")
    for project in ("backend", "agents"):
        uv(
            "build", "--wheel", "--quiet", "--out-dir", str(out),
            # The isolated build environment (hatchling and its dependencies) is pinned and hashed too.
            "--build-constraints", str(BUILD_CONSTRAINTS), "--require-hashes",
            str(ROOT / project),
        )
    return sorted(str(w) for w in out.glob("*.whl"))


def locked(project: str) -> Path:
    """The project's third-party dependencies exactly as uv.lock pins them, with hashes. Installing the
    wheels alone would resolve every transitive dependency afresh from PyPI at deploy time."""
    req = BUILD / f"{project}-requirements.txt"
    uv("export", "--frozen", "--no-dev", "--no-emit-project", "--no-emit-local", "--quiet",
        "--format", "requirements-txt", "--output-file", str(req), cwd=ROOT / project)
    return req


def install(out: Path, project: str, wheels: list[str]) -> None:
    uv("pip", "install", "--quiet", "--no-cache", "--target", str(out), *PLATFORM,
        # --no-deps: the export is the whole locked tree, and re-resolving it would drop the lock's
        # override-dependencies (pillow above strands-agents-tools' cap).
        "--require-hashes", "--no-deps", "-r", str(locked(project)))
    uv("pip", "install", "--quiet", "--no-cache", "--target", str(out), *PLATFORM, "--no-deps", *wheels)


def build_lambda(local: list[str]) -> None:
    out = fresh(BUILD / "lambda")
    install(out, "backend", [w for w in local if "backend" in Path(w).name])
    normalise(out)


def build_agents(local: list[str]) -> None:
    out = fresh(BUILD / "agents")
    install(out, "agents", local)
    for name, body in SHIMS.items():
        (out / name).write_text(body)
    normalise(out)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"agent bundle: {size / 1e6:.0f} MB unzipped (limit 750 MB)")
    if size > 700e6:
        sys.exit("agent bundle is too large for AgentCore direct code deployment")


def build_site() -> None:
    frontend = ROOT / "frontend"
    if not (frontend / "package.json").exists():
        # Without a site the stack deploys no app and CloudFront answers 403: stop here instead.
        sys.exit("frontend/package.json is missing: the web app cannot be built (use a full checkout)")
    # --ignore-scripts: deploy.sh runs this with the operator's deployment credentials in the
    # environment, and a postinstall script of any of the web app's transitive dependencies would
    # run with them (P2-012). The web app's own build is "npm run build" below, which is explicit.
    npm("ci", "--ignore-scripts", "--no-audit", "--no-fund", cwd=frontend)
    npm("run", "build", cwd=frontend)
    shutil.rmtree(BUILD / "site", ignore_errors=True)
    shutil.copytree(frontend / "dist", BUILD / "site")


def main() -> None:
    targets = sys.argv[1:] or ["lambda", "agents", "site"]
    local = wheels() if {"lambda", "agents"} & set(targets) else []
    if "lambda" in targets:
        build_lambda(local)
    if "agents" in targets:
        build_agents(local)
    if "site" in targets:
        build_site()


if __name__ == "__main__":
    main()
