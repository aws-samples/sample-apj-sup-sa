#!/usr/bin/env python3
"""Two high-consequence guards: distress corroboration, and BMI cross-validation.

WHY THESE EXIST
Both come from live calls, and both are cases where a single unreliable transcript
caused an irreversible thing to happen.

DISTRESS. The transcript "I saw him suddenly." - a mis-hearing of an ordinary answer
- was read as a self-harm disclosure. The caller was told "I'm arranging for a doctor
to speak with you", the questionnaire halted, and a clinician was paged.

This is the one guard where being wrong in either direction does real harm, so the
tests below are weighted accordingly. Missing a genuine disclosure is far worse than
escalating on a mis-transcription, so a real disclosure must escalate IMMEDIATELY and
those checks are the ones that matter most here. Everything else gets one gentle
question first, and a second signal escalates regardless of wording.

BMI. A call recorded 183cm alongside a weight of "Thirty" and both passed validation:
A100 permits 100-230cm, A110 permits 25-300kg, because 25kg is a real weight for a
child applicant. Each bound is right; the pair is a BMI of 9, which no living person
has. Individually plausible and jointly impossible is exactly what per-field
validation cannot catch, and it would have reached the underwriter looking like a
measurement.

Run:  ./.venv/bin/python tests/test_safety_guards.py
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

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    print("[1] A GENUINE disclosure escalates immediately, with no gentle detour")
    # The most important block in this file. A false negative here delays help.
    for said in ("I don't want to live anymore",
                 "I have been thinking about killing myself",
                 "sometimes I want to die",
                 "I have thought about ending my life",
                 "I tried to harm myself last year",
                 "मैं मरना चाहती हूँ",
                 "मैंने आत्महत्या के बारे में सोचा है",
                 "main marna chahti hoon"):
        check(f"{said[:46]!r} corroborates", orch.distress_corroborated(said))

    print("\n[2] Mis-transcriptions do NOT corroborate, so they get asked about first")
    for said in ("I saw him suddenly.",
                 "And so then.",
                 "Seaman.",
                 "Enceladus?",
                 "It's tied up.",
                 "Maybe once a week.",
                 "I have bronchitis for sure"):
        check(f"{said[:46]!r} does not corroborate",
              not orch.distress_corroborated(said))

    print("\n[2b] A risk or a relative's illness is not the caller's diagnosis")
    # The live utterance is first. It became an offer to record Chronic Ischaemic
    # Heart Disease as a CURRENT condition, which on an insurance application is a
    # material misstatement made in the applicant's name.
    for said in ("They did say I could have heart problems in a few years.",
                 "The doctor warned me I might develop diabetes",
                 "I'm at risk of kidney disease",
                 "there's a chance of it getting worse",
                 "my father had diabetes",
                 "heart disease runs in the family",
                 "my mother has thyroid problems",
                 "mujhe aage chal kar sugar ho sakta hai",
                 "परिवार में शुगर है"):
        check(f"{said[:46]!r} is hypothetical",
              orch.is_hypothetical_mention(said))

    print("\n[2c] An ACTUAL diagnosis is still classified")
    # The costly direction: suppressing a real disclosure would lose it from the
    # underwriting record entirely.
    for said in ("I have diabetes",
                 "I was diagnosed with bronchitis two years back",
                 "mujhe sugar hai",
                 "I have bronchitis for sure",
                 "It was breast cancer, in 2019",
                 "मुझे शुगर है",
                 "I take insulin for my diabetes",
                 "I had a heart attack in 2019"):
        check(f"{said[:46]!r} is a real diagnosis",
              not orch.is_hypothetical_mention(said))

    print("\n[3] Height and weight must describe the same body")
    # The live pair, and the reason per-field bounds could not catch it.
    err = orch.implausible_bmi({"A100": 183, "A110": 30})
    check("183cm with 30kg is rejected", bool(err), err or "")
    check("the reason names the body mass index",
          "body mass index" in (err or "").lower(), err or "")

    check("183cm with 400kg is rejected",
          bool(orch.implausible_bmi({"A100": 183, "A110": 400})))

    print("\n[4] Real bodies pass, including the extremes of real bodies")
    # False rejections here make a genuine applicant repeat themselves, and the
    # thin and the very heavy are exactly the applicants an underwriter cares about.
    for h, w, why in ((175, 70, "average"),
                      (183, 55, "tall and thin"),
                      (150, 40, "short and light"),
                      (160, 120, "obese"),
                      (183, 150, "severely obese"),
                      (145, 35, "small adult"),
                      (110, 20, "child applicant")):
        check(f"{h}cm/{w}kg passes ({why})",
              orch.implausible_bmi({"A100": h, "A110": w}) is None,
              str(orch.implausible_bmi({"A100": h, "A110": w}) or ""))

    print("\n[4b] Medication is judged against the condition, flag-only")
    # The question this answers: "does it even check if thyroxine is a valid
    # medicine for thyroid?" It does now - and the verdict goes to the
    # underwriter's record, never into the conversation, because a bot arguing
    # pharmacology with a patient would be wrong often enough to be dangerous.
    check("thyroxine for hypothyroidism is consistent",
          orch.medication_consistency("E03", "I take thyroxine daily")
          == ("consistent", "thyroxine"))
    check("Thyronorm (Indian brand) for thyroid is consistent",
          orch.medication_consistency("E03", "Thyronorm 50 every morning")
          == ("consistent", "thyronorm"))
    check("metformin for a THYROID disclosure is flagged",
          orch.medication_consistency("E03", "metformin twice a day")
          == ("unexpected", "metformin"))
    check("insulin for diabetes is consistent",
          orch.medication_consistency("E11", "main insulin leti hoon")
          == ("consistent", "insulin"))
    check("Devanagari इंसुलिन judged the same",
          orch.medication_consistency("E11", "मैं इंसुलिन लेती हूँ")
          == ("consistent", "इंसुलिन"))
    check("an unrecognised answer is not judged",
          orch.medication_consistency("E03", "some white tablet") is None)
    check("a broadly-prescribed drug is not judged",
          orch.medication_consistency("E03", "prednisolone") is None)
    check("no condition code, no judgement",
          orch.medication_consistency(None, "thyroxine") is None)

    print("\n[5] Incomplete or unusable data is not an error")
    # One value alone is the per-field bounds' job. Complaining here would re-ask a
    # question that was answered perfectly well.
    check("height alone passes", orch.implausible_bmi({"A100": 183}) is None)
    check("weight alone passes", orch.implausible_bmi({"A110": 30}) is None)
    check("nothing passes", orch.implausible_bmi({}) is None)
    check("not_captured passes",
          orch.implausible_bmi({"A100": "not_captured", "A110": 70}) is None)
    check("zero height passes rather than dividing by zero",
          orch.implausible_bmi({"A100": 0, "A110": 70}) is None)

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Real disclosures escalate at once, mis-hearings get asked about, and an "
          "impossible height/weight pair cannot reach the record.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
