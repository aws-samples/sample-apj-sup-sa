#!/usr/bin/env python
"""Build the shared prompt cache: synthesise every fixed utterance once.

Measured with tools/prompt_inventory.py: 85 of the 92 utterances the agent can
speak contain no placeholders, so they are identical for every customer forever.
Synthesising them live was costing ~1.7s per turn, which was the largest single
item in a 2.8s turn budget.

This walks the form schema in DynamoDB - not a hardcoded list - so a health item
added at runtime is picked up by a re-run with no code change. That matters
because "new items without redeployment" is a stated requirement, and a cache
built from a frozen list would quietly stop covering new questions.

Personalised utterances (the 7 containing {{customerName}} and friends) are
deliberately NOT built here. They are synthesised per call while the phone rings.

Idempotent: an entry whose content hash already exists is skipped, so a re-run
after editing one prompt costs one synthesis rather than 85.

Run:  ./.venv/bin/python tools/build_prompt_cache.py
      ./.venv/bin/python tools/build_prompt_cache.py --dry-run
      ./.venv/bin/python tools/build_prompt_cache.py --language hi
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

import hvi_prompts  # noqa: E402
import hvi_state as st  # noqa: E402
from hvi_prompt_cache import PromptCache, cache_key, s3_key  # noqa: E402
# The SAME identifier expansion the runtime applies, for the same reason as the
# splitter below: this tool and media/tts.py both synthesise, so two copies would be
# two pronunciations of one utterance. It runs after cache_key, so keys are still
# content-addressed on the original text.
from hvi_prompt_cache import speakable_identifiers  # noqa: E402
# The SAME splitter the runtime uses (media/tts.py), so the cache is seeded with
# exactly the segment keys that will be looked up. Two splitters would be two ways
# to compute the key - the bug class this cache has had four times already.
from hvi_prompt_cache import split_segments as tts_split  # noqa: E402

FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")
DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
ENDPOINT = os.environ.get("OMNIVOICE_ENDPOINT", "hvi-omnivoice")
BUCKET = os.environ.get("PROMPT_CACHE_BUCKET", "")

PLACEHOLDER = re.compile(r"\{\{\s*\w+\s*\}\}")
PROMPT_KEYS = {"en": "prompt", "hi": "promptHi", "hinglish": "promptHinglish"}
REPROMPT_KEYS = {"en": "reprompt", "hi": "repromptHi", "hinglish": "repromptHinglish"}

# Short closed set the orchestrator uses between questions. Cached for the same
# reason as the prompts: they are fixed strings, spoken constantly.
ACKNOWLEDGEMENTS = {
    "en": ["Thank you.", "Got it, thank you.", "Understood.",
           "Sorry, could you please repeat that?",
           "Thank you. That's all I needed."],
    "hi": ["धन्यवाद।", "ठीक है, धन्यवाद।", "समझ गई।",
           "माफ़ कीजिए, क्या आप दोहरा सकते हैं?",
           "धन्यवाद। मुझे इतनी ही जानकारी चाहिए थी।"],
    "hinglish": ["Thank you.", "Theek hai, thank you.", "Samajh gayi.",
                 "Sorry, aap dohra sakte hain?",
                 "Thank you. Bas itni hi information chahiye thi."],
}

# Re-ask lead-ins from agent/orchestrator.py. Fixed strings spoken constantly -
# the honest-invalid lead was heard three times in one minute on a live call and
# missed the cache every time, ~2s of GPU synthesis per miss, on exactly the turn
# where the caller is already having trouble. If a lead is added there it MUST be
# added here, or it silently costs dead air on every use.
REASK_LEADS = {
    "en": ["Sorry, I didn't quite catch that.",
           "That doesn't seem right — I may have misheard.",
           "Of course."],
    "hi": ["माफ़ कीजिए, मैं ठीक से सुन नहीं पाई।",
           "यह सही नहीं लग रहा, शायद मैंने ग़लत समझा।",
           "ज़रूर।"],
    "hinglish": ["Maaf kijiye, main theek se sun nahi payi.",
                 "Yeh sahi nahi lag raha, shayad maine galat samjha.",
                 "Zaroor."],
}


def synthesise(rt, text: str, language: str) -> bytes:
    """Synthesise one utterance IN ITS OWN LANGUAGE.

    `language` is not decoration. The endpoint keeps a reference clip per language
    and clones from the one this names, because a cross-lingual reference imports
    its own language's accent.

    This call omitted it, and the consequence was self-cancelling in a way worth
    recording: every Hindi prompt was synthesised from the DEFAULT (English)
    reference and then stored under the "hi" key. The audio was anglicised Hindi,
    it was indistinguishable from correct at the API level, and because a cache hit
    never reaches the endpoint, the Hindi reference would never have been used on a
    real call. 85 of 92 utterances are served from this cache, so the per-language
    references would have been decorative.
    """
    resp = rt.invoke_endpoint(
        EndpointName=ENDPOINT,
        ContentType="application/json",
        Accept="audio/pcm",
        Body=json.dumps({"text": speakable_identifiers(text),
                         "language": language}),
    )
    return resp["Body"].read()


def crm_insurer_names() -> list[str] | None:
    """Distinct insurer names in the CRM projection, for the opening utterance.

    Scanned HERE rather than added to common/hvi_state.py on purpose: that module
    ships to the media host, which deliberately holds no DynamoDB permissions at
    all, and widening its surface to solve a build-time question would be a poor
    trade. This is a build tool run with developer credentials.

    Returns None when the table cannot be read, which makes hvi_prompts.cacheable()
    fall back to its default name. A missing insurer costs one live synthesis on the
    opening, not a failure.
    """
    try:
        table = boto3.resource("dynamodb", region_name=DATA_REGION).Table(st.TBL_CRM)
        names: set[str] = set()
        kwargs: dict = {"ProjectionExpression": "insurerName"}
        while True:
            page = table.scan(**kwargs)
            for row in page.get("Items", []):
                name = (row.get("insurerName") or "").strip()
                if name:
                    names.add(name)
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return sorted(names) or None
    except Exception as e:  # noqa: BLE001
        print(f"could not read insurer names from the CRM ({e}); using the default")
        return None


def collect(language: str | None) -> list[tuple[str, str, str]]:
    """Every shareable utterance as (language, text, source_label)."""
    items = st.get_form_items(FORM_ID)
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(lang: str, text: str, label: str) -> None:
        text = (text or "").strip()
        if not text or PLACEHOLDER.search(text):
            return
        # SPLIT EXACTLY AS THE RUNTIME SPLITS, using the same function, because the
        # cache is content-addressed: the runtime now looks up SEGMENTS (see
        # media/tts.py split_segments and the dead-air analysis there), so seeding
        # whole utterances would populate keys nothing ever queries. This is the
        # fourth instance of the builder-and-consumer-disagree bug class in this
        # cache's history; importing the one splitter is what prevents the fifth.
        for i, seg in enumerate(tts_split(text)):
            if (lang, seg) in seen:
                continue
            seen.add((lang, seg))
            out.append((lang, seg, f"{label}[{i}]" if i else label))

    for item in items:
        for lang, key in PROMPT_KEYS.items():
            if language and lang != language:
                continue
            add(lang, item.get(key), f"{item['itemId']}.{key}")
        for lang, key in REPROMPT_KEYS.items():
            if language and lang != language:
                continue
            add(lang, item.get(key), f"{item['itemId']}.{key}")

    for lang, texts in ACKNOWLEDGEMENTS.items():
        if language and lang != language:
            continue
        for i, t in enumerate(texts):
            add(lang, t, f"ack.{lang}.{i}")
    for lang, texts in REASK_LEADS.items():
        if language and lang != language:
            continue
        for i, t in enumerate(texts):
            add(lang, t, f"reask.{lang}.{i}")

    # The language-selection question. THE most latency-sensitive utterance in the
    # whole call: it is the first thing said, the customer has just picked up, and
    # live synthesis is 2-3 seconds during which the line sounds dead - which is
    # exactly the failure a caller already reported once.
    #
    # The language key here is the language it will be SYNTHESISED in, which for a
    # bilingual utterance is not the language of the call. It has to match the
    # ttsLanguage the orchestrator sends or the lookup misses.
    # The opening carries the insurer's name, so it is cached once per insurer. See
    # hvi_prompts.cacheable(). Names come from the CRM rather than a constant, so a
    # second insurer is picked up by a re-run with no code change - the same property
    # the form schema has.
    insurers = crm_insurer_names()
    if insurers:
        print(f"insurers in the CRM: {', '.join(insurers)}")
    for lang, text, label in hvi_prompts.cacheable(insurers):
        if language and lang != language:
            continue
        add(lang, text, label)

    # THE GRAPH'S PROTOCOL QUESTIONS. These were never seeded, and they are most
    # of what a caller with any condition actually hears: a thyroid disclosure is
    # followed by FU_CONTROLLED, FU_TSH_LAST and friends, every one of which
    # logged "segment cache miss, synthesising live" on a real call. They are as
    # fixed as the form's prompts - the graph seed is the source - so there is no
    # reason for the first caller with each condition to pay GPU latency for them.
    kg_path = os.path.join(os.path.dirname(__file__), "..", "seed", "health-kg.json")
    try:
        with open(kg_path, encoding="utf-8") as fh:
            kg = json.load(fh)
        fu_prompt_keys = {"en": "prompt_en", "hi": "prompt_hi",
                          "hinglish": "prompt_hinglish"}
        for fu in kg.get("followUpQuestions", []):
            for lang, key in fu_prompt_keys.items():
                if language and lang != language:
                    continue
                add(lang, fu.get(key), f"kg.{fu.get('id')}.{key}")
    except OSError as e:
        # The seed lives in the repo, so this only fails when run from an odd cwd.
        # Everything else is still worth building.
        print(f"warning: could not read {kg_path} ({e}); graph follow-up prompts "
              f"not seeded, they will be synthesised on first use")

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="build the shared prompt cache")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--language", choices=["en", "hi", "hinglish"])
    ap.add_argument("--force", action="store_true",
                    help="re-synthesise entries that already exist")
    args = ap.parse_args()

    if not BUCKET:
        print("PROMPT_CACHE_BUCKET is not set. Run scripts/54-prompt-cache.sh, "
              "which creates the bucket and exports it.")
        return 2

    work = collect(args.language)
    print(f"form {FORM_ID}: {len(work)} shareable utterance(s)")
    print(f"bucket {BUCKET}, endpoint {ENDPOINT}\n")

    s3 = boto3.client("s3", region_name=DATA_REGION)
    cache = PromptCache(bucket=BUCKET, local_dir="")

    existing: set[str] = set()
    if not args.force:
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=BUCKET, Prefix="prompt-cache/"):
                for obj in page.get("Contents", []):
                    existing.add(os.path.basename(obj["Key"])[:-4])
        except Exception as e:  # noqa: BLE001
            print(f"could not list existing entries ({e}); building all")

    todo = [(l, t, s) for (l, t, s) in work if cache_key(t, l) not in existing]
    print(f"{len(work) - len(todo)} already cached, {len(todo)} to build")

    if args.dry_run:
        for lang, text, label in todo[:20]:
            print(f"  WOULD build {lang:9s} {label:24s} {text[:52]!r}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        return 0

    if not todo:
        print("nothing to do")
        return 0

    rt = boto3.client("sagemaker-runtime", region_name=DATA_REGION)
    built = failed = 0
    started = time.time()

    for i, (lang, text, label) in enumerate(todo, start=1):
        try:
            pcm = synthesise(rt, text, lang)
            if len(pcm) < 1600:  # under 0.1s of audio
                # Near-empty audio is a synthesis failure that returns HTTP 200.
                # Caching it would make the agent silently skip that question on
                # every future call, which is far worse than a cache miss.
                raise ValueError(f"suspiciously short audio: {len(pcm)} bytes")
            key = cache.put(text, lang, pcm)
            built += 1
            secs = len(pcm) / 2 / 8000
            print(f"  [{i:3d}/{len(todo)}] {lang:9s} {secs:4.1f}s  "
                  f"{key[:10]}  {label:22s} {text[:40]!r}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [{i:3d}/{len(todo)}] FAILED {lang} {label}: {e}")

    elapsed = time.time() - started
    print(f"\nbuilt {built}, failed {failed}, in {elapsed:.0f}s")
    if failed:
        print("failures are safe: those utterances fall back to live synthesis.")
    print(f"\ns3://{BUCKET}/prompt-cache/")
    return 1 if failed and not built else 0


if __name__ == "__main__":
    raise SystemExit(main())
