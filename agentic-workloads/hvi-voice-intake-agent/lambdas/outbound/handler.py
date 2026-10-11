"""
Places the outbound call, but only after the compliance gates pass.

The mentor's line was "that call will only trigger when everything is checked and
confirmed", and the regulatory exposure is real, so every gate is a HARD block
with its decision written to hvi-gate-audit. A skipped check must be visible
afterwards, not silently absent.

Gates, in order:

  1. policy valid        customer exists, phone present, intake still outstanding
  2. DPDP consent        an ACTIVE consent record predating the call
  3. TRAI time window    09:00-21:00 IST only. Outside it the call is deferred,
                         not attempted
  4. TRAI attempt cap    at most 3 attempts per customer per day
  5. DND / NCPR          registry check before every attempt
  6. 48h SLA             informational: flags breach risk, does not block

This is a Lambda plus EventBridge Scheduler rather than a Step Functions state
machine. The mentor pushed back on Step Functions for agent logic, and he was
right about that - but the gate is not agent logic, it is an authorisation check
that has to leave evidence. Step Functions would give the 2-year execution
history for free; here the audit table serves that purpose explicitly, which is
also easier to query when a regulator asks a specific question.
"""
from __future__ import annotations

import base64 as _b64
import datetime as _dt
import json
import logging
import os
import sys
import uuid
import zoneinfo

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
import hvi_state as st  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("hvi-outbound")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
TELEPHONY_REGION = os.environ.get("TELEPHONY_REGION", "us-east-1")
CALLER_ID = os.environ["OUTBOUND_CALLER_ID"]

# SMA_ID is no longer used to place calls and is kept only so an older
# deployment's environment does not fail at import. See _originate for why.
SMA_ID = os.environ.get("SMA_ID", "")

# The Asterisk bridge that owns the RTP. Resolved by tag if not given.
ASTERISK_INSTANCE_ID = os.environ.get("ASTERISK_INSTANCE_ID", "")
PREFIX = os.environ.get("PREFIX", "hvi")
CALL_DIR = os.environ.get("HVI_CALL_DIR", "/var/lib/hvi/calls")
PENDING_DIR = os.environ.get("HVI_PENDING_DIR", "/var/lib/hvi/pending")
TBL_AUDIT = os.environ.get("TBL_AUDIT", "hvi-gate-audit")
TBL_ATTEMPTS = os.environ.get("TBL_ATTEMPTS", "hvi-call-attempts")
TBL_CONSENT = os.environ.get("TBL_CONSENT", "hvi-consent-ledger")

# The pjsip endpoint calls are ORIGINATED through. Deliberately not "chime", which
# is the endpoint inbound calls arrive on.
#
# One trunk, two endpoints. "chime" carries from_user only; putting callerid on it
# would overwrite the caller ID of INBOUND calls, which once made the agent greet
# the wrong customer and read out their policy number. "chime-out" has no identify
# section, so nothing inbound can select it, and it is therefore free to carry a
# display name - without which the From header goes out as "Anonymous" and the
# destination carrier rejected the call one second after ringing.
TRUNK_ENDPOINT = os.environ.get("TRUNK_ENDPOINT", "chime-out")

CALL_START_HOUR = int(os.environ.get("TRAI_CALL_START_HOUR", "9"))
CALL_END_HOUR = int(os.environ.get("TRAI_CALL_END_HOUR", "21"))
MAX_ATTEMPTS = int(os.environ.get("TRAI_MAX_ATTEMPTS_PER_DAY", "3"))
SLA_HOURS = int(os.environ.get("SLA_HOURS_FROM_PURCHASE", "48"))

# Set true only for demo calls outside the TRAI window. Recorded in the audit
# trail as an explicit override so it can never look like the gate passed.
ALLOW_WINDOW_OVERRIDE = os.environ.get("ALLOW_WINDOW_OVERRIDE", "false").lower() == "true"

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

ddb = boto3.resource("dynamodb", region_name=DATA_REGION)
ssm = boto3.client("ssm", region_name=DATA_REGION)
ec2 = boto3.client("ec2", region_name=DATA_REGION)


def audit(call_ref: str, gate: str, passed: bool, detail: str,
          override: bool = False) -> None:
    """Every gate decision, pass or fail, lands here."""
    try:
        ddb.Table(TBL_AUDIT).put_item(Item=st.to_ddb({
            "callId": call_ref,
            "checkedAt": f"{st.now_iso()}#{gate}",
            "gate": gate,
            "passed": passed,
            "override": override,
            "detail": detail[:900],
        }))
    except Exception as e:
        log.error("could not write gate audit for %s/%s: %s", call_ref, gate, e)


class GateFailure(Exception):
    def __init__(self, gate: str, reason: str, retryable: bool = False,
                 retry_after: str | None = None):
        super().__init__(f"{gate}: {reason}")
        self.gate = gate
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


# --------------------------------------------------------------------------- #
def gate_policy(ref: str, customer: dict | None, phone: str) -> dict:
    if not customer:
        audit(ref, "policy", False, f"no customer for phone {phone}")
        raise GateFailure("policy", "customer not found")
    if not customer.get("phone"):
        audit(ref, "policy", False, "customer has no phone number")
        raise GateFailure("policy", "no phone on record")
    status = customer.get("underwritingStatus")
    if status not in (None, "awaiting-medical-history"):
        audit(ref, "policy", False, f"underwritingStatus={status}")
        raise GateFailure("policy", f"intake not outstanding ({status})")
    audit(ref, "policy", True, f"{customer['customerId']} awaiting medical history")
    return customer


def gate_consent(ref: str, customer_id: str) -> dict:
    """DPDP: explicit consent must exist BEFORE processing. Hard block."""
    r = ddb.Table(TBL_CONSENT).query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("customerId").eq(customer_id),
        ScanIndexForward=False, Limit=5,
    )
    records = [st.from_ddb(i) for i in r.get("Items", [])]
    active = next((c for c in records if c.get("status") == "ACTIVE"), None)
    if not active:
        audit(ref, "consent", False,
              f"no ACTIVE consent among {len(records)} record(s)")
        raise GateFailure("consent", "no active DPDP consent")
    audit(ref, "consent", True,
          f"granted {active.get('grantedAt')} via {active.get('channel')} "
          f"tc={active.get('tcVersion')}")
    return active


def gate_time_window(ref: str) -> _dt.datetime:
    """TRAI: 09:00-21:00 IST. Outside the window the call is deferred."""
    now = _dt.datetime.now(IST)
    if CALL_START_HOUR <= now.hour < CALL_END_HOUR:
        audit(ref, "trai_window", True, f"{now.strftime('%H:%M')} IST is inside the window")
        return now

    nxt = now.replace(hour=CALL_START_HOUR, minute=0, second=0, microsecond=0)
    if now.hour >= CALL_END_HOUR:
        nxt += _dt.timedelta(days=1)

    if ALLOW_WINDOW_OVERRIDE:
        audit(ref, "trai_window", True,
              f"{now.strftime('%H:%M')} IST is OUTSIDE 0{CALL_START_HOUR}:00-{CALL_END_HOUR}:00; "
              "proceeding under ALLOW_WINDOW_OVERRIDE for a demo call",
              override=True)
        log.warning("TRAI window overridden for a demo call at %s IST", now.strftime("%H:%M"))
        return now

    audit(ref, "trai_window", False,
          f"{now.strftime('%H:%M')} IST outside the window; defer to {nxt.isoformat()}")
    raise GateFailure("trai_window", f"outside 09:00-21:00 IST",
                      retryable=True, retry_after=nxt.isoformat())


def gate_attempts(ref: str, customer_id: str, now_ist: _dt.datetime) -> int:
    """TRAI: at most 3 attempts per customer per day."""
    day = now_ist.strftime("%Y-%m-%d")
    r = ddb.Table(TBL_ATTEMPTS).get_item(
        Key={"customerId": customer_id, "attemptDate": day})
    count = int(st.from_ddb(r.get("Item") or {}).get("attempts", 0))
    if count >= MAX_ATTEMPTS:
        audit(ref, "attempt_cap", False,
              f"{count} attempts already today, cap is {MAX_ATTEMPTS}")
        nxt = (now_ist + _dt.timedelta(days=1)).replace(
            hour=CALL_START_HOUR, minute=0, second=0, microsecond=0)
        raise GateFailure("attempt_cap", f"{count} attempts today",
                          retryable=True, retry_after=nxt.isoformat())
    audit(ref, "attempt_cap", True, f"attempt {count + 1} of {MAX_ATTEMPTS} today")
    return count


def gate_dnd(ref: str, customer: dict) -> None:
    """
    TRAI DND / NCPR check before every attempt. A violation is Rs 1,00,000.

    The seeded CRM record carries dndRegistered as a stand-in for the NCPR API.
    In production this calls the registry; the gate structure is unchanged, only
    the lookup is. Treated as a hard block either way.
    """
    if customer.get("dndRegistered"):
        audit(ref, "dnd", False, "number is on the DND/NCPR registry")
        raise GateFailure("dnd", "number is DND-registered")
    audit(ref, "dnd", True, "not on the DND/NCPR registry (source: CRM projection)")


def gate_sla(ref: str, customer: dict, now_ist: _dt.datetime) -> dict:
    """
    48h from purchase. Informational: reports breach risk, never blocks - a late
    call is better than no call, and the breach itself needs to be visible.
    """
    purchased = customer.get("purchasedAt")
    if not purchased:
        audit(ref, "sla", True, "no purchase timestamp on record")
        return {"known": False}
    try:
        p = _dt.datetime.fromisoformat(purchased)
    except ValueError:
        audit(ref, "sla", True, f"unparseable purchasedAt {purchased!r}")
        return {"known": False}

    deadline = p + _dt.timedelta(hours=SLA_HOURS)
    remaining = (deadline - now_ist).total_seconds() / 3600
    breached = remaining < 0
    audit(ref, "sla", True,
          f"{'BREACHED by' if breached else 'remaining'} {abs(remaining):.1f}h "
          f"against the {SLA_HOURS}h SLA")
    return {"known": True, "deadline": deadline.isoformat(),
            "hoursRemaining": round(remaining, 1), "breached": breached}


# --------------------------------------------------------------------------- #
def record_attempt(customer_id: str, now_ist: _dt.datetime) -> None:
    ddb.Table(TBL_ATTEMPTS).update_item(
        Key={"customerId": customer_id, "attemptDate": now_ist.strftime("%Y-%m-%d")},
        UpdateExpression=("SET attempts = if_not_exists(attempts, :z) + :one, "
                          "    lastAttemptAt = :t"),
        ExpressionAttributeValues=st.to_ddb({":z": 0, ":one": 1, ":t": st.now_iso()}),
    )


def _asterisk_instance() -> str:
    if ASTERISK_INSTANCE_ID:
        return ASTERISK_INSTANCE_ID
    r = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [f"{PREFIX}-asterisk"]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ])
    for res in r.get("Reservations", []):
        for inst in res.get("Instances", []):
            return inst["InstanceId"]
    raise RuntimeError(f"no running {PREFIX}-asterisk instance found")


def _run_on_asterisk(command: str, timeout: int = 60) -> str:
    """Run one command on the Asterisk box over SSM and return stdout."""
    iid = _asterisk_instance()
    cid = ssm.send_command(
        InstanceIds=[iid],
        DocumentName="AWS-RunShellScript",
        # SSM executes with /bin/sh (dash on Ubuntu), so anything bashish has to
        # be piped into bash explicitly rather than relying on a shebang.
        Parameters={"commands": [f"printf '%s' '{command}' | base64 -d | bash"],
                    "executionTimeout": [str(timeout)]},
    )["Command"]["CommandId"]

    import time as _t
    for _ in range(timeout // 2):
        _t.sleep(2)
        inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            if inv["Status"] != "Success":
                raise RuntimeError(
                    f"asterisk command {inv['Status']}: {inv.get('StandardErrorContent', '')[:400]}")
            return inv.get("StandardOutputContent", "")
    raise RuntimeError("asterisk command timed out")


def _discard_ticket(phone: str) -> None:
    """Remove a pending outbound ticket, best effort.

    Called when placement fails. Best effort on purpose: the call has already
    failed and the caller is being told so, and an SSM error here must not turn a
    reported failure into an unhandled one. A ticket that survives is logged loudly
    because it is a trap for the NEXT call, not for this one.
    """
    try:
        script = f"rm -f -- {PENDING_DIR}/{phone}\n"
        _run_on_asterisk(_b64.b64encode(script.encode()).decode(), timeout=30)
        log.info("discarded pending ticket for %s", phone)
    except Exception as e:  # noqa: BLE001
        log.error("COULD NOT REMOVE pending ticket %s/%s (%s: %s). The next "
                  "answered call to this number may adopt an abandoned session. "
                  "Remove it by hand.", PENDING_DIR, phone, type(e).__name__, e)


def _originate(call_id: str, phone: str, customer: dict, is_resume: bool) -> str:
    """Place the outbound call through our own Asterisk, not the Chime SMA.

    WHY NOT create_sip_media_application_call ANY MORE
    The SMA is action-based: PlayAudio only plays a finished S3 file and
    RecordAudio only yields a finished S3 recording, and actions run sequentially
    per leg. It therefore cannot play and listen at the same time, which makes
    voice barge-in structurally impossible. Terminating SIP on our own Asterisk
    puts the RTP under our control - and it is the same integration Tata
    Communications will need, so swapping carrier becomes a pjsip.conf change.

    HOW THE callId REACHES THE BOT
    The session already exists - this function is called after the gates and
    start_session - so the media pipeline must be told which session this call
    belongs to. If it were left to identify the caller by phone number it would
    find THIS session as resumable and start another one resuming it, so a brand
    new call would open with "we were cut off earlier". So a ticket carrying the
    callId is written keyed by the destination number, and the dialplan renames it
    to the media UUID the moment the call is answered. The rename is the handoff:
    it is atomic, and it guarantees a stale ticket cannot be picked up by a later
    call.
    """
    # normalise_language, not a raw read: the CRM stores the Salesforce picklist
    # spelling (en-IN / hi-IN / hinglish) and every consumer downstream branches
    # on en / hi / hinglish. Passing hi-IN through here means the media box asks
    # for Hindi audio under a key nothing matches, and the call runs in English.
    ticket = json.dumps({
        "callId": call_id,
        "phone": phone,
        "customerId": customer.get("customerId"),
        "language": st.normalise_language(customer.get("preferredLanguage")),
        "isResume": is_resume,
        "direction": "outbound",
    }, ensure_ascii=False)

    # Base64 so neither the JSON quoting nor the '+' in E.164 has to survive two
    # layers of shell.
    ticket_b64 = _b64.b64encode(ticket.encode()).decode()

    # PREFLIGHT, because `asterisk -rx "channel originate"` EXITS 0 WHEN IT DOES
    # NOTHING.
    #
    # Measured: with the hvi-outbound context absent from the dialplan, the
    # originate printed no output and returned success. The ticket was written, the
    # attempt was recorded, the audit said placement=true, and this function
    # returned "" - which the caller reported to the operator as
    # {"placed": true, "asterisk": ""}. No phone rang. The box was running a
    # dialplan from the previous day that predated the context being added, and
    # nothing in the success path could tell the difference.
    #
    # So the two preconditions are asserted explicitly rather than inferred from an
    # exit code: the context the call is being sent INTO, and the endpoint it is
    # being sent THROUGH. Both are one cheap CLI call and both produce an
    # unambiguous error instead of a silent no-op.
    # The endpoint checked here is the one dialled below, and it is NOT the endpoint
    # that receives inbound calls. See TRUNK_ENDPOINT.
    check = _run_on_asterisk(_b64.b64encode(
        ("set -uo pipefail\n"
         "echo '--CTX--'\n"
         "asterisk -rx 'dialplan show hvi-outbound' 2>&1 | tail -3\n"
         "echo '--EP--'\n"
         f"asterisk -rx 'pjsip show endpoint {TRUNK_ENDPOINT}' 2>&1 "
         f"| grep -E '^ Endpoint:  {TRUNK_ENDPOINT}' || true\n").encode()).decode(),
        timeout=60)

    ctx_part = check.split("--EP--")[0]
    if "no existence" in ctx_part or "1 extension" not in ctx_part:
        raise RuntimeError(
            "asterisk has no usable hvi-outbound context, so an originate would "
            "silently do nothing. Re-run scripts/71-asterisk-config.sh. "
            f"dialplan said: {ctx_part.replace(chr(10), ' ')[:200]}")
    if TRUNK_ENDPOINT not in check.split("--EP--")[-1]:
        raise RuntimeError(
            f"asterisk has no pjsip endpoint '{TRUNK_ENDPOINT}' to originate "
            "through. Re-run scripts/71-asterisk-config.sh - it is the script that "
            "creates the outbound-only endpoint.")

    script = (
        f"set -euo pipefail\n"
        f"install -d -o asterisk -g hvi -m 2770 {PENDING_DIR} {CALL_DIR}\n"
        f"printf '%s' '{ticket_b64}' | base64 -d > {PENDING_DIR}/{phone}\n"
        f"chown asterisk:hvi {PENDING_DIR}/{phone}\n"
        f"chmod 0640 {PENDING_DIR}/{phone}\n"
        # Originating with the destination number AS the extension is what lets
        # the dialplan find the ticket: ${EXTEN} in hvi-outbound is the number.
        f"asterisk -rx \"channel originate PJSIP/{phone}@{TRUNK_ENDPOINT} "
        f"extension {phone}@hvi-outbound\"\n"
        # Evidence the channel actually came into being. An originate that failed
        # to create a channel leaves nothing here, which is the difference between
        # "ringing" and "silently did nothing".
        f"sleep 2\n"
        f"echo '--CHANNELS--'\n"
        f"asterisk -rx 'core show channels' 2>&1 | tail -4\n"
    )
    out = _run_on_asterisk(_b64.b64encode(script.encode()).decode(), timeout=90)

    if "--CHANNELS--" in out:
        head, chans = out.split("--CHANNELS--", 1)
        if "0 active channels" in chans and "0 active calls" in chans:
            raise RuntimeError(
                "originate produced no channel: asterisk reported 0 active "
                f"channels two seconds later. {chans.replace(chr(10), ' ')[:200]}")
        return f"{head.strip()} channels:{chans.replace(chr(10), ' ').strip()[:160]}"
    return out.strip()


def lambda_handler(event, _context):
    phone = event.get("phone")
    customer_id = event.get("customerId")
    dry_run = bool(event.get("dryRun"))
    ref = f"pending-{uuid.uuid4()}"

    log.info("outbound request phone=%s customerId=%s dryRun=%s", phone, customer_id, dry_run)

    customer = (st.get_customer(customer_id) if customer_id
                else st.get_customer_by_phone(phone) if phone else None)
    if customer and not phone:
        phone = customer.get("phone")

    # ---- gates ----
    try:
        customer = gate_policy(ref, customer, phone or "")
        consent = gate_consent(ref, customer["customerId"])
        now_ist = gate_time_window(ref)
        attempts_today = gate_attempts(ref, customer["customerId"], now_ist)
        gate_dnd(ref, customer)
        sla = gate_sla(ref, customer, now_ist)
    except GateFailure as g:
        log.warning("gate blocked the call: %s", g)
        return {
            "placed": False, "blockedBy": g.gate, "reason": g.reason,
            "retryable": g.retryable, "retryAfter": g.retry_after,
            "auditRef": ref,
        }

    # ---- resume or start ----
    # form_id resolved BEFORE the lookup: a session collected against a different
    # questionnaire must not be inherited, because answers are keyed by item id
    # and item ids only mean anything within one form.
    form_id = customer.get("assignedFormId", "arogya-sanjeevani-v1")
    resumable = st.find_resumable_session(phone, form_id)

    # A DRY RUN MUST NOT WRITE, and this one used to.
    #
    # start_session ran BEFORE the dry_run check, so the smoke test that proves
    # the deployment works left a real session in hvi-call-session. Within the
    # resume window and on the same form, the next call would find it and open
    # with "we were cut off earlier, let me carry on from where we stopped" -
    # about a conversation that never happened, because the dry run never dialled
    # anyone. That is the same class of defect as the 19-hour resume that produced
    # a complete-looking record nobody had been asked.
    #
    # Everything useful survives as a read: whether a resume WOULD happen, and
    # which session it would attach to. lambdas/send_confirmation makes the same
    # promise ("dry run does not invoke SES") and this now matches it.
    if dry_run:
        return {"placed": False, "dryRun": True, "callId": None,
                "wouldResume": bool(resumable),
                "wouldResumeCallId": (resumable or {}).get("callId"),
                "formId": form_id, "sla": sla, "auditRef": ref,
                "consentGrantedAt": consent.get("grantedAt"),
                "attemptsToday": attempts_today}

    session = st.start_session(phone, customer, form_id, resume_from=resumable)
    call_id = session["callId"]
    is_resume = int(session.get("resumeCount") or 0) > 0

    audit(call_id, "session", True,
          f"{'resuming ' + resumable['callId'] if is_resume else 'new intake'}; "
          f"{len(session.get('answers') or {})} answers carried, "
          f"{len(session.get('pendingFollowUps') or [])} follow-ups owed")

    # ---- place the call ----
    try:
        detail = _originate(call_id, phone, customer, is_resume)
    except Exception as e:
        log.exception("call placement failed")
        audit(call_id, "placement", False, str(e)[:400])
        # THE TICKET MUST GO WITH THE FAILURE.
        #
        # _originate writes /var/lib/hvi/pending/<phone> BEFORE it dials, because
        # the dialplan renames that file at answer time to hand the callId to the
        # media pipeline. When the dial then fails, the file stayed - and it names
        # a callId this branch is about to mark ABANDONED. The next answered call
        # to the same number would rename that ticket in and adopt a dead session,
        # which is indistinguishable from the resume defect it was designed to
        # prevent. A real 486 from the carrier left exactly this behind.
        _discard_ticket(phone)
        # ABANDONED, not DROPPED. DROPPED is in RESUMABLE and means "we were
        # talking and the line went down", which earns the next call an opening of
        # "we were cut off earlier, let me carry on from where we stopped". A
        # placement failure never reached the customer at all, so there is nothing
        # to carry on from and saying so would be telling them about a
        # conversation that never happened. Marking it DROPPED made every retry
        # after a failed originate greet a first-time caller as a returning one.
        st.mark_status(call_id, st.STATUS_ABANDONED,
                       note=f"placement failed, customer never reached: {e}"[:300])
        return {"placed": False, "blockedBy": "placement", "reason": str(e),
                "callId": call_id, "auditRef": ref}

    record_attempt(customer["customerId"], now_ist)
    audit(call_id, "placement", True,
          f"originated via asterisk to {phone} from {CALLER_ID}: {detail[:200]}")
    log.info("call placed callId=%s to=%s", call_id, phone)

    return {
        "placed": True, "callId": call_id,
        "to": phone, "from": CALLER_ID, "isResume": is_resume,
        "answersCarried": len(session.get("answers") or {}),
        "followUpsOwed": len(session.get("pendingFollowUps") or []),
        "sla": sla, "auditRef": ref, "asterisk": detail[:200],
    }
