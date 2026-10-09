"""Load the samples' shell-compatible ``.env`` assignments."""

import argparse
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Iterator

_ENV_KEY = re.compile(
    r"(?:AWS_(?:REGION|PROFILE)|OMNI_[A-Z0-9_]+|MODEL_ID|JUDGE_MODEL_ID|SHOP_LOG_GROUP|ALERTS_TOPIC_ARN)"
)


def _strip_inline_comment(value: str) -> str:
    """Remove an unquoted comment introduced after whitespace."""
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "#" and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def parse_env_value(value: str) -> str:
    """Parse one shell-style assignment value without expanding variables."""
    value = _strip_inline_comment(value).strip()
    if not value:
        return ""
    words = shlex.split(value, comments=False, posix=True)
    if len(words) != 1:
        raise ValueError("environment values containing whitespace must be quoted")
    return words[0]


def parse_env(path: Path) -> Iterator[tuple[str, str]]:
    """Yield validated assignments from *path*."""
    if not path.exists():
        return
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE assignment")
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.fullmatch(key):
            raise ValueError(f"{path}:{line_number}: invalid environment key {key!r}")
        try:
            value = parse_env_value(value)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        yield key, value


def load_env(path: Path) -> None:
    """Load non-empty assignments from *path*, preserving shell fallbacks."""
    for key, value in parse_env(path):
        if value:
            os.environ[key] = value


def _shell_exports(path: Path) -> None:
    """Print literal shell exports for the shared Bash/Zsh loader."""
    for key, value in parse_env(path):
        if value:
            print(f"export {key}={shlex.quote(value)} || return $?")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.shell:
            _shell_exports(args.path)
        else:
            parser.error("an output format is required")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc
