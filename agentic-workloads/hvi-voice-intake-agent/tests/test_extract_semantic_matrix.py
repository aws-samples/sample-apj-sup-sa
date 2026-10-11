#!/usr/bin/env python3
"""Live Bedrock text-semantic contract across English, Hindi, and Hinglish.

Telephone acoustics are deliberately absent. This isolates the probabilistic
language-understanding boundary and requires structured scope for every medical
mention, including mixed and multiple-condition sentences.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
sys.path.insert(0, str(ROOT / "agent"))

import extract  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)
        if detail:
            print(f"        {detail}")


SURFACES = {
    "en": {
        "affirmative": "Yes, that is correct.",
        "negative": "No, never.",
        "dont_know": "I do not know.",
        "repeat": "Sorry, could you repeat that?",
        "refusal": "I do not want to continue this questionnaire.",
        "current": "I have asthma.",
        "family": "My father had diabetes.",
        "hypothetical": "The doctor said I might develop diabetes in the future.",
        "negated": "I do not have diabetes.",
        "mixed": "My father had diabetes, but I have asthma.",
        "two_current": "I have diabetes and asthma.",
        "distress": "I want to kill myself.",
        "medication": "I take metformin every day.",
        "correction": "My weight is not 70; it is 68 kilograms.",
    },
    "hi": {
        "affirmative": "हाँ, यह सही है।",
        "negative": "नहीं, कभी नहीं।",
        "dont_know": "मुझे नहीं पता।",
        "repeat": "माफ़ कीजिए, सवाल दोबारा बोलिए।",
        "refusal": "मैं यह प्रश्नावली जारी नहीं रखना चाहती।",
        "current": "मुझे अस्थमा है।",
        "family": "मेरे पिता को मधुमेह था।",
        "hypothetical": "डॉक्टर ने कहा कि मुझे भविष्य में मधुमेह हो सकता है।",
        "negated": "मुझे मधुमेह नहीं है।",
        "mixed": "मेरे पिता को मधुमेह था, लेकिन मुझे अस्थमा है।",
        "two_current": "मुझे मधुमेह और अस्थमा है।",
        "distress": "मैं खुद को मारना चाहती हूँ।",
        "medication": "मैं हर दिन मेटफॉर्मिन लेती हूँ।",
        "correction": "मेरा वजन 70 नहीं, 68 किलो है।",
    },
    "hinglish": {
        "affirmative": "Haan, bilkul sahi hai.",
        "negative": "Nahi, kabhi nahi.",
        "dont_know": "Mujhe nahi pata.",
        "repeat": "Sorry, sawaal dobara boliye.",
        "refusal": "Main questionnaire continue nahi karna chahti.",
        "current": "Mujhe asthma hai.",
        "family": "Mere father ko diabetes tha.",
        "hypothetical": "Doctor ne kaha future mein diabetes ho sakta hai.",
        "negated": "Mujhe diabetes nahi hai.",
        "mixed": "Mere father ko diabetes tha, lekin mujhe asthma hai.",
        "two_current": "Mujhe diabetes aur asthma hai.",
        "distress": "Main khud ko maarna chahti hoon.",
        "medication": "Main roz metformin leti hoon.",
        "correction": "Mera weight 70 nahi, 68 kilo hai.",
    },
}


def mentions(reading: dict) -> list[dict]:
    return [row for row in (reading.get("conditionMentions") or [])
            if isinstance(row, dict)]


def scopes(reading: dict) -> list[str]:
    return [str(row.get("scope")) for row in mentions(reading)]


def interpret(language: str, kind: str, utterance: str) -> dict:
    if kind == "correction":
        question, answer_type = "What is your weight in kilograms?", "number"
        item = {"unit": "kg", "validation": {"min": 25, "max": 300}}
    elif kind == "medication":
        question, answer_type = "What medication are you taking?", "text"
        item = {}
    else:
        question, answer_type = "Do you have any medical conditions?", "boolean"
        item = {}
    return extract.understand(
        question_text=question,
        answer_type=answer_type,
        utterance=utterance,
        language=language,
        item=item,
        customer_name="Priya",
    )


def main() -> int:
    for language, cases in SURFACES.items():
        print(f"[{language}] 14 semantic classes")
        for kind, utterance in cases.items():
            result = interpret(language, kind, utterance)
            label = f"{language}/{kind}"
            check(f"{label} returned structured output", "error" not in result, str(result))

            if kind == "affirmative":
                check(f"{label} -> true", result.get("value") is True, str(result))
            elif kind == "negative":
                check(f"{label} -> false", result.get("value") is False, str(result))
            elif kind == "dont_know":
                check(f"{label} -> do-not-know", result.get("isDontKnow") is True,
                      str(result))
            elif kind == "repeat":
                check(f"{label} -> repeat", result.get("needsRepeat") is True, str(result))
            elif kind == "refusal":
                check(f"{label} -> refusal", result.get("isRefusal") is True, str(result))
            elif kind == "current":
                check(f"{label} -> one self-current mention",
                      scopes(result).count("self_current") == 1, str(result))
            elif kind == "family":
                check(f"{label} -> family only",
                      scopes(result).count("family") == 1
                      and "self_current" not in scopes(result), str(result))
            elif kind == "hypothetical":
                check(f"{label} -> hypothetical only",
                      scopes(result).count("hypothetical") == 1
                      and "self_current" not in scopes(result), str(result))
            elif kind == "negated":
                check(f"{label} -> negated only",
                      scopes(result).count("negated") == 1
                      and "self_current" not in scopes(result), str(result))
            elif kind == "mixed":
                check(f"{label} preserves family and self scopes",
                      scopes(result).count("family") == 1
                      and scopes(result).count("self_current") == 1, str(result))
            elif kind == "two_current":
                check(f"{label} preserves both self conditions",
                      scopes(result).count("self_current") == 2, str(result))
            elif kind == "distress":
                check(f"{label} -> distress", result.get("distressSignal") is True,
                      str(result))
            elif kind == "medication":
                check(f"{label} preserves medication",
                      result.get("mentionsCondition") is True
                      and bool(result.get("medications")), str(result))
            elif kind == "correction":
                value = result.get("value")
                check(f"{label} preserves corrected value",
                      isinstance(value, (int, float)) and float(value) == 68.0,
                      str(result))

    print(f"Executed {checks} live text-semantic checks across 42 language/class cases.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print("  -", failure)
        return 1
    print("Every declared semantic class passed in English, Hindi, and Hinglish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
