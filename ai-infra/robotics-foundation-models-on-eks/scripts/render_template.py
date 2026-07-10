#!/usr/bin/env python3
"""Render repository templates.

Templates use @@NAME@@ placeholders. Values are read from environment variables.
This intentionally avoids shell heredoc expansion so Kubernetes manifests can
contain ordinary shell syntax such as ${LD_LIBRARY_PATH:-}.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render_template.py TEMPLATE", file=sys.stderr)
        return 2

    template_path = Path(sys.argv[1])
    text = template_path.read_text()
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in os.environ:
            missing.add(key)
            return ""
        return os.environ[key]

    rendered = PLACEHOLDER.sub(replace, text)
    if missing:
        names = ", ".join(sorted(missing))
        print(f"{template_path}: missing template values: {names}", file=sys.stderr)
        return 1

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
