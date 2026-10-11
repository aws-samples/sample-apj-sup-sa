#!/usr/bin/env python3
"""Adversarial end-to-end conversations across every underwriting path.

WHY THIS EXISTS
Every live-call failure so far was a COMPOUND path: not "does the cancer screen
work" but "what happens when the screen's yes is followed by garbage, then a
correction, then a second disease". The unit suites pin each mechanism alone;
nothing drove the orchestrator through the sequences a real caller produces.
This suite is that driver. Every scenario is either a replay of a real failure
or an adjacent path the same class of caller will hit next.

Runs against the LOCAL orchestrator with real DynamoDB, Neptune, OpenSearch and
Bedrock - the same stack the deployed agent wraps. Slow (~2-3 min); that is the
price of testing the thing itself rather than a mock of it.

Run:  ./.venv/bin/python tests/test_underwriting_stress.py
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod), os.path.join(_agent_common, _mod))

import orchestrator as orch  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

failures: list[str] = []
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_underwriting_stress")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Call:
    """Drives one conversation and remembers where it is."""

    def __init__(self) -> None:
        cust = st.get_customer_by_phone(PHONE)
        s = st.start_session(PHONE, cust, FORM_ID)
        self.id = s["callId"]
        st.set_language(self.id, "en", confirmed=True)
        self.turn = 0
        self.reply: dict = orch.handle_turn(self.id, "", 1, language_hint="en")
        self.turn = 1

    def say(self, text: str) -> dict:
        self.turn += 2
        self.reply = orch.handle_turn(self.id, text, self.turn, language_hint="en")
        return self.reply

    def say_landing(self, text: str, alt: str | None = None) -> dict:
        """Say something that MUST be understood, repeating once if it is not.

        The extraction step is a language model, so a phrasing it parses nine
        times out of ten will occasionally come back "sorry, I didn't catch
        that" - and the agent re-asking is CORRECT behaviour, not a bug. A test
        that asserts a hard outcome on the far side of that step is flaky by
        construction, which is how a green suite failed once inside the full
        run. Repeating once is exactly what a real caller does, and it does not
        weaken the assertion: the check still has to pass on the retry.

        Use only where the utterance is setup for the behaviour under test. Never
        use it where the re-ask itself is what is being asserted.
        """
        r = self.say(text)
        if any("re-asked" in n for n in (r.get("notes") or [])):
            r = self.say(alt or text)
        return r

    def confirm_expecting(self, icd_prefix: str, label: str = "") -> dict:
        """Say yes to a read-back ONLY if it offers the code we are expecting.

        `if askedKind == "confirmation": say("yes")` is an agreeable caller, and an
        agreeable caller cannot detect a misclassification - it records one. That is
        not hypothetical. A bare "Yes" to A200 intermittently produced a read-back
        for Uterine Fibroid; the blind version confirmed it, and the run then failed
        three assertions later with "declared=['D25']" - a message that blamed the
        deferred-mention logic for a wrong answer accepted much earlier.

        Judged on awaitingConfirmation's icd10, not on the spoken text, because the
        read-back QUOTES the caller's phrase back to them: "you mentioned 'breast
        cancer'... is that Malignant Neoplasm of Breast?" contains the word cancer
        whichever code it is actually offering, so matching the words proves
        nothing. A prefix is enough - C for any cancer, E0 for any thyroid disorder
        - since which sub-code the classifier lands on is not what these scenarios
        are about.
        """
        if self.reply.get("askedKind") != "confirmation":
            return self.reply
        offered = (self.session().get("awaitingConfirmation") or {}).get("icd10") or ""
        if offered.startswith(icd_prefix):
            return self.say("yes")
        check(f"{label or 'the read-back'} offers {icd_prefix}*, not something else",
              False, f"offered {offered!r}: {utt(self.reply)[:90]}")
        return self.say("no")

    def drive_to(self, ref: str, answers: dict | None = None, limit: int = 40) -> bool:
        base = {"A010": "Yes, speaking", "A020": "Yes, go ahead",
                "A030": "22 March 1987", "A100": "175 centimetres",
                "A110": "70 kilograms"}
        base.update(answers or {})
        for _ in range(limit):
            if self.reply.get("askedRef") == ref:
                return True
            if self.reply.get("isComplete"):
                return False
            self.say(base.get(self.reply.get("askedRef") or "", "no"))
        return False

    def session(self) -> dict:
        return st.get_session(self.id) or {}

    def cleanup(self) -> None:
        try:
            st.mark_status(self.id, st.STATUS_ABANDONED, note="stress cleanup")
        except Exception as exc:  # noqa: BLE001
            print(f"  note: cleanup failed: {exc}")


def utt(r: dict) -> str:
    return (r.get("agentUtterance") or "")


def main() -> int:  # noqa: PLR0915
    calls: list[Call] = []

    # ------------------------------------------------------------------ #
    print("[1] Topic lock: garbage answers to the which-condition probe stay "
          "on topic, then flag")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    check("probe opened", c.reply.get("askedKind") == "conditionDetail",
          utt(c.reply)[:60])
    r1 = c.say("here they are")            # live STT garbage
    check("stays on topic after garbage 1",
          r1.get("askedKind") == "conditionDetail", utt(r1)[:70])
    r2 = c.say("bye josh")                 # more live garbage
    check("stays on topic after garbage 2",
          r2.get("askedKind") == "conditionDetail", utt(r2)[:70])
    r3 = c.say("white golden")
    check("third failure flags and moves on",
          r3.get("askedKind") != "conditionDetail",
          f"kind={r3.get('askedKind')}")
    check("flagged for the underwriter",
          any("UNDERWRITER" in n for n in (r3.get("notes") or [])),
          str(r3.get("notes"))[:80])

    # ------------------------------------------------------------------ #
    print("\n[2] Recovery: garbage once, then a real name resolves the topic")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say("enceladus")                     # garbage attempt
    r = c.say("it was breast cancer, in 2019")
    sess = c.session()
    ok = (sess.get("awaitingConfirmation") or {}).get("icd10") == "C50" \
        or "C50" in [d.get("icd10") for d in sess.get("declaredConditions") or []]
    check("real name resolves after garbage", ok, utt(r)[:70])

    # ------------------------------------------------------------------ #
    print("\n[3] One condition at a time: a second disease defers, then runs")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say("breast cancer, five years back")
    c.confirm_expecting("C", "the cancer read-back")
    # NAME THE CONDITION, not just "some protocol is running". This asserted only
    # that pendingFollowUps was non-empty, so a run where a DIFFERENT condition's
    # protocol opened sailed past here and failed three steps later with
    # "declared=['D25'] awaiting=C50" - a message that blamed the deferred-mention
    # logic for something that went wrong at this line.
    running = c.session().get("pendingFollowUps") or []
    check("the CANCER protocol is the one running",
          bool(running) and all(str(f.get("icd10") or "").startswith("C")
                                for f in running),
          f"owed={[(f.get('icd10'), f.get('questionId')) for f in running][:4]} "
          f"declared={[d.get('icd10') for d in c.session().get('declaredConditions') or []]}")
    r = c.say("in 2019, and I also have diabetes")
    sess = c.session()
    check("diabetes did NOT hijack the cancer protocol",
          (sess.get("awaitingConfirmation") or {}).get("icd10") in (None, "C50"),
          f"awaiting={(sess.get('awaitingConfirmation') or {}).get('icd10')}")
    check("diabetes was deferred, not dropped",
          bool(sess.get("deferredMentions")),
          str(sess.get("deferredMentions"))[:60])
    # Finish the cancer follow-ups with plain answers.
    for _ in range(10):
        if not c.session().get("pendingFollowUps"):
            break
        c.say("no, all done with treatment in 2020")
    sess = c.session()
    declared = [d.get("icd10") for d in sess.get("declaredConditions") or []]
    # PICKED UP, OR PRESERVED FOR A HUMAN - which is what the deferred floor
    # actually promises. A deferred mention is classified with
    # DEFERRED_MENTION_MIN_SCORE (0.85), and whether "and I also have diabetes"
    # clears it depends on which sub-phrase the extractor hands over: "diabetes"
    # alone is an exact graph synonym at 1.0, the whole clause measures 0.8198.
    # Below the floor the phrase is deliberately recorded for human review instead
    # of minting a condition off a weak guess, so demanding that it always becomes
    # E11 asserted against the design and failed intermittently. What must NEVER
    # happen is the disclosure disappearing, and that is now what is checked.
    picked_up = (sess.get("awaitingConfirmation") or {}).get("icd10", "").startswith("E1") \
        or any(str(x).startswith("E1") for x in declared) \
        or bool(sess.get("pendingFollowUps"))
    flagged = any("diabet" in (row.get("phrase") or "").lower()
                  or "sugar" in (row.get("phrase") or "").lower()
                  for row in sess.get("unclassifiedMentions") or [])
    still_queued = any("diabet" in str(m).lower()
                       for m in sess.get("deferredMentions") or [])
    check("the deferred diabetes is picked up, or preserved for review - never "
          "silently dropped", picked_up or flagged or still_queued,
          f"declared={declared} "
          f"awaiting={(sess.get('awaitingConfirmation') or {}).get('icd10')} "
          f"deferred={sess.get('deferredMentions')} "
          f"flagged={[r.get('phrase') for r in sess.get('unclassifiedMentions') or []]}")

    # ------------------------------------------------------------------ #
    print("\n[4] 'I don't know' on a protocol follow-up is an answer, not a failure")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say("thyroid problem")
    c.confirm_expecting("E0", "the thyroid read-back")
    before = c.reply.get("askedRef")
    r = c.say("I don't know")
    check("dont-know moves to the next question rather than re-asking",
          r.get("askedRef") != before, f"{before} -> {r.get('askedRef')}")

    # ------------------------------------------------------------------ #
    print("\n[5] Family history and hypotheticals never enter the record")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    r = c.say("no, but my father had cancer")
    sess = c.session()
    check("father's cancer not declared",
          not sess.get("declaredConditions") and not sess.get("awaitingConfirmation"),
          utt(r)[:60])
    check("the screen's own answer was recorded",
          (sess.get("answers") or {}).get("A200") in (False, "false"),
          repr((sess.get("answers") or {}).get("A200")))

    # ------------------------------------------------------------------ #
    print("\n[6] Impossible values on every numeric gate")
    c = Call(); calls.append(c)
    c.drive_to("A030")
    r = c.say("5 September 1,500")
    check("comma-formatted 1,500 rejected",
          r.get("askedRef") == "A030", utt(r)[:60])
    c.say("22 March 1987")
    r = c.say("50 feet")
    check("50 feet rejected honestly",
          r.get("askedRef") == "A100" and "सुन नहीं" not in utt(r), utt(r)[:60])
    c.say("5 feet 8 inches")
    r = c.say("one thousand kilos")
    check("1000kg rejected", r.get("askedRef") == "A110", utt(r)[:60])

    # ------------------------------------------------------------------ #
    print("\n[7] The clean path completes, reads back, and honours a correction")
    c = Call(); calls.append(c)
    ok = c.drive_to("A200")
    check("reached the condition screens", ok)
    for _ in range(30):
        if c.reply.get("askedKind") == "summaryConfirm" or c.reply.get("isComplete"):
            break
        c.say("no")
    check("summary confirmation asked before closing",
          c.reply.get("askedKind") == "summaryConfirm", utt(c.reply)[:70])
    r = c.say("actually I also have asthma")
    sess = c.session()
    heard = bool(sess.get("awaitingConfirmation") or sess.get("pendingFollowUps")
                 or [d for d in sess.get("declaredConditions") or []])
    check("late disclosure at the summary reopens the protocol", heard, utt(r)[:70])

    # ------------------------------------------------------------------ #
    print("\n[8] Refusal mid-questionnaire confirms once, then stops cleanly")
    c = Call(); calls.append(c)
    c.drive_to("A100")
    r = c.say("I don't want to continue this call")
    check("refusal is confirmed, not obeyed instantly",
          r.get("askedKind") == "refusalConfirm", utt(r)[:60])
    r = c.say("yes")
    check("confirmed refusal ends the questionnaire",
          r.get("stopQuestionnaire") is True, utt(r)[:60])

    for c in calls:
        c.cleanup()

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Every adversarial path holds: topics lock, garbage flags, second "
          "diseases queue, impossible values bounce, and the record closes only "
          "after the caller confirms it.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
