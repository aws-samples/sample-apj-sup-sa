#!/usr/bin/env python
"""Print the authoritative prompt-cache voice version.

Exists so the deploy script can REPORT the value without REDEFINING it. The
previous arrangement had scripts/72-media-deploy.sh carry its own literal default,
which shadowed the one in common/hvi_prompt_cache.py and orphaned the entire cache
- see the long comment at the top of that script.

Reading it through an import rather than grepping the source means the answer is
whatever the code actually computes, including an environment override, which is
exactly what the media host will see.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from hvi_prompt_cache import VOICE_VERSION  # noqa: E402

print(VOICE_VERSION)
