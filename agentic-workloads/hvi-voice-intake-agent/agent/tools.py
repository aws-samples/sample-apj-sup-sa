"""
The tools the agent may call. Nothing else is reachable.

A closed tool registry is the point. The agent cannot invent a data source, and
every call is recorded, so "why did the agent ask that?" and "where did that
value come from?" both have concrete answers. That is the auditability the
customer asked about.

Division of labour worth keeping straight:

  Neptune    ENFORCES   deterministic protocol. Given a condition, the graph
                        dictates the follow-ups. The agent does not choose them.
  OpenSearch INTERPRETS probabilistic. Maps what the patient actually said onto
                        an ICD-10 code, including Hindi and Hinglish.
  DynamoDB   REMEMBERS  per-turn checkpoint, so a dropped call resumes.

Tool results carry provenance fields (protocolSource, classifierScore,
needsConfirmation) which are persisted rather than discarded once the agent has
read them.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import sys
import time
from typing import Any

import boto3

# There was no logger in this module and one line already used it: the
# adjudication failure path called log.warning, which raised NameError from
# inside an except block and replaced the real error with a misleading one.
log = logging.getLogger("hvi-agent.tools")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
import hvi_state as st  # noqa: E402

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
GRAPH_FN = os.environ.get("GRAPH_FN", "hvi-graph")
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "cohere.embed-multilingual-v3")
IDX_CONCEPTS = "hvi-icd-concepts"
IDX_TRANSCRIPTS = "hvi-call-transcripts"

# There is deliberately no SES client or sender here: confirmation email is sent
# by the send-confirmation Lambda (EMAIL_FN), which owns SES_FROM_EMAIL. The
# agent only passes a callId.
_lambda = boto3.client("lambda", region_name=DATA_REGION)
_bedrock = boto3.client("bedrock-runtime", region_name=DATA_REGION)
_session = boto3.Session()

# Records every tool call for the current invocation. The runtime attaches this
# to its response so the orchestrator can persist it.
TRACE: list[dict] = []

# ---- per-stage timing ---------------------------------------------------- #
# One timer around the whole turn tells you a turn was slow and nothing about
# which backend made it slow, so a regression in the graph Lambda and a
# regression in the embedding call look identical. These accumulate wall-clock
# per backend for the turn being handled: app.py resets at the start and reads
# the summary into the response and the turn record, so percentiles can be
# computed per stage afterwards from stored turns rather than from a live probe.
STAGE_MS: dict[str, list[float]] = {}


def reset_stage_timing() -> None:
    """Call once per turn, before any tool runs."""
    STAGE_MS.clear()


@contextlib.contextmanager
def _timed(stage: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        STAGE_MS.setdefault(stage, []).append((time.perf_counter() - started) * 1000)


def stage_summary() -> dict:
    """Total milliseconds and call count per backend for the current turn."""
    return {stage: {"ms": round(sum(samples), 1), "calls": len(samples)}
            for stage, samples in sorted(STAGE_MS.items())}


def _timed_lambda_invoke(stage: str, **kwargs):
    """_lambda.invoke with its wall-clock recorded against a named stage."""
    with _timed(stage):
        return _lambda.invoke(**kwargs)


def _trace(tool: str, args: dict, result: Any, error: str | None = None) -> None:
    TRACE.append({
        "tool": tool,
        "args": {k: v for k, v in args.items() if k != "self"},
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "ok": error is None,
        "error": error,
        "resultSummary": _summarise(result),
    })


def _summarise(r: Any) -> Any:
    if isinstance(r, dict):
        keep = ("icd10", "found", "score", "confident", "needsConfirmation",
                "redFlag", "protocolSource", "mandatoryCount", "interactionCount",
                "turnIndex", "status", "messageId", "itemId")
        return {k: r[k] for k in keep if k in r}
    if isinstance(r, list):
        return {"count": len(r)}
    return r


def _hash_id(v: str) -> str:
    """One-way hash so PII stays out of the search index (DPDP erasure)."""
    return hashlib.sha256(v.encode()).hexdigest()[:32]


def _os_request(method: str, path: str, body: Any = None) -> Any:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    import requests

    url = f"https://{OPENSEARCH_ENDPOINT}{path}"
    data = json.dumps(body) if body is not None else None
    creds = _session.get_credentials().get_frozen_credentials()
    req = AWSRequest(method=method, url=url, data=data,
                     headers={"Content-Type": "application/json"})
    SigV4Auth(creds, "es", DATA_REGION).add_auth(req)
    with _timed("opensearch"):
        r = requests.request(method, url, data=data, headers=dict(req.headers),
                             timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"opensearch {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def _graph(action: str, **kw) -> dict:
    payload = {"action": action, **kw}
    with _timed("graph"):
        r = _lambda.invoke(FunctionName=GRAPH_FN,
                           Payload=json.dumps(payload).encode())
    body = json.loads(r["Payload"].read())
    if not body.get("ok"):
        raise RuntimeError(f"graph {action}: {body.get('error')}")
    return body["result"]


# ═══════════════════════════════════════════════════════════════════════════ #
# 1. session and form state
# ═══════════════════════════════════════════════════════════════════════════ #
def get_session_state(call_id: str) -> dict:
    """
    Everything needed to start or resume a call: the live form schema, the
    answers already collected, and the next question to ask.

    Called once at the top of every call. On a resume the answers carried over
    from the dropped leg are already in here, so the agent continues rather than
    starting again.
    """
    try:
        session = st.get_session(call_id)
        if not session:
            raise ValueError(f"unknown callId {call_id}")

        items = st.get_form_items(session["formId"])
        answers = session.get("answers") or {}
        nxt = st.next_item(items, answers)

        result = {
            "callId": call_id,
            "customerName": session.get("customerName"),
            "customerEmail": session.get("customerEmail"),
            "policyNumber": session.get("policyNumber"),
            "language": session.get("language", "hinglish"),
            "formId": session["formId"],
            "status": session["status"],
            "isResume": int(session.get("resumeCount") or 0) > 0,
            "resumeCount": session.get("resumeCount", 0),
            "answeredCount": len(answers),
            "remainingCount": st.remaining_count(items, answers),
            "answers": answers,
            "nextItem": nxt,
            "lastCompletedTurn": session.get("lastCompletedTurn", -1),
        }
        _trace("get_session_state", {"call_id": call_id}, result)
        return result
    except Exception as e:
        _trace("get_session_state", {"call_id": call_id}, None, str(e))
        raise


def save_answer(call_id: str, item_id: str, value: Any, turn_index: int,
                patient_phrase: str | None = None,
                stt_confidence: float | None = None) -> dict:
    """
    Commit one answer and advance the checkpoint.

    Called after EVERY patient response, before the next question is asked. That
    ordering is what bounds data loss on a dropped call to the turn in flight.
    The raw phrase is stored next to the parsed value so an underwriter can see
    what was actually said.
    """
    try:
        # The customer's turn row was already written when the utterance arrived,
        # so annotate it with the interpretation rather than writing a second row.
        # If the row is missing (a direct tool call outside a live turn), create it.
        st.annotate_turn(call_id, turn_index, item_id=item_id,
                         extracted=value, valid=True, text=patient_phrase)
        session = st.save_answer(call_id, item_id, value, turn_index)
        items = st.get_form_items(session["formId"])
        nxt = st.next_item(items, session.get("answers") or {})

        result = {
            "saved": True,
            "itemId": item_id,
            "turnIndex": turn_index,
            "answeredCount": len(session.get("answers") or {}),
            "remainingCount": st.remaining_count(items, session.get("answers") or {}),
            "nextItem": nxt,
            "isComplete": nxt is None,
        }
        _trace("save_answer", {"call_id": call_id, "item_id": item_id,
                               "value": value, "turn_index": turn_index}, result)
        return result
    except Exception as e:
        _trace("save_answer", {"call_id": call_id, "item_id": item_id}, None, str(e))
        raise


def record_agent_turn(call_id: str, turn_index: int, text: str,
                      item_id: str | None = None) -> dict:
    """Log what the agent said, so the transcript reconstructs both sides."""
    st.record_turn(call_id, turn_index, "agent", item_id=item_id, text=text)
    return {"recorded": True, "turnIndex": turn_index}


# ═══════════════════════════════════════════════════════════════════════════ #
# 2. classification - OpenSearch INTERPRETS
# ═══════════════════════════════════════════════════════════════════════════ #
_NEGATION = ("nahi", "nahin", "नहीं", "not", "don't", "dont", "doesn't",
             "no ", "never", "kabhi nahi", "कभी नहीं")
_QUANTIFIER = ("koi", "koii", "कोई", "any", "none", "nothing", "kuch", "कुछ")
# Deliberately GENERIC illness words only. "तकलीफ" is excluded because
# "साँस की तकलीफ" is asthma, and "dawa"/"दवा" is excluded because a denial of
# medication is not a denial of the condition.
_GENERIC_ILLNESS = ("bimari", "bimaari", "बीमारी", "beemari",
                    "problem", "प्रॉब्लम", "प्रोब्लम",
                    "disease", "illness", "condition", "medical",
                    "मेडिकल", "रोग", "health issue")

# Curated aliases that resolve to ONE code but are generic PARENT terms spanning
# clinical siblings, so a match on them must be read back rather than recorded.
# They stay single-coded in the KG (keeping the curated one-alias-one-code sweeps
# and the M15 protocol path intact); this list only stops their graph hit from
# COUNTING AS CORROBORATION, so the match flows as uncorroborated ->
# needsConfirmation -> read-back. "arthritis" is M15 (osteoarthritis) but the word
# alone cannot tell it from M06 (rheumatoid); confirmed, it still records M15.
# casefolded for comparison.
_GENERIC_PARENT_ALIASES = frozenset({"arthritis"})

# Sentence-final punctuation, including the Devanagari danda and double danda.
# Trailing only, with any whitespace around it.
_TRAILING_PUNCT = re.compile(r"[\s।॥.,;:!?\u0964\u0965]+$")


def _for_matching(phrase: str) -> str:
    """The phrase with sentence-final punctuation removed.

    WHY THIS IS NOT COSMETIC. Measured on call-47ce35b0: the caller answered the lung
    screen "हाँ, अस्थमा।" and it resolved to J46 Status asthmaticus - a
    life-threatening asthma attack - at 0.8707 by embedding alone. The SAME words
    without the trailing danda resolve to J45 Asthma at 0.8798 WITH graph
    corroboration. One punctuation mark moved the nearest neighbour across a
    clinically serious boundary and, because the graph does not corroborate J46, it
    also turned a silent correct recording into an uncorroborated read-back.

    The damage did not stop at the wrong label. J46 has no node in the graph, so no
    follow-up protocol exists for it and the DEFAULT protocol ran; the code was later
    corrected to J45, which queued J45's real protocol on top. The caller was asked
    when she was diagnosed twice, answered "हाँ" to the repeat out of confusion, and
    each "हाँ" to a date question tripped explain-then-reask - which is how one
    stray danda produced both "it would not take 2015" and "it explains diseases I
    did not ask about".

    Normalised here rather than at the call sites because every caller of the
    classifier deserves it, and because STT punctuation is not a property of what the
    caller said. Deliberately TRAILING ONLY: internal punctuation carries meaning that
    the embedding should see, and "नहीं, मुझे अस्थमा है" must keep its comma.
    """
    stripped = _TRAILING_PUNCT.sub("", str(phrase or ""))
    # Never return empty from a non-empty input: a phrase that is nothing but
    # punctuation is better handled downstream as noise than as a blank query.
    return stripped or str(phrase or "")


def is_blanket_denial(phrase: str) -> bool:
    """
    True when the phrase denies having ANY condition, rather than describing one.

    Why this is narrow on purpose: negation appears inside plenty of genuine
    positive declarations, and over-triggering here would suppress red flags.
    "ek taraf ka haath pair kaam nahi kar raha" (one side not working) is a
    stroke, "sugar control nahi hota" asserts diabetes, and "saans nahi aati"
    is a respiratory symptom. All contain a negation.

    What separates a blanket denial is its structure: a universal quantifier
    plus a GENERIC illness word plus a negation, with no specific condition
    named - "mujhe koi bimari nahi hai", "I don't have any medical conditions".
    Requiring all three keeps the specific declarations above out of scope.
    """
    p = f" {(phrase or '').lower().strip()} "
    if not any(n in p for n in _NEGATION):
        return False
    if not any(q in p for q in _QUANTIFIER):
        return False
    return any(g in p for g in _GENERIC_ILLNESS)


_ADJUDICATE_TOOL = [{"toolSpec": {
    "name": "judge_candidate",
    "description": ("Decide whether a patient's phrase names the ONE proposed "
                    "ICD-10 condition."),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "namesThisCondition": {
                "type": "boolean",
                "description": ("true if the phrase could reasonably belong to THIS "
                                "condition - naming it, using a lay term for it, or "
                                "describing one of its symptoms. false ONLY if the "
                                "phrase names a DIFFERENT condition, denies this "
                                "one, or is not about the patient's health."),
            },
            "reasoning": {
                "type": "string",
                "description": ("One short sentence naming the condition the phrase "
                                "actually asserts."),
            },
        },
        "required": ["namesThisCondition", "reasoning"],
    }},
}}]

# On by default; a kill switch because this sits on the live call path.
ADJUDICATE = os.environ.get("ADJUDICATE_UNCORROBORATED", "1") not in ("0", "false", "")
REASONING_MODEL = os.environ.get("BEDROCK_MODEL_ID",
                                 "mistral.mistral-large-3-675b-instruct")


def _adjudicate_uncorroborated(phrase: str, ranked: list[dict]) -> dict | None:
    """Ask the reasoning model whether the phrase names ANY candidate at all.

    THE ONE QUESTION NEAREST NEIGHBOUR CANNOT ANSWER.
    Vector search reports what is closest, never whether anything is right. It has
    no way to return "not in my vocabulary", so a condition outside the index is
    handed the least dissimilar code that is. Corroboration by the curated graph is
    the existing defence, and it works - but only for the 38 conditions the graph
    covers. Beyond those, the read-back is the only guard, and a caller who is
    tiring or agreeable defeats it.

    Measured, and still failing after the reference vocabulary was added:

        "I have coeliac disease"  -> E11 Type 2 Diabetes at 0.786

    K90 is in the index, but WHO titles it "Intestinal malabsorption" and files the
    word coeliac at K90.0, so no amount of vocabulary at this granularity retrieves
    it. No threshold helps either: 0.786 sits inside the range real disclosures
    occupy. What is needed is a judgement about MEANING, which is what the model
    can supply and the index cannot.

    DELIBERATELY NARROW. It may only veto, never choose. Allowing it to swap in a
    different candidate would mean recomputing severity, category, corroboration
    and red-flag state around a code the retrieval layer did not rank first, and
    the alternatives walk already exists to offer other candidates after a rejected
    read-back. A veto needs none of that and delivers the case nothing else can
    reach: "none of these".

    ASKED ABOUT THE TOP CANDIDATE ONLY, and that precision is load-bearing. An
    earlier version showed the whole candidate list and asked whether the phrase
    named ANY of them, which is unanswerable in a way that matters: for "मुझे
    सोरायसिस नहीं, सारकॉइडोसिस है" - I do NOT have psoriasis, I have sarcoidosis -
    the honest answer to "any of these?" is yes, because sarcoidosis was in the
    list. The reply came back true and the code recorded was still L40 Psoriasis,
    the very condition the sentence denies. One code, one question, no ambiguity
    about which candidate a yes refers to.

    It also NEVER promotes. A yes leaves the match exactly as uncorroborated as it
    was, so the read-back and the review flag both still happen. The only outcome
    it can produce is more caution, never less.

    Returns None to mean "leave the classification alone" - including on any error,
    because a live call must not fail over a second opinion.
    """
    if not ADJUDICATE or not ranked:
        return None
    top = ranked[0]
    prompt = (
        "A patient disclosed a medical condition during an insurance intake call.\n"
        "A vector search proposed this ICD-10 code as the closest match:\n\n"
        f"  {top['icd10']}  {top.get('label_en') or ''}\n\n"
        f"The patient said, verbatim: {phrase!r}\n\n"
        "Could that phrase reasonably belong to THAT condition?\n\n"
        "Similarity ranking is not correctness. The search returns its closest "
        "entry even when the real condition is absent from its vocabulary, so a "
        "high rank is no evidence on its own. Judge on meaning, not shared words.\n\n"
        "This is an intake screen, not a diagnosis. Its job is to ESCALATE anything "
        "that might belong to the condition, so answer TRUE generously:\n"
        "  - the phrase names the condition, or a lay or regional term for it\n"
        "  - the phrase describes a SYMPTOM or presentation of it, even without "
        "naming it. 'saans nahi aati' for asthma is a yes: breathlessness is how a "
        "patient reports it, and a screen that discarded symptoms would be useless\n"
        "  - you are unsure. Uncertainty is a yes, because a yes only causes the "
        "condition to be read back to the patient for confirmation\n\n"
        "Answer FALSE only when one of these is clearly true:\n"
        "  - the phrase names a DIFFERENT condition. 'coeliac disease' offered "
        "against Type 2 Diabetes is a no - they are separate diseases, however "
        "close the similarity score\n"
        "  - the phrase DENIES this condition. 'X नहीं है', 'not X but Y', 'I don't "
        "have X'. A denial is never a disclosure of X, even when X is named in it\n"
        "  - the phrase is not about the patient's health at all\n\n"
        "The phrase may be in Hindi, Hinglish or English, and may mix scripts."
    )
    try:
        resp = _bedrock.converse(
            modelId=REASONING_MODEL,
            system=[{"text": "You are a careful medical coder. You are terse and "
                             "you never guess."}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={"tools": _ADJUDICATE_TOOL,
                        "toolChoice": {"tool": {"name": "judge_candidate"}}},
            inferenceConfig={"maxTokens": 300, "temperature": 0.0},
        )
    except Exception as e:  # noqa: BLE001
        log.warning("adjudication call failed for %r: %s", phrase[:40], e)
        return None

    for c in resp["output"]["message"].get("content", []):
        if "toolUse" in c and c["toolUse"]["name"] == "judge_candidate":
            out = dict(c["toolUse"].get("input") or {})
            if "namesThisCondition" not in out:
                return None
            return {"namesACandidate": bool(out.get("namesThisCondition")),
                    "reasoning": str(out.get("reasoning") or "")[:200]}
    return None


def classify_condition(phrase: str) -> dict:
    """
    Map what the patient said to an ICD-10 code.

    Neptune is tried first for an exact known synonym, because a curated match
    beats a similarity score. Vector search handles the long tail. The reply
    carries needsConfirmation: when true the agent must read the interpretation
    back to the patient instead of silently recording it. Red-flag presentations
    always set it - misreading exertional chest pain as joint pain is a clinical
    error, not a rounding error.

    A blanket denial short-circuits before either search runs. Cross-lingual
    embeddings place "mujhe koi bimari nahi hai" close to the conditions
    themselves, because the vocabulary is identical and only the polarity
    differs: measured directly, that phrase returned J45 asthma at 0.810 and
    "koi medical problem nahi" returned I10 hypertension at 0.800, both above
    the 0.75 confidence threshold. Upstream, extract.py is meant to set
    mentionsCondition=False and never call this, but leaving a denial one model
    slip away from writing a disease into a medical record contradicts the
    orchestrator's whole premise that the record is true by construction rather
    than by a model remembering. So the guard lives in code as well.
    """
    try:
        # What the GRAPH and the EMBEDDING see. `phrase` itself is left untouched
        # throughout and is what gets returned, stored and read back: the record must
        # carry the caller's own words, punctuation and all. See _for_matching.
        matchable = _for_matching(phrase)
        if is_blanket_denial(phrase):
            result = {
                "phrase": phrase,
                "icd10": None,
                "label_en": None,
                "source": "negation_guard",
                "score": 0.0,
                "confident": False,
                "ambiguous": False,
                "corroborated": False,
                "redFlag": False,
                "needsConfirmation": False,
                "confirmReason": None,
                "deniedCondition": True,
                "candidates": [],
            }
            _trace("classify_condition", {"phrase": phrase}, result)
            return result

        graph_hit = _graph("resolve_condition", phrase=matchable)
        if graph_hit.get("found") and graph_hit.get("matchType") == "exact_synonym":
            # A curated exact alias is authoritative only when it identifies one
            # distinct condition. Neptune may contain duplicate term vertices,
            # so collapse duplicates of the same code; aliases owned by multiple
            # codes are unsafe to resolve by traversal order.
            exact_by_code = {
                match.get("icd10"): match
                for match in graph_hit.get("matches", [])
                if match.get("icd10")
            }
            exact_matches = [exact_by_code[code] for code in sorted(exact_by_code)]
            if len(exact_matches) != 1:
                result = {
                    "phrase": phrase,
                    "icd10": None,
                    "label_en": None,
                    "source": "neptune_ambiguous_exact_synonym",
                    "score": 1.0,
                    "confident": False,
                    "ambiguous": True,
                    "corroborated": True,
                    "redFlag": any(int(m.get("severityTier") or 1) >= 4
                                   for m in exact_matches),
                    "needsConfirmation": True,
                    "confirmReason": "exact synonym maps to multiple conditions",
                    "candidates": exact_matches,
                }
                _trace("classify_condition", {"phrase": phrase}, result)
                return result

            m = exact_matches[0]
            tier = int(m.get("severityTier") or 1)

            # An exact synonym match is certain about the CODE but says nothing
            # about whether the patient meant it. "seene mein dard" is a stored
            # synonym for ischaemic heart disease, so it matches perfectly - and
            # recording a cardiac diagnosis off one ambiguous phrase without
            # reading it back is exactly the failure this guard exists to stop.
            # High-severity conditions are always confirmed, however the match
            # was reached.
            high_severity = tier >= 4

            # A GENERIC PARENT alias resolves to one curated code (so the sweeps
            # and its protocol path hold) but the word alone cannot distinguish
            # the sibling it was recorded as - "arthritis" is M15 osteoarthritis
            # here, yet a caller could mean M06 rheumatoid. So it is read back
            # rather than silently recorded, and it does not count as
            # corroboration, EVEN at low severity. This is the single-code
            # alternative to dual-mapping it in the KG, which would break the
            # one-alias-one-code sweeps.
            generic_parent = matchable.strip().casefold() in _GENERIC_PARENT_ALIASES

            result = {
                "phrase": phrase,
                "icd10": m["icd10"],
                "label_en": m["label_en"],
                "label_hi": m.get("label_hi"),
                "category": m.get("category"),
                "severityTier": tier,
                "source": "neptune_exact_synonym",
                "score": 1.0,
                "confident": not generic_parent,
                "ambiguous": generic_parent,
                "corroborated": not generic_parent,
                "redFlag": high_severity,
                "needsConfirmation": high_severity or generic_parent,
                "confirmReason": ("high severity condition" if high_severity
                                  else "generic term; which specific condition"
                                  if generic_parent else None),
                "candidates": graph_hit["matches"],
            }
            _trace("classify_condition", {"phrase": phrase}, result)
            return result

        # Fall through to vector similarity.
        with _timed("bedrock_embed"):
            resp = _bedrock.invoke_model(
                modelId=EMBED_MODEL,
                body=json.dumps({"texts": [matchable],
                                 "input_type": "search_query"}),
            )
        vec = json.loads(resp["body"].read())["embeddings"][0]

        res = _os_request("POST", f"/{IDX_CONCEPTS}/_search", {
            "size": 15,
            "query": {"knn": {"embedding": {"vector": vec, "k": 15}}},
            "_source": ["icd10", "label_en", "label_hi", "surfaceForm", "termType",
                        "category", "severityTier", "sensitivity", "redFlag"],
        })

        # THE NULL CLASS DECIDES FIRST. The index carries distractor documents -
        # ordinary conversational phrases (seed/distractors.json) - precisely so
        # that noise has a nearest neighbour that is not a disease. If the top
        # hit is a distractor, the caller said something conversational, not a
        # condition, and that verdict outranks every score threshold below:
        # thresholds on a disease-only index were the bug ("Here they are." ->
        # Epilepsy at 0.79, "नहीं" -> Parkinson's), because garbage and genuine
        # cross-lingual disclosures overlap in cosine score. They do NOT overlap
        # in what their nearest neighbour IS.
        hits = res["hits"]["hits"]
        if hits and hits[0]["_source"].get("termType") == "distractor":
            result = {
                "icd10": None, "label_en": None, "source": "opensearch_knn",
                "score": hits[0]["_score"], "confident": False,
                "ambiguous": False, "redFlag": False, "needsConfirmation": False,
                "nullMatch": True,
                "matchedVia": hits[0]["_source"].get("surfaceForm"),
                "confirmReason": "nearest neighbour is conversational, not a "
                                 "condition",
                "candidates": [],
            }
            _trace("classify_condition", {"phrase": phrase}, result)
            return result
        # Distractor mass among the top hits discounts confidence even when a
        # disease narrowly wins: a real disclosure's neighbourhood is all
        # synonyms of that disease; noise sits in a mixed neighbourhood.
        distractor_hits = sum(1 for h in hits[:8]
                              if h["_source"].get("termType") == "distractor")

        best: dict[str, dict] = {}
        for hit in hits:
            s = hit["_source"]
            icd = s["icd10"]
            if not icd:
                continue  # distractors carry no code and are counted above
            if icd not in best or hit["_score"] > best[icd]["score"]:
                best[icd] = {
                    "icd10": icd, "label_en": s["label_en"],
                    "label_hi": s.get("label_hi"),
                    "category": s.get("category"),
                    "severityTier": s.get("severityTier"),
                    "sensitivity": s.get("sensitivity"),
                    "matchedVia": s["surfaceForm"], "matchedType": s.get("termType"),
                    "redFlag": bool(s.get("redFlag")), "score": hit["_score"],
                }
        ranked = sorted(best.values(), key=lambda x: -x["score"])[:5]
        top = ranked[0] if ranked else None

        # Ambiguity only counts when the two leading candidates are clinically
        # DIFFERENT. Cross-lingual embeddings place related conditions close
        # together, so a narrow gap on its own is not doubt: "mujhe sugar hai"
        # scores Type 2 and Type 1 diabetes within 0.025 of each other, but both
        # are endocrine and both feed the same form item, so the right move is to
        # ask which type as a follow-up, not to question the classification.
        # A narrow gap between, say, diabetes and liver disease is real doubt.
        #
        # Computed BEFORE confidence because confidence now depends on it - see
        # below.
        ambiguous = False
        if len(ranked) > 1 and (ranked[0]["score"] - ranked[1]["score"]) < 0.03:
            ambiguous = ranked[0].get("category") != ranked[1].get("category")

        # Corroboration: if the graph independently reached the same code from a
        # known synonym token, two different methods agree and the match is not
        # in doubt regardless of the margin.
        #
        # EXCEPT a GENERIC PARENT TERM. "arthritis" is a curated alias of M15
        # (osteoarthritis) so it resolves there and its curated protocol path
        # holds - but the word alone does not tell osteoarthritis from rheumatoid
        # (M06), so it must be READ BACK, not silently recorded as the milder one.
        # Treating its graph hit as corroboration is what skipped that read-back
        # and filed a caller who said only "arthritis" as osteoarthritis. Listed
        # here rather than dual-mapped in the KG, because dual-mapping breaks the
        # one-alias-one-code curated sweeps; this keeps the single code AND forces
        # confirmation, which is the whole point.
        matched_phrase = str(graph_hit.get("phrase") or matchable).strip().casefold()
        generic_parent = matched_phrase in _GENERIC_PARENT_ALIASES
        corroborated = bool(
            graph_hit.get("found")
            and top
            and not generic_parent
            and any(m["icd10"] == top["icd10"] for m in graph_hit.get("matches", []))
        )
        if corroborated:
            ambiguous = False

        # Confidence needs a good score AND a neighbourhood that agrees with
        # itself. Three signals, all of them relative rather than tuned:
        #
        #   score >= 0.75        a floor, not a separator. Controls run 0.805 to
        #                        0.893 and false positives 0.730 to 0.826, so the
        #                        ranges OVERLAP and no threshold in that gap is
        #                        safe. Raising it to chase one test is the
        #                        methodology error this file already warns about.
        #   distractor_hits < 3  conversational mass in the top eight means the
        #                        utterance lives among small talk.
        #   not ambiguous        the top two candidates are not a statistical tie
        #                        across different body systems.
        #
        # The third one is the addition, and it is what a flat neighbourhood looks
        # like from the inside. Measured: "White Golden." - STT garbage - returned
        # E03 at 0.7957 and H40 at 0.7957, an EXACT tie between an endocrine and
        # an ophthalmic condition, with ten codes spread over 0.013 across four
        # body systems and not one distractor in the top ten. Nothing in that
        # picture is a thyroid disclosure; it is the shape of a phrase with no
        # true neighbour, where ranking is near-arbitrary.
        #
        # Note this cannot cost a real disclosure anything: corroboration clears
        # `ambiguous` a few lines up, so every graph-corroborated match stays
        # confident regardless. What it withholds confidence from is precisely the
        # uncorroborated tie, which is the case that most needs a read-back.
        #
        # It also replaces a fragile signal with a stable one. distractor_hits
        # counts within a fixed window, so its meaning shifts whenever the index
        # changes size - adding the reference vocabulary moved distractors out of
        # the top eight for some phrases and silently re-enabled confidence on
        # them. A comparison between the top two candidates does not care how many
        # documents are in the index.
        confident = (bool(top) and top["score"] >= 0.75
                     and distractor_hits < 3
                     and not ambiguous)

        tier = int((top or {}).get("severityTier") or 1)
        high_severity = bool(top) and tier >= 4

        # redFlag is the OR of two independent signals, because the indexed
        # document only carries one of them.
        #
        # Only symptomPhrase documents have a redFlag field; ColloquialTerm
        # documents do not. So reading redFlag off the matched document alone
        # means "heart ki problem" resolves to I25 at tier 4 and still reports
        # redFlag=False, purely because it matched a colloquial term rather than
        # a symptom phrase. The graph fast path derives the flag from severity
        # instead, so the two paths disagreed on the same condition - which
        # surfaced when new synonyms moved phrases between them.
        #
        # Taking either signal keeps the paths consistent and is the clinically
        # safe direction: a tier-4-or-above condition deserves the flag however
        # it was mentioned.
        red = bool(top and top["redFlag"]) or high_severity

        # THE UNCORROBORATED CASE. A high kNN score with no independent graph
        # agreement is the one failure mode this classifier cannot detect from
        # the inside, because a confident wrong answer looks exactly like a
        # confident right one.
        #
        # Measured on nine real conditions deliberately absent from the graph
        # (tests/diag_unlisted_conditions.py), every single one was handed a code
        # for a DIFFERENT disease, and three were recorded silently:
        #
        #   "I have coeliac disease"            -> K51 ulcerative colitis  0.783
        #   "मुझे सोरायसिस नहीं, सारकॉइडोसिस है"  -> L40 psoriasis           0.826
        #   "mera gall bladder nikala gaya tha" -> N20 kidney stones       0.791
        #
        # The Hindi one is the clearest harm: it says "I do NOT have psoriasis, I
        # have sarcoidosis", and psoriasis was what got written down. Scoped
        # negation like that is outside is_blanket_denial by design - that guard
        # requires a generic illness word and no named condition, and widening it
        # to catch this would start suppressing "sugar control nahi hota", which
        # asserts diabetes.
        #
        # The clean separator is not the score. Controls ran 0.836-0.893 and the
        # false positives 0.730-0.826, which overlap - a threshold placed in that
        # gap is one embedding refresh away from silently dropping real
        # conditions, and tuning one number until a single test passes is the
        # methodology error already made once on the Hindi STT prompt.
        #
        # What separates them cleanly is provenance: every control was reached by
        # BOTH methods, every false positive by embeddings alone. Nearest
        # neighbour always returns something, so on its own it cannot say "not in
        # my vocabulary" - it can only say what is closest. The curated graph can.
        # So an uncorroborated match is treated as a hypothesis to be read back,
        # never a fact to be recorded. The patient is the arbiter the classifier
        # lacks: they know whether they have psoriasis.
        uncorroborated = bool(top) and not corroborated

        # SECOND OPINION ON THE UNCORROBORATED PATH ONLY.
        #
        # Corroborated matches are left alone deliberately. Two methods already
        # agreed on those, they are the common case for governed conditions, and
        # putting a model call in front of them would spend latency on every
        # ordinary disclosure to re-check work that was never in doubt. The call
        # fires exactly where the evidence is thinnest.
        # TWO CASES ARE WITHHELD FROM ADJUDICATION, both for safety rather than
        # for cost, and both learned by breaking four suites.
        #
        # 1. SYMPTOM MATCHES. The index deliberately carries symptom phrases that
        #    a clinician mapped to a condition - "chalne par seene mein dard" to
        #    ischaemic heart disease, "ek taraf ka haath pair kaam nahi kar raha"
        #    to stroke. Asking "does this phrase assert that condition?" is the
        #    WRONG QUESTION for a symptom, and a careful medical coder answers it
        #    correctly with no: chest pain on walking is not a diagnosis of I25.
        #    The model then vetoes the exact presentations the graph exists to
        #    escalate. Curated symptom mappings are authored clinical judgement and
        #    a second opinion has no standing to overrule them.
        #
        # 2. RED FLAGS AND HIGH SEVERITY. The two error directions are not equally
        #    costly. A wrong code that gets read back can be rejected by the
        #    caller; a suppressed red flag is silent, and the whole reason
        #    exertional chest pain is in the index is that missing it is a clinical
        #    error rather than a rounding error. So a model is never allowed to be
        #    the reason a red-flag presentation disappears.
        #
        # What remains adjudicable is exactly the intended target: a NAMED
        # condition, not severe, reached through similarity alone.
        adjudication = None
        skip_reason = None
        if uncorroborated:
            if (top or {}).get("matchedType") == "symptom":
                skip_reason = "symptom phrase: a curated clinical mapping, not a guess"
            elif red or high_severity:
                skip_reason = "red flag or high severity: never suppressed by a model"
        if uncorroborated and not skip_reason:
            adjudication = _adjudicate_uncorroborated(phrase, ranked)
            if adjudication and not adjudication["namesACandidate"]:
                # The vocabulary genuinely does not contain what the patient said.
                # Returning no code is the honest answer, and the orchestrator
                # already treats that as the escalate-to-human case: it records the
                # phrase verbatim so an underwriter can read "I have coeliac
                # disease" and code it on sight, which is strictly better than
                # filing Type 2 Diabetes because that was nearest.
                result = {
                    "phrase": phrase,
                    "icd10": None, "label_en": None,
                    "source": "opensearch_knn+llm_rejected",
                    "score": round(top["score"], 4) if top else 0.0,
                    "confident": False, "ambiguous": False,
                    "corroborated": False, "uncorroborated": True,
                    "redFlag": False, "needsConfirmation": False,
                    "unmappable": True,
                    "rejectedCandidate": top["icd10"] if top else None,
                    "confirmReason": (
                        f"nearest code was {top['icd10'] if top else '?'} at "
                        f"{top['score']:.2f} but adjudication judged the phrase "
                        f"names something else: {adjudication['reasoning']}"),
                    "candidates": ranked,
                }
                _trace("classify_condition", {"phrase": phrase}, result)
                return result

        reasons = []
        if red:
            reasons.append("red-flag presentation")
        if high_severity:
            reasons.append("high severity condition")
        if ambiguous:
            reasons.append("two clinically different candidates scored close")
        if top and not confident:
            reasons.append(f"low similarity ({top['score']:.2f})")
        if uncorroborated:
            reasons.append("similarity match not corroborated by the clinical graph")
        if adjudication and adjudication["namesACandidate"]:
            # Recorded, but it changes nothing: a yes does not upgrade the match.
            # The read-back and the review flag both still stand, because the
            # adjudicator judged plausibility and not provenance.
            reasons.append("adjudication found the phrase plausible for this code")
        if skip_reason:
            reasons.append(f"adjudication withheld ({skip_reason})")

        result = {
            "phrase": phrase,
            "icd10": top["icd10"] if top else None,
            "label_en": top["label_en"] if top else None,
            "label_hi": top.get("label_hi") if top else None,
            "category": top.get("category") if top else None,
            "severityTier": tier if top else None,
            "matchedVia": top.get("matchedVia") if top else None,
            "source": "opensearch_knn" + ("+neptune_corroborated" if corroborated else ""),
            "score": round(top["score"], 4) if top else 0.0,
            "confident": confident,
            "ambiguous": ambiguous,
            "corroborated": corroborated,
            "uncorroborated": uncorroborated,
            "redFlag": red,
            # Red flags and high-severity conditions always get read back,
            # whatever the score. Misreading exertional chest pain, or recording
            # a cancer history off one unclear phrase, is a clinical error rather
            # than a rounding error.
            #
            # So does anything the graph did not corroborate: on this path a
            # read-back is the only thing standing between a nearest-neighbour
            # guess and a disease the patient does not have appearing in an
            # underwriting record. The cost is one extra question on genuine
            # long-tail conditions, which is the right trade.
            "needsConfirmation": bool(top) and (red or high_severity or ambiguous
                                                or uncorroborated
                                                or not confident),
            "confirmReason": "; ".join(reasons) or None,
            "adjudicated": bool(adjudication),
            "candidates": ranked,
        }
        _trace("classify_condition", {"phrase": phrase}, result)
        return result
    except Exception as e:
        _trace("classify_condition", {"phrase": phrase}, None, str(e))
        raise


# ═══════════════════════════════════════════════════════════════════════════ #
# 3. protocol - Neptune ENFORCES
# ═══════════════════════════════════════════════════════════════════════════ #
def get_followup_questions(icd10: str, language: str = "hinglish") -> dict:
    """
    The follow-ups the medical protocol REQUIRES for a confirmed condition.

    The agent does not get to decide these. Diabetes always yields the same six.
    TB asks about course completion and drug resistance; it does not ask about
    insulin. The returned protocolSource is the traversal path, and it is
    persisted onto the record so an auditor can see the question came from the
    graph rather than from the model.
    """
    try:
        result = _graph("get_followups", icd10=icd10, language=language)
        _trace("get_followup_questions", {"icd10": icd10, "language": language}, result)
        return result
    except Exception as e:
        _trace("get_followup_questions", {"icd10": icd10}, None, str(e))
        raise


def get_condition_explanation(icd10: str, language: str = "en") -> dict:
    """Fetch the authored, non-diagnostic lay explanation for a governed condition."""
    try:
        result = _graph("get_condition", icd10=icd10)
        explanation = (result.get("explain_hi") if language == "hi"
                       else result.get("explain_en"))
        rendered = {
            **result,
            "language": language,
            "explanation": explanation or result.get("explain_en"),
        }
        _trace("get_condition_explanation", {"icd10": icd10,
                                               "language": language}, rendered)
        return rendered
    except Exception as e:
        _trace("get_condition_explanation", {"icd10": icd10}, None, str(e))
        raise


def check_medication_interactions(medications: list[str]) -> dict:
    """Flag clinically significant interactions once two or more drugs are named."""
    try:
        result = _graph("check_interactions", medications=medications)
        _trace("check_medication_interactions", {"medications": medications}, result)
        return result
    except Exception as e:
        _trace("check_medication_interactions", {"medications": medications}, None, str(e))
        raise


# ═══════════════════════════════════════════════════════════════════════════ #
# 4. audit log
# ═══════════════════════════════════════════════════════════════════════════ #
def log_turn(call_id: str, turn_index: int, role: str, text: str,
             item_id: str | None = None, language: str | None = None,
             stt_confidence: float | None = None,
             classified_icd10: str | None = None,
             classifier_score: float | None = None,
             protocol_source: str | None = None,
             latency_ms: int | None = None,
             stage_ms: dict | None = None,
             customer_id: str | None = None,
             policy_number: str | None = None) -> dict:
    """
    Write one turn to OpenSearch for post-call search and the audit trail.

    The customer id is hashed rather than stored. IRDAI needs the medical record
    for eight years; DPDP grants erasure. Hashing satisfies both - the clinical
    content survives, the identifier does not.
    """
    try:
        doc = {
            "callId": call_id, "turnIndex": turn_index, "role": role, "text": text,
            "itemId": item_id, "language": language, "sttConfidence": stt_confidence,
            "classifiedIcd10": classified_icd10, "classifierScore": classifier_score,
            "protocolSource": protocol_source, "latencyMs": latency_ms,
            "policyNumber": policy_number,
            "customerIdHash": _hash_id(customer_id) if customer_id else None,
            "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "eventType": "turn",
        }
        # Flattened rather than nested, so a percentiles aggregation can name the
        # field directly: stageGraphMs, stageBedrockEmbedMs, stageOpensearchMs.
        for stage, sample in (stage_ms or {}).items():
            key = "".join(part.capitalize() for part in stage.split("_"))
            doc[f"stage{key}Ms"] = sample.get("ms")
            doc[f"stage{key}Calls"] = sample.get("calls")
        _os_request("POST", f"/{IDX_TRANSCRIPTS}/_doc", {k: v for k, v in doc.items() if v is not None})
        return {"logged": True, "turnIndex": turn_index}
    except Exception as e:
        # Never fail a live call because the audit index is unavailable. The
        # DynamoDB turn row is the durable record; OpenSearch is the search view.
        _trace("log_turn", {"call_id": call_id, "turn_index": turn_index}, None, str(e))
        return {"logged": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════ #
# 5. completion
# ═══════════════════════════════════════════════════════════════════════════ #
def generate_summary(call_id: str) -> dict:
    """Compile the collected answers into an underwriting-ready structure.

    MIRRORS THE SALESFORCE WRITEBACK, and has to. This used to compile
    session["answers"] and nothing else, which meant it reported a screen answer
    without the protocol that the screen opened:

        Q: Asthma, tuberculosis, COPD, ... or any other lung condition?
        A: Yes

    That is the whole record it produced. The probe did fire, the caller did say
    which condition, and J45 plus all five follow-up answers were sitting in the
    session - but declaredConditions and followUpAnswers are not form items, so
    none of it appeared here. Read as an underwriting summary, a bare "Yes" is
    indistinguishable from the agent never having asked.

    The flag was a second, worse disagreement. It keyed off True booleans in the
    conditions and hospitalisation sections, while crm_writeback keys off declared
    conditions and unclassified mentions. A VOLUNTEERED condition - one disclosed
    without a screen ever being answered yes - therefore came out "clear" here and
    "refer" in Salesforce: two different answers to what the patient declared,
    which is exactly what that handler's header warns against. The rule below is
    the writeback's rule, deliberately duplicated rather than reinterpreted.
    """
    try:
        session = st.get_session(call_id)
        if not session:
            raise ValueError(f"unknown callId {call_id}")
        items = {i["itemId"]: i for i in st.get_form_items(session["formId"])}
        answers = session.get("answers") or {}

        by_section: dict[str, list] = {}
        positives = []
        for iid, val in sorted(answers.items()):
            meta = items.get(iid, {})
            sec = meta.get("section", "other")
            by_section.setdefault(sec, []).append({
                "itemId": iid, "label": meta.get("label", iid),
                "questionnaireRef": meta.get("questionnaireRef"),
                "question": meta.get("prompt", ""), "answer": val,
            })
            if val is True and meta.get("section") in ("conditions", "hospitalisation"):
                positives.append({"itemId": iid, "label": meta.get("label")})

        # Group the protocol answers under the condition that mandated them, the
        # same way and from the same source as crm_writeback: followUpAnswers,
        # keyed "{icd10}::{questionId}". Not pendingFollowUps - that is the queue
        # of questions still OWED, so it is empty by definition once the call has
        # finished, which is how the clinical detail went missing there too.
        followups_by_icd: dict[str, dict] = {}
        protocol_by_icd: dict[str, str] = {}
        for key, entry in (session.get("followUpAnswers") or {}).items():
            if isinstance(entry, dict):
                icd = entry.get("icd10") or (key.split("::", 1)[0]
                                             if "::" in key else "")
                qid = entry.get("questionId") or key.split("::", 1)[-1]
                if entry.get("protocolSource"):
                    protocol_by_icd.setdefault(icd, entry["protocolSource"])
                followups_by_icd.setdefault(icd, {})[qid] = {
                    "answer": entry.get("value"),
                    "question": entry.get("prompt"),
                    "answeredAt": entry.get("answeredAt"),
                }
            else:
                icd, _, qid = key.partition("::")
                followups_by_icd.setdefault(icd, {})[qid or key] = {"answer": entry}

        conditions = []
        for d in session.get("declaredConditions") or []:
            icd = d.get("icd10") or ""
            conditions.append({
                "icd10": icd,
                "label": d.get("label"),
                "patientPhrase": d.get("patientPhrase"),
                "confidence": d.get("confidence"),
                # Provenance, mirroring crm_writeback. corroborated answers the
                # question confidence cannot: whether anything other than a
                # nearest-neighbour distance backs this code.
                "corroborated": d.get("corroborated"),
                "confirmedByPatient": d.get("confirmedByPatient"),
                "declaredAt": d.get("at"),
                "protocolSource": d.get("protocolSource") or protocol_by_icd.get(icd),
                "followUps": followups_by_icd.get(icd, {}),
            })

        # Top level, not buried, because it is the first thing a reviewer should
        # see and it is often resolvable on sight by a human who can read the
        # phrase the graph could not.
        needs_review = [
            {"patientPhrase": m.get("phrase"), "reason": m.get("reason"),
             "at": m.get("at"), "resolvedTo": m.get("itemId")}
            for m in (session.get("unclassifiedMentions") or [])
        ]

        # A code the graph never corroborated is also a review item. The rule is
        # SHARED with crm_writeback through hvi_state rather than copied, because
        # a summary that says "clear" while the writeback says "refer" is worse
        # than either verdict, and two copies stay identical only until one is
        # edited. See st.provenance_review_rows for the live call behind it.
        needs_review += st.provenance_review_rows(session)

        # crm_writeback._underwriting_flag, kept identical on purpose. An
        # unresolved mention outranks everything: the phrase the machine could not
        # interpret must never be the one it marks as needing no review.
        if session.get("escalated") or session.get("medicalTestRequired"):
            flag = "medical-test-required"
        elif needs_review or conditions or positives:
            flag = "refer"
        else:
            flag = "clear"

        result = {
            "callId": call_id,
            "customerName": session.get("customerName"),
            "policyNumber": session.get("policyNumber"),
            "answeredCount": len(answers),
            "sections": by_section,
            "positiveDeclarations": positives,
            "conditions": conditions,
            "needsHumanReview": needs_review,
            "underwritingFlag": flag,
            "resumeCount": session.get("resumeCount", 0),
            "callLegs": len(session.get("priorCallIds") or []) + 1,
        }
        _trace("generate_summary", {"call_id": call_id}, result)
        return result
    except Exception as e:
        _trace("generate_summary", {"call_id": call_id}, None, str(e))
        raise


def archive_now(call_id: str, reason: str = "") -> dict:
    """Write this call's durable S3 archive and point the session at it. NEVER RAISES.

    THE ARCHIVE IS NOT A COMPLETION ARTEFACT, which is the whole reason this is a
    function rather than four lines inside submit_to_crm.

    It used to be called from exactly one place - the clean-completion path - so a
    call that escalated, was terminated for a failed identity check, or was simply
    abandoned mid-questionnaire produced no archive at all. Those transcripts lived
    only in hvi-call-state and were deleted by its 30-day TTL, which means the
    calls MOST worth keeping were the ones guaranteed to disappear: a self-harm
    escalation, an identity failure, a caller who gave up halfway. The record of a
    call that went wrong is evidence; the record of a call that went perfectly is
    paperwork.

    Safe to call more than once. Re-archiving overwrites the same two keys with a
    later, more complete snapshot, and the bucket is versioned, so the earlier one
    is still recoverable. ``reason`` is logged and traced only - it names the
    ending that triggered the write, so a reader can tell an escalation archive
    from a completion archive without diffing the documents.

    Lazily imports hvi_archive and swallows everything: a missing bucket, a denied
    PutObject or an unstaged module must never turn a call into a failed one.
    """
    try:
        import hvi_archive

        session = st.get_session(call_id) or {}
        if not session:
            _trace("archive_call", {"call_id": call_id, "reason": reason}, None,
                   "no session")
            return {"ok": False, "skipped": False, "error": "no session"}
        archived = hvi_archive.archive_call(call_id, session,
                                           st.get_turns(call_id))
        if archived.get("transcriptUri") or archived.get("recordingUri"):
            st.set_archive_uris(call_id, archived.get("transcriptUri"),
                                archived.get("recordingUri"))
        _trace("archive_call", {"call_id": call_id, "reason": reason}, archived)
        return archived
    except Exception as e:  # noqa: BLE001 - an archive never fails a call
        log.warning("call archive skipped for %s (%s): %s", call_id, reason, e)
        _trace("archive_call", {"call_id": call_id, "reason": reason}, None, str(e))
        return {"ok": False, "skipped": False, "error": str(e)}


def link_recording(call_id: str) -> dict:
    """Attach this call's archived audio to the session, and to the CRM. NEVER RAISES.

    WHY THIS IS A SEPARATE, LATER STEP THAN THE ARCHIVE
    ---------------------------------------------------
    archive_now writes the transcript when the call ENDS, and asks S3 for the
    recording in the same breath. The audio is not there yet - it sits in pipecat's
    buffer on the media host until the pipeline tears down, which happens after the
    caller has heard the closing words. So that HEAD always missed, recordingUri was
    never set, and Salesforce's Recording_URI__c was blank on every call even when
    the WAV was already in the bucket. Measured: one call had audio/call.wav in S3
    and recordingUri None; the calls that had a transcript had no audio linked. No
    call had both.

    The media host is the only actor that knows the upload finished, and it has no
    DynamoDB permissions by design (see media/agent_bridge.py), so it calls the
    agent instead - the agent owns the session. It sends only a callId: the key is
    deterministic, so there is nothing to trust and everything to verify, and
    hvi_archive.find_recording still proves the object exists with a HEAD before any
    URI reaches a medical record.

    THE CRM HALF. Medical_History__c is upserted on Call_Id__c, an External Id, so
    re-invoking the writeback UPDATES the row this call already wrote rather than
    adding a second one - which is what lets a field be filled after the fact.
    Gated on the call having actually COMPLETED, because that is the only ending
    that wrote a CRM record: re-invoking for a terminated or escalated call would
    CREATE an underwriting record for an intake that was never finished.

    Idempotent: a second call with the URI already on the session does nothing.
    """
    result = {"ok": False, "found": False, "recordingUri": None,
              "crmUpdated": False, "error": None}
    try:
        import hvi_archive

        session = st.get_session(call_id) or {}
        if not session:
            result["error"] = "no session"
            _trace("link_recording", {"call_id": call_id}, None, "no session")
            return result

        uri = hvi_archive.find_recording(call_id)
        if not uri:
            # Normal for a surface that records nothing, a disabled recorder, or an
            # upload that failed. Nothing to link is not a failure.
            result["ok"] = True
            _trace("link_recording", {"call_id": call_id}, result)
            return result
        result["found"] = True
        result["recordingUri"] = uri

        if session.get("recordingUri") == uri:
            result["ok"] = True
            log.info("recording for %s is already linked", call_id)
            _trace("link_recording", {"call_id": call_id}, result)
            return result

        # transcript_uri=None leaves an already-written transcriptUri alone; see
        # set_archive_uris.
        st.set_archive_uris(call_id, None, uri)
        log.info("linked recording for %s -> %s", call_id, uri)

        if session.get("status") == st.STATUS_COMPLETED:
            try:
                from hvi_crm_gateway import crm_client
                gateway = crm_client()
                if gateway is not None:
                    gateway.submit(call_id)
                else:
                    _timed_lambda_invoke(
                        "lambda_crm",
                        FunctionName=os.environ.get("CRM_FN", "hvi-crm-writeback"),
                        InvocationType="RequestResponse",
                        Payload=json.dumps({"action": "submit",
                                            "callId": call_id}).encode(),
                    )
                result["crmUpdated"] = True
                log.info("refreshed the CRM record for %s with the recording URI",
                         call_id)
            except Exception as e:  # noqa: BLE001 - the URI is safely on the session
                # The session now points at the audio either way, so an underwriter
                # can still find it and a later writeback will carry it. Losing the
                # call over a CRM refresh would be far worse.
                result["error"] = f"crm refresh failed: {e}"
                log.warning("could not refresh the CRM record for %s: %s",
                            call_id, e)

        result["ok"] = True
        _trace("link_recording", {"call_id": call_id}, result)
        return result
    except Exception as e:  # noqa: BLE001 - linking audio never fails a call
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("could not link the recording for %s: %s", call_id, e)
        _trace("link_recording", {"call_id": call_id}, None, str(e))
        return result


def submit_to_crm(call_id: str) -> dict:
    """Finalise locally, confirm Salesforce writeback, then mark completed.

    A Lambda ``Event`` response proves only queue acceptance. Underwriting
    completion is reported only after the writeback function synchronously
    returns ``written: true``; otherwise the session remains resumable.
    """
    try:
        record = st.finalize_session(call_id, mark_completed=False)

        # ---- durable archive, BEFORE the writeback --------------------------
        # The transcript goes to S3 here and its URI is written onto the session,
        # so the writeback below finds it and fills Salesforce's
        # Transcript_URI__c / Recording_URI__c. Ordering is the whole point:
        # archiving after the writeback would leave both fields blank until some
        # later update, which is exactly how they came to be permanently empty.
        #
        # archive_now never raises, so this needs no guard of its own. Same
        # function every non-clean ending uses - see its docstring for why the
        # archive is not a completion artefact.
        archive_now(call_id, reason="completed")

        # Only the WRITEBACK INVOKE is brokered through the gateway when enabled;
        # finalize_session above and mark_status below are the state machine's own
        # DynamoDB work and stay direct by design (Option A, and DynamoDB is not a
        # Gateway tool). See docs/ARCHITECTURE_OPTION_A_GATEWAY.md.
        from hvi_crm_gateway import crm_client
        gateway = crm_client()
        if gateway is not None:
            body = gateway.submit(call_id)  # raises unless written=True
            status_code = 200
        else:
            payload = {"action": "submit", "callId": call_id}
            response = _timed_lambda_invoke(
                "lambda_crm",
                FunctionName=os.environ.get("CRM_FN", "hvi-crm-writeback"),
                InvocationType="RequestResponse",
                Payload=json.dumps(payload).encode(),
            )
            raw = response["Payload"].read()
            body = json.loads(raw or b"{}")
            if response.get("FunctionError") or not body.get("written"):
                raise RuntimeError(
                    body.get("errorMessage") or body.get("error")
                    or response.get("FunctionError")
                    or "CRM writeback was not confirmed")
            status_code = response["StatusCode"]

        st.mark_status(call_id, st.STATUS_COMPLETED,
                       note="intake complete and CRM writeback confirmed")
        result = {
            "finalised": True,
            "written": True,
            "callId": call_id,
            "answerCount": record["answerCount"],
            "crmStatusCode": status_code,
            # Carried through so a UI can link to the record that was just
            # written rather than asking the underwriter to search for it. Both
            # may be None: the Id needs a fallback read on the update path, and
            # the URL additionally needs the org host to be knowable.
            "recordId": body.get("recordId"),
            "recordUrl": body.get("recordUrl"),
        }
        _trace("submit_to_crm", {"call_id": call_id}, result)
        return result
    except Exception as e:
        _trace("submit_to_crm", {"call_id": call_id}, None, str(e))
        raise


def send_confirmation_email(call_id: str) -> dict:
    """
    Post-call summary with the 48-hour correction window the customer asked for.
    Sent from ap-south-1 so the content never leaves India.

    Dispatch is either the direct hvi-send-confirmation Lambda invoke (default) or,
    when EMAIL_VIA_GATEWAY is set, an AgentCore Gateway MCP tool - the state machine
    decides WHEN to send exactly as before; only the transport changes. See
    docs/ARCHITECTURE_OPTION_A_GATEWAY.md. Lazy import so the direct path carries no
    gateway dependency.
    """
    try:
        from hvi_email_gateway import email_client
        gateway = email_client()
        if gateway is not None:
            body = gateway.send_confirmation(call_id)
            _trace("send_confirmation_email", {"call_id": call_id,
                                               "via": "gateway"}, body)
            return body

        r = _timed_lambda_invoke(
            "lambda_email",
            FunctionName=os.environ.get("EMAIL_FN", "hvi-send-confirmation"),
            InvocationType="RequestResponse",
            Payload=json.dumps({"callId": call_id}).encode(),
        )
        body = json.loads(r["Payload"].read())
        if r.get("FunctionError") or not body.get("sent"):
            raise RuntimeError(body.get("error") or r.get("FunctionError")
                               or "confirmation email was not sent")
        _trace("send_confirmation_email", {"call_id": call_id}, body)
        return body
    except Exception as e:
        _trace("send_confirmation_email", {"call_id": call_id}, None, str(e))
        raise


def escalate_to_human(call_id: str, reason: str, detail: str = "") -> dict:
    """
    Hand off to a clinician.

    Triggers: a positive self-harm answer, a red-flag symptom the patient will
    not confirm, three failed attempts at one question, an explicit request, or
    evident distress. On a self-harm disclosure the agent must stop the
    questionnaire - it does not continue and collect the remaining items.
    """
    try:
        st.mark_status(call_id, st.STATUS_ESCALATED,
                       note=f"{reason}: {detail}"[:400])
        # An escalated call never reaches submit_to_crm, so without this its
        # transcript existed ONLY in the 30-day checkpoint table. A self-harm
        # disclosure or a red-flag symptom the patient would not confirm is
        # precisely the conversation someone may need to read back months later,
        # and it was the one guaranteed to be deleted. Never raises.
        archive_now(call_id, reason=f"escalated:{reason}")
        result = {"escalated": True, "callId": call_id, "reason": reason,
                  "stopQuestionnaire": reason in ("self_harm_disclosure",
                                                  "medical_emergency", "distress")}
        _trace("escalate_to_human", {"call_id": call_id, "reason": reason}, result)
        return result
    except Exception as e:
        _trace("escalate_to_human", {"call_id": call_id, "reason": reason}, None, str(e))
        raise


# The closed registry. Anything not here is unreachable by the agent.
REGISTRY = {
    "get_session_state": get_session_state,
    "save_answer": save_answer,
    "record_agent_turn": record_agent_turn,
    "classify_condition": classify_condition,
    "get_followup_questions": get_followup_questions,
    "get_condition_explanation": get_condition_explanation,
    "check_medication_interactions": check_medication_interactions,
    "log_turn": log_turn,
    "generate_summary": generate_summary,
    "submit_to_crm": submit_to_crm,
    "send_confirmation_email": send_confirmation_email,
    "escalate_to_human": escalate_to_human,
}
