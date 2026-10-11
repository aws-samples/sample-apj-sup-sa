#!/usr/bin/env python3
"""Would a person reading the emailed record believe it?

THE GAP THIS CLOSES. tests/test_positive_disclosures.py already drives exactly
this call - tobacco, alcohol, a gallstone operation, Pantoprazole, a liver
function test, dengue fever - and it passed while the customer was emailed a
list of SEVENTEEN things "we could not identify", of which sixteen were noise:

    'Cigarettes'  'smoke'  'Beer'  'drink alcohol'
    'Pantoprazole, forty milligrams once a day'
    'liver function test'  'enzymes were high'
    'it is fully resolved now'  'the medicine'  'medication for it'
    'salbutamol inhaler'  x6

Every existing assertion passed because they all asked "did the branch open?"
and "was the answer stored?". None of them read the record the way a customer or
an underwriter reads it. Telling someone we could not identify "Beer" as a
diagnosis is absurd on its own; worse, it buried the one genuine item - dengue
fever - among sixteen others, and a reviewer facing that list stops reading.

AND IT RUNS IN ALL THREE LANGUAGES. Every full-call test in the suite pinned
language_hint="en" (test_positive_disclosures 240/243/257,
test_underwriting_stress 56/58/63, test_agentcore_deployed 102). Hindi and
Hinglish were covered per-phrase and at language selection, but no test had ever
driven a COMPLETE call to a finished record in either, so nothing verified that
a Devanagari or code-mixed caller ends up with a sane record.

Run:  ./.venv/bin/python tests/test_record_sanity.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod),
                os.path.join(_agent_common, _mod))

import orchestrator as orch  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

# The REAL email builder, not a reimplementation of its filtering. Which rows
# reach the customer is a rule that belongs in one place; asserting against a copy
# of it here would pass happily while the shipped letter said something else.
sys.path.insert(0, os.path.join(ROOT, "lambdas", "send_confirmation"))
import handler as mail  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail[:200]}" if detail else ""))
    if not ok:
        failures.append(label)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_record_sanity")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")
_ITEM = re.compile(r"^A\d{3}$")

# The same caller, the same disclosures, in three languages. Content is held
# constant on purpose: if the record differs between languages, the difference is
# the defect.
SAID = {
    "en": {
        "A010": "Yes, speaking",
        "A020": "Yes, that is fine, go ahead",
        "A030": "22 March 1987",
        "A100": "165 centimetres",
        "A110": "62 kilograms",
        "A230": "Yes, I have asthma",
        "A400": "Yes, I smoke",
        "A410": "Cigarettes, about ten a day, for fifteen years",
        "A420": "Yes, I drink sometimes",
        "A430": "Beer, around four bottles a week, for about ten years",
        "A500": "Yes, I was hospitalised once",
        "A510": "Gallstone surgery",
        "A520": "In 2019",
        "A530": "Yes, it is fully resolved now",
        "A540": "Yes, I am still taking medication for it",
        "A550": "Pantoprazole, forty milligrams once a day",
        "A600": "Yes, one test came back abnormal",
        "A610": "A liver function test in 2020, the enzymes were high",
        "A620": "Yes, there is something in the family",
        "A630": "My father has type 2 diabetes",
        "A640": "No, never",
        "A900": "Yes",
        "A910": "I had dengue fever in 2021 and recovered fully",
        "A920": "No, I do not have an ABHA number",
    },
    "hi": {
        "A010": "\u0939\u093e\u0901, \u092e\u0948\u0902 \u092c\u094b\u0932 "
                "\u0930\u0939\u0940 \u0939\u0942\u0901",
        "A020": "\u0939\u093e\u0901, \u0920\u0940\u0915 \u0939\u0948, "
                "\u0906\u0917\u0947 \u092c\u0922\u093c\u093f\u092f\u0947",
        "A030": "22 \u092e\u093e\u0930\u094d\u091a 1987",
        "A100": "165 \u0938\u0947\u0902\u091f\u0940\u092e\u0940\u091f\u0930",
        "A110": "62 \u0915\u093f\u0932\u094b",
        "A230": "\u0939\u093e\u0901, \u092e\u0941\u091d\u0947 \u0905\u0938\u094d"
                "\u0925\u092e\u093e \u0939\u0948",
        "A400": "\u0939\u093e\u0901, \u092e\u0948\u0902 \u0938\u093f\u0917\u0930"
                "\u0947\u091f \u092a\u0940\u0924\u0940 \u0939\u0942\u0901",
        "A410": "\u0938\u093f\u0917\u0930\u0947\u091f, \u0926\u093f\u0928 "
                "\u092e\u0947\u0902 \u0926\u0938, \u092a\u0902\u0926\u0930\u0939 "
                "\u0938\u093e\u0932 \u0938\u0947",
        "A420": "\u0939\u093e\u0901, \u0915\u092c\u094d\u0939\u0940 \u0915\u092c"
                "\u094d\u0939\u0940",
        "A430": "\u092c\u0940\u092f\u0930, \u0939\u092b\u094d\u0924\u0947 "
                "\u092e\u0947\u0902 \u091a\u093e\u0930 \u092c\u094b\u0924\u0932, "
                "\u0926\u0938 \u0938\u093e\u0932 \u0938\u0947",
        "A500": "\u0939\u093e\u0901, \u090f\u0915 \u092c\u093e\u0930 \u0905\u0938"
                "\u094d\u092a\u0924\u093e\u0932 \u092e\u0947\u0902 \u092d\u0930"
                "\u094d\u0924\u0940 \u0939\u0941\u0908 \u0925\u0940",
        "A510": "\u092a\u093f\u0924\u094d\u0924 \u0915\u0940 \u092a\u0925\u0930"
                "\u0940 \u0915\u093e \u0911\u092a\u0930\u0947\u0936\u0928",
        "A520": "2019 \u092e\u0947\u0902",
        "A530": "\u0939\u093e\u0901, \u0905\u092c \u092a\u0942\u0930\u0940 "
                "\u0924\u0930\u0939 \u0920\u0940\u0915 \u0939\u0948",
        "A540": "\u0939\u093e\u0901, \u0905\u092d\u0940 \u092d\u0940 \u0926\u0935"
                "\u093e \u0932\u0947 \u0930\u0939\u0940 \u0939\u0942\u0901",
        "A550": "\u092a\u0948\u0902\u091f\u094b\u092a\u094d\u0930\u093e\u091c"
                "\u094b\u0932, \u091a\u093e\u0932\u0940\u0938 \u092e\u093f\u0932"
                "\u0940\u0917\u094d\u0930\u093e\u092e \u0926\u093f\u0928 "
                "\u092e\u0947\u0902 \u090f\u0915 \u092c\u093e\u0930",
        "A600": "\u0939\u093e\u0901, \u090f\u0915 \u091c\u093e\u0902\u091a "
                "\u092e\u0947\u0902 \u0917\u0921\u093c\u092c\u0921\u093c \u0925\u0940",
        "A610": "2020 \u092e\u0947\u0902 \u0932\u093f\u0935\u0930 \u092b\u0902"
                "\u0915\u094d\u0936\u0928 \u091f\u0947\u0938\u094d\u091f, \u0910"
                "\u0902\u091c\u093e\u0907\u092e \u092c\u0922\u093c\u0947 \u0939"
                "\u0941\u090f \u0925\u0947",
        "A620": "\u0939\u093e\u0901, \u092a\u0930\u093f\u0935\u093e\u0930 "
                "\u092e\u0947\u0902 \u0915\u0941\u091b \u0939\u0948",
        "A630": "\u092e\u0947\u0930\u0947 \u092a\u093f\u0924\u093e \u0915\u094b "
                "\u091f\u093e\u0907\u092a \u091f\u0942 \u0921\u093e\u092f\u092c"
                "\u0940\u091f\u0940\u091c\u093c \u0939\u0948",
        "A640": "\u0928\u0939\u0940\u0902, \u0915\u092d\u0940 \u0928\u0939\u0940\u0902",
        "A900": "\u0939\u093e\u0901",
        "A910": "\u092e\u0941\u091d\u0947 2021 \u092e\u0947\u0902 \u0921\u0947\u0902"
                "\u0917\u0942 \u0939\u0941\u0906 \u0925\u093e \u0914\u0930 "
                "\u092a\u0942\u0930\u0940 \u0924\u0930\u0939 \u0920\u0940\u0915 "
                "\u0939\u094b \u0917\u092f\u093e",
        "A920": "\u0928\u0939\u0940\u0902, \u092e\u0947\u0930\u093e ABHA "
                "\u0928\u0939\u0940\u0902 \u0939\u0948",
    },
    "hinglish": {
        "A010": "Haan, main bol rahi hoon",
        "A020": "Haan theek hai, aage badhiye",
        "A030": "22 March 1987",
        "A100": "165 centimetre",
        "A110": "62 kilo",
        "A230": "Haan, mujhe asthma hai",
        "A400": "Haan, main cigarette peeti hoon",
        "A410": "Cigarette, din mein das, pandrah saal se",
        "A420": "Haan, kabhi kabhi peeti hoon",
        "A430": "Beer, hafte mein chaar bottle, das saal se",
        "A500": "Haan, ek baar hospital mein bharti hui thi",
        "A510": "Pitt ki pathri ka operation",
        "A520": "2019 mein",
        "A530": "Haan, ab poori tarah theek hai",
        "A540": "Haan, abhi bhi dawa le rahi hoon",
        "A550": "Pantoprazole, chalees milligram din mein ek baar",
        "A600": "Haan, ek jaanch mein gadbad thi",
        "A610": "2020 mein liver function test, enzyme badhe hue the",
        "A620": "Haan, parivaar mein kuch hai",
        "A630": "Mere pita ko type two diabetes hai",
        "A640": "Nahi, kabhi nahi",
        "A900": "Haan",
        "A910": "Mujhe 2021 mein dengue hua tha aur poori tarah theek ho gaya",
        "A920": "Nahi, mera ABHA nahi hai",
    },
}

NO = {"en": "No", "hi": "\u0928\u0939\u0940\u0902", "hinglish": "Nahi"}

PROTOCOL = {
    "en": {"medVerify": "Yes, that is the medicine",
           "summaryConfirm": "Yes, that is all correct",
           "distressCheck": "No, I am alright",
           "refusalConfirm": "No, I am happy to answer",
           "explanationCheck": "Yes, that is what I meant",
           "explanationClarify": "Yes, that is what I meant"},
    "hi": {"medVerify": "\u0939\u093e\u0901, \u092f\u0939\u0940 \u0926\u0935\u093e "
                        "\u0939\u0948",
           "summaryConfirm": "\u0939\u093e\u0901, \u0938\u092c \u0938\u0939\u0940 "
                             "\u0939\u0948",
           "distressCheck": "\u0928\u0939\u0940\u0902, \u092e\u0948\u0902 \u0920"
                            "\u0940\u0915 \u0939\u0942\u0901",
           "refusalConfirm": "\u0928\u0939\u0940\u0902, \u092e\u0948\u0902 "
                             "\u091c\u0935\u093e\u092c \u0926\u0942\u0901\u0917\u0940",
           "explanationCheck": "\u0939\u093e\u0901, \u092f\u0939\u0940 \u092e"
                               "\u0924\u0932\u092c \u0925\u093e",
           "explanationClarify": "\u0939\u093e\u0901, \u092f\u0939\u0940 \u092e"
                                 "\u0924\u0932\u092c \u0925\u093e"},
    "hinglish": {"medVerify": "Haan, yahi dawa hai",
                 "summaryConfirm": "Haan, sab sahi hai",
                 "distressCheck": "Nahi, main theek hoon",
                 "refusalConfirm": "Nahi, main jawab doongi",
                 "explanationCheck": "Haan, yahi matlab tha",
                 "explanationClarify": "Haan, yahi matlab tha"},
}

# WHAT THIS CALLER ACTUALLY DISCLOSED. A read-back is agreed to only if it names
# one of these, and refused otherwise.
#
# Getting this list wrong in EITHER direction invalidates the suite. Agreeing to
# everything means the record fills with diagnoses the caller never had and every
# check still passes - the existing positive-disclosures suite carries a comment
# about exactly that, having once accepted Cirrhosis of Liver. Refusing everything
# is the mirror image, and it is the mistake this suite made first: with only
# "asthma" here, the agent correctly retrieved K80 for the gallstone operation and
# A97 for dengue, offered both for confirmation, and the harness said no to both -
# then the suite blamed the classifier for a rejection it had caused itself.
#
# This caller discloses three things: asthma, a gallstone operation, and past
# dengue. All three are real disclosures and all three deserve a yes.
#
# GALLBLADDER TERMS ONLY, never bare "pathri"/"पथरी". That word means stone in
# general and belongs to N20 Kidney Stones too. Keeping it out means this suite
# still fails if a gallstone disclosure ever comes back as the wrong organ, which
# is the documented original K80 defect.
CLAIMS = {
    "en": ("asthma", "gallstone", "gall bladder", "gallbladder", "cholelith",
           "dengue"),
    "hi": ("\u0905\u0938\u094d\u0925\u092e\u093e", "asthma",
           "\u092a\u093f\u0924\u094d\u0924\u093e\u0936\u092f",      # gallbladder
           "\u092a\u093f\u0924\u094d\u0924 \u0915\u0940",           # of the bile
           "gallstone", "cholelith",
           "\u0921\u0947\u0902\u0917\u0942", "dengue"),
    "hinglish": ("asthma", "pitt ki", "pittashay", "gallstone", "gall bladder",
                 "gallbladder", "cholelith", "dengue"),
}

# CONDITION-AWARE follow-up answers. The existing harness returns "a salbutamol
# inhaler" for ANY treatment question, so the gallstone record ended up saying the
# patient takes an inhaler for gallstones - clinically incoherent, and it would
# also mask genuine cross-contamination between two conditions' protocols. Here
# the treatment answer depends on which condition is being discussed.
TREATMENT = {
    "en": {"asthma": "A salbutamol inhaler when I need it",
           "gall": "Pantoprazole tablets", "other": "Nothing at the moment"},
    "hi": {"asthma": "\u091c\u0930\u0942\u0930\u0924 \u092a\u0930 \u0938\u093e"
                     "\u0932\u094d\u092c\u094d\u092f\u0942\u091f\u093e\u092e"
                     "\u094b\u0932 \u0907\u0928\u094d\u0939\u0947\u0932\u0930",
           "gall": "\u092a\u0948\u0902\u091f\u094b\u092a\u094d\u0930\u093e\u091c"
                   "\u094b\u0932 \u0917\u094b\u0932\u0940",
           "other": "\u0905\u092d\u0940 \u0915\u0941\u091b \u0928\u0939\u0940\u0902"},
    "hinglish": {"asthma": "Zarurat par salbutamol inhaler",
                 "gall": "Pantoprazole goli",
                 "other": "Abhi kuch nahi"},
}


def reply_to(reply: dict, lang: str, email: str, ctx: dict) -> str:
    ref = reply.get("askedRef") or ""
    kind = reply.get("askedKind") or ""
    utt = (reply.get("agentUtterance") or "").lower()
    said = SAID[lang]

    if kind == "confirmation":
        return ("Yes, that is right" if lang == "en" else
                "\u0939\u093e\u0901, \u0938\u0939\u0940 \u0939\u0948" if lang == "hi"
                else "Haan, sahi hai") if any(
            c.lower() in utt for c in CLAIMS[lang]) else (
            "No, that is not something I have" if lang == "en" else
            "\u0928\u0939\u0940\u0902, \u092e\u0941\u091d\u0947 \u0935\u094b "
            "\u0928\u0939\u0940\u0902 \u0939\u0948" if lang == "hi" else
            "Nahi, mujhe wo nahi hai")

    if kind == "conditionDetail":
        if "liver" in utt or "test" in utt or "\u091c\u093e\u0902\u091a" in utt:
            return said["A610"]
        if any(w in utt for w in ("hospital", "surgery", "operation",
                                  "\u0911\u092a\u0930\u0947\u0936\u0928")):
            return said["A510"]
        if any(w in utt for w in ("smok", "tobacco", "cigarette",
                                  "\u0938\u093f\u0917\u0930\u0947\u091f")):
            return said["A410"]
        return said["A230"]

    if kind in PROTOCOL[lang]:
        return PROTOCOL[lang][kind]
    if ref in said:
        return said[ref]
    if "email" in utt or "@" in utt:
        return email

    if ref and not _ITEM.match(ref):
        # Which condition is this protocol about? Track it so the treatment
        # answer belongs to the right disease.
        # WHICH CONDITION IS THIS PROTOCOL ABOUT? The agent names it in the prompt
        # while it is still ambiguous ("... for Asthma?"), so read it from there
        # rather than guessing from English keywords - guessing left both
        # conditions with the same treatment answer in Hindi, which made the
        # cross-contamination assertion vacuous.
        topic = ctx.get("topic") or "other"
        if any(w in utt for w in ("asthma", "\u0905\u0938\u094d\u0925\u092e\u093e",
                                  "inhaler", "\u0907\u0928\u094d\u0939\u0947\u0932",
                                  "pump", "\u0938\u093e\u0902\u0938")):
            ctx["topic"] = topic = "asthma"
        elif any(w in utt for w in ("gall", "cholelith", "pitt", "pathri",
                                    "\u092a\u093f\u0924\u094d\u0924",
                                    "\u092a\u0925\u0930\u0940")):
            ctx["topic"] = topic = "gall"
        # HOSPITAL IS CHECKED BEFORE TREATMENT. "Have you needed hospital
        # treatment or a nebuliser for breathing?" contains the word treatment, so
        # a naive treatment match answers a yes/no question with the name of a
        # drug. That is what produced A920's sibling defect in the live record:
        # FU_RESP_HOSPITALISATION was answered "a salbutamol inhaler" three times,
        # failed boolean coercion each time, and was stored as 'not_captured'.
        if any(w in utt for w in ("hospital", "admitted", "nebuli",
                                  "\u0905\u0938\u094d\u092a\u0924\u093e\u0932",
                                  "bharti")):
            return NO[lang]
        if any(w in utt for w in ("treatment", "medicat", "medicine",
                                  "\u0926\u0935\u093e", "dawa", "ilaaj")):
            return TREATMENT[lang][topic]
        if any(w in utt for w in ("how often", "how many times", "\u0915\u093f"
                                  "\u0924\u0928\u0940 \u092c\u093e\u0930")):
            return ("About twice a week" if lang == "en" else
                    "\u0939\u092b\u094d\u0924\u0947 \u092e\u0947\u0902 \u0926"
                    "\u094b \u092c\u093e\u0930" if lang == "hi" else
                    "Hafte mein do baar")
        if any(w in utt for w in ("diagnos", "when", "how long", "\u0915\u092c",
                                  "kab")):
            return "2015"
        if any(w in utt for w in ("control", "\u0928\u093f\u092f\u0902\u0924"
                                  "\u094d\u0930\u0923", "kaabu")):
            return ("Yes, it is well controlled" if lang == "en" else
                    "\u0939\u093e\u0901, \u0915\u093e\u092c\u0942 \u092e\u0947"
                    "\u0902 \u0939\u0948" if lang == "hi" else "Haan, kaabu mein hai")
        if any(w in utt for w in ("surgery", "operation", "\u0911\u092a\u0930"
                                  "\u0947\u0936\u0928")):
            ctx["topic"] = "gall"
            return ("Yes, I had the operation" if lang == "en" else
                    "\u0939\u093e\u0901, \u0911\u092a\u0930\u0947\u0936\u0928 "
                    "\u0939\u0941\u0906 \u0925\u093e" if lang == "hi" else
                    "Haan, operation hua tha")
        return NO[lang]
    return NO[lang]


# Words that can NEVER be a diagnosis. If any of these reaches the customer as
# "we could not identify this", the non-condition guard has a hole.
NOT_CONDITIONS = (
    "salbutamol", "pantoprazole", "inhaler", "tablet", "goli", "milligram",
    "cigarette", "smoke", "tobacco", "beer", "alcohol", "drink",
    "liver function test", "enzyme", "blood test", "\u091c\u093e\u0902\u091a",
    "resolved", "\u0920\u0940\u0915 \u0939\u0948", "medicine", "medication",
    "\u0926\u0935\u093e", "dawa", "\u0938\u093f\u0917\u0930\u0947\u091f",
)


def run(lang: str, email: str) -> dict:
    session = st.start_session(PHONE, st.get_customer_by_phone(PHONE), FORM_ID)
    call_id = session["callId"]
    st.set_language(call_id, lang, confirmed=True)
    ctx: dict = {}
    turn = 1
    reply = orch.handle_turn(call_id, "", turn, language_hint=lang)
    for _ in range(95):
        if reply.get("isComplete") or reply.get("stopQuestionnaire"):
            break
        said = reply_to(reply, lang, email, ctx)
        turn += 1
        reply = orch.handle_turn(call_id, said, turn, language_hint=lang)
    return {"callId": call_id, "session": st.get_session(call_id) or {},
            "complete": bool(reply.get("isComplete")), "turns": turn}


def main() -> int:  # noqa: PLR0915
    cust = st.get_customer_by_phone(PHONE)
    if not cust:
        print(f"FAIL: no seeded customer for {PHONE}")
        return 1
    email = cust.get("customerEmail") or "test@example.com"

    for lang in ("en", "hi", "hinglish"):
        print(f"\n{'=' * 74}\n[{lang}] a complete call, then read the record as a "
              f"person would\n{'=' * 74}")
        r = run(lang, email)
        sess = r["session"]
        answers = sess.get("answers") or {}
        # WHAT THE CUSTOMER IS ACTUALLY SHOWN, taken from the rendered letter.
        # The session keeps every phrase the classifier was given, for the
        # underwriter; the letter carries only genuine doubt about a diagnosis.
        message = mail.build_message(r["callId"])
        plain = message["plain"]
        review = (plain.split("NOTED FOR REVIEW", 1)[1]
                  if "NOTED FOR REVIEW" in plain else "")
        for stop in ("\nDECLARATION", "\nHOW THIS WAS VERIFIED"):
            if stop in review:
                review = review.split(stop, 1)[0]
        phrases = [ln.strip().strip('"') for ln in review.splitlines()
                   if ln.strip().startswith('"')]
        mentions = phrases
        stored = sess.get("unclassifiedMentions") or []
        declared = sess.get("declaredConditions") or []
        codes = [d.get("icd10") for d in declared]
        print(f"    {r['turns']} turns, {len(answers)} answers, "
              f"{len(declared)} conditions {codes}; {len(stored)} phrases stored "
              f"for the underwriter, {len(phrases)} shown to the customer")
        if phrases:
            print(f"    customer sees: {phrases}")

        check(f"[{lang}] the call ran to completion", r["complete"])

        # THE DEFECT. Not one review item may be a drug, a habit, a test or a
        # status phrase.
        offenders = [p for p in phrases
                     if any(w in p.lower() for w in NOT_CONDITIONS)]
        check(f"[{lang}] no medication, habit, test or status phrase is shown to "
              f"the customer as an unidentified condition",
              not offenders, f"offenders={offenders}")

        # Duplicates made the list look longer and less credible.
        dupes = {p for p in phrases if phrases.count(p) > 1}
        check(f"[{lang}] the review list has no duplicates", not dupes,
              f"duplicated={sorted(dupes)}")

        # A short list is the point: a reviewer must actually read it.
        check(f"[{lang}] the customer's review list is short enough to be read",
              len(phrases) <= 4, f"{len(phrases)} items: {phrases}")

        # The underwriter must still be able to see everything, so the cleanup
        # must be a presentation rule and not silent deletion.
        check(f"[{lang}] nothing was discarded - the underwriter still has every "
              f"phrase", len(stored) >= len(phrases),
              f"stored={len(stored)} shown={len(phrases)}")

        # The conditions themselves must survive in every language.
        check(f"[{lang}] asthma is recorded", "J45" in codes, str(codes))
        check(f"[{lang}] the gallstone operation is recorded",
              "K80" in codes, str(codes))

        # Follow-up answers must stay attached to their own condition.
        fa = sess.get("followUpAnswers") or {}
        unnamespaced = [k for k in fa if "::" not in k]
        check(f"[{lang}] every follow-up answer is filed under its condition",
              not unnamespaced, f"global keys={unnamespaced}")
        treat = {k: str((v or {}).get("value") or "")
                 for k, v in fa.items() if k.endswith("FU_TREATMENT")}
        if len(treat) >= 2:
            vals = list(treat.values())
            check(f"[{lang}] two conditions do not share one treatment answer",
                  len(set(vals)) == len(vals), str(treat))

        # Lifestyle and test answers must still be CAPTURED - the fix must not
        # have silenced them, only stopped them being classified as diseases.
        for ref, what in (("A410", "tobacco detail"), ("A430", "alcohol detail"),
                          ("A550", "medication list"), ("A610", "test detail")):
            check(f"[{lang}] {what} ({ref}) is still recorded",
                  ref in answers and answers[ref] not in (None, "", []),
                  f"{ref}={answers.get(ref)!r}")

    # NEGATIVE CONTROL. Everything above checks that noise is kept OUT of the
    # letter, and a filter that hid every row would satisfy all of it while
    # silently removing the customer's only chance to correct a real miss. So
    # prove the opposite case still works: a genuinely unidentified disclosure
    # must reach the customer.
    print(f"\n{'=' * 74}\nnegative control: a real unidentified condition is still "
          f"shown\n{'=' * 74}")
    cust2 = st.get_customer_by_phone(PHONE)
    control = st.start_session(PHONE, cust2, FORM_ID)
    cid = control["callId"]
    st.set_language(cid, "en", confirmed=True)
    st.record_unclassified_mention(
        cid, "some rare illness I cannot name", item_id="A910",
        reason="no confident ICD-10 match")
    st.mark_noise_phrase(cid, "Pantoprazole forty milligrams")
    st.record_unclassified_mention(
        cid, "Pantoprazole forty milligrams", item_id="A550",
        reason="no confident ICD-10 match")
    st.record_declaration(cid)
    plain = mail.build_message(cid)["plain"]
    check("a genuine unidentified disclosure IS shown to the customer",
          "some rare illness I cannot name" in plain,
          "the filter is hiding everything, including real misses")
    check("a drug name is NOT shown to the customer",
          "Pantoprazole forty milligrams" not in plain, plain[-400:])
    stored = (st.get_session(cid) or {}).get("unclassifiedMentions") or []
    check("but the drug name is still on the record for the underwriter",
          any("Pantoprazole" in str(m.get("phrase")) for m in stored),
          str(stored))

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} record-sanity checks passed in English, Hindi and "
          f"Hinglish.")
    print("  The review list now carries conditions only, and a customer reading")
    print("  the email is not told we failed to identify 'Beer'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
