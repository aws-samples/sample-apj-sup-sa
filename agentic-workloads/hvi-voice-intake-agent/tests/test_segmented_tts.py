#!/usr/bin/env python3
"""Segmented TTS: utterances split into cacheable pieces, builder and runtime agree.

WHY THIS EXISTS
A completed 63-turn live call logged "prompt cache miss, synthesising live" on
EVERY SINGLE TURN, and the caller reported "a lot of silence". The cache was
healthy; nothing qualified for it, because the orchestrator glues a personalised
acknowledgement ("ठीक है, अनिता जी।") to the front of each question and the cache
is content-addressed on the full string. One personalised prefix made the whole
utterance unique, every turn, every call.

The fix splits utterances at sentence boundaries so the repeated ack and the fixed
question hit the cache independently. That creates a COHERENCE OBLIGATION, which is
what most of this file guards: the builder (tools/build_prompt_cache.py) must seed
exactly the segment keys the runtime (media/tts.py) looks up. This cache's history
already has four bugs of the builder-and-consumer-disagree class - stale
VOICE_VERSION, a builder sending no language, an env var pinning an old voice, a
sandbox making the disk read-only. Both sides now import ONE splitter from
common/hvi_prompt_cache.py, and this test fails if either stops.

Run:  ./.venv/bin/python tests/test_segmented_tts.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from hvi_prompt_cache import split_segments  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    print("[1] The live utterance that missed the cache now splits")
    # Verbatim from the 63-turn call: ack + question, glued.
    segs = split_segments("ठीक है, अनिता जी। और आपका वर्तमान वजन किलोग्राम में?")
    check("splits into ack and question", len(segs) == 2, str(segs))
    check("the ack is its own segment", segs[0] == "ठीक है, अनिता जी।", segs[0])
    check("the question is its own segment",
          segs[1] == "और आपका वर्तमान वजन किलोग्राम में?", segs[1])

    print("\n[2] Danda, question mark and full stop all split")
    segs = split_segments("Okay, thank you. When were you first diagnosed with this?")
    check("English ack splits off", len(segs) == 2, str(segs))
    segs = split_segments("माफ़ कीजिए, मैं ठीक से सुन नहीं पाई। इसका कारण क्या था?")
    check("Hindi apology splits off", len(segs) == 2, str(segs))

    print("\n[3] Dots inside identifiers do NOT split")
    # The completion summary contains both of these, and splitting them would
    # synthesise fragments like "user@example" and "com".
    segs = split_segments("मैं user@example.com पर सारांश भेज दूँगी।")
    check("email survives whole", any("user@example.com" in s for s in segs),
          str(segs))
    segs = split_segments("Your application AS-2026-0098421 is on record.")
    check("policy id survives whole",
          any("AS-2026-0098421" in s for s in segs), str(segs))

    print("\n[4] Tiny fragments merge rather than synthesising alone")
    segs = split_segments("जी। और आपका वर्तमान वजन किलोग्राम में?")
    check("a two-character sentence merges forward", len(segs) == 1, str(segs))

    print("\n[5] Degenerate input")
    check("empty yields nothing", split_segments("") == [])
    check("whitespace yields nothing", split_segments("   ") == [])
    check("single sentence passes through",
          split_segments("नमस्ते।") == ["नमस्ते।"])

    print("\n[6] Builder and runtime use the SAME splitter")
    # The coherence property. If either side grows its own implementation this
    # cache regresses to full-utterance keys - silently, because a miss just means
    # slow, not broken.
    import importlib

    tts_mod = importlib.import_module("media.tts")
    src = open(os.path.join(os.path.dirname(__file__), "..", "media", "tts.py"),
               encoding="utf-8").read()
    check("media/tts.py imports the shared splitter",
          "from common.hvi_prompt_cache import split_segments" in src)
    bsrc = open(os.path.join(os.path.dirname(__file__), "..", "tools",
                             "build_prompt_cache.py"), encoding="utf-8").read()
    check("build_prompt_cache.py imports the shared splitter",
          "from hvi_prompt_cache import split_segments" in bsrc)
    check("media/tts.py defines no rival sentence regex",
          "_SENTENCE_SPLIT" not in src)
    check("_split_segments survives a broken import",
          tts_mod._split_segments("hello there") == ["hello there"])  # noqa: SLF001

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Utterances split into cacheable segments and the builder seeds exactly "
          "the keys the runtime looks up.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
