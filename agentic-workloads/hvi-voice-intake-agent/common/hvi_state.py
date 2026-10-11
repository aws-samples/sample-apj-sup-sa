"""
Shared state layer for the health voice intake stack.

This module is the single source of truth for three things:

  1. The dynamic form engine. Form items live as DynamoDB rows and are read fresh
     at the start of every call and again on every resume, so a new health
     question goes live with one PutItem and no redeployment.

  2. Per-turn checkpointing. Every conversational turn is written to
     hvi-call-state (callId + turnIndex) *before* the next question is asked, and
     the answer is folded into the hvi-call-session pointer in the same breath.
     A call that drops therefore loses at most the turn in flight.

  3. Resume. find_resumable_session() looks up an unfinished session by phone
     number so a callback can inherit the answers already collected and restart
     at the first unanswered item.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import logging
import os
import time
import uuid
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

REGION = os.environ.get(
    "DATA_REGION", os.environ.get("AWS_REGION", "ap-south-1"))

TBL_CALL_STATE = os.environ.get("TBL_CALL_STATE", "hvi-call-state")
TBL_CALL_SESSION = os.environ.get("TBL_CALL_SESSION", "hvi-call-session")
TBL_CRM = os.environ.get("TBL_CRM", "hvi-crm-cache")
TBL_FORM_SCHEMA = os.environ.get("TBL_FORM_SCHEMA", "hvi-form-schema")
TBL_RESPONSES = os.environ.get("TBL_RESPONSES", "hvi-intake-responses")

TURN_TTL_DAYS = int(os.environ.get("TURN_TTL_DAYS", "30"))
TURN_LEASE_SECONDS = int(os.environ.get("TURN_LEASE_SECONDS", "300"))

_ddb = boto3.resource("dynamodb", region_name=REGION)

STATUS_IN_PROGRESS = "in_progress"
STATUS_DROPPED = "dropped"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"
STATUS_TERMINATED = "terminated"
STATUS_ESCALATED = "escalated"
RESUMABLE = (STATUS_IN_PROGRESS, STATUS_DROPPED)

log = logging.getLogger("hvi.state")

# How stale an unfinished session may be and still be continued. Resume exists so
# a call that drops mid-answer does not lose the answer, not so one interview can
# span days - and the resume opening tells the caller "we were cut off earlier",
# which stops being true long before this. Four hours covers a dropped call and a
# prompt callback.
RESUME_MAX_AGE_SECS = float(os.environ.get("RESUME_MAX_AGE_SECS", str(4 * 3600)))

_SECURITY_TERMINATION_NOTES = frozenset({
    "identity_not_confirmed",
    "consent_not_granted",
    "identity_verification_mismatch",
    "identity_verification_unavailable",
})
_TERMINAL_STATUSES = frozenset({
    STATUS_COMPLETED,
    STATUS_ABANDONED,
    STATUS_TERMINATED,
    STATUS_ESCALATED,
})


def is_terminal_session(session: dict | None) -> bool:
    """Return whether a session must reject all further automated turns.

    The status-note fallback protects rows written before STATUS_TERMINATED was
    introduced, when security failures were incorrectly stored as resumable
    drops.
    """
    session = session or {}
    status = session.get("status")
    return (status in _TERMINAL_STATUSES
            or (status == STATUS_DROPPED
                and session.get("statusNote") in _SECURITY_TERMINATION_NOTES))


def status_after_agent_hangup(
    session: dict | None,
    *,
    is_complete: bool = False,
    escalated: bool = False,
) -> str:
    """Choose a hangup status without erasing a stronger persisted decision."""
    current = (session or {}).get("status")
    if is_terminal_session(session):
        return current
    if is_complete:
        return STATUS_COMPLETED
    if escalated:
        return STATUS_ESCALATED
    if current and current != STATUS_IN_PROGRESS:
        return current
    return STATUS_DROPPED


def acquire_turn_lease(
    call_id: str,
    owner: str,
    lease_seconds: int = TURN_LEASE_SECONDS,
) -> dict | None:
    """Atomically serialize a complete dialogue turn for one call.

    The owner is never displaced automatically. A process that is merely paused
    must not resume after another worker has taken over and write stale health
    data. ``turnLeaseUntil`` is retained as an operational age signal, not as an
    automatic takeover condition; recovery of an orphaned lease is an explicit
    operator action after establishing that its worker is gone.
    """
    now = int(time.time())
    try:
        result = _ddb.Table(TBL_CALL_SESSION).update_item(
            Key={"callId": call_id},
            UpdateExpression=("SET turnLeaseOwner = :owner, turnLeaseUntil = :until, "
                              "updatedAt = :updated"),
            ConditionExpression=("attribute_exists(callId) AND "
                                 "attribute_not_exists(turnLeaseOwner)"),
            ExpressionAttributeValues=to_ddb({
                ":owner": owner,
                ":until": now + max(1, int(lease_seconds)),
                ":updated": now_iso(),
            }),
            ReturnValues="ALL_NEW",
        )
        return from_ddb(result.get("Attributes"))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return None
        raise


def release_turn_lease(call_id: str, owner: str) -> bool:
    """Release only the caller's lease; an expired owner cannot clear a successor."""
    try:
        _ddb.Table(TBL_CALL_SESSION).update_item(
            Key={"callId": call_id},
            UpdateExpression="REMOVE turnLeaseOwner, turnLeaseUntil SET updatedAt = :updated",
            ConditionExpression="turnLeaseOwner = :owner",
            ExpressionAttributeValues=to_ddb({":owner": owner, ":updated": now_iso()}),
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def checkpoint_admitted_turn(
    call_id: str,
    turn_index: int,
    fingerprint: str,
    response: dict,
) -> None:
    """Cache one completed ingress turn for monotonic, idempotent retries.

    The complete dialogue mutation remains protected by the non-displaceable turn
    lease. This checkpoint is written before the response is returned, allowing a
    sequential network retry with the same turn and payload to replay the response
    without invoking the orchestrator or mutating health state a second time.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("SET lastAdmittedTurn = :turn, "
                          "lastTurnFingerprint = :fingerprint, "
                          "lastTurnResponse = :response, updatedAt = :updated"),
        ExpressionAttributeValues=to_ddb({
            ":turn": int(turn_index),
            ":fingerprint": fingerprint,
            ":response": response,
            ":updated": now_iso(),
        }),
    )

# The three language modes, in the form the ORCHESTRATOR uses.
#
# There are two vocabularies in play and they do not match, which is a bug that
# survived every test because it fails silently in the safe-looking direction.
#
# Salesforce's HVI_Preferred_Language__c is a restricted picklist of en-IN /
# hi-IN / hinglish. The orchestrator branches on "en" / "hi" / "hinglish", almost
# always as a dict lookup with English as the fallback:
#
#     {"hi": "...", "hinglish": "..."}.get(language, "<English>")
#
# So a customer whose CRM record says hi-IN does not match "hi", falls through to
# the default, and is spoken to in ENGLISH for the entire call. Nothing raises,
# nothing logs, and en-IN happens to land on the right answer by accident because
# English IS the fallback - so the bug is invisible unless the caller wanted
# Hindi, which is one of the three modes the mentor asked for explicitly.
#
# crm_writeback already maps the other direction ("hi" -> "hi-IN") to satisfy the
# picklist. This is the missing inbound half of that pair.
LANGUAGES = ("en", "hi", "hinglish")

_LANGUAGE_ALIASES = {
    "en-in": "en", "en_in": "en", "en": "en", "english": "en",
    "hi-in": "hi", "hi_in": "hi", "hi": "hi", "hindi": "hi",
    "hinglish": "hinglish", "hi-en": "hinglish", "en-hi": "hinglish",
}


def normalise_language(value: str | None) -> str:
    """Map any CRM or carrier spelling of a language onto en / hi / hinglish.

    Defaults to hinglish rather than en: the product is an Indian health insurer
    calling Indian policyholders, and code-mixed Hindi-English is the most widely
    understood of the three. Defaulting to English would mean an unrecognised
    value silently produces the least accessible call.
    """
    if not value:
        return "hinglish"
    return _LANGUAGE_ALIASES.get(str(value).strip().lower(), "hinglish")


# --------------------------------------------------------------------------- #
# type helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def to_ddb(obj: Any) -> Any:
    """DynamoDB will not accept float, so normalise to Decimal."""
    if isinstance(obj, float):
        return decimal.Decimal(str(obj))
    if isinstance(obj, list):
        return [to_ddb(v) for v in obj]
    if isinstance(obj, tuple):
        return [to_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_ddb(v) for k, v in obj.items()}
    return obj


def from_ddb(obj: Any) -> Any:
    """Undo Decimal so the value is JSON-serialisable again."""
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [from_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: from_ddb(v) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- #
# CRM
# --------------------------------------------------------------------------- #
def get_customer(customer_id: str) -> dict | None:
    # CRM sync and inbound bootstrap read this primary-key record immediately
    # after projecting it. Strong consistency prevents a successful write from
    # being mistaken for an unknown customer during that hand-off.
    r = _ddb.Table(TBL_CRM).get_item(
        Key={"customerId": customer_id}, ConsistentRead=True)
    return from_ddb(r.get("Item")) if r.get("Item") else None


def get_customer_by_phone(phone: str) -> dict | None:
    table = _ddb.Table(TBL_CRM)
    result = table.query(
        IndexName="phone-index",
        KeyConditionExpression=Key("phone").eq(phone),
    )
    items = list(result.get("Items") or [])
    while result.get("LastEvaluatedKey"):
        result = table.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phone").eq(phone),
            ExclusiveStartKey=result["LastEvaluatedKey"],
        )
        items.extend(result.get("Items") or [])
    if not items:
        return None
    # A phone can legitimately be reused across policies, and live test fixtures
    # also leave historical keys if cleanup was interrupted. A hash-only GSI has
    # no defined order, so Limit=1 selected an arbitrary customer. Prefer the
    # latest Salesforce modification, with projection time as the fallback.
    latest = max(items, key=lambda row: (
        row.get("sourceLastModified") or "",
        row.get("syncedAt") or "",
        row.get("customerId") or "",
    ))
    return from_ddb(latest)


# --------------------------------------------------------------------------- #
# dynamic form schema
# --------------------------------------------------------------------------- #
def get_form_items(form_id: str, include_inactive: bool = False) -> list[dict]:
    """
    Return the live, ordered list of form items.

    Called at call start and on every resume. Because this is a fresh read,
    an item added to DynamoDB a second ago is included in the very next call.
    """
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("formId").eq(form_id)}
    table = _ddb.Table(TBL_FORM_SCHEMA)
    while True:
        r = table.query(**kwargs)
        items.extend(from_ddb(i) for i in r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    items = [i for i in items if i.get("itemId") != "__META__"]
    if not include_inactive:
        items = [i for i in items if i.get("active", True)]
    items.sort(key=lambda i: int(i.get("order", 0)))
    return items


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1")
    return bool(v)


def show_item(item: dict, answers: dict) -> bool:
    """Evaluate an item's showIf gate against the answers collected so far."""
    cond = item.get("showIf")
    if not cond:
        return True
    dep = cond.get("itemId")
    if dep not in answers:
        # Parent not answered yet, so a conditional child cannot be shown.
        return False
    actual = answers[dep]
    op = cond.get("operator", "eq")
    expected = cond.get("value")

    if op == "eq":
        if isinstance(expected, bool):
            return _truthy(actual) == expected
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "notin":
        return actual not in (expected or [])
    if op == "gt":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == "exists":
        return actual not in (None, "")
    return True


def next_item(items: Iterable[dict], answers: dict) -> dict | None:
    """
    First item, in order, that is visible given current answers and not yet
    answered. This is what makes resume trivial: hand it the inherited answers
    map and it returns exactly where the previous call left off.
    """
    for item in items:
        iid = item.get("itemId")
        if iid in answers:
            continue
        if not show_item(item, answers):
            continue
        return item
    return None


def remaining_count(items: Iterable[dict], answers: dict) -> int:
    n = 0
    for item in items:
        if item.get("itemId") in answers:
            continue
        if not show_item(item, answers):
            continue
        n += 1
    return n


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
def find_resumable_session(phone: str, form_id: str | None = None) -> dict | None:
    """
    Most recent unfinished session for a phone number. Drives callback resume.
    Uses the phone-index GSI, descending on updatedAt.

    THREE THINGS DISQUALIFY A SESSION EVEN THOUGH ITS STATUS SAYS RESUMABLE.

    A DIFFERENT FORM. Answers are keyed by item id, and item ids only mean
    something within one questionnaire. Carrying them into a different form
    inherits answers to questions this call will never ask, and skips questions it
    should. Measured: a caller was moved from the 27-item rehearsal form to the
    38-item production one, the next call resumed the old session, and tobacco and
    alcohol were never asked because the rehearsal form had already collected
    them. The record was complete and the interview was not, which is the worst
    combination available - it looks finished.

    AGE. "We were cut off earlier, so let me carry on from where we stopped" is a
    claim about a dropped call, and a caller hears it as minutes. Said 19 hours
    later it is simply false, and it drops the listener into the middle of a
    protocol they have no memory of. Resume exists so a call that drops mid-answer
    does not lose the answer; it is not a way to conduct one interview across
    several days.

    NOTHING TO RESUME. A session that collected no answers, owes no follow-up and
    declared no condition carries no state, so resuming it inherits nothing and
    costs two things it has no way to pay for: the caller is told "we were cut off
    earlier" about a call they were never on, and start_session queues a full
    identity re-verification (name, then date of birth) that the previous leg never
    completed in the first place. Measured on call-416a12ed: a session THIRTEEN
    SECONDS old with zero answers was resumed - the same call reconnecting, not a
    callback - and the caller was apologised to for a drop that had not happened and
    then asked to confirm who she was before the questionnaire had begun.

    This is deliberately a test of PROGRESS and not of age. A call that genuinely
    drops thirty seconds in, with five answers recorded, should absolutely resume;
    the interval is not what makes a resume wrong. Having nothing to carry is.

    The first two are configurable rather than absolute because the right window is
    an operational judgement, not a property of the code. The third is not: an empty
    session is worth nothing to resume at any age.
    """
    r = _ddb.Table(TBL_CALL_SESSION).query(
        IndexName="phone-index",
        KeyConditionExpression=Key("phone").eq(phone),
        ScanIndexForward=False,
        Limit=10,
    )
    for item in r.get("Items", []):
        decoded = from_ddb(item)
        if decoded.get("status") not in RESUMABLE or is_terminal_session(decoded):
            continue
        if form_id and decoded.get("formId") and decoded["formId"] != form_id:
            log.info("not resuming %s: it was collected against form %s and this "
                     "call uses %s, so its answers do not transfer",
                     decoded.get("callId"), decoded["formId"], form_id)
            continue
        age = _session_age_seconds(decoded)
        if age is not None and age > RESUME_MAX_AGE_SECS:
            log.info("not resuming %s: last touched %.1f hours ago, past the "
                     "%.1f hour resume window", decoded.get("callId"),
                     age / 3600.0, RESUME_MAX_AGE_SECS / 3600.0)
            continue
        if not _has_resumable_progress(decoded):
            log.info("not resuming %s: it collected no answers, owes no follow-up "
                     "and declared no condition, so there is nothing to carry - "
                     "resuming it would only claim a dropped call and re-verify an "
                     "identity it never established", decoded.get("callId"))
            continue
        return decoded
    return None


# What makes a session worth carrying forward. Read from the same keys start_session
# inherits, so a new piece of durable conversational state cannot be added there and
# silently fail to count as progress here.
_RESUMABLE_PROGRESS_KEYS = (
    "answers", "pendingFollowUps", "followUpAnswers", "declaredConditions",
    "deferredMentions", "awaitingConfirmation", "pendingConditionDetail",
)


def _has_resumable_progress(session: dict) -> bool:
    """Is there anything in this session a new call would actually inherit?

    Deliberately checks the PAYLOAD and not the turn count. A session can hold
    several turns and still have nothing to resume - the greeting, a language
    answer and a misheard reply are turns, none of them is progress - and it is
    the payload that start_session copies across.

    languageConfirmed is NOT progress. Inheriting it saves one short question;
    resuming to get it costs the false "we were cut off earlier" and two identity
    re-verifications, which is a bad trade at any age. A fresh call simply asks
    which language again, which is the correct opening for a caller who has not
    answered anything yet.
    """
    return any(session.get(key) for key in _RESUMABLE_PROGRESS_KEYS)


def _session_age_seconds(session: dict) -> float | None:
    """Seconds since the session was last touched, or None if unknowable."""
    stamp = session.get("updatedAt") or session.get("createdAt")
    if not stamp:
        return None
    try:
        when = _dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds()


def start_session(
    phone: str,
    customer: dict,
    form_id: str,
    resume_from: dict | None = None,
) -> dict:
    """
    Create a session for a new call. When resume_from is supplied the answers
    already collected are carried across and the old session is retired, so the
    new call continues rather than starting over.
    """
    call_id = f"call-{uuid.uuid4()}"
    ts = now_iso()

    inherited: dict = {}
    prior: list = []
    resume_count = 0
    pending_followups: list = []
    followup_answers: dict = {}
    declared: list = []
    # Pending conversational obligations that must survive a dropped call. A
    # callback must not forget an unconfirmed interpretation, a deferred second
    # condition, a medicine verification, an explanation, or the final summary.
    # Attempt counters deliberately reset: the new audio leg gets a clean chance
    # to understand the customer while preserving what is still owed.
    obligations = {
        "awaitingConfirmation": None,
        "pendingConditionDetail": None,
        "deferredMentions": [],
        "rejectedCodes": [],
        "unclassifiedMentions": [],
        "medCheckPending": None,
        "medChecksAsked": [],
        "distressCheckPending": False,
        "refusalConfirmPending": False,
        "summaryAskPending": False,
        "summaryAskCount": 0,
        "explanationPending": None,
        # When the caller confirmed the read-back record as correct. The written
        # transcript asserts a declaration of completeness and accuracy, and that
        # assertion has to rest on a recorded fact rather than on the fact that
        # the call happened to reach the end. See record_declaration.
        "declarationConfirmedAt": None,
    }
    # The CRM preference is only a STARTING POINT now that the agent asks the
    # customer directly. It is overwritten the moment they answer.
    language = normalise_language(customer.get("preferredLanguage"))
    language_confirmed = False
    # A resumed call re-verifies the caller's identity before it continues. The
    # prior leg's checks do not carry authority across a gap and a new call leg:
    # a number can change hands, and a callback is exactly when re-proving who is
    # on the line matters most. The queue lists the identity items to re-ask, in
    # order - name (A010) then date of birth (A030). The orchestrator asks each
    # ahead of everything else and fails closed on a wrong name/DOB (A010 onFalse
    # / A030 onMismatch), draining each as it re-verifies. Empty on a first call,
    # which is therefore unaffected. Consent (A020) is NOT re-asked.
    identity_reconfirm_queue: list = []
    if resume_from:
        inherited = dict(resume_from.get("answers") or {})
        prior = list(resume_from.get("priorCallIds") or [])
        prior.append(resume_from["callId"])
        resume_count = int(resume_from.get("resumeCount") or 0) + 1
        pending_followups = list(resume_from.get("pendingFollowUps") or [])
        followup_answers = dict(resume_from.get("followUpAnswers") or {})
        declared = list(resume_from.get("declaredConditions") or [])
        identity_reconfirm_queue = ["A010", "A030"]
        for key, default in tuple(obligations.items()):
            value = resume_from.get(key, default)
            if isinstance(default, list):
                value = list(value or [])
            elif isinstance(default, dict):
                value = dict(value or {})
            obligations[key] = value
        if resume_from.get("languageConfirmed"):
            language = normalise_language(resume_from.get("language"))
            language_confirmed = True

    session = {
        "callId": call_id,
        "phone": phone,
        "customerId": customer.get("customerId"),
        "customerName": customer.get("customerName"),
        "customerEmail": customer.get("customerEmail"),
        "dateOfBirth": customer.get("dateOfBirth"),
        # Carried from the application, never asked on the call - the insurer's own
        # telephonic report prints it in the header rather than putting it to the
        # customer, and asking someone to state their sex to verify a policy they
        # already hold would be both odd and pointless. Underwriting needs it
        # because ratings and some clinical protocols are sex-specific.
        "gender": customer.get("gender"),
        "policyNumber": customer.get("policyNumber"),
        "insurerName": customer.get("insurerName"),
        "language": language,
        # Has the CUSTOMER chosen, as opposed to the CRM having a guess on file?
        # This is the difference between a preference and a decision, and the
        # orchestrator treats them differently: an unconfirmed language may be
        # overridden by whatever the customer is heard to speak, a confirmed one
        # may not, because overriding an explicit answer is a bug.
        "languageConfirmed": language_confirmed,
        # Identity items a resumed call still owes a re-verification for, in ask
        # order (name, then DOB); empty on a first call. Drained by
        # pop_identity_reconfirm as each re-verifies.
        "identityReconfirmQueue": identity_reconfirm_queue,
        # Bounded re-asking. Two unusable answers to "Hindi or English?" means the
        # line or the accent is against us, and a third attempt annoys someone we
        # have not started the actual questionnaire with yet.
        "languageAskCount": 0,
        "formId": form_id,
        "status": STATUS_IN_PROGRESS,
        "answers": inherited,
        "askedItems": [],
        "lastCompletedTurn": -1,
        "lastAdmittedTurn": -1,
        "lastTurnFingerprint": None,
        "lastTurnResponse": None,
        "currentItemId": None,
        "resumeCount": resume_count,
        "priorCallIds": prior,
        # Protocol state. Initialised as empty containers so the update
        # expressions elsewhere can assume the attributes exist.
        "pendingFollowUps": pending_followups,
        "followUpAnswers": followup_answers,
        "declaredConditions": declared,
        # Questions we GAVE UP on after an explanation and a re-ask (the give-up
        # ladder in the orchestrator). Each entry is {ref,label?,reason?}. A
        # non-empty list makes the closing tell the caller a person will call to
        # complete them; nothing else reads it.
        "humanFollowUpItems": [],
        **obligations,
        "repeatCounts": {},
        "createdAt": ts,
        "updatedAt": ts,
    }
    _ddb.Table(TBL_CALL_SESSION).put_item(Item=to_ddb(session))

    if resume_from:
        # Retire the old session so it is not picked up again by a later callback.
        mark_status(resume_from["callId"], STATUS_ABANDONED, note=f"superseded by {call_id}")

    return session


def get_session(call_id: str) -> dict | None:
    # Conversation decisions routinely read immediately after a write (for
    # example, the turn that completes the final protocol follow-up must drain
    # the next deferred condition). An eventually consistent read can briefly
    # return the old queue and strand that obligation until another turn, or
    # forever when the questionnaire has ended. Key reads are low-volume and
    # correctness-critical, so always request the latest committed state.
    r = _ddb.Table(TBL_CALL_SESSION).get_item(
        Key={"callId": call_id}, ConsistentRead=True)
    return from_ddb(r.get("Item")) if r.get("Item") else None


def mark_status(call_id: str, status: str, note: str | None = None) -> None:
    expr = "SET #s = :s, updatedAt = :u"
    vals: dict[str, Any] = {":s": status, ":u": now_iso()}
    names = {"#s": "status"}
    if note:
        expr += ", statusNote = :n"
        vals[":n"] = note
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=to_ddb(vals),
    )


def set_language(call_id: str, language: str, confirmed: bool = True) -> str:
    """Record the language the call will run in. Returns the normalised value.

    Persisted rather than held in the orchestrator, for the same reason answers are:
    the language is part of the call's state, and a call that drops after the
    customer said "Hindi" must come back in Hindi. Before this existed the language
    was fixed at session creation from the CRM and the only in-call adjustment was a
    per-turn local variable that never survived the turn - so the agent could
    "switch to Hindi", say one sentence, and silently revert.
    """
    language = normalise_language(language)
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET #l = :l, languageConfirmed = :c, updatedAt = :u",
        ExpressionAttributeNames={"#l": "language"},
        ExpressionAttributeValues=to_ddb(
            {":l": language, ":c": bool(confirmed), ":u": now_iso()}
        ),
    )
    return language


def pop_identity_reconfirm(call_id: str, item_id: str) -> list:
    """Drop one item from the resume identity-reconfirm queue after it re-verified.

    Called only after an identity item re-verified successfully on a resumed call
    (a wrong name/DOB terminates instead, so draining on success is the only
    path). When the queue empties, the ordinary question flow resumes at the
    first outstanding follow-up or unanswered item. Read-modify-write is safe
    because the turn lease serialises turns for a call. Returns the remaining
    queue.
    """
    sess = get_session(call_id) or {}
    queue = [q for q in (sess.get("identityReconfirmQueue") or []) if q != item_id]
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET identityReconfirmQueue = :q, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":q": queue, ":u": now_iso()}),
    )
    return queue


def record_language_ask(call_id: str) -> int:
    """Count one attempt at asking which language. Returns the new count.

    ADD rather than read-modify-write: a caller talking over the question can put
    two turns in flight, and a lost increment would mean asking forever.
    """
    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="ADD languageAskCount :one SET updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":one": 1, ":u": now_iso()}),
        ReturnValues="UPDATED_NEW",
    )
    return int(from_ddb(r["Attributes"]).get("languageAskCount") or 1)


def set_pending_condition_detail(call_id: str, ref: str, phrase: str) -> None:
    """Remember that we just asked WHICH condition, so the reply gets classified.

    The 13 condition screens (A200-A320) are yes/no questions. A bare "yes" to
    "have you ever been diagnosed with cancer, a tumour, polyp or cyst?" records a
    true answer and names nothing, so there is no phrase to classify and the
    protocol that should follow never opens - the call moves straight to the next
    screen. Every one of those items carries followUpGroup="condition_detail" in the
    form schema, which nothing implemented.

    The specific condition cannot be assumed from the item either. "Cancer" alone
    classifies to C34 in English and C50 in Hinglish - lung or breast decided by
    nothing but nearest-neighbour distance, 0.0037 apart. Guessing the site would
    put a wrong tumour type in an underwriting record and then ask a
    breast-cancer patient about smoking. So the caller is asked, and this field is
    what makes the answer land in the right place instead of being read as a reply
    to whatever the next screen happens to be.

    Cleared as soon as it is consumed, so an unrelated later utterance is never
    retro-fitted onto a condition.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET pendingConditionDetail = :p, updatedAt = :u",
        ExpressionAttributeValues=to_ddb(
            {":p": {"ref": ref, "phrase": phrase}, ":u": now_iso()}
        ),
    )


def set_distress_check_pending(call_id: str, pending: bool) -> None:
    """Mark that we asked one gentle question before escalating a distress signal.

    A mis-transcription ("I saw him suddenly.") caused a clinician to be paged and
    the questionnaire to halt. The model's distress judgement is retained, but an
    irreversible action taken on one unreliable transcript gets a single corroborating
    turn first.

    This flag is what guarantees it stays a single turn. With it set, the next
    distress signal escalates regardless of wording, so the check can never come
    between a person and help.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET distressCheckPending = :p, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":p": bool(pending), ":u": now_iso()}),
    )


def set_explanation_pending(call_id: str, pending: dict | None) -> None:
    """Persist the understanding check after a lay disease explanation.

    The original underwriting question remains unanswered while the caller hears
    the explanation. Persisting its text and reference prevents a dropped call or
    a following "yes" from being mistaken for the medical answer.
    """
    if pending is None:
        _ddb.Table(TBL_CALL_SESSION).update_item(
            Key={"callId": call_id},
            UpdateExpression="REMOVE explanationPending SET updatedAt = :u",
            ExpressionAttributeValues=to_ddb({":u": now_iso()}),
        )
        return
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET explanationPending = :p, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":p": pending, ":u": now_iso()}),
    )


def record_summary_ask(call_id: str, pending: bool) -> int:
    """Count one 'anything to correct?' round and set whether an answer is owed.

    ADD for the count, same reasoning as record_language_ask: two turns in flight
    must not lose an increment, or the closing question is asked forever. The
    count is what bounds the correction loop - the requirement is to close only
    after the caller confirms, but a caller whose answers cannot be parsed twice
    is finalised with what is on record rather than trapped at the end of a call
    they cannot leave.
    """
    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("ADD summaryAskCount :one "
                          "SET summaryAskPending = :p, updatedAt = :u"),
        ExpressionAttributeValues=to_ddb(
            {":one": 1, ":p": bool(pending), ":u": now_iso()}),
        ReturnValues="UPDATED_NEW",
    )
    return int(from_ddb(r["Attributes"]).get("summaryAskCount") or 1)


def clear_summary_ask_pending(call_id: str) -> None:
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET summaryAskPending = :p, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":p": False, ":u": now_iso()}),
    )


def record_declaration(call_id: str) -> str:
    """Record that the caller heard the record read back and confirmed it.

    THE DECLARATION HAS TO BE A FACT, NOT A TEMPLATE. The written transcript we
    send tells the customer they declared the information complete, true and
    accurate - the same assertion the insurer's own telephonic report makes, and
    the thing the policy can later be voided against. Printing that sentence on
    every transcript regardless would make it worthless at exactly the moment it
    matters, and worse, untrue: a call can reach the end without a confirmation,
    because the correction loop is deliberately bounded at two rounds so a caller
    whose corrections cannot be parsed is not trapped.
    
    Inferring it from summaryAskPending being false was the alternative and it is
    not good enough - that flag is also cleared when the loop gives up.

    Timestamped rather than boolean, so the transcript can say WHEN.
    """
    at = now_iso()
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET declarationConfirmedAt = :a, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":a": at, ":u": at}),
    )
    return at


def render_prompt(item: dict, language: str, customer: dict) -> str:
    """The question text in one language, with placeholders filled.

    Lives here rather than in the orchestrator because TWO things now need it and
    they must not disagree: the agent asking the question on the call, and the
    written transcript telling the customer what they were asked. A second copy
    of this in the email Lambda would drift the first time a prompt was reworded,
    and the customer would be asked to verify answers against questions that were
    never put to them.
    """
    key = {"hi": "promptHi", "hinglish": "promptHinglish"}.get(language, "prompt")
    return fill_placeholders(item.get(key) or item.get("prompt") or "", customer)


def fill_placeholders(text: str, customer: dict) -> str:
    """Substitute the {{...}} tokens a schema string may carry.

    SEPARATE FROM render_prompt because the prompt is not the only schema string
    spoken to a caller. The reprompt is too, and it was being spoken RAW: a live
    call on the deployed agent said, out loud,

        "Sorry, I did not catch that. Am I speaking with {{customerName}}?"

    render_prompt held the only copy of this token table, so any other path that
    reached for schema text had no way to fill it and no way to notice. One table,
    one implementation, and a new token cannot be added to one caller and silently
    missed by the other.
    """
    for token, value in (
        ("{{customerName}}", customer.get("customerName") or ""),
        ("{{customerEmail}}", customer.get("customerEmail") or ""),
        ("{{policyNumber}}", customer.get("policyNumber") or ""),
        ("{{insurerName}}", customer.get("insurerName") or "your insurer"),
    ):
        text = text.replace(token, str(value))
    return text.strip()


def mark_noise_phrase(call_id: str, phrase: str) -> None:
    """Remember that a phrase came from a question that cannot ask about a disease.

    A drug name, a habit, a test result or a yes/no. The phrase is still harvested,
    still deferred and still classified - a caller can volunteer a real condition
    in the answer to anything, and dropping phrases by question was measured to
    break two suites - but if classification FAILS, that failure is not doubt about
    a diagnosis and must not be shown to the customer as such.

    Durable rather than per-turn because the deferred queue is drained several
    turns after the phrase was spoken, and by then the question that produced it is
    long gone.
    """
    text = str(phrase or "").strip()
    if not text:
        return
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("SET noisePhrases = list_append("
                          "      if_not_exists(noisePhrases, :empty), :p), "
                          "    updatedAt = :u"),
        ExpressionAttributeValues=to_ddb({":empty": [], ":p": [text],
                                          ":u": now_iso()}),
    )


def defer_mention(call_id: str, phrase: str) -> None:
    """Park a condition mentioned while another condition's protocol is running.

    The conversation is LOCKED to one condition at a time: a caller working
    through their cancer follow-ups who adds "aur mujhe sugar bhi hai" is not
    changing the subject, they are queueing the next one. Classifying it
    immediately used to park a confirmation that OUTRANKED the running
    protocol's follow-ups (next_question serves confirmations first), so the
    dialogue lurched mid-protocol to a different disease - the caller
    experienced it as the bot losing the thread. Deferred mentions are
    classified when the current protocol drains.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("SET deferredMentions = list_append("
                          "if_not_exists(deferredMentions, :empty), :m), "
                          "updatedAt = :u"),
        ExpressionAttributeValues=to_ddb(
            {":empty": [], ":m": [phrase[:300]], ":u": now_iso()}),
    )


def pop_deferred_mention(call_id: str) -> str | None:
    """Take the oldest deferred mention, or None."""
    session = get_session(call_id)
    queue = list((session or {}).get("deferredMentions") or [])
    if not queue:
        return None
    head, rest = queue[0], queue[1:]
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET deferredMentions = :q, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":q": rest, ":u": now_iso()}),
    )
    return head


def add_human_followup(call_id: str, ref: str, label: str | None = None,
                       reason: str | None = None) -> None:
    """Record a question we GAVE UP on, for a person to complete later.

    Appended by the orchestrator's give-up ladder when a follow-up or form item
    the caller could not answer is skipped after an explanation and a re-ask. The
    closing reads a non-empty list back as "someone will call you to complete a
    few more questions", so this is the single source of that promise. Idempotent
    per ref - the same skipped question is never listed twice - which the turn
    lease makes safe against the read-then-write here.
    """
    if not ref:
        return
    session = get_session(call_id) or {}
    existing = [e for e in (session.get("humanFollowUpItems") or [])
                if isinstance(e, dict)]
    if any(e.get("ref") == ref for e in existing):
        return
    entry: dict[str, Any] = {"ref": ref}
    if label:
        entry["label"] = str(label)[:120]
    if reason:
        entry["reason"] = str(reason)[:300]
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("SET humanFollowUpItems = list_append("
                          "if_not_exists(humanFollowUpItems, :empty), :e), "
                          "updatedAt = :u"),
        ExpressionAttributeValues=to_ddb(
            {":empty": [], ":e": [entry], ":u": now_iso()}),
    )


def retract_condition(call_id: str, icd10: str, phrase: str, reason: str) -> dict:
    """Withdraw a declared condition on the caller's explicit say-so.

    From a live call: a deferred breathlessness phrase was classified as
    Uterine Fibroid, confirmed by a confused caller, and their later explicit
    "फाइब्रॉइड नहीं है मुझे" had no path to act on - the protocol kept asking
    fibroid questions at someone who had just denied having one.

    The declared entry MOVES to retractedConditions rather than vanishing: an
    underwriter must be able to see that a condition was declared, confirmed,
    and then withdrawn, in that order. The remaining pending follow-ups for the
    condition are dropped, already-given answers are kept for the audit trail,
    and the code joins rejectedCodes so the classifier cannot re-offer it.
    """
    session = get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")

    declared = list(session.get("declaredConditions") or [])
    keep = [row for row in declared if row.get("icd10") != icd10]
    moved = [row for row in declared if row.get("icd10") == icd10]
    for row in moved:
        row["retractedAt"] = now_iso()
        row["retractionPhrase"] = phrase[:300]
        row["retractionReason"] = reason[:300]
    pending = [row for row in (session.get("pendingFollowUps") or [])
               if row.get("icd10") != icd10]
    rejected = list(session.get("rejectedCodes") or [])
    if icd10 not in rejected:
        rejected.append(icd10)

    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=(
            "SET declaredConditions = :d, pendingFollowUps = :p, "
            "    rejectedCodes = :r, "
            "    retractedConditions = list_append("
            "      if_not_exists(retractedConditions, :empty), :m), "
            "    updatedAt = :u"
        ),
        ExpressionAttributeValues=to_ddb({
            ":d": keep, ":p": pending, ":r": rejected,
            ":empty": [], ":m": moved, ":u": now_iso(),
        }),
        ReturnValues="ALL_NEW",
    )
    return from_ddb(r["Attributes"])


def supersede_condition(call_id: str, superseded_icd10: str,
                        surviving_icd10: str, reason: str) -> dict:
    """One disclosure was classified twice. Keep the better-evidenced code.

    From a live resumed call, both of these on one record from one sentence:

        F21  Schizotypal disorder   said: "हाँ, स्किजोफ्रेनिया है मुझे"   0.8221
        F20  Schizophrenia          said: "स्किजोफ्रेनिया"                0.8888

    The first leg classified the disclosure before the Devanagari synonym for F20
    was indexed and banked F21 in pendingFollowUps. The resume drained that queue,
    then reclassified the same words against the patched index, got F20, and minted
    it as a second condition. The caller answered the same three follow-ups twice
    and heard both diagnoses read back to her.

    NOT retract_condition, and the difference is material. A retraction is the
    CALLER withdrawing a disclosure; recording this as one would tell an
    underwriter that she denied having schizophrenia, which is false. This is us
    correcting our own classification of something she never stopped saying. So the
    superseded row moves to its own list, with what replaced it and why.

    THE ANSWERS ARE COPIED, NOT MOVED. The superseded code's follow-up answers stay
    under their original keys for the audit trail, and are also copied onto the
    surviving code where it has none - which is what stops the caller being asked
    the same three questions a second time. Any queued follow-up the copy has just
    answered is dropped from the queue for the same reason.

    rejectedCodes is deliberately NOT touched: the caller rejected nothing. The
    re-mint loop is closed by the guard in the orchestrator instead - a later
    classification of the same phrase finds the surviving code and is resolved
    against it by evidence, which is deterministic and reaches the same answer
    every time.
    """
    session = get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")

    declared = list(session.get("declaredConditions") or [])
    keep = [row for row in declared if row.get("icd10") != superseded_icd10]
    moved = [row for row in declared if row.get("icd10") == superseded_icd10]
    if not moved:
        return session
    for row in moved:
        row["supersededAt"] = now_iso()
        row["supersededBy"] = surviving_icd10
        row["supersessionReason"] = str(reason)[:300]

    answers = dict(session.get("followUpAnswers") or {})
    for key, rec in list(answers.items()):
        if not isinstance(rec, dict) or rec.get("icd10") != superseded_icd10:
            continue
        new_key = f"{surviving_icd10}::{rec.get('questionId')}"
        if new_key in answers:
            continue
        carried = dict(rec)
        carried["icd10"] = surviving_icd10
        carried["carriedFrom"] = key
        answers[new_key] = carried

    # Everything the surviving code now has an answer for, including what was just
    # copied across.
    answered = {rec.get("questionId") for rec in answers.values()
                if isinstance(rec, dict) and rec.get("icd10") == surviving_icd10}
    pending = [
        row for row in (session.get("pendingFollowUps") or [])
        if not (row.get("icd10") == superseded_icd10
                or (row.get("icd10") == surviving_icd10
                    and row.get("questionId") in answered))
    ]

    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=(
            "SET declaredConditions = :d, pendingFollowUps = :p, "
            "    followUpAnswers = :fa, "
            "    supersededConditions = list_append("
            "      if_not_exists(supersededConditions, :empty), :m), "
            "    updatedAt = :u"
        ),
        ExpressionAttributeValues=to_ddb({
            ":d": keep, ":p": pending, ":fa": answers,
            ":empty": [], ":m": moved, ":u": now_iso(),
        }),
        ReturnValues="ALL_NEW",
    )
    return from_ddb(r["Attributes"])


def add_rejected_code(call_id: str, icd10: str) -> None:
    """Remember that the caller explicitly rejected this ICD-10 read-back.

    What makes re-classification DETERMINISTIC instead of circular: a caller who
    says "cancer", rejects the breast-cancer guess, and says "cancer" again would
    get the identical nearest-neighbour answer forever, because the classifier is
    a pure function of the phrase. The rejected set is the memory that breaks the
    cycle - classification skips these codes and moves to the next candidate the
    graph corroborates.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("SET rejectedCodes = list_append("
                          "if_not_exists(rejectedCodes, :empty), :c), "
                          "updatedAt = :u"),
        ExpressionAttributeValues=to_ddb(
            {":empty": [], ":c": [icd10], ":u": now_iso()}),
    )


def set_med_check(call_id: str, pending: dict | None,
                  mark_asked: str | None = None) -> None:
    """Set or clear the one-shot medication verification.

    `pending` holds the treatment answer being verified ({ref, med, icd10, value})
    while the caller is asked "are you sure?" - the answer is deliberately NOT
    recorded yet, because the point of asking is that it might be wrong.

    `mark_asked` remembers the follow-up ref permanently, which is what makes the
    check ONE-shot per question: a caller who insists on an unexpected medicine is
    believed and flagged, never asked twice. Nobody should have to defend their
    prescription to a machine.
    """
    if pending is not None:
        _ddb.Table(TBL_CALL_SESSION).update_item(
            Key={"callId": call_id},
            UpdateExpression="SET medCheckPending = :p, updatedAt = :u",
            ExpressionAttributeValues=to_ddb({":p": pending, ":u": now_iso()}),
        )
        return
    expr = "REMOVE medCheckPending SET updatedAt = :u"
    vals: dict = {":u": now_iso()}
    if mark_asked:
        expr += (", medChecksAsked = list_append("
                 "if_not_exists(medChecksAsked, :empty), :ref)")
        vals[":empty"] = []
        vals[":ref"] = [mark_asked]
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=expr,
        ExpressionAttributeValues=to_ddb(vals),
    )


def set_refusal_confirm_pending(call_id: str, pending: bool) -> None:
    """Mark that we have asked "would you like to stop?" and are awaiting the answer.

    A live call ended on the transcript "I'm going to go." That may have been a
    refusal; the same call also turned ordinary answers into "Seaman.", "Enceladus?"
    and "And so then.", so it may not have been. Ending a medical intake call on one
    ambiguous transcript is not recoverable - the caller has to be rung back and
    starts over - whereas asking once costs a single turn.

    The flag is what stops that check becoming a loop: with it set, a second refusal
    is taken at face value and the call ends. Nobody should have to argue with a
    machine to get off the phone.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET refusalConfirmPending = :p, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":p": bool(pending), ":u": now_iso()}),
    )


def clear_pending_condition_detail(call_id: str) -> None:
    """Consume the pending condition-detail probe."""
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="REMOVE pendingConditionDetail SET updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":u": now_iso()}),
    )


def attach_call_identifiers(call_id: str, transaction_id: str | None, sip_call_id: str | None) -> None:
    """Record the Chime SDK identifiers so a live call can be looked up later."""
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET transactionId = :t, sipCallId = :c, updatedAt = :u",
        ExpressionAttributeValues=to_ddb(
            {":t": transaction_id or "", ":c": sip_call_id or "", ":u": now_iso()}
        ),
    )


def set_archive_uris(call_id: str, transcript_uri: str | None = None,
                     recording_uri: str | None = None) -> None:
    """Point the session at this call's durable S3 archive.

    These two keys are the ONLY input to Salesforce's Recording_URI__c and
    Transcript_URI__c - lambdas/crm_writeback copies them straight across - so
    writing them here is what turns a permanently blank pair of CRM fields into a
    link an underwriter can follow. Written BEFORE the writeback runs, which is
    why the archive happens at finalisation rather than after it.

    Each field is set only when supplied, so archiving a transcript without a
    recording (the browser leg records no audio) cannot blank out a recording URI
    a telephony leg had already stored.
    """
    sets, vals = ["updatedAt = :u"], {":u": now_iso()}
    if transcript_uri:
        sets.append("transcriptUri = :t")
        vals[":t"] = transcript_uri
    if recording_uri:
        sets.append("recordingUri = :r")
        vals[":r"] = recording_uri
    if len(sets) == 1:
        return
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeValues=to_ddb(vals),
    )


# --------------------------------------------------------------------------- #
# per-turn checkpoint
# --------------------------------------------------------------------------- #
def record_turn(
    call_id: str,
    turn_index: int,
    role: str,
    item_id: str | None = None,
    text: str | None = None,
    audio_key: str | None = None,
    stt_confidence: float | None = None,
    extracted: Any = None,
    valid: bool | None = None,
    validation_error: str | None = None,
    extra: dict | None = None,
) -> None:
    """Append one turn to hvi-call-state. Ordered replay of a call comes free."""
    item: dict[str, Any] = {
        "callId": call_id,
        "turnIndex": turn_index,
        "role": role,
        "timestamp": now_iso(),
        "expiresAt": int(time.time()) + TURN_TTL_DAYS * 86400,
    }
    if item_id is not None:
        item["itemId"] = item_id
    if text is not None:
        item["text"] = text
    if audio_key is not None:
        item["audioKey"] = audio_key
    if stt_confidence is not None:
        item["sttConfidence"] = stt_confidence
    if extracted is not None:
        item["extractedValue"] = extracted
    if valid is not None:
        item["valid"] = valid
    if validation_error:
        item["validationError"] = validation_error
    if extra:
        item.update(extra)

    _ddb.Table(TBL_CALL_STATE).put_item(Item=to_ddb(item))


def get_turns(call_id: str) -> list[dict]:
    r = _ddb.Table(TBL_CALL_STATE).query(
        KeyConditionExpression=Key("callId").eq(call_id),
        ScanIndexForward=True,
    )
    return [from_ddb(i) for i in r.get("Items", [])]


def annotate_turn(
    call_id: str,
    turn_index: int,
    item_id: str | None = None,
    extracted: Any = None,
    valid: bool | None = None,
    validation_error: str | None = None,
    text: str | None = None,
) -> None:
    """
    Attach the parsed answer to an already-recorded turn.

    The turn row is written when the utterance arrives, so the transcript is
    complete even for utterances that do not map to a form answer. When the agent
    later decides what the utterance meant, that interpretation is added here
    rather than written as a second row. One row per thing actually said.
    """
    sets, vals = ["annotatedAt = :a"], {":a": now_iso()}
    if text is not None:
        # UpdateItem creates the row if it is absent, so seed the fields that a
        # normally-recorded turn would already carry. Otherwise a direct
        # save_answer call outside a live turn leaves a row with no transcript.
        sets.append("#t = if_not_exists(#t, :txt)")
        vals[":txt"] = text
        sets.append("#r = if_not_exists(#r, :role)")
        vals[":role"] = "customer"
        sets.append("#ts = if_not_exists(#ts, :now)")
        vals[":now"] = now_iso()
        sets.append("expiresAt = if_not_exists(expiresAt, :exp)")
        vals[":exp"] = int(time.time()) + TURN_TTL_DAYS * 86400
    if item_id is not None:
        sets.append("itemId = :i")
        vals[":i"] = item_id
    if extracted is not None:
        sets.append("extractedValue = :e")
        vals[":e"] = extracted
    if valid is not None:
        sets.append("valid = :v")
        vals[":v"] = valid
    if validation_error:
        sets.append("validationError = :err")
        vals[":err"] = validation_error

    names = {}
    if text is not None:
        # text, role and timestamp are all reserved-ish or clash with keywords.
        names = {"#t": "text", "#r": "role", "#ts": "timestamp"}

    kwargs: dict[str, Any] = {
        "Key": {"callId": call_id, "turnIndex": turn_index},
        "UpdateExpression": "SET " + ", ".join(sets),
        "ExpressionAttributeValues": to_ddb(vals),
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names

    _ddb.Table(TBL_CALL_STATE).update_item(**kwargs)


def save_answer(call_id: str, item_id: str, value: Any, turn_index: int) -> dict:
    """
    Commit one answer and advance the checkpoint atomically-ish.

    answers.<itemId> is set, the item is appended to askedItems, and
    lastCompletedTurn moves forward. This single UpdateItem is the checkpoint a
    dropped call resumes from.
    """
    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=(
            "SET answers.#iid = :v, "
            "    askedItems = list_append(if_not_exists(askedItems, :empty), :one), "
            "    lastCompletedTurn = :t, "
            "    currentItemId = :iid_s, "
            "    updatedAt = :u"
        ),
        ExpressionAttributeNames={"#iid": item_id},
        ExpressionAttributeValues=to_ddb(
            {
                ":v": value,
                ":empty": [],
                ":one": [item_id],
                ":t": turn_index,
                ":iid_s": item_id,
                ":u": now_iso(),
            }
        ),
        ReturnValues="ALL_NEW",
    )
    return from_ddb(r["Attributes"])


def set_current_item(call_id: str, item_id: str | None) -> None:
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET currentItemId = :i, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":i": item_id, ":u": now_iso()}),
    )


# --------------------------------------------------------------------------- #
# condition follow-up queue
#
# When a condition is declared, Neptune returns the follow-ups the protocol
# requires. Those are queued ON THE SESSION rather than held in memory, for the
# same reason answers are: if the line drops after "yes I have diabetes" but
# before the follow-ups are asked, the resumed call must still owe them. Holding
# the queue in the agent process would silently lose that obligation.
# --------------------------------------------------------------------------- #
def queue_followups(
    call_id: str,
    icd10: str,
    condition_label: str,
    questions: list[dict],
    protocol_source: str,
    patient_phrase: str | None = None,
    confidence: float | None = None,
    corroborated: bool | None = None,
    confirmed_by_patient: bool | None = None,
) -> dict:
    """Append protocol-mandated follow-ups to the session's pending queue."""
    pending = [
        {
            "icd10": icd10,
            "conditionLabel": condition_label,
            "questionId": q.get("questionId"),
            "prompt": q.get("prompt"),
            "answerType": q.get("answerType", "text"),
            "mandatory": bool(q.get("mandatory")),
            "sensitivity": q.get("sensitivity"),
            "clinicalNote": q.get("clinicalNote"),
            "formField": q.get("formField"),
            "protocolSource": protocol_source,
            # Carried on every follow-up so the question can name the condition it
            # belongs to. The graph writes prompts like "What treatment or
            # medication are you taking for it?", and "it" is unambiguous only in
            # the graph - on a call, arriving after an unrelated screen, the caller
            # genuinely does not know which condition is meant and asks "For what?".
            # Stored per follow-up rather than looked up later so a queue holding
            # two protocols cannot attribute a question to the wrong one.
            "conditionLabel": condition_label,
            "icd10": icd10,
        }
        for q in questions
    ]
    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=(
            "SET pendingFollowUps = list_append("
            "      if_not_exists(pendingFollowUps, :empty), :new), "
            "    declaredConditions = list_append("
            "      if_not_exists(declaredConditions, :empty), :cond), "
            "    updatedAt = :u"
        ),
        ExpressionAttributeValues=to_ddb({
            ":empty": [],
            ":new": pending,
            # patientPhrase and confidence are recorded HERE, at the moment of
            # classification, because this is the only point where both are known.
            # They are the difference between an underwriting record that says
            # "E11" and one that says the patient said "haan mujhe sugar hai" and
            # that it was mapped to E11 at 0.94 - the first asks an underwriter to
            # trust the mapping blindly, the second lets them check it.
            # HOW THE CODE WAS ESTABLISHED, not just how well it scored.
            #
            # confidence alone cannot answer the question an underwriter actually
            # has, which is "should I trust this code?". A similarity score is
            # only a distance to the nearest thing in the index, and the index
            # always returns something - so 0.80 means "closest" and not
            # "correct". These two fields record the provenance instead:
            #
            #   corroborated        an independent method - the curated clinical
            #                       graph - reached the same code from a known
            #                       synonym. Two methods agreeing is evidence.
            #   confirmedByPatient  the interpretation was read back and the
            #                       caller said yes.
            #
            # The pair matters because a caller can agree to a wrong guess. On a
            # live call "पांच साल पहले ब्रॉन्काइटिस" (bronchitis) was offered COPD,
            # then chronic kidney disease, then CHRONIC VIRAL HEPATITIS - and by
            # the third she said yes, so B18 went onto her record at 0.7959 with
            # nothing to distinguish it from a graph-corroborated match. Recording
            # corroborated=False is what lets the writeback route that to a human
            # instead of stamping the file clean.
            ":cond": [{"icd10": icd10, "label": condition_label,
                       "protocolSource": protocol_source,
                       "patientPhrase": patient_phrase,
                       "confidence": confidence,
                       "corroborated": corroborated,
                       "confirmedByPatient": confirmed_by_patient,
                       "at": now_iso()}],
            ":u": now_iso(),
        }),
        ReturnValues="ALL_NEW",
    )
    return from_ddb(r["Attributes"])


def provenance_review_rows(session: dict) -> list[dict]:
    """Review rows for declared conditions the clinical graph never corroborated.

    WHY THIS LIVES HERE RATHER THAN IN BOTH REPORT BUILDERS
    generate_summary and the Salesforce writeback must reach the same verdict, and
    the two disagreeing is the failure mode this codebase already warns about
    twice. Two copies of a rule stay identical only until someone edits one, and a
    behavioural test cannot catch that because it exercises one builder at a time.
    Both import hvi_state - crm_writeback is packaged with this exact file - so a
    single shared function makes the agreement structural instead of aspirational.

    WHAT IT CATCHES
    Review used to be triggered only by a mention we FAILED to code. The more
    dangerous case is the one we succeeded at unsafely: an embedding match the
    curated graph could not confirm, which the caller then agreed to.

    Measured, on a live call: "पांच साल पहले ब्रॉन्काइटिस" - bronchitis - was
    offered COPD (no), chronic kidney disease (no), then CHRONIC VIRAL HEPATITIS,
    and by the third guess she said yes. B18 was written at 0.7959 with four
    hepatitis follow-ups behind it, unclassifiedMentions empty, and the file
    stamped as needing no second look. Read-back is a real guard and it caught the
    first two guesses, but a tiring caller defeats it - so agreement cannot be the
    only evidence that survives into the record.

    `is not False` is deliberate and NOT a falsy test. Rows written before
    provenance existed carry None, meaning "unknown", and retroactively asserting
    those were uncorroborated would flag historical intakes on an inference.
    """
    rows: list[dict] = []
    for d in session.get("declaredConditions") or []:
        if d.get("corroborated") is not False:
            continue
        score = d.get("confidence")
        detail = f" at {float(score):.2f}" if isinstance(score, (int, float)) else ""
        agreed = (" The caller did confirm the read-back."
                  if d.get("confirmedByPatient") else "")
        rows.append({
            "patientPhrase": d.get("patientPhrase"),
            "reason": (f"{d.get('icd10')} was matched on embedding similarity"
                       f"{detail} and the clinical graph did not corroborate it."
                       f"{agreed} Confirm the code before underwriting."),
            "at": d.get("at"),
            "resolvedTo": d.get("icd10"),
        })
    return rows


def classified_by(declared_row: dict) -> str:
    """How a declared condition was reached, without overstating the evidence.

    This was the constant "neptune-graph+opensearch-knn" on every row, which reads
    as "two independent methods agreed" - precisely the claim an underwriter would
    rely on to accept a code without checking. On an uncorroborated match only the
    embeddings spoke, so the string has to say so.
    """
    corroborated = declared_row.get("corroborated")
    if corroborated is True:
        return "neptune-graph+opensearch-knn"
    if corroborated is False:
        return "opensearch-knn (not corroborated by the graph)"
    return "neptune-graph+opensearch-knn (provenance not recorded)"


def record_unclassified_mention(call_id: str, phrase: str, item_id: str | None,
                                reason: str = "no confident ICD-10 match",
                                kind: str = "unidentified") -> dict:
    """Durably record a health mention the classifier could not resolve.

    WHY THIS EXISTS, AND WHY IT IS A SAFETY FIX RATHER THAN A FEATURE
    The knowledge graph covers a fixed set of conditions. A patient who declares
    something outside it - sarcoidosis, a rare autoimmune disease, a surgery
    phrased unusually - used to leave only a transient per-turn note. The free-text
    answer was saved, but no condition was declared, so the writeback counted zero
    conditions and stamped the underwriting record "clear".

    That is the wrong direction of error for a medical intake. The flag exists to
    route a human underwriter's attention, and the one case that most needs a human
    is the one the machine could not interpret. Recorded here so it survives the
    call, reaches Salesforce, and forces a review.

    Deliberately keeps the patient's exact words: an underwriter can often read
    "mujhe sarcoidosis hai" and classify it instantly, where the embedding could
    not.

    DEDUPLICATED ON PHRASE. One live call recorded 'salbutamol inhaler' six
    times, four of them inside two seconds, because a re-asked question
    re-processes the same utterance and each attempt classified it again. The
    duplicates carried no new information and made the review list look longer
    and less credible than the single real item buried in it. The FIRST
    occurrence is kept, since its timestamp is when the caller actually said it.
    """
    existing = get_session(call_id) or {}
    already = {str((m or {}).get("phrase") or "").strip().casefold()
               for m in (existing.get("unclassifiedMentions") or [])}
    if str(phrase or "").strip().casefold() in already:
        # Return the record unchanged rather than raising: the caller's intent
        # ("make sure a human sees this phrase") is already satisfied.
        return existing

    # A phrase the orchestrator already knew could not be a diagnosis - it came
    # from a question asking for a drug, a habit, a date or a yes/no. Still
    # recorded, because an underwriter reading the trail should see everything the
    # classifier was given, but not offered to the customer as a condition we
    # failed to identify. Only downgrades the DEFAULT kind, so an explicit
    # "elaboration" or "ungoverned" is never overwritten.
    if kind == "unidentified":
        noisy = {str(p or "").strip().casefold()
                 for p in (existing.get("noisePhrases") or [])}
        if str(phrase or "").strip().casefold() in noisy:
            kind = "noise"
    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=(
            "SET unclassifiedMentions = list_append("
            "      if_not_exists(unclassifiedMentions, :empty), :m), "
            "    updatedAt = :u"
        ),
        ExpressionAttributeValues=to_ddb({
            ":empty": [],
            ":m": [{"phrase": phrase, "itemId": item_id, "reason": reason,
                    # "unidentified" means the classifier could not resolve it and
                    # the customer is invited to correct us. "elaboration" means it
                    # WAS resolved, to a condition already on the record, and the
                    # row exists for the underwriter rather than as an admission of
                    # doubt. The customer-facing letter shows only the former.
                    "kind": kind, "at": now_iso()}],
            ":u": now_iso(),
        }),
        ReturnValues="ALL_NEW",
    )
    return from_ddb(r["Attributes"])


def peek_followup(session: dict) -> dict | None:
    q = session.get("pendingFollowUps") or []
    return q[0] if q else None


def complete_followup(call_id: str, value: Any) -> dict:
    """
    Record the answer to the head of the queue and drop it.

    Read-modify-write rather than a DynamoDB list REMOVE, because the answer and
    the removal have to land together. If they diverged, a resumed call would
    either re-ask an answered follow-up or drop one entirely.
    """
    session = get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")

    queue = list(session.get("pendingFollowUps") or [])
    if not queue:
        return session
    head, rest = queue[0], queue[1:]

    answers = dict(session.get("followUpAnswers") or {})
    key = f"{head.get('icd10')}::{head.get('questionId')}"
    answers[key] = {
        "value": value,
        "questionId": head.get("questionId"),
        "icd10": head.get("icd10"),
        "prompt": head.get("prompt"),
        "protocolSource": head.get("protocolSource"),
        # Which schema field this answer populates, carried from the graph's own
        # declaration. This is what lets the form skip a child item the protocol
        # already collected - both FU_DIAG_WHEN and item A520 declare
        # formField "since", and without this link the caller is asked twice and
        # says so: "एक साल पहले बोला था तो?"
        "formField": head.get("formField"),
        "answeredAt": now_iso(),
    }

    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression=("SET pendingFollowUps = :rest, "
                          "    followUpAnswers = :fa, updatedAt = :u"),
        ExpressionAttributeValues=to_ddb({":rest": rest, ":fa": answers,
                                          ":u": now_iso()}),
        ReturnValues="ALL_NEW",
    )
    return from_ddb(r["Attributes"])


def set_awaiting_confirmation(call_id: str, payload: dict | None) -> None:
    """
    Park a classification that needs reading back to the patient.

    Persisted so a drop mid-confirmation does not quietly accept an
    unconfirmed red-flag interpretation on the resumed call.
    """
    _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET awaitingConfirmation = :c, updatedAt = :u",
        ExpressionAttributeValues=to_ddb({":c": payload, ":u": now_iso()}),
    )


def record_repeat(call_id: str, item_id: str) -> int:
    """
    Count consecutive failed attempts on one question and return the new count.
    The orchestrator gives up after three rather than looping forever.
    """
    r = _ddb.Table(TBL_CALL_SESSION).update_item(
        Key={"callId": call_id},
        UpdateExpression="SET repeatCounts.#k = if_not_exists(repeatCounts.#k, :z) + :one, updatedAt = :u",
        ExpressionAttributeNames={"#k": item_id},
        ExpressionAttributeValues=to_ddb({":z": 0, ":one": 1, ":u": now_iso()}),
        ReturnValues="ALL_NEW",
    )
    return int(from_ddb(r["Attributes"]).get("repeatCounts", {}).get(item_id, 1))


def clear_repeats(call_id: str, item_id: str) -> None:
    try:
        _ddb.Table(TBL_CALL_SESSION).update_item(
            Key={"callId": call_id},
            UpdateExpression="REMOVE repeatCounts.#k",
            ExpressionAttributeNames={"#k": item_id},
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# completion
# --------------------------------------------------------------------------- #
def finalize_session(call_id: str, mark_completed: bool = True) -> dict:
    """
    Freeze the collected answers into hvi-intake-responses. By default the
    session is marked completed for direct callers; CRM submission passes
    ``mark_completed=False`` and marks it only after synchronous writeback is
    confirmed, so a failed dispatch remains resumable.
    """
    session = get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")

    items = get_form_items(session["formId"])
    by_id = {i["itemId"]: i for i in items}
    answers = session.get("answers") or {}

    structured = []
    for iid, val in sorted(answers.items()):
        meta = by_id.get(iid, {})
        structured.append(
            {
                "itemId": iid,
                "label": meta.get("label", iid),
                "section": meta.get("section", "unknown"),
                "question": meta.get("prompt", ""),
                "answer": val,
                "answerType": meta.get("answerType", "text"),
            }
        )

    record = {
        "customerId": session["customerId"],
        "callId": call_id,
        "formId": session["formId"],
        "phone": session.get("phone"),
        "customerName": session.get("customerName"),
        "customerEmail": session.get("customerEmail"),
        "submittedAt": now_iso(),
        "resumeCount": session.get("resumeCount", 0),
        "priorCallIds": session.get("priorCallIds", []),
        "answerCount": len(structured),
        "responses": structured,
    }
    _ddb.Table(TBL_RESPONSES).put_item(Item=to_ddb(record))
    if mark_completed:
        mark_status(call_id, STATUS_COMPLETED, note="intake complete")
    return record
