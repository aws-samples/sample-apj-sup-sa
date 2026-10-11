#!/usr/bin/env python
"""Does the prompt cache actually save the latency it was built to save?

The cache exists on a claim - that TTS is ~1712ms of a 2.8s turn budget and that
most agent speech is fixed text - so the claim is measured here rather than
asserted in a comment.

Also covers the properties that make it safe:

  CONTENT-ADDRESSED   editing a prompt changes the key, so stale audio cannot be
                      served. This is what lets prompts be edited in DynamoDB
                      without an explicit invalidation step.
  LANGUAGE IN THE KEY same text in two languages must not collide. Without this a
                      Hinglish call could be served the English rendering of an
                      identical string.
  A MISS IS NOT AN ERROR  health items are addable at runtime, so the cache always
                      potentially lags the schema. A miss must fall back to live
                      synthesis and then heal itself.

Run:  ./.venv/bin/python tests/test_prompt_cache.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import uuid

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

import hvi_state as st  # noqa: E402
from hvi_prompt_cache import (  # noqa: E402
    PromptCache, cache_key, pcm_to_wav, s3_key, split_segments, wav_to_pcm,
)

REGION = os.environ.get("DATA_REGION", "ap-south-1")
BUCKET = os.environ.get("PROMPT_CACHE_BUCKET", "")
ENDPOINT = os.environ.get("OMNIVOICE_ENDPOINT", "hvi-omnivoice")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    if not BUCKET:
        print("PROMPT_CACHE_BUCKET not set; run scripts/54-prompt-cache.sh first")
        return 2

    print(f"bucket {BUCKET}\n")
    tmp_dir = os.path.join("/tmp", f"hvi-cache-test-{uuid.uuid4().hex[:8]}")
    cache = PromptCache(bucket=BUCKET, local_dir=tmp_dir)
    s3 = boto3.client("s3", region_name=REGION)
    written_keys: list[str] = []

    try:
        # ---- 1. keying ------------------------------------------------------
        print("[1] the key is content-addressed")
        a = cache_key("Do you smoke?", "en")
        b = cache_key("Do you smoke?", "en")
        c = cache_key("Do you smoke or chew tobacco?", "en")
        d = cache_key("Do you smoke?", "hi")
        check("stable for the same text", a == b)
        check("EDITED TEXT GETS A NEW KEY, so stale audio is impossible", a != c)
        check("language is part of the key", a != d)
        # Whitespace-only differences should NOT bust the cache - a re-indented
        # prompt in DynamoDB is the same utterance.
        check("whitespace is normalised",
              cache_key("Do  you   smoke?", "en") == cache_key("Do you smoke?", "en"))

        # ---- 2. wav round trip ----------------------------------------------
        print("\n[2] the container round-trips losslessly")
        pcm = bytes(range(256)) * 40
        check("pcm -> wav -> pcm is identical", wav_to_pcm(pcm_to_wav(pcm)) == pcm,
              f"{len(pcm)} bytes")

        # ---- 3. real entries are there ---------------------------------------
        #
        # CHECKED PER SEGMENT, WHICH IS WHAT THE RUNTIME FETCHES. This asserted a
        # whole-prompt lookup, and that only ever worked because every prompt it
        # happened to sample was short enough to be a single segment. Lengthening
        # the consent prompt to state the non-disclosure consequences split it into
        # five, all five cached and every one of them served from cache on a call -
        # and the whole-prompt key, which nothing ever asks for, was reported as a
        # cache miss. Using the same splitter the builder and media/tts.py use is
        # the point: two ways to compute the key is the bug class this cache has
        # already had four times.
        print("\n[3] the built cache actually covers the form, per segment")
        items = st.get_form_items(FORM_ID)
        for key, lang in (("prompt", "en"), ("promptHi", "hi"),
                          ("promptHinglish", "hinglish")):
            sample = [i for i in items
                      if (i.get(key) or "") and "{{" not in (i.get(key) or "")][:5]
            missing = [
                (item["itemId"], seg[:48])
                for item in sample
                for seg in split_segments(item[key])
                if cache.get(seg, lang) is None
            ]
            total = sum(len(split_segments(item[key])) for item in sample)
            check(f"{lang} prompts are cached ({total} segments)", not missing,
                  str(missing))

        # ---- 4. the actual point: latency -----------------------------------
        print("\n[4] MEASURED: cache hit vs live synthesis")
        # A SEGMENT, because that is the unit the cache stores and the media layer
        # fetches. Timing a whole multi-sentence prompt would time a key that is
        # never requested on a call.
        english = [i for i in items
                   if (i.get("prompt") or "") and "{{" not in (i.get("prompt") or "")]
        text = next(seg for item in english for seg in split_segments(item["prompt"])
                    if cache.get(seg, "en") is not None)

        # Warm the disk tier once, so this is not measuring the first S3 fetch.
        cache.get(text, "en")

        # MEMO TIER, measured but NOT the number that matters. Within one call
        # each prompt is spoken once, so the in-process memo almost never gets a
        # second look at the same text. Reporting the memo figure as "the cache
        # hit latency" would overstate the result by orders of magnitude.
        memo_times = []
        for _ in range(12):
            t0 = time.perf_counter()
            assert cache.get(text, "en")
            memo_times.append((time.perf_counter() - t0) * 1000)
        memo_p50 = statistics.median(memo_times)

        # DISK TIER, which is the realistic per-utterance path. The memo is
        # cleared between reads so each one goes to the filesystem, as it does on
        # a live call.
        disk_times = []
        for _ in range(12):
            cache._memo.clear()
            t0 = time.perf_counter()
            got = cache.get(text, "en")
            disk_times.append((time.perf_counter() - t0) * 1000)
            assert got
        disk_p50 = statistics.median(disk_times)
        hit_p50 = disk_p50

        rt = boto3.client("sagemaker-runtime", region_name=REGION)
        live_times = []
        for _ in range(3):
            t0 = time.perf_counter()
            rt.invoke_endpoint(EndpointName=ENDPOINT,
                               ContentType="application/json",
                               Accept="audio/pcm",
                               Body=json.dumps({"text": text}))
            live_times.append((time.perf_counter() - t0) * 1000)
        live_p50 = statistics.median(live_times)

        print(f"      memo tier    p50 {memo_p50:8.3f} ms   (not the operative number)")
        print(f"      DISK tier    p50 {disk_p50:8.3f} ms   <- the real per-utterance path")
        print(f"      live synth   p50 {live_p50:8.1f} ms")
        print(f"      speedup      {live_p50 / max(disk_p50, 0.001):.0f}x on the disk tier")
        check("a disk hit is at least 50x faster than synthesising",
              live_p50 / max(disk_p50, 0.001) > 50,
              f"{live_p50 / max(disk_p50, 0.001):.0f}x")
        # The whole justification: TTS has to leave the critical path, not merely
        # get quicker.
        check("a hit is effectively free (<20ms)", hit_p50 < 20,
              f"{hit_p50:.2f}ms")

        # ---- 5. a miss is survivable ----------------------------------------
        print("\n[5] a miss returns None rather than raising")
        novel = f"This question was added at runtime {uuid.uuid4()}"
        check("unknown text misses cleanly", cache.get(novel, "en") is None)

        print("\n[6] a miss can heal itself")
        cache.put(novel, "en", pcm)
        written_keys.append(cache_key(novel, "en"))
        # New cache object so nothing is served from the in-process memo.
        fresh = PromptCache(bucket=BUCKET, local_dir=tmp_dir + "-2")
        got = fresh.get(novel, "en")
        check("written back and retrievable from S3", got == pcm,
              f"{len(got or b'')} bytes")

        print("\n[7] a corrupt local entry is recovered, not fatal")
        key = cache_key(novel, "en")
        bad_path = os.path.join(tmp_dir + "-2", f"{key}.wav")
        if os.path.exists(bad_path):
            with open(bad_path, "wb") as fh:
                fh.write(b"not a wav file")
            recovered = PromptCache(bucket=BUCKET, local_dir=tmp_dir + "-2")
            check("re-fetched from S3 after local corruption",
                  recovered.get(novel, "en") == pcm)
        else:
            check("local entry was persisted for the corruption test", False,
                  "no local file")

        print(f"\ncache tier counters: {cache.stats}")

    finally:
        print("\ncleaning up")
        for k in written_keys:
            try:
                s3.delete_object(Bucket=BUCKET, Key=s3_key(k))
                print(f"  deleted s3://{BUCKET}/{s3_key(k)}")
            except Exception as e:  # noqa: BLE001
                print(f"  could not delete {k}: {e}")
        for d in (tmp_dir, tmp_dir + "-2"):
            try:
                for f in os.listdir(d):
                    os.unlink(os.path.join(d, f))
                os.rmdir(d)
            except OSError:
                pass

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("prompt cache OK: content-addressed, measured fast, safe on a miss.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
