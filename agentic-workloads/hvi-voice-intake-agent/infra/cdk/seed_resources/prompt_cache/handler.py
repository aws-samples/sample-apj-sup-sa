"""Prompt-cache seed custom-resource handler (Agent stack, omnivoice path only).

Builds the shared prompt cache — the ~85 placeholder-free utterances the agent
speaks on every call — and uploads them under ``prompt-cache/`` of the
``hvi-artifacts`` bucket. It does **not** reimplement any synthesis: it reuses
``tools/build_prompt_cache.py`` verbatim (vendored beside this handler along with
the ``common/`` modules it imports and ``seed/health-kg.json``), exactly as
``scripts/54-prompt-cache.sh`` invokes that tool.

Created only when ``hvi:tts=omnivoice`` (the whole OmniVoice subtree is gated),
and sequenced after the OmniVoice endpoint is InService (Inference) and the form
schema is seeded (Data) via stack/resource dependencies.

Offline note: this handler is only *staged* at synth (a local file copy); it is
executed exclusively at deploy time with real credentials.

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType`` and
``ResourceProperties`` (``PromptCacheBucket``, ``OmniVoiceEndpoint``, ``FormId``,
``DataRegion`` and ``SeedHash`` for change detection). Those are also provided as
the env vars ``tools/build_prompt_cache.py`` reads.
"""

from __future__ import annotations

import os
import sys


def _physical_id(event: dict) -> str:
    return event.get("PhysicalResourceId") or "hvi-prompt-cache"


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})

    if request_type == "Delete":
        # The cache objects live under RETAIN with the artifacts bucket; the
        # bucket's own lifecycle owns teardown, so there is nothing to undo.
        return {"PhysicalResourceId": _physical_id(event)}

    # build_prompt_cache.py reads its configuration from the environment.
    os.environ["PROMPT_CACHE_BUCKET"] = props["PromptCacheBucket"]
    os.environ["OMNIVOICE_ENDPOINT"] = props["OmniVoiceEndpoint"]
    os.environ["FORM_ID"] = props.get("FormId", "arogya-sanjeevani-v1")
    os.environ["DATA_REGION"] = props.get("DataRegion", "ap-south-1")

    # The tool and the common modules it imports are vendored flat beside this
    # handler (the Lambda task root is on sys.path), so a plain import resolves.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    import build_prompt_cache  # noqa: E402  (vendored at deploy time)

    rc = build_prompt_cache.main()
    # main() returns non-zero only when every synthesis failed; a partial build
    # is acceptable (misses fall back to live synthesis), matching scripts/54.
    if rc not in (0, 1):
        raise RuntimeError(f"prompt-cache build failed (rc={rc})")

    return {"PhysicalResourceId": _physical_id(event)}
