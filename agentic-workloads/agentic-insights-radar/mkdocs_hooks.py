# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""mkdocs hook: links from docs/ to files outside it (README, source files) point at the repository."""

from __future__ import annotations

import posixpath
import re
from typing import Any

# The sample lives in a folder of the aws-samples/sample-apj-sup-sa repository.
REPO = "https://github.com/aws-samples/sample-apj-sup-sa/blob/main/"
SAMPLE = "agentic-workloads/agentic-insights-radar"
LINK = re.compile(r"\]\((\.\./[^)\s#]+)(#[^)\s]*)?\)")


def on_page_markdown(markdown: str, page: Any, **_: Any) -> str:
    base = posixpath.dirname(page.file.src_uri)

    def fix(m: re.Match[str]) -> str:
        target = posixpath.normpath(posixpath.join(SAMPLE, "docs", base, m.group(1)))
        if target.startswith(SAMPLE + "/docs/"):
            return m.group(0)
        return f"]({REPO}{target}{m.group(2) or ''})"

    return LINK.sub(fix, markdown)
