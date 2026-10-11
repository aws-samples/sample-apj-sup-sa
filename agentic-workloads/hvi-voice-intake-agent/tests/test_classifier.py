#!/usr/bin/env python
"""
Tests the OpenSearch k-NN classifier, and specifically the part that justifies
having it at all.

Neptune resolves *known* synonyms deterministically. If OpenSearch only handled
the same phrases, it would be redundant. So this suite is weighted towards
utterances nobody put in the graph: full sentences, unlisted paraphrases, and
Hinglish code-mixing. Those are the long tail the vector index exists for.

Run:  ./.venv/bin/python tests/test_classifier.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import opensearch_admin as osa  # noqa: E402

import boto3  # noqa: E402

lam = boto3.client("lambda", region_name=os.environ.get("DATA_REGION", "ap-south-1"))
failures: list[str] = []


def graph_resolve(phrase: str) -> list[str]:
    """Ask Neptune for an exact/known-synonym match."""
    r = lam.invoke(FunctionName="hvi-graph",
                   Payload=json.dumps({"action": "resolve_condition",
                                       "phrase": phrase}).encode())
    body = json.loads(r["Payload"].read())
    res = body.get("result", {})
    if not res.get("found"):
        return []
    return [m["icd10"] for m in res.get("matches", [])]


def row(phrase: str, expect: str) -> None:
    g = graph_resolve(phrase)
    c = osa.classify(phrase, k=3)
    top = c["topMatch"]
    got = top["icd10"] if top else None
    score = top["score"] if top else 0.0

    graph_hit = expect in g
    vec_hit = got == expect
    ok = graph_hit or vec_hit

    verdict = "PASS" if ok else "FAIL"
    who = "graph" if graph_hit else ("vector" if vec_hit else "-")
    print(f"  {verdict}  {phrase[:44]:<46} want={expect:<5} "
          f"graph={(g[0] if g else '-'):<5} vec={got or '-':<5} "
          f"({score:.3f}) via={who}")
    if not ok:
        failures.append(phrase)
    return graph_hit, vec_hit


def main() -> int:
    if not osa.HOST:
        print("OPENSEARCH_ENDPOINT not set - source config/env.sh")
        return 1

    print("\n[1] Known synonyms - both layers should agree")
    known = [("sugar ki bimari", "E11"), ("lakwa", "I63"), ("dama", "J45")]
    for p, e in known:
        row(p, e)

    print("\n[2] Unlisted phrasing - this is what the vector index is for")
    unseen = [
        ("mujhe sugar ki problem hai bahut saalon se", "E11"),
        ("my blood sugar levels are always high", "E11"),
        ("doctor ne bola hai ki mera blood pressure zyada hai", "I10"),
        ("I get very breathless when I climb stairs", "J45"),
        ("chalne par meri chhaati mein dard hota hai", "I25"),
        ("I had a growth removed from my breast", "C50"),
        ("mere gurde mein stone tha operation hua tha", "N20"),
        ("I take tablets every day for my thyroid", "E03"),
        ("mujhe bahut ghabrahat hoti hai aur neend nahi aati", "F41"),
        ("meri haddi tut gayi thi accident mein", "S72"),
        ("khoon ki kami hai mujhe", "D50"),
        ("I was coughing blood and lost weight for months", "A15"),
    ]
    vec_wins = 0
    for p, e in unseen:
        g, v = row(p, e)
        if v and not g:
            vec_wins += 1

    print("\n[3] Devanagari input")
    for p, e in [("मुझे मधुमेह है", "E11"), ("मेरी किडनी में पथरी थी", "N20")]:
        row(p, e)

    print("\n[4] Sensitive conditions still classify, with the flag intact")
    r = osa.classify("I am HIV positive and on treatment", k=3)
    top = r["topMatch"]
    ok = top and top["icd10"] == "B20"
    print(f"  {'PASS' if ok else 'FAIL'}  HIV disclosure -> {top['icd10'] if top else '-'} "
          f"sensitivity={top.get('sensitivity') if top else '-'}")
    if not ok:
        failures.append("HIV classification")
    elif top.get("sensitivity") != "maximum":
        failures.append("HIV sensitivity flag missing")
        print("  FAIL  sensitivity flag should be 'maximum'")
    else:
        print("  PASS  sensitivity flag preserved through classification")

    print("\n[5] Red-flag presentations demand confirmation, not a silent guess")
    red_flags = [
        ("chalne par seene mein dard hota hai", "I25"),
        ("khoon ki khaansi aa rahi hai", "A15"),
        ("ek taraf ka haath pair kaam nahi kar raha", "I63"),
        ("chhaati mein gaanth hai", "C50"),
    ]
    for phrase, expect in red_flags:
        r = osa.classify(phrase, k=3)
        top = r["topMatch"]
        hit = top and top["icd10"] == expect
        flagged = r["needsConfirmation"]
        ok = hit and flagged
        print(f"  {'PASS' if ok else 'FAIL'}  {phrase[:40]:<42} -> "
              f"{top['icd10'] if top else '-':<5} redFlag={r['redFlag']} "
              f"needsConfirmation={flagged}")
        if not ok:
            failures.append(f"red flag {phrase}")

    print("\n[6] Nonsense should not confidently match")
    r = osa.classify("what is the weather like tomorrow", k=3)
    print(f"        top={r['topMatch']['icd10'] if r['topMatch'] else '-'} "
          f"score={r['topMatch']['score']:.3f} confident={r['confident']}")
    if r["confident"]:
        failures.append("off-topic phrase matched confidently")
        print("  FAIL  off-topic utterance was marked confident")
    else:
        print("  PASS  off-topic utterance correctly below confidence threshold")

    print("\n" + "=" * 78)
    print(f"Vector index resolved {vec_wins} phrasing(s) the graph alone could not.")
    if failures:
        print(f"FAILED: {len(failures)} case(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All classifier checks passed.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
