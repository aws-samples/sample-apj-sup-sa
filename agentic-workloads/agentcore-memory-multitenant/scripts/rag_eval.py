#!/usr/bin/env python3
"""Standalone RAG eval: asks every question in kb/answer_key.json through
the live /api/chat (CloudFront URL from deploy/state.json -- the same path
a shopper uses: WAF -> CloudFront -> backend -> Gateway -> kb_search), each
as its brand/agent with a fresh shopper, and scores the answers.

Per question (100 points):
  correctness 50  every `must_include` group appears in the reply
  retrieval   20  kb_search retrieved a passage from `expected_doc`
  citation    20  the reply cites a passage from `expected_doc`
  grounded    10  kb_search was called at all
A reply containing any `must_not_include` string (another brand's policy
fact) is a LEAK: that question scores 0 and the run fails. Questions with
`expected_doc: null` are pure isolation probes: 100 if no leak, else 0.

Usage:
    python3 scripts/rag_eval.py                       # print report
    python3 scripts/rag_eval.py --min-score 80        # exit 1 below 80 or on any leak
    python3 scripts/rag_eval.py --json-out report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSWER_KEY = ROOT / "kb" / "answer_key.json"
STATE = ROOT / "deploy" / "state.json"


def ask(base_url: str, tenant: str, agent: str, question: str, retries: int = 2) -> dict:
    body = json.dumps(
        {
            "tenant_id": tenant,
            "agent_id": agent,
            "shopper_id": "rageval" + uuid.uuid4().hex[:10],
            "message": question,
        }
    ).encode()
    headers = {"Content-Type": "application/json", "x-amz-content-sha256": hashlib.sha256(body).hexdigest()}
    for attempt in range(retries + 1):
        req = urllib.request.Request(base_url + "/api/chat", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # 429 = WAF rate limit or Lambda reserved-concurrency throttle.
            if exc.code == 429 and attempt < retries:
                time.sleep(30)
                continue
            raise
    raise RuntimeError("unreachable")


def _normalize(text: str) -> str:
    # Models write ranges with en/em dashes ("1\u20132 days"); the key uses "-".
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014"):
        text = text.replace(dash, "-")
    return text.lower()


def score_question(q: dict, out: dict) -> dict:
    reply = out.get("reply", "")
    low = _normalize(reply)
    leaks = [s for s in q.get("must_not_include", []) if _normalize(s) in low]
    kb_called = any(tc.get("name") == "kb_search" for tc in out.get("tool_calls", []))
    citations = out.get("citations", [])
    expected = q.get("expected_doc")

    if expected is None:
        score = 0 if leaks else 100
        parts = {"isolation": score}
    else:
        correct = all(any(_normalize(alt) in low for alt in group) for group in q.get("must_include", []))
        retrieved = any(c.get("doc_id") == expected for c in citations)
        cited = any(c.get("doc_id") == expected and c.get("cited") for c in citations)
        parts = {
            "correctness": 50 if correct else 0,
            "retrieval": 20 if retrieved else 0,
            "citation": 20 if cited else 0,
            "grounded": 10 if kb_called else 0,
        }
        score = 0 if leaks else sum(parts.values())
    return {
        "id": q["id"],
        "tenant": q["tenant"],
        "score": score,
        "parts": parts,
        "leaks": leaks,
        "kb_called": kb_called,
        "cited": [c["cite"] for c in citations if c.get("cited")],
        "cost_usd": out.get("cost_usd", 0),
        "input_tokens": out.get("input_tokens", 0),
        "output_tokens": out.get("output_tokens", 0),
        "cache_read_tokens": out.get("cache_read_tokens", 0),
        "reply": reply,
    }


def run(base_url: str | None = None) -> dict:
    base_url = base_url or json.loads(STATE.read_text())["webui"]["cloudfront_url"]
    questions = json.loads(ANSWER_KEY.read_text())["questions"]
    results = []
    for q in questions:
        try:
            out = ask(base_url, q["tenant"], q["agent"], q["question"])
            results.append(score_question(q, out))
        except Exception as exc:  # noqa: BLE001
            results.append({"id": q["id"], "tenant": q["tenant"], "score": 0, "parts": {}, "leaks": [], "error": str(exc)})
    overall = round(sum(r["score"] for r in results) / len(results), 1)
    return {
        "score": overall,
        "questions": len(results),
        "leaks": sum(len(r["leaks"]) for r in results),
        "errors": sum(1 for r in results if r.get("error")),
        "cost_usd": round(sum(r.get("cost_usd", 0) for r in results), 6),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--base-url", default=None, help="defaults to webui.cloudfront_url in deploy/state.json")
    args = parser.parse_args()

    report = run(args.base_url)
    for r in report["results"]:
        flag = "LEAK " if r["leaks"] else ("ERROR" if r.get("error") else "     ")
        detail = r.get("error") or f"{r['parts']} cited={r.get('cited')}"
        print(f"{flag} {r['score']:>5} {r['id']:<22} {detail}")
    print(
        f"\nRAG eval score: {report['score']}/100 over {report['questions']} questions; "
        f"leaks={report['leaks']} errors={report['errors']} bedrock_cost=${report['cost_usd']}"
    )
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2))
        print(f"report written to {args.json_out}")
    if report["leaks"]:
        print("FAIL: cross-brand policy leak")
        sys.exit(1)
    if args.min_score is not None and report["score"] < args.min_score:
        print(f"FAIL: score {report['score']} < --min-score {args.min_score}")
        sys.exit(1)


if __name__ == "__main__":
    main()
