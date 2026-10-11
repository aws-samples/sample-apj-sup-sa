#!/usr/bin/env python3
"""Stress wave 2: the INTERSECTIONS of dialogue features.

Wave 1 (test_underwriting_stress.py) drives each underwriting path under
adversarial input. This wave crosses the features against each other, because
that is where the remaining live failures have come from: every mechanism
worked alone, and a caller combined two of them.

Each scenario names the two features being crossed. Runs against the local
orchestrator with real DynamoDB, Neptune, OpenSearch and Bedrock.

Run:  ./.venv/bin/python tests/test_underwriting_stress2.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from test_underwriting_stress import Call, check, failures, utt  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
import hvi_state as st  # noqa: E402


def main() -> int:  # noqa: PLR0915
    calls: list[Call] = []

    # ------------------------------------------------------------------ #
    print("[1] Language switch x running protocol: the topic survives Hindi")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say("breast cancer, five years back")
    c.confirm_expecting("C", "the cancer read-back")
    ref_before = c.reply.get("askedRef")
    r = c.say("Hindi")
    check("switch acknowledged in Hindi",
          "हिंदी" in utt(r) or "है" in utt(r) or "ठीक" in utt(r), utt(r)[:70])
    check("the protocol question is re-asked, not abandoned",
          r.get("askedRef") == ref_before or bool(c.session().get("pendingFollowUps")),
          f"{ref_before} -> {r.get('askedRef')}")
    r = c.say("दो हज़ार उन्नीस में")
    check("Hindi answer lands on the protocol",
          bool(c.session().get("followUpAnswers")) or r.get("askedKind") == "followup",
          utt(r)[:60])

    # ------------------------------------------------------------------ #
    #
    # THIS ASSERTS THE INVARIANT, NOT ONE PARTICULAR LIVE PATH.
    #
    # It used to require at least two rejected read-backs from "cancer, five years
    # back", and failed intermittently with "0 read-backs offered". That was the
    # test being wrong, not the agent: when the classifier resolves the phrase
    # confidently and the graph corroborates it, opening the protocol WITHOUT a
    # read-back is the correct outcome, and a suite that fails on correct
    # behaviour teaches everyone to ignore it.
    #
    # What must hold on EVERY path is that the disclosure is never dropped, and
    # that if read-backs are offered and rejected the chain ends by ASKING rather
    # than abandoning. The exact candidate-walking mechanics are pinned
    # deterministically, with a scripted classifier, in
    # tests/test_retraction_and_readback.py - which is where a behaviour that
    # depends on how many candidates the index returns belongs.
    print("\n[2] Rejection chain x open probe: exhaust candidates, then name it")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say("cancer, five years back")
    sess = c.session()
    check("the disclosure is never dropped, whichever path it takes",
          bool(sess.get("awaitingConfirmation") or sess.get("pendingFollowUps")
               or sess.get("declaredConditions")
               or sess.get("pendingConditionDetail")
               or sess.get("unclassifiedMentions")),
          f"kind={c.reply.get('askedKind')} session={ {k: bool(sess.get(k)) for k in ('awaitingConfirmation', 'pendingFollowUps', 'declaredConditions', 'pendingConditionDetail', 'unclassifiedMentions')} }")

    rejections = 0
    for _ in range(4):
        if c.reply.get("askedKind") == "confirmation":
            c.say("no")
            rejections += 1
        else:
            break
    print(f"        {rejections} read-back(s) offered and rejected")
    if rejections:
        # Having rejected every guess, the caller must be asked - never left with
        # the topic silently closed.
        check("a rejection chain ends by asking, not abandoning",
              c.reply.get("askedKind") in ("conditionDetail", "confirmation"),
              f"kind={c.reply.get('askedKind')}: {utt(c.reply)[:60]}")
        check("what the caller said survives the rejections for a human",
              bool(c.session().get("unclassifiedMentions")
                   or c.session().get("declaredConditions")),
              str(c.session().get("unclassifiedMentions"))[:80])
        if c.reply.get("askedKind") == "conditionDetail":
            c.say_landing("it was skin cancer")
            sess = c.session()
            check("the named type after exhaustion is classified",
                  bool(sess.get("awaitingConfirmation")
                       or sess.get("pendingFollowUps")
                       or sess.get("declaredConditions")),
                  utt(c.reply)[:60])
    else:
        # Resolved on the first pass. The obligation still has to exist.
        check("a confidently resolved disclosure owes protocol questions",
              bool(c.session().get("pendingFollowUps")
                   or c.session().get("declaredConditions")),
              f"kind={c.reply.get('askedKind')}: {utt(c.reply)[:60]}")

    # ------------------------------------------------------------------ #
    print("\n[3] Hypothetical x probe: 'doctor says I might get it' declares nothing")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    r = c.say("the doctor warned me I might develop diabetes")
    sess = c.session()
    check("no diagnosis from a warning",
          not sess.get("declaredConditions")
          and not (sess.get("awaitingConfirmation") or {}).get("icd10"),
          f"awaiting={(sess.get('awaitingConfirmation') or {}).get('icd10')}")
    check("the topic is not silently dropped either",
          r.get("askedKind") in ("conditionDetail", "item"),
          f"kind={r.get('askedKind')}: {utt(r)[:60]}")

    # ------------------------------------------------------------------ #
    print("\n[4] Med mismatch x protocol: wrong medicine mid-protocol verified once")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say_landing("thyroid problem")
    c.confirm_expecting("E0", "the thyroid read-back")
    # Walk to the treatment question and SAY SO IF WE NEVER GET THERE. The bound
    # was four turns, which a single re-ask along the way could exhaust; the suite
    # then silently tested nothing and failed later on an empty record, blaming
    # the revision for a walk that never arrived.
    reached = False
    for _ in range(8):
        ref = c.reply.get("askedRef") or ""
        spoken = utt(c.reply).lower()
        if ref.startswith("FU_TREATMENT") or "treatment" in spoken \
                or "इलाज" in utt(c.reply):
            reached = True
            break
        c.say("2019")
    check("the protocol reached the treatment question", reached,
          f"ref={c.reply.get('askedRef')}: {utt(c.reply)[:60]}")

    if reached:
        r = c.say_landing("I take metformin for it", alt="metformin")
        check("mismatched medicine is verified once",
              r.get("askedKind") == "medVerify",
              f"kind={r.get('askedKind')}: {utt(r)[:60]}")
        # say_landing, not say: the revision is one language-model parse away, and
        # the agent re-asking it once is correct behaviour rather than a defect.
        # Repeating once is what a real caller does and the assertion still has to
        # hold afterwards.
        c.say_landing("sorry, I meant Thyronorm", alt="Thyronorm")
        fua = c.session().get("followUpAnswers") or {}
        vals = " ".join(str(v.get("value", "")).lower() for v in fua.values()
                        if isinstance(v, dict))
        check("the revision replaced the answer", "thyronorm" in vals, vals[:70])
        # THE SAFETY PROPERTY, which holds however the revision was parsed: a drug
        # the caller corrected must never end up on the record as their treatment.
        # Recording metformin against hypothyroidism would be a clinical error in
        # the underwriting file, and nothing asserted against it before.
        check("the mismatched medicine is never recorded as the treatment",
              "metformin" not in vals, vals[:70])

    # ------------------------------------------------------------------ #
    print("\n[5] Repeat x summary: 'can you repeat that' re-reads the summary")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    for _ in range(30):
        if c.reply.get("askedKind") == "summaryConfirm" or c.reply.get("isComplete"):
            break
        c.say("no")
    check("reached the summary", c.reply.get("askedKind") == "summaryConfirm")
    r = c.say("sorry, can you repeat that?")
    check("summary repeated rather than treated as a correction",
          r.get("askedKind") == "summaryConfirm" and not r.get("isComplete"),
          f"kind={r.get('askedKind')}: {utt(r)[:60]}")
    r = c.say("no, all correct")
    check("then confirms and closes", r.get("isComplete") is True, utt(r)[:60])

    # ------------------------------------------------------------------ #
    print("\n[6] Uncorroborated distress x protocol: one gentle check, topic resumes")
    c = Call(); calls.append(c)
    c.drive_to("A200")
    c.say("Yes")
    c.say("breast cancer, five years back")
    c.confirm_expecting("C", "the cancer read-back")
    ref_before = c.reply.get("askedRef")
    # An utterance the model may read distress into, without corroborating words
    # - the live "I saw him suddenly." class.
    r = c.say("I saw him suddenly")
    if r.get("askedKind") == "distressCheck":
        r = c.say("no no, I was talking about my doctor visit")
        check("gentle check then back to the protocol",
              not r.get("escalated"), f"kind={r.get('askedKind')}")
    check("questionnaire did not halt on an uncorroborated signal",
          not c.reply.get("stopQuestionnaire"), utt(c.reply)[:60])
    check("the protocol is still owed",
          bool(c.session().get("pendingFollowUps")), ref_before)

    # ------------------------------------------------------------------ #
    print("\n[7] Volunteering x non-screen item: disease named during lifestyle")
    c = Call(); calls.append(c)
    ok = c.drive_to("A400")
    if ok:
        r = c.say("no tobacco, but I do have high blood pressure")
        sess = c.session()
        heard = bool((sess.get("awaitingConfirmation") or {}).get("icd10")
                     or any(str(d.get("icd10", "")).startswith("I10")
                            for d in sess.get("declaredConditions") or []))
        check("condition volunteered on a lifestyle item is picked up", heard,
              f"awaiting={(sess.get('awaitingConfirmation') or {}).get('icd10')} "
              f"utt={utt(r)[:50]}")
    else:
        check("condition volunteered on a lifestyle item is picked up", False,
              "never reached A400")

    # ------------------------------------------------------------------ #
    print("\n[8] Garbage year x three strikes: date question gives up gracefully")
    c = Call(); calls.append(c)
    c.drive_to("A030")
    c.say("5 September 3000")
    c.say("5 September 1500")
    r = c.say("14 September 2500")
    moved_on = r.get("askedRef") != "A030"
    check("three impossible dates do not loop forever", moved_on,
          f"ref={r.get('askedRef')}: {utt(r)[:60]}")

    for c in calls:
        c.cleanup()

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Feature intersections hold: switches, rejections, hypotheticals, "
          "medications, repeats, distress and volunteering all compose.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
