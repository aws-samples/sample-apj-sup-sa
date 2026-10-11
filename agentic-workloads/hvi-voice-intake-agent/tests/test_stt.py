#!/usr/bin/env python
"""
Measures the pinned Whisper endpoint on telephony-grade Hindi, English and
Hinglish.

The fixtures are 8kHz mono, matching what a SIP trunk delivers. Scoring is
keyword recall rather than exact transcript equality because the downstream
classifier needs medical slots, not filler-word fidelity.

STT is Whisper-only. This integration diagnostic hits the live Whisper endpoint
so scripts/51-whisper-status.sh checks the endpoint it just inspected.

Run:  ./.venv/bin/python tests/test_stt.py
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "agent"))

import hvi_stt  # noqa: E402
from wer_harness import slot_recall  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "audio"
PRODUCT_MODES = {"english", "hindi", "hinglish"}
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def main() -> int:
    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.exists():
        print("no fixtures - run: ./.venv/bin/python tools/make_test_audio.py")
        return 1
    manifest = [
        fixture for fixture in json.loads(manifest_path.read_text())
        if fixture.get("mode") in PRODUCT_MODES
    ]
    if not manifest:
        # Guard against a vacuous pass: a manifest regenerated without mode
        # fields must fail loudly rather than check nothing and exit 0.
        print("no product-mode fixtures in the manifest - nothing was tested")
        return 1

    print("test engine: whisper")
    print(f"Whisper endpoint: {hvi_stt.WHISPER_ENDPOINT}")
    print(f"fixtures: {len(manifest)} at 8kHz mono\n")

    latencies: list[float] = []

    for fixture in manifest:
        path = FIXTURES / fixture["file"]
        audio = path.read_bytes()

        t0 = time.time()
        result = hvi_stt.transcribe(audio, engine="whisper")
        elapsed = time.time() - t0
        latencies.append(elapsed)

        text = result.get("text", "")

        print(f"  {fixture['id']}  ({fixture['durationSeconds']}s audio, "
              f"{elapsed:.2f}s transcribe)")
        print(f"      said : {fixture['text']}")
        print(f"      heard: {text or '(nothing)'}")

        if result.get("error"):
            check(f"{fixture['id']}: transcribed", False,
                  str(result["error"])[:120])
            continue

        check(f"{fixture['id']}: produced text", bool(text), "empty transcript")
        if not text:
            continue

        hits, total, missed = slot_recall(fixture, text)
        check(f"{fixture['id']}: medical slots {hits}/{total}",
              hits == total, f"missed={missed}; transcript={text[:80]!r}")

    if latencies:
        print("\n  Whisper transcription latency: "
              f"p50 {statistics.median(latencies):.2f}s, "
              f"avg {statistics.mean(latencies):.2f}s, "
              f"max {max(latencies):.2f}s")

    print("\n[final] Does a real transcript survive into the classifier?")
    hinglish = next((fixture for fixture in manifest
                     if fixture["id"] == "hinglish_diabetes"), None)
    if hinglish:
        result = hvi_stt.transcribe(
            (FIXTURES / hinglish["file"]).read_bytes(), engine="whisper")
        text = result.get("text", "")
        if text:
            os.environ.setdefault("OPENSEARCH_ENDPOINT",
                                  os.environ.get("OPENSEARCH_ENDPOINT", ""))
            try:
                import tools as agent_tools
                classification = agent_tools.classify_condition(text)
                print(f"      transcript -> {classification.get('icd10')} "
                      f"({classification.get('source')}, "
                      f"{classification.get('score')})")
                check("transcript classifies to diabetes",
                      classification.get("icd10") == "E11",
                      f"got {classification.get('icd10')} from {text!r}")
            except Exception as exc:
                check("classifier reachable", False, str(exc)[:120])
        else:
            check("transcript available for classification", False, "empty")

    print("\n" + "=" * 68)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Whisper handles telephony-grade Hindi, English and Hinglish.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
