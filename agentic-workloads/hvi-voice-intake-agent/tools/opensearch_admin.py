#!/usr/bin/env python
"""
OpenSearch admin for the two indices this stack needs.

  hvi-icd-concepts     k-NN index that INTERPRETS what a patient meant.
                       One document per (condition, surface form), so E11 is
                       reachable from "Type 2 Diabetes Mellitus", "मधुमेह",
                       "sugar ki bimari" and "madhumeh" alike.

  hvi-call-transcripts Turn-level transcript and event log. Backs post-call
                       search and the IRDAI audit trail.

Embeddings use cohere.embed-multilingual-v3 in ap-south-1. An earlier design called
for dual English + Hindi indices; a single cross-lingual model is used instead
because it places translations near each other in one space, which removes the
need to reconcile two similarity scores. Same outcome, one moving part.

Why this exists alongside Neptune: the graph resolves *known* synonyms
deterministically. This handles the long tail - phrasing nobody thought to add
to the graph. Graph first, vectors as the fallback.

The index is built from three tiers, and the distinction is load-bearing:
  curated    seed/health-kg.json     - GOVERNED. Synonyms, symptom phrases and a
                                       mandatory follow-up protocol per condition.
  reference  seed/icd10-reference.json - IDENTIFIABLE only. WHO ICD-10 categories
                                       with no protocol, so a match is reported
                                       uncorroborated, read back, and flagged.
  null       seed/distractors.json   - ordinary conversation, so that noise has a
                                       nearest neighbour which is not a disease.

Usage
  python tools/opensearch_admin.py create-indices        # FIRST TIME ONLY, drops both
  python tools/opensearch_admin.py reload-concepts seed/health-kg.json
  python tools/opensearch_admin.py load-concepts seed/health-kg.json  # append only
  python tools/opensearch_admin.py delete-concept-alias E10 "शुगर"
  python tools/opensearch_admin.py classify "mujhe sugar ki bimari hai"
  python tools/opensearch_admin.py search-transcripts "hospital"
  python tools/opensearch_admin.py info
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = os.environ.get("DATA_REGION", "ap-south-1")
HOST = os.environ.get("OPENSEARCH_ENDPOINT", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "cohere.embed-multilingual-v3")
EMBED_DIM = 1024

IDX_CONCEPTS = "hvi-icd-concepts"
IDX_TRANSCRIPTS = "hvi-call-transcripts"

_session = boto3.Session()
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def os_request(method: str, path: str, body: Any = None) -> Any:
    """Signed request to OpenSearch. The domain uses an IAM access policy."""
    if not HOST:
        raise SystemExit("OPENSEARCH_ENDPOINT not set - source config/env.sh")
    url = f"https://{HOST}{path}"
    data = json.dumps(body) if body is not None else None
    creds = _session.get_credentials().get_frozen_credentials()

    req = AWSRequest(method=method, url=url, data=data,
                     headers={"Content-Type": "application/json"})
    SigV4Auth(creds, "es", REGION).add_auth(req)

    r = requests.request(method, url, data=data, headers=dict(req.headers), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"opensearch {r.status_code} {method} {path}: {r.text[:500]}")
    return r.json() if r.text else {}


def embed(texts: list[str], input_type: str = "search_document") -> list[list[float]]:
    """Batch-embed with Cohere multilingual. Cohere caps a call at 96 texts."""
    out: list[list[float]] = []
    for i in range(0, len(texts), 96):
        chunk = texts[i:i + 96]
        resp = _bedrock.invoke_model(
            modelId=EMBED_MODEL,
            body=json.dumps({"texts": chunk, "input_type": input_type}),
        )
        out.extend(json.loads(resp["body"].read())["embeddings"])
    return out


# --------------------------------------------------------------------------- #
def create_indices() -> None:
    concepts = {
        "settings": {
            "index": {"knn": True, "knn.algo_param.ef_search": 100},
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBED_DIM,
                    # nmslib was removed in OpenSearch 3.0. lucene is used here
                    # rather than faiss because the index is small (a few hundred
                    # vectors) and lucene needs no separate native library.
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "lucene",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
                "icd10": {"type": "keyword"},
                "surfaceForm": {"type": "text"},
                "surfaceFormExact": {"type": "keyword"},
                "termType": {"type": "keyword"},
                "label_en": {"type": "text"},
                "label_hi": {"type": "text"},
                "category": {"type": "keyword"},
                "severityTier": {"type": "integer"},
                "sensitivity": {"type": "keyword"},
                "formItems": {"type": "keyword"},
                "redFlag": {"type": "boolean"},
            }
        },
    }

    transcripts = {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "callId": {"type": "keyword"},
                "turnIndex": {"type": "integer"},
                "role": {"type": "keyword"},
                "text": {"type": "text"},
                "itemId": {"type": "keyword"},
                "language": {"type": "keyword"},
                "sttConfidence": {"type": "float"},
                "extractedValue": {"type": "text"},
                "classifiedIcd10": {"type": "keyword"},
                "classifierScore": {"type": "float"},
                "protocolSource": {"type": "text"},
                "customerIdHash": {"type": "keyword"},
                "policyNumber": {"type": "keyword"},
                "timestamp": {"type": "date"},
                "latencyMs": {"type": "integer"},
                "eventType": {"type": "keyword"},
            }
        },
    }

    for name, body in ((IDX_CONCEPTS, concepts), (IDX_TRANSCRIPTS, transcripts)):
        try:
            os_request("HEAD", f"/{name}")
            print(f"index {name} already exists - deleting and recreating")
            os_request("DELETE", f"/{name}")
        except RuntimeError:
            pass
        os_request("PUT", f"/{name}", body)
        print(f"created {name}")


def load_concepts(path: str) -> None:
    doc = json.loads(open(path).read())

    # Build one row per surface form so every way of saying a condition is
    # independently retrievable.
    rows: list[dict] = []
    for c in doc["conditions"]:
        base = {
            "icd10": c["icd10"],
            "label_en": c["label_en"],
            "label_hi": c.get("label_hi", ""),
            "category": c.get("category", ""),
            "severityTier": int(c.get("severityTier", 1)),
            "sensitivity": c.get("sensitivity", "normal"),
            "formItems": c.get("formItems", []),
        }
        rows.append({**base, "surfaceForm": c["label_en"], "termType": "label_en"})
        if c.get("label_hi"):
            rows.append({**base, "surfaceForm": c["label_hi"], "termType": "label_hi"})
        for term in c.get("colloquial", []):
            rows.append({**base, "surfaceForm": term, "termType": "colloquial"})

    # Symptom phrases matter as much as names. Patients describe what they feel
    # rather than naming a diagnosis, and name-only indexing sent exertional
    # chest pain to osteoarthritis and haemoptysis to hepatitis.
    by_icd = {c["icd10"]: c for c in doc["conditions"]}
    for sp in doc.get("symptomPhrases", []):
        c = by_icd.get(sp["icd10"])
        if not c:
            print(f"  warning: symptom phrase references unknown {sp['icd10']}")
            continue
        rows.append({
            "icd10": c["icd10"],
            "label_en": c["label_en"],
            "label_hi": c.get("label_hi", ""),
            "category": c.get("category", ""),
            "severityTier": int(c.get("severityTier", 1)),
            "sensitivity": c.get("sensitivity", "normal"),
            "formItems": c.get("formItems", []),
            "surfaceForm": sp["phrase"],
            "termType": "symptom",
            "redFlag": bool(sp.get("redFlag")),
        })

    # THE NULL CLASS. A k-NN index that contains only diseases can only ever
    # answer with a disease - "Here they are." returned Epilepsy at 0.79 because
    # Epilepsy was merely the LEAST DISSIMILAR disease on offer. Indexing
    # ordinary conversation (seed/distractors.json) gives garbage somewhere
    # nearer to land: an acknowledgement embeds closer to other
    # acknowledgements than to any diagnosis, so the nearest neighbour of noise
    # becomes a distractor, and retrieving a distractor IS the classifier
    # saying "this is not a condition" - deterministically, with no threshold
    # tuning. The corpus is grown from real failures; see the seed file.
    dpath = os.path.join(os.path.dirname(path), "distractors.json")
    try:
        ddoc = json.loads(open(dpath).read())
        n_distractors = 0
        for grp in ddoc.get("distractors", []):
            for phrase in grp.get("phrases", []):
                rows.append({
                    "icd10": "", "label_en": "", "label_hi": "",
                    "category": "distractor", "severityTier": 0,
                    "sensitivity": "normal", "formItems": [],
                    "surfaceForm": phrase, "termType": "distractor",
                })
                n_distractors += 1
        print(f"added {n_distractors} distractor phrases (the null class)")
    except OSError as e:
        print(f"warning: no distractor corpus ({e}); the index can only answer "
              "with diseases, so conversational noise WILL classify as one")

    # THE REFERENCE TIER. The curated conditions above are what the graph
    # GOVERNS; these are the codes it can merely IDENTIFY.
    #
    # Without them this index can only answer with one of 38 diseases, and
    # nearest neighbour always answers - so a real condition outside the graph is
    # handed the least dissimilar of the 38. Measured: bronchitis reached B18
    # Chronic Viral Hepatitis, gallstones reached N20 Kidney Stones, coeliac
    # disease reached K51 Ulcerative Colitis. That is the same defect the null
    # class fixed for conversational noise, still open for real diseases.
    #
    # A reference hit is deliberately NOT equivalent to a curated one. It carries
    # no follow-up protocol, so the graph cannot corroborate it, so
    # classify_condition marks it uncorroborated, the agent reads it back, and
    # _queue_protocol applies the default protocol and flags the record for human
    # review. The gain is a right code with a review flag instead of a wrong code
    # filed as fact.
    #
    # severityTier is left at 1 ON PURPOSE. The curated graph assigns severity
    # clinically per condition; inventing a tier for 1,412 codes would be
    # fabricating clinical data. Safety comes from the uncorroborated path, which
    # forces a read-back and a review flag regardless of tier.
    rpath = os.path.join(os.path.dirname(path), "icd10-reference.json")
    try:
        rdoc = json.loads(open(rpath).read())
        n_reference = 0
        n_alias = 0
        for entry in rdoc.get("codes", []):
            if not entry.get("icd10") or not entry.get("label_en"):
                continue
            base = {
                "icd10": entry["icd10"],
                "label_en": entry["label_en"],
                "label_hi": "",
                "category": entry.get("category") or "",
                "severityTier": 1,
                "sensitivity": "normal",
                "formItems": [],
                "termType": "reference",
                "governed": False,
            }
            rows.append({**base, "surfaceForm": entry["label_en"]})
            n_reference += 1
            # Extra retrievable names for the same code. WHO sometimes titles a
            # category generically and puts the word patients say one level down -
            # K90 is "Intestinal malabsorption" while coeliac lives at K90.0 - so
            # the title alone is unreachable from the disclosure. Multiple surface
            # forms per code cost nothing in candidate quality because
            # classify_condition collapses to the best score per code.
            for alias in entry.get("aliases") or []:
                rows.append({**base, "surfaceForm": alias})
                n_alias += 1
        print(f"added {n_reference} reference codes (identifiable, not governed)"
              + (f" plus {n_alias} curated aliases" if n_alias else ""))
    except OSError as e:
        print(f"warning: no reference vocabulary ({e}); the index can only answer "
              "with the curated conditions, so a disease outside them WILL be "
              "given the nearest curated code instead")

    print(f"embedding {len(rows)} surface forms with {EMBED_MODEL}")
    vectors = embed([r["surfaceForm"] for r in rows])

    lines = []
    for i, r in enumerate(rows):
        r["embedding"] = vectors[i]
        r["surfaceFormExact"] = r["surfaceForm"].lower()
        lines.append(json.dumps({"index": {"_index": IDX_CONCEPTS}}))
        lines.append(json.dumps(r))

    payload = "\n".join(lines) + "\n"
    creds = _session.get_credentials().get_frozen_credentials()
    url = f"https://{HOST}/_bulk"
    req = AWSRequest(method="POST", url=url, data=payload,
                     headers={"Content-Type": "application/x-ndjson"})
    SigV4Auth(creds, "es", REGION).add_auth(req)
    resp = requests.post(url, data=payload, headers=dict(req.headers), timeout=120)
    if resp.status_code >= 400:
        raise RuntimeError(f"bulk failed {resp.status_code}: {resp.text[:500]}")
    result = resp.json()
    if result.get("errors"):
        first = next(i for i in result["items"] if i["index"].get("error"))
        raise RuntimeError(f"bulk had errors, first: {first}")

    os_request("POST", f"/{IDX_CONCEPTS}/_refresh")
    count = os_request("GET", f"/{IDX_CONCEPTS}/_count")["count"]
    governed_docs = sum(1 for r in rows if r["termType"] not in
                        ("distractor", "reference"))
    print(f"indexed {count} concept documents: {governed_docs} governed surface "
          f"forms covering {len(doc['conditions'])} conditions, "
          f"{sum(1 for r in rows if r['termType'] == 'reference')} reference codes, "
          f"{sum(1 for r in rows if r['termType'] == 'distractor')} null class")


def reload_concepts(path: str) -> None:
    """Clear the concepts index and rebuild it. The ONLY safe way to reload.

    TWO HAZARDS THIS EXISTS TO AVOID, both of which have bitten.

    1. `create-indices` is not a reload. It drops BOTH indices, and
       hvi-call-transcripts holds thousands of real turn-level telemetry
       documents that are not derived from any seed file, so they cannot be
       regenerated. Reaching for it to refresh concepts destroys the audit trail.

    2. `load-concepts` on a populated index DUPLICATES it. The bulk rows carry no
       _id, so OpenSearch assigns a fresh one per document and every surface form
       is indexed again. Duplicates do not merely waste space: they crowd the
       top-k window, and the classifier's null-class test reads the SINGLE top
       hit while its confidence test counts distractors in the top eight. Both
       are decided by what fills that window.

    So: delete by query, scoped to the concepts index only, then load.
    """
    before = os_request("GET", f"/{IDX_CONCEPTS}/_count")["count"]
    print(f"clearing {IDX_CONCEPTS} ({before} documents) - "
          f"{IDX_TRANSCRIPTS} is NOT touched")
    result = os_request(
        "POST", f"/{IDX_CONCEPTS}/_delete_by_query?refresh=true&conflicts=proceed",
        {"query": {"match_all": {}}})
    print(f"  deleted {result.get('deleted', 0)}")
    load_concepts(path)


def delete_concept_alias(icd10: str, surface_form: str) -> None:
    """Delete one stale condition/alias mapping without touching either index."""
    result = os_request(
        "POST",
        f"/{IDX_CONCEPTS}/_delete_by_query?refresh=true&conflicts=proceed",
        {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"icd10": icd10}},
                        {"term": {"surfaceFormExact": surface_form.lower()}},
                    ]
                }
            }
        },
    )
    print(json.dumps({
        "icd10": icd10,
        "surfaceForm": surface_form,
        "deleted": result.get("deleted", 0),
        "versionConflicts": result.get("version_conflicts", 0),
    }, ensure_ascii=False))


# --------------------------------------------------------------------------- #
def classify(phrase: str, k: int = 5) -> dict:
    """
    Map a patient utterance to ICD-10 candidates.

    Results are collapsed to the best score per condition, because five surface
    forms of diabetes matching is one answer, not five.
    """
    vec = embed([phrase], input_type="search_query")[0]
    res = os_request("POST", f"/{IDX_CONCEPTS}/_search", {
        "size": k * 3,
        "query": {"knn": {"embedding": {"vector": vec, "k": k * 3}}},
        "_source": ["icd10", "label_en", "label_hi", "surfaceForm", "termType",
                    "category", "severityTier", "sensitivity", "formItems", "redFlag"],
    })

    best: dict[str, dict] = {}
    for hit in res["hits"]["hits"]:
        s = hit["_source"]
        icd = s["icd10"]
        if icd not in best or hit["_score"] > best[icd]["score"]:
            best[icd] = {
                "icd10": icd,
                "label_en": s["label_en"],
                "label_hi": s.get("label_hi"),
                "category": s.get("category"),
                "severityTier": s.get("severityTier"),
                "sensitivity": s.get("sensitivity"),
                "formItems": s.get("formItems", []),
                "matchedVia": s["surfaceForm"],
                "matchedType": s.get("termType"),
                "redFlag": bool(s.get("redFlag")),
                "score": hit["_score"],
            }

    ranked = sorted(best.values(), key=lambda x: -x["score"])[:k]
    top = ranked[0] if ranked else None
    confident = bool(top) and top["score"] >= 0.75

    # A red-flag presentation must never be accepted on a thin margin. If the
    # runner-up is close, the agent should ask a clarifying question or escalate
    # rather than commit to a guess - misreading exertional chest pain as joint
    # pain is the kind of error that matters clinically, not just statistically.
    ambiguous = (
        len(ranked) > 1
        and (ranked[0]["score"] - ranked[1]["score"]) < 0.03
    )
    needs_confirmation = bool(top) and (top["redFlag"] or ambiguous or not confident)

    return {
        "phrase": phrase,
        "topMatch": top,
        "candidates": ranked,
        "confident": confident,
        "ambiguous": ambiguous,
        "redFlag": bool(top and top["redFlag"]),
        # The agent reads this: True means confirm with the patient or escalate,
        # never silently record the classification.
        "needsConfirmation": needs_confirmation,
    }


def search_transcripts(q: str, size: int = 10) -> dict:
    return os_request("POST", f"/{IDX_TRANSCRIPTS}/_search", {
        "size": size,
        "query": {"multi_match": {"query": q, "fields": ["text", "extractedValue"]}},
        "sort": [{"timestamp": {"order": "desc"}}],
    })


def info() -> None:
    print(json.dumps(os_request("GET", "/_cluster/health"), indent=2))
    for idx in (IDX_CONCEPTS, IDX_TRANSCRIPTS):
        try:
            c = os_request("GET", f"/{idx}/_count")["count"]
            print(f"{idx}: {c} documents")
        except RuntimeError as e:
            print(f"{idx}: {e}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-indices")
    lc = sub.add_parser("load-concepts"); lc.add_argument("path")
    rc = sub.add_parser("reload-concepts"); rc.add_argument("path")
    da = sub.add_parser("delete-concept-alias")
    da.add_argument("icd10"); da.add_argument("surface_form")
    cl = sub.add_parser("classify"); cl.add_argument("phrase"); cl.add_argument("--k", type=int, default=5)
    st = sub.add_parser("search-transcripts"); st.add_argument("query")
    sub.add_parser("info")

    a = p.parse_args()
    if a.cmd == "create-indices":
        create_indices()
    elif a.cmd == "load-concepts":
        load_concepts(a.path)
    elif a.cmd == "reload-concepts":
        reload_concepts(a.path)
    elif a.cmd == "delete-concept-alias":
        delete_concept_alias(a.icd10, a.surface_form)
    elif a.cmd == "classify":
        print(json.dumps(classify(a.phrase, a.k), indent=2, ensure_ascii=False))
    elif a.cmd == "search-transcripts":
        print(json.dumps(search_transcripts(a.query), indent=2, ensure_ascii=False))
    elif a.cmd == "info":
        info()
    return 0


if __name__ == "__main__":
    sys.exit(main())
