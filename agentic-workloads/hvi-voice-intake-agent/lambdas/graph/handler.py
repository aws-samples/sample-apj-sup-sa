"""
Neptune knowledge-graph Lambda. Runs inside the VPC because Neptune has no
public endpoint.

Two jobs:

  1. Seeding. Loads seed/health-kg.json into the graph. Idempotent - it drops
     and rebuilds the hvi-managed vertices so re-running is safe.

  2. Serving the AgentCore tools. The important one is get_followups, which is
     the ENFORCES mechanism: given a confirmed condition it returns the exact
     follow-up questions the medical protocol requires, plus the traversal path
     that produced them. That path is what answers "why was this question
     asked?" for an IRDAI auditor - the agent did not decide, the graph did.

Neptune has IAM database authentication enabled, so every request is SigV4
signed. We use the HTTP /gremlin endpoint rather than the WebSocket driver to
keep the dependency footprint small.

Actions
  health             -> connectivity + vertex counts
  seed               -> (re)build the graph from the bundled JSON
  get_followups      -> mandatory follow-ups for a condition, with audit path
  resolve_condition  -> map a colloquial phrase to an ICD-10 node
  check_interactions -> drug-interaction warnings for a medication list
  stats              -> label counts, for the demo
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

NEPTUNE_ENDPOINT = os.environ["NEPTUNE_ENDPOINT"]
NEPTUNE_PORT = os.environ.get("NEPTUNE_PORT", "8182")
REGION = os.environ.get("NEPTUNE_REGION", os.environ.get("AWS_REGION", "ap-south-1"))

GREMLIN_URL = f"https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin"
SEED_FILE = pathlib.Path(__file__).parent / "health-kg.json"

_session = boto3.Session()


def gremlin(query: str) -> Any:
    """
    Run one Gremlin query over the signed HTTP endpoint.

    Signing uses botocore's SigV4Auth rather than requests-aws4auth. Neptune is
    strict about the canonical request, in particular the host header carrying
    the :8182 port and the payload hash matching the exact bytes sent, and
    botocore gets both right. It is also already present in the Lambda runtime,
    so this removes a dependency.
    """
    body = json.dumps({"gremlin": query})
    creds = _session.get_credentials().get_frozen_credentials()

    # types=false asks Neptune for untyped GraphSON, so results come back as
    # plain JSON instead of {"@type":"g:Int64","@value":3} wrappers. Saves
    # writing a GraphSON deserialiser.
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/vnd.gremlin-v3.0+json;types=false",
    }

    aws_req = AWSRequest(method="POST", url=GREMLIN_URL, data=body, headers=headers)
    SigV4Auth(creds, "neptune-db", REGION).add_auth(aws_req)

    r = requests.post(
        GREMLIN_URL,
        data=body,
        headers=dict(aws_req.headers),
        timeout=55,
    )
    if r.status_code != 200:
        raise RuntimeError(f"neptune {r.status_code}: {r.text[:600]}")
    return r.json().get("result", {}).get("data")


def _esc(v: Any) -> str:
    """Escape a value for inline Gremlin. Neptune's HTTP endpoint has no bindings."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
    return f"'{s}'"


# --------------------------------------------------------------------------- #
# seeding
# --------------------------------------------------------------------------- #
def seed(_: dict) -> dict:
    doc = json.loads(SEED_FILE.read_text())

    # Remove only what we manage, so a shared cluster stays intact.
    gremlin("g.V().has('managedBy','hvi').drop()")

    counts = {"conditions": 0, "followUps": 0, "medications": 0,
              "colloquial": 0, "edges": 0}

    # ---- follow-up question vertices ----
    for fu in doc["followUpQuestions"]:
        props = [
            f".property('questionId',{_esc(fu['id'])})",
            f".property('mandatory',{_esc(bool(fu.get('mandatory')))})",
            f".property('answerType',{_esc(fu.get('answerType','text'))})",
            f".property('prompt_en',{_esc(fu.get('prompt_en',''))})",
            f".property('prompt_hi',{_esc(fu.get('prompt_hi',''))})",
            f".property('prompt_hinglish',{_esc(fu.get('prompt_hinglish',''))})",
            ".property('managedBy','hvi')",
        ]
        if fu.get("sensitivity"):
            props.append(f".property('sensitivity',{_esc(fu['sensitivity'])})")
        if fu.get("clinicalNote"):
            props.append(f".property('clinicalNote',{_esc(fu['clinicalNote'])})")
        if fu.get("formField"):
            props.append(f".property('formField',{_esc(fu['formField'])})")
        gremlin(f"g.addV('FollowUpQuestion').property(id,{_esc(fu['id'])})" + "".join(props))
        counts["followUps"] += 1

    # ---- medication vertices ----
    for m in doc["medications"]:
        gremlin(
            f"g.addV('Medication').property(id,{_esc('MED_' + m['name'])})"
            f".property('name',{_esc(m['name'])})"
            f".property('generic',{_esc(m.get('generic',''))})"
            f".property('label_hi',{_esc(m.get('label_hi',''))})"
            f".property('drugClass',{_esc(m.get('drugClass',''))})"
            ".property('managedBy','hvi')"
        )
        counts["medications"] += 1

    # ---- condition vertices, then their edges ----
    explanations = doc.get("conditionExplanations") or {}
    for c in doc["conditions"]:
        cid = f"COND_{c['icd10']}"
        explanation = explanations.get(c["icd10"]) or {}
        props = [
            f".property('icd10',{_esc(c['icd10'])})",
            f".property('label_en',{_esc(c['label_en'])})",
            f".property('label_hi',{_esc(c.get('label_hi',''))})",
            f".property('category',{_esc(c.get('category',''))})",
            f".property('severityTier',{_esc(int(c.get('severityTier',1)))})",
            f".property('formItems',{_esc(','.join(c.get('formItems',[])))})",
            f".property('explain_en',{_esc(explanation.get('explain_en',''))})",
            f".property('explain_hi',{_esc(explanation.get('explain_hi',''))})",
            ".property('managedBy','hvi')",
        ]
        if c.get("sensitivity"):
            props.append(f".property('sensitivity',{_esc(c['sensitivity'])})")
        gremlin(f"g.addV('Condition').property(id,{_esc(cid)})" + "".join(props))
        counts["conditions"] += 1

        # Colloquial terms as their own vertices. Keeping them in the graph means
        # the medical team can add a regional synonym without touching the
        # embedding pipeline, and the graph lookup stays deterministic.
        #
        # Deliberately colloquial[] only, NOT the labels. Indexing label_en and
        # label_hi as well was tried on 2026-08-23 to make a condition findable
        # by its own name, and it broke two safety properties: "मुझे सोरायसिस
        # नहीं, सारकॉइडोसिस है" - denying psoriasis while asserting an ungoverned
        # condition - started resolving to L40 with corroborated=True, which
        # cleared the veto that made the classifier decline, and one label
        # displaced its own condition in the surface-form sweep. Graph presence
        # is what corroboration means here, so this list has to stay curated:
        # a name we want resolvable goes in colloquial[] on purpose, one entry at
        # a time.
        for term in c.get("colloquial", []):
            tid = f"TERM_{c['icd10']}_{abs(hash(term)) % 10**9}"
            gremlin(
                f"g.addV('ColloquialTerm').property(id,{_esc(tid)})"
                f".property('term',{_esc(term)})"
                f".property('termLower',{_esc(term.lower())})"
                f".property('icd10',{_esc(c['icd10'])})"
                ".property('managedBy','hvi')"
            )
            gremlin(
                f"g.V({_esc(cid)}).addE('KNOWN_AS').to(__.V({_esc(tid)}))"
                ".property('managedBy','hvi')"
            )
            counts["colloquial"] += 1
            counts["edges"] += 1

    # Edges are added after all vertices exist, so ordering never breaks a link.
    for c in doc["conditions"]:
        cid = f"COND_{c['icd10']}"

        for order, fu_id in enumerate(c.get("followUps", []), start=1):
            gremlin(
                f"g.V({_esc(cid)}).addE('REQUIRES').to(__.V({_esc(fu_id)}))"
                f".property('order',{order}).property('managedBy','hvi')"
            )
            counts["edges"] += 1

        if c.get("subtypeOf"):
            gremlin(
                f"g.V({_esc(cid)}).addE('SUBTYPE_OF')"
                f".to(__.V({_esc('COND_' + c['subtypeOf'])}))"
                ".property('managedBy','hvi')"
            )
            counts["edges"] += 1

    for m in doc["medications"]:
        for icd in m.get("treats", []):
            gremlin(
                f"g.V({_esc('MED_' + m['name'])}).addE('TREATS')"
                f".to(__.V({_esc('COND_' + icd)})).property('managedBy','hvi')"
            )
            counts["edges"] += 1

    for ix in doc["interactions"]:
        gremlin(
            f"g.V({_esc('MED_' + ix['a'])}).addE('INTERACTS_WITH')"
            f".to(__.V({_esc('MED_' + ix['b'])}))"
            f".property('severity',{_esc(ix['severity'])})"
            f".property('effect',{_esc(ix['effect'])})"
            ".property('managedBy','hvi')"
        )
        counts["edges"] += 1

    return {"seeded": True, "counts": counts}


# --------------------------------------------------------------------------- #
# tool: the ENFORCES path
# --------------------------------------------------------------------------- #
def get_followups(event: dict) -> dict:
    """
    Mandatory follow-up questions for a condition.

    Returns the questions AND the traversal path. The path is the audit
    evidence: it shows the questions came from the protocol graph rather than
    from model improvisation.
    """
    icd10 = event.get("icd10")
    if not icd10:
        return {"error": "icd10 required"}
    lang = event.get("language", "en")
    cid = f"COND_{icd10}"

    rows = gremlin(
        f"g.V({_esc(cid)}).outE('REQUIRES').order().by('order')"
        ".project('order','q')"
        ".by(values('order'))"
        ".by(__.inV().valueMap(true))"
    )
    if not rows:
        return {"icd10": icd10, "found": False, "followUps": []}

    prompt_key = {"hi": "prompt_hi", "hinglish": "prompt_hinglish"}.get(lang, "prompt_en")

    def first(v):
        return v[0] if isinstance(v, list) and v else v

    followups = []
    for row in rows:
        q = row["q"]
        followups.append({
            "questionId": first(q.get("questionId")),
            "order": row["order"],
            "mandatory": first(q.get("mandatory")),
            "answerType": first(q.get("answerType")),
            "prompt": first(q.get(prompt_key)) or first(q.get("prompt_en")),
            "sensitivity": first(q.get("sensitivity")),
            "clinicalNote": first(q.get("clinicalNote")),
            "formField": first(q.get("formField")),
        })

    cond = gremlin(f"g.V({_esc(cid)}).valueMap(true)")
    label = first(cond[0].get("label_en")) if cond else icd10

    return {
        "icd10": icd10,
        "conditionLabel": label,
        "found": True,
        "language": lang,
        "mandatoryCount": sum(1 for f in followups if f["mandatory"]),
        "followUps": followups,
        # Carried into the Salesforce record so the reason is auditable.
        "protocolSource": (
            f"neptune:Condition/{icd10} -REQUIRES-> "
            f"[{', '.join(f['questionId'] for f in followups)}]"
        ),
    }


def get_condition(event: dict) -> dict:
    """Return authored labels and lay explanation for one governed condition."""
    icd10 = event.get("icd10")
    if not icd10:
        return {"error": "icd10 required"}
    rows = gremlin(
        f"g.V({_esc('COND_' + icd10)}).valueMap(true)"
    )
    if not rows:
        return {"icd10": icd10, "found": False}

    def first(v):
        return v[0] if isinstance(v, list) and v else v

    row = rows[0]
    return {
        "icd10": icd10,
        "found": True,
        "label_en": first(row.get("label_en")),
        "label_hi": first(row.get("label_hi")),
        "explain_en": first(row.get("explain_en")),
        "explain_hi": first(row.get("explain_hi")),
        "category": first(row.get("category")),
    }


# Punctuation a caller never means as part of a condition name. STT puts a full
# stop on a one-word reply ("Alzheimer's.") and Hindi output ends on a danda.
# Trimmed at the edges only: apostrophes and hyphens inside a word are part of
# real names, so "alzheimer's" and "non-hodgkin" survive intact.
_EDGE_PUNCT = " \t\r\n.,;:!?\"'`()[]{}\u2018\u2019\u201c\u201d\u0964\u0965\u2013\u2014-"


def _normalise_phrase(raw) -> str:
    """Lowercase a caller phrase and drop punctuation they did not intend.

    This used to be .strip().lower(), which left the full stop attached. On
    call-4475670b the caller answered the "which condition?" prompt with
    "Alzheimer's." and matched nothing: not the exact termLower index, not
    TextP.containing (the stored term carries no full stop), and not the token
    path (one token, still carrying the stop). Every clarify attempt was spent
    on that and the caller hung up.
    """
    return (raw or "").strip(_EDGE_PUNCT).lower()


def resolve_condition(event: dict) -> dict:
    """
    Map a patient phrase to an ICD-10 node by exact colloquial match.

    This is the deterministic half of classification. OpenSearch k-NN handles
    the fuzzy half. Graph first, because an exact known-synonym hit is more
    trustworthy than a similarity score.
    """
    phrase = _normalise_phrase(event.get("phrase"))
    if not phrase:
        return {"error": "phrase required"}

    rows = gremlin(
        f"g.V().hasLabel('ColloquialTerm').has('termLower',{_esc(phrase)})"
        ".in('KNOWN_AS').valueMap(true)"
    )
    match_type = "exact_synonym"

    if not rows:
        # TextP.containing matches a stored term that contains the phrase, which
        # catches "sugar" inside "sugar ki bimari".
        rows = gremlin(
            f"g.V().hasLabel('ColloquialTerm')"
            f".has('termLower',TextP.containing({_esc(phrase)}))"
            ".in('KNOWN_AS').dedup().valueMap(true).limit(5)"
        )
        match_type = "phrase_within_synonym"

    if not rows:
        # Reverse direction: the phrase contains a known synonym, e.g. the
        # patient said "mujhe sugar hai" and "sugar" is the stored term.
        tokens = [t for t in phrase.split() if len(t) > 2]
        for tok in tokens:
            rows = gremlin(
                f"g.V().hasLabel('ColloquialTerm').has('termLower',{_esc(tok)})"
                ".in('KNOWN_AS').dedup().valueMap(true).limit(5)"
            )
            if rows:
                match_type = f"token_match:{tok}"
                break

    if not rows:
        return {"phrase": phrase, "found": False,
                "hint": "fall back to the OpenSearch k-NN classifier"}

    def first(v):
        return v[0] if isinstance(v, list) and v else v

    matches = [{
        "icd10": first(r.get("icd10")),
        "label_en": first(r.get("label_en")),
        "label_hi": first(r.get("label_hi")),
        "category": first(r.get("category")),
        "severityTier": first(r.get("severityTier")),
    } for r in rows]

    return {"phrase": phrase, "found": True, "matchType": match_type,
            "matches": matches}


def check_interactions(event: dict) -> dict:
    """Flag clinically significant interactions across the declared medications."""
    meds = event.get("medications") or []
    if len(meds) < 2:
        return {"medications": meds, "interactions": [],
                "note": "fewer than two medications, nothing to compare"}

    known = {m.lower(): m for m in
             [r for r in gremlin("g.V().hasLabel('Medication').values('name')")]}

    resolved, unknown = [], []
    for m in meds:
        base = m.split()[0].lower() if m else ""
        if base in known:
            resolved.append(known[base])
        else:
            unknown.append(m)

    found = []
    for i, a in enumerate(resolved):
        for b in resolved[i + 1:]:
            for x, y in ((a, b), (b, a)):
                rows = gremlin(
                    f"g.V({_esc('MED_' + x)}).outE('INTERACTS_WITH')"
                    f".where(__.inV().hasId({_esc('MED_' + y)}))"
                    ".project('severity','effect')"
                    ".by(values('severity')).by(values('effect'))"
                )
                for r in rows or []:
                    found.append({"drugA": x, "drugB": y,
                                  "severity": r["severity"], "effect": r["effect"]})

    return {"medications": meds, "resolved": resolved, "unrecognised": unknown,
            "interactions": found, "interactionCount": len(found)}


def stats(_: dict) -> dict:
    rows = gremlin("g.V().has('managedBy','hvi').groupCount().by(label)")
    edges = gremlin("g.E().has('managedBy','hvi').groupCount().by(label)")
    return {"vertices": rows[0] if rows else {}, "edges": edges[0] if edges else {}}


def health(_: dict) -> dict:
    v = gremlin("g.V().limit(1).count()")
    return {"connected": True, "endpoint": NEPTUNE_ENDPOINT, "probe": v}


ACTIONS = {
    "health": health,
    "seed": seed,
    "get_followups": get_followups,
    "get_condition": get_condition,
    "resolve_condition": resolve_condition,
    "check_interactions": check_interactions,
    "stats": stats,
}


def lambda_handler(event, _context):
    action = event.get("action", "health")
    fn = ACTIONS.get(action)
    if not fn:
        return {"error": f"unknown action {action}",
                "available": sorted(ACTIONS)}
    try:
        return {"ok": True, "action": action, "result": fn(event)}
    except Exception as exc:  # surfaced to the caller rather than swallowed
        return {"ok": False, "action": action, "error": str(exc)}
