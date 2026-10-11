"""
Deterministic turn orchestration.

This module owns the protocol. It decides what is asked, in what order, and when
state is written. The model's only contribution is interpreting the customer's
words (see extract.py) and supplying a short acknowledgement.

The consequence worth stating plainly: the audit trail is now true by
construction. save_answer is called because the code path reached it, not because
a model remembered to. Neptune's mandated follow-ups are asked because they are
queued in DynamoDB, not because a model chose to fetch them. When an auditor asks
"was every required question asked?", the answer is a property of the system
rather than a property of a particular model's instruction-following on the day.

Question priority on each turn:
  1. a classification waiting to be confirmed  (red flags must not slip through)
  2. the head of the protocol follow-up queue  (Neptune-mandated)
  3. the next unanswered form item             (schema order)
  4. nothing left -> completion
"""
from __future__ import annotations

import logging
import os
import re
import sys
import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
import hvi_prompts  # noqa: E402
import hvi_state as st  # noqa: E402

import extract  # noqa: E402
import tools  # noqa: E402

# Bound at import time, NOT accessed through the `extract` module attribute:
# the test harnesses replace orch.extract with a scripted double that carries
# only understand/coerce, and retraction's cross-script matching must keep
# working under that swap.
from extract import _consonant_skeleton as _skeleton_of  # noqa: E402
from extract import _edit_distance as _skeleton_distance  # noqa: E402

# Same reasoning, for the same reason: the suites swap orch.st for MemoryState, and
# a PURE text function is not something a state double has any business faking. Went
# through st.fill_placeholders first and every double raised AttributeError, which
# is the failure this import-time binding exists to prevent.
from hvi_state import fill_placeholders as _fill_placeholders  # noqa: E402

log = logging.getLogger("hvi-agent.orchestrator")

MAX_REPEATS = int(os.environ.get("MAX_REPEATS", "3"))

# Classification floor for DEFERRED mentions - phrases spoken mid-protocol and
# classified later. Raised from 0.78 after a live call: "सास फूलती थी मेरी",
# said as a symptom answer during the cancer follow-ups, was deferred and
# nearest-neighboured to Uterine Fibroid at 0.8064, read back a minute later,
# and confirmed by a disoriented caller. Mid-protocol speech is dominated by
# symptoms and details OF THE ACTIVE condition, so it earns a stricter floor
# than a direct disclosure; genuine second diseases either hit an exact graph
# synonym (which bypasses the floor) or score above it, and anything below is
# recorded as an unclassified mention for human review rather than lost.
_DEFERRED_MIN_SCORE = float(os.environ.get("DEFERRED_MENTION_MIN_SCORE", "0.85"))

# Floor for a FIRST, DIRECT disclosure the clinical graph does not corroborate.
# Deliberately the SAME 0.78 the other unvetted paths use, and deliberately NOT
# higher - a measurement settled that, after a first attempt at 0.85 was wrong in
# both directions:
#
#   Cyst.                                       D25  0.8971  uncorroborated
#   Cholesterol.                                E78  0.8982  uncorroborated
#   chest pain when walking                     I25  0.8863  uncorroborated, RED FLAG
#   Mujhe chalne par seene mein dard hota hai   I25  0.8786  uncorroborated, RED FLAG
#   heart disease                               I25  0.8756  uncorroborated, RED FLAG
#   शुलूज़े (garbled)                            E11  0.7668  uncorroborated
#
# A garbled echo and a real one-word disclosure are the SAME OBJECT to a
# similarity index: 0.8971 against 0.8982. No threshold separates them, and 0.85
# sat in the middle of the band where genuine cardiac disclosures live - so it let
# the garbage through while intermittently suppressing chest pain on exertion,
# which is the single worst thing here to drop. It failed intermittently because
# the extractor hands over a slightly different sub-phrase each time.
#
# What DOES separate them is not available to the classifier: only the caller
# knows whether they have a cyst. That is what the read-back is for, and an
# uncorroborated match always sets needsConfirmation, so it is always read back
# rather than recorded. The real defect behind the "Cyst." call was the caller
# rubber-stamping that read-back - and the browser echo, where the agent's own
# "हाँ" confirmed its own question, is the mechanism that produced the
# rubber-stamping. That is fixed in the media layer, where it belongs.
#
# So this floor is set where it can only help: below the genuine band (0.87+) and
# above the noise that carries no clinical content at all.
_DIRECT_MIN_SCORE = float(os.environ.get("DIRECT_DISCLOSURE_MIN_SCORE", "0.78"))

# How close a declared condition has to be to the best match before an unvetted
# phrase is read as detail of that condition rather than a new disease. See the
# elaboration guard in _handle_condition.
_ELABORATION_TIE_MARGIN = float(
    os.environ.get("ELABORATION_TIE_MARGIN", "0.02"))


# --------------------------------------------------------------------------- #
# language selection
#
# The call opens by ASKING which language to use, rather than trusting the CRM's
# HVI_Preferred_Language__c. The CRM value is a guess made by whoever typed the
# record; being spoken to in the wrong language is the most immediate way for this
# call to fail, and unlike almost everything else here it is trivially fixable by
# asking. So the CRM value survives only as the pre-answer default.
#
# TWO LANGUAGES ARE OFFERED, NOT THREE. Hinglish is not a menu option because it is
# not how anyone answers "which language?" - nobody asks for "code-mixed". It stays
# fully supported as INPUT: the customer may code-mix freely in either mode and be
# understood. Whisper transcribes code-mixed speech verbatim, and script_of() in
# common/hvi_stt.py classifies a genuinely mixed transcript as "mixed".
#
# The consequence for the agent's OUTPUT is deliberate: once the customer has
# chosen, the agent stops language-matching them. See the gate on detectedLanguage
# in handle_turn - overriding an explicit answer would be a bug, not politeness.
# --------------------------------------------------------------------------- #

# Two attempts, then infer. A third "sorry, English or Hindi?" on a call where the
# questionnaire has not started is worse than picking the likelier answer and
# letting the customer correct us.
LANGUAGE_ASK_MAX = 2

# The text itself lives in common/hvi_prompts.py because tools/build_prompt_cache.py
# needs the byte-identical string to pre-synthesise it. A copy here that drifted by
# one character would silently turn every call's first utterance back into a 2-3
# second silence while the GPU synthesises it live.
LANGUAGE_CHOICE_PROMPT = hvi_prompts.LANGUAGE_CHOICE_PROMPT
LANGUAGE_CHOICE_REPROMPT = hvi_prompts.LANGUAGE_CHOICE_REPROMPT

# Deterministic, NOT a model call. This is a two-way choice on a closed vocabulary,
# and spending ~650ms of LLM latency plus a failure mode on it would be
# indefensible - especially as it sits on the very first turn, where the customer
# has just picked up the phone and silence reads as a dead line.
_EN_TOKENS = (
    "english", "englis", "inglish", "ingles", "angrezi", "angreji", "angrez",
    # Devanagari renderings, which is what STT returns when the customer says the
    # English word inside a Hindi sentence.
    "अंग्रेज़ी", "अंग्रेजी", "इंग्लिश", "इंगलिश",
)
_HI_TOKENS = ("hindi", "hindee", "हिंदी", "हिन्दी")


def parse_language_choice(utterance: str) -> str | None:
    """Read a language choice. Returns "en", "hi", or None when unclear.

    None covers three genuinely different cases that all want the same handling -
    ask again: nothing recognisable, and BOTH languages named ("Hindi English dono",
    or someone reading the menu back). Guessing on an ambiguous answer would be
    worse than one more question.
    """
    u = (utterance or "").strip().lower()
    if not u:
        return None
    has_en = any(t in u for t in _EN_TOKENS)
    has_hi = any(t in u for t in _HI_TOKENS)
    if has_en and has_hi:
        return None
    if has_en:
        return "en"
    if has_hi:
        return "hi"
    return None


# Short enough that the utterance is the request and not a sentence that merely
# mentions a language. "Hindi" and "Hindi mein boliye" qualify; a medical answer
# that happens to contain the word does not.
_SWITCH_MAX_WORDS = 4


def parse_language_switch(utterance: str) -> str | None:
    """A language name said AFTER the choice was settled. Returns "en"/"hi"/None.

    WHY THIS IS SEPARATE FROM parse_language_choice
    A live caller said "Hindi" three times. STT heard "Indeed." then produced
    garbage, and only on the third attempt transcribed it correctly - by which
    point the selection window had closed, the fallback had inferred English from
    the Latin script of the garbage, and the correct answer was handed to the
    medical extractor as a reply to an identity question. The caller had asked for
    Hindi, plainly, and got English for the rest of the call.

    Two attempts is the right cap for the opening menu - more would be an IVR - so
    the fix is not to keep asking. It is to accept the instruction whenever it
    arrives, because a caller naming a language is never answering a question about
    their health.

    DELIBERATELY NARROW. It requires a short utterance containing exactly one
    language name, so "I take medicine for my sugar" cannot trip it, and neither
    can "Hindi ya English dono chalega" - which names both and means neither. This
    only overrides an explicit choice with another explicit choice; it is not the
    detectedLanguage auto-switch, which stays gated because inferring from the
    mixture of a Hinglish sentence would flap all call.
    """
    u = (utterance or "").strip().lower()
    if not u or len(u.split()) > _SWITCH_MAX_WORDS:
        return None
    return parse_language_choice(u)


# Words that carry no clinical information. An utterance made only of these is an
# affirmation and nothing more, however long it is: "yes I do have that" names no
# condition. Covers English, Hindi and Hinglish because a caller in en mode answers
# in whichever comes out.
_AFFIRMATION_ONLY = {
    "yes", "yeah", "yep", "yup", "ya", "sure", "correct", "right", "true", "ok",
    "okay", "affirmative", "definitely", "absolutely", "indeed",
    "haan", "haa", "ha", "hain", "ji", "haanji", "jee", "bilkul", "sahi",
    "हाँ", "हां", "जी", "बिल्कुल", "सही",
    # Filler and function words that can pad an affirmation without adding content.
    "i", "do", "did", "have", "had", "has", "it", "that", "this", "was", "is",
    "am", "a", "an", "the", "to", "me", "my", "mujhe", "mera", "meri", "hai",
    "tha", "thi", "hua", "and", "but", "well", "um", "uh", "so", "actually",
    "मुझे", "मेरा", "मेरी", "है", "था", "थी", "हुआ",
}


# The two items whose values constrain each other. Read from the schema's own ids so
# a renumbered form fails loudly here rather than silently skipping the check.
_HEIGHT_ITEM = "A100"
_WEIGHT_ITEM = "A110"

# Generous bounds. Real people sit roughly 12-60; anorexia nervosa and severe obesity
# both fall inside that, and this is not trying to be a clinical finding. It is
# rejecting arithmetic that cannot describe a body, so the limits are set where a
# genuine outlier is still accepted and a transcription error is not.
_BMI_MIN, _BMI_MAX = 10.0, 65.0


def implausible_bmi(answers: dict) -> str | None:
    """Reason the recorded height and weight cannot belong to the same person.

    Returns None when they are fine, or when either is missing - a single value is
    the individual bounds' job, and complaining before both are known would re-ask a
    question that was answered perfectly well.

    Exists because 183cm and 30kg both passed their own validation on a live call.
    Individually plausible, jointly impossible, and it would have reached the
    underwriter looking like a real measurement.
    """
    try:
        h = float(answers.get(_HEIGHT_ITEM))
        w = float(answers.get(_WEIGHT_ITEM))
    except (TypeError, ValueError):
        return None
    if h <= 0 or w <= 0:
        return None
    bmi = w / ((h / 100.0) ** 2)
    if bmi < _BMI_MIN:
        return (f"{w:.0f}kg at {h:.0f}cm gives a body mass index of {bmi:.0f}, "
                f"below the survivable minimum of {_BMI_MIN:.0f}")
    if bmi > _BMI_MAX:
        return (f"{w:.0f}kg at {h:.0f}cm gives a body mass index of {bmi:.0f}, "
                f"above the plausible maximum of {_BMI_MAX:.0f}")
    return None


# Framings that describe a RISK, a POSSIBILITY or someone else's condition rather
# than a diagnosis this caller has.
_HYPOTHETICAL = re.compile(
    r"\b(could have|might have|may have|may develop|might develop|could develop|"
    r"risk of|at risk|chance of|chances of|possibility of|possible that|"
    r"warned me|told me i might|said i could|worried about|afraid i|scared i|"
    r"in a few years|in future|in the future|later on|down the line|"
    r"if it gets worse|might become|could become|"
    r"my mother|my father|my brother|my sister|my family|runs in the family|"
    r"family history|my wife|my husband|my son|my daughter)\b"
    r"|\b(ho sakta hai|ho sakti hai|khatra hai|risk hai|aage chal kar|"
    r"maa ko|pita ko|bhai ko|behen ko|parivar mein)\b"
    r"|हो सकता है|हो सकती है|ख़तरा|खतरा|आगे चलकर|परिवार में|माँ को|पिता को", re.I)


def is_hypothetical_mention(utterance: str) -> bool:
    """Is this a risk, a possibility, or somebody else's illness - not a diagnosis?

    A live caller said "They did say I could have heart problems in a few years."
    That is a doctor describing FUTURE RISK. The classifier, which has no notion of
    tense or certainty, returned Chronic Ischaemic Heart Disease and offered to record
    it as a current condition. On an insurance application that is a material
    misstatement, and it would have been the applicant's word for it.

    Family history is in here for the same reason and is just as consequential: "my
    father had diabetes" is underwriting-relevant information about a relative, and
    recording it as the applicant's own diagnosis is simply false.

    Used to SUPPRESS automatic classification, never to discard the utterance - the
    words stay in the transcript, so an underwriter still sees the risk and the family
    history. What changes is that the agent stops asserting a diagnosis the caller
    never claimed.
    """
    return bool(_HYPOTHETICAL.search(utterance or ""))


# A PURE TIME EXPRESSION IS NOT A DISEASE. Asked when a condition was
# diagnosed, callers answer "तीन साल पहले", "पांच साल पहले।", "छह साल पहले",
# "two years back" - and on a live call each of those was sent to the classifier,
# scored 0.80-0.83 against the nearest ICD-10 vector, and filed as an
# unresolvable health mention. Nothing was minted, but the noise is real and the
# same path is one bad margin away from proposing a diagnosis built out of a
# date. An embedding index returns its nearest neighbour for ANY string; the
# defence has to be refusing to ask it about strings that carry no clinical
# content.
#
# Deliberately requires the WHOLE phrase to be time words plus optional
# yes/no - "sugar teen saal pehle" still classifies, because it names something.
_TIME_ONLY = re.compile(
    # optional leading yes/no: "haan, teen saal pehle"
    r"^[\s\W]*(?:(?:haan|haa|ji|yes|nahi|nahin|no|हाँ|हां|जी|नहीं|नही)[\s,\.।]+)?"
    # optional approximator, which may PRECEDE a number ("about five years ago")
    r"(?:(?:about|around|approximately|lagbhag|karib|qareeb|लगभग|करीब|तकरीबन)"
    r"[\s\W]*)?"
    # optional article stacked on a quantifier: "a few days ago", "a couple of"
    r"(?:(?:a|an)\s+)?"
    # quantity: digits, or a word numeral, or a vague quantifier
    r"(?:\d+|"
    r"एक|दो|तीन|चार|पाँच|पांच|छह|छे|सात|आठ|नौ|दस|ग्यारह|बारह|कुछ|कई|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"ek|do|teen|char|chaar|paanch|panch|chhe|chha|saat|aath|nau|das|"
    r"kuch|kai|couple|few|several|an|a"
    r")[\s\W]*"
    # the time unit itself - this is what makes it a duration
    r"(?:साल|सालों|बरस|वर्ष|महीने|महीना|महीनों|माह|दिन|दिनों|हफ्ते|हफ़्ते|सप्ताह|"
    r"saal|saalon|baras|varsh|mahine|mahina|mahinon|maah|din|dinon|hafte|"
    r"hafta|week|weeks|year|years|month|months|day|days"
    r")[\s\W]*"
    # optional "ago" tail
    r"(?:पहले|पहिले|पूर्व|हो\s*गए|हो\s*गये|हुए|हुआ|से|"
    r"pehle|pahle|pehlay|ago|back|earlier|se|hue|hua|ho\s*gaye"
    r")?[\s\W]*$",
    re.I)


def is_time_only(utterance: str) -> bool:
    """Is the whole utterance a relative time and nothing else?"""
    text = " ".join((utterance or "").split())
    return bool(text) and bool(_TIME_ONLY.match(text))


def _current_condition_phrases(reading: dict, utterance: str) -> list[str]:
    """Return each distinct condition the caller attributes to themselves.

    New extractor responses preserve every mention and its scope. Legacy model
    responses remain safe: a sentence containing family/risk framing is not
    automatically classified because one singular phrase cannot identify which
    clause belongs to the caller.
    """
    # See _TIME_ONLY: "three years ago" names no condition, whatever the model
    # attached to it.
    if is_time_only(utterance):
        return []

    mentions = reading.get("conditionMentions")
    phrases: list[str] = []
    if isinstance(mentions, list):
        for mention in mentions:
            if not isinstance(mention, dict) or mention.get("scope") != "self_current":
                continue
            phrase = str(mention.get("phrase") or "").strip()
            # A mention whose own phrase is only a date is the same error one
            # level down: the model split "haan, teen saal pehle" out as a
            # mention. Drop it and keep any real sibling mention.
            if not phrase or is_time_only(phrase):
                continue
            if phrase.casefold() not in {p.casefold() for p in phrases}:
                phrases.append(phrase)
        return phrases

    phrase = str(reading.get("conditionPhrase") or "").strip()
    if not phrase or is_time_only(phrase) or is_hypothetical_mention(utterance):
        return []
    return [phrase]


# Explicit denial constructions, all three language modes. Deliberately the
# "does not have it" family only: "don't know", "not sure", and bare "no" must
# never look like retractions.
_RETRACTION_NEGATION = re.compile(
    r"\b(?:do not have|don't have|dont have|never had|do not|don't|dont|"
    r"nahi hai|nahin hai|nahi tha|nahin tha|nahi hui|nahin hui)\b"
    r"|नहीं\s+(?:है|था|थी|हुई|हुआ)|नही\s+(?:है|था|थी|हुई|हुआ)", re.I)

# Family/other-person framing excludes a retraction: "my father doesn't have
# cancer" is about the father, not the record.
# "I didn't say yes" / "मैंने हाँ नहीं बोला" - a denial ABOUT the answer just
# recorded, rather than an answer to the question. Requires the negation and the
# affirmation-reference close together so an ordinary sentence containing both
# words cannot match.
_SCREEN_ANSWER_DENIAL = re.compile(
    r"(?:\bno\b|\bnahi\b|\bnahin\b|नहीं|नही)[^\n]{0,40}"
    r"(?:\bhaan\b|\byes\b|हाँ|हां|बोला|कहा|\bsaid\b)"
    r"|(?:\bhaan\b|\byes\b|हाँ|हां)[^\n]{0,25}(?:\bnahi\b|\bnahin\b|\bno\b|नहीं|नही)",
    re.I)

# "I don't have it" / "नहीं है मुझे" - a denial of HAVING something, as opposed
# to a bare "no". Used only where the question already named the condition.
_DENIES_POSSESSION = re.compile(
    r"\b(?:do not have|don't have|dont have|never had|nahi hai|nahin hai|"
    r"nahi h|mujhe nahi|nahi hua|nahin hua)\b"
    r"|नहीं\s*(?:है|हुआ|हुई|था|थी)|नही\s*(?:है|हुआ|हुई|था|थी)"
    r"|मुझे\s*नहीं|नहीं\s*मुझे", re.I)

_OTHER_PERSON = re.compile(
    r"\b(?:father|mother|papa|mummy|bhai|behen|family|husband|wife|son|"
    r"daughter|uncle|aunt|dada|dadi|nana|nani)\b"
    r"|पिता|माता|पापा|मम्मी|भाई|बहन|परिवार|पति|पत्नी|बेटा|बेटी", re.I)


def _detect_retraction(utterance: str, session: dict) -> tuple[str, str] | None:
    """Did the caller explicitly deny a condition already on their record?

    Requires BOTH an explicit denial construction and the condition's NAME in
    the same utterance - matched against the declared label and the caller's
    own original phrase, tolerantly across scripts (a Hinglish caller says
    "फाइब्रॉइड", the record says "Uterine Fibroid"; consonant skeletons bridge
    the script gap the same way the date parser's month recovery does).
    Returns (icd10, label) of the first named match, or None.
    """
    if not _RETRACTION_NEGATION.search(utterance or ""):
        return None
    if _OTHER_PERSON.search(utterance):
        return None

    u_tokens = [t for t in re.findall(r"[A-Za-z\u0900-\u097F]+", utterance.lower())
                if len(t) >= 4]
    if not u_tokens:
        return None
    u_skeletons = {t: _skeleton_of(t) for t in u_tokens}

    for row in (session.get("declaredConditions") or []):
        icd10, label = row.get("icd10"), str(row.get("label") or "")
        if not icd10:
            continue
        # FUNCTION WORDS ARE NOT THE CONDITION'S NAME, and including them deleted
        # disclosures off live Hindi records.
        #
        # patientPhrase is the caller's own sentence, so for hypertension it is
        # "हाँ, मुझे उच्च रक्तचाप है" - which contributes "मुझे" as a name token.
        # "मुझे" then appears in almost every Hindi denial too, so answering a LATER
        # screen "नहीं, मुझे इनमें से कोई नहीं है" matched on it, satisfied both
        # halves of the test, and retracted the condition. Measured: one rehearsal
        # lost BOTH hypertension and asthma that way - each declared from a phrase
        # containing "मुझे" - and the caller was told "मैंने Essential Hypertension
        # आपके रिकॉर्ड से हटा दिया है" for a screen she had merely answered no to.
        #
        # _AFFIRMATION_ONLY is reused rather than a new list: it is already defined
        # as the words that carry no clinical information, which is exactly the
        # property that disqualifies a token from identifying a disease. The LABEL
        # still supplies real names, so a genuine named retraction is unaffected.
        name_tokens = [t for t in re.findall(
            r"[A-Za-z\u0900-\u097F]+",
            f"{label} {row.get('patientPhrase') or ''}".lower())
            if len(t) >= 4 and t not in _AFFIRMATION_ONLY]
        for name in name_tokens:
            name_skeleton = _skeleton_of(name)
            for token, token_skeleton in u_skeletons.items():
                if token == name:
                    return icd10, label
                if (len(name_skeleton) >= 4 and len(token_skeleton) >= 3
                        and _skeleton_distance(token_skeleton, name_skeleton)
                        <= max(1, len(name_skeleton) // 4)):
                    return icd10, label
    return None


def _phrase_grounded(phrase: str, utterance: str) -> bool:
    """Does the model's condition phrase share a content word with the utterance?

    The test that separates "the caller named a condition" from "the model invented
    one". A hallucination from a bare yes shares nothing with "हाँ।"; a real
    disclosure shares its own words. Content words only (3+ characters), so "है"
    and "the" cannot ground anything on their own.

    KNOWN DEFECT, DELIBERATELY LEFT IN PLACE. This answers False for essentially
    every Devanagari phrase, including one that is a literal substring of the
    utterance. Python's \\w follows str.isalnum and a Devanagari vowel sign is
    category Mn, for which isalpha() is False, so [^\\w\\s] treats every matra as
    punctuation and shatters each word into single consonants:

        'मेरा पैर टूटा था।'  ->  'म र  प र ट ट  थ  '  ->  no token survives len >= 3

    Both sides tokenise to the empty set and the intersection is empty. English is
    unaffected - 'gallstone removal operation' tokenises correctly - which is why it
    went unnoticed.

    WHY IT IS NOT FIXED. This function's caller in the screening block decides whether
    to classify the MODEL's extracted condition phrase or the CALLER's raw words.
    Returning False sends Hindi down the caller's-words path, which is deterministic.
    Correcting the regex sends it down the model's-phrase path instead, and that
    phrase is LLM output that varies between runs.

    MEASURED, not assumed. With the regex corrected, five identical Hindi rehearsals
    of docs/DEMO_SCRIPT_SHOWCASE.md produced FOUR different shapes - 52, 53 and 54
    turns, with the gallstone read-back firing zero times, once, and twice - and two
    of the five picked up a re-ask. Reverting restored two shapes and five clean runs.
    The record came out correct in every case either way, so this is a predictability
    regression rather than a correctness one, but a numbered script read aloud on a
    call cannot rely on it. tests/measure_showcase_spine.sh is the harness.

    Fixing it properly means deciding which input a condition screen SHOULD classify -
    a model paraphrase or the caller's own words - and then re-measuring Hindi. That is
    a change on its own footing, not a side effect of a regex repair. Until someone
    makes it deliberately, the accident is the safer behaviour: classifying what the
    caller actually said is both faithful and stable.

    The corrected form is r"[^\\w\\s\\u0900-\\u097F]|[\\u0964\\u0965]", which spares
    the Devanagari block while still stripping dandas. tests/diag_carry_grounding.py
    demonstrates the difference on both scripts.
    """
    _strip = r"[^\w\s]|[\u0964\u0965]"
    u_tokens = {t for t in re.sub(_strip, " ", (utterance or "").lower()).split()
                if len(t) >= 3}
    p_tokens = {t for t in re.sub(_strip, " ", (phrase or "").lower()).split()
                if len(t) >= 3}
    return bool(u_tokens & p_tokens)


# Medication -> ICD-10 prefixes the drug is ordinarily prescribed for. Covers the
# common Indian medication names a caller is likely to say on a health-insurance
# intake, so a named drug can be reconciled against the condition being screened.
#
# PREFIXES, not exact codes: thyroxine is consistent with any E03-E05 thyroid
# disorder, and telling those apart is not this check's job.
_MED_CONDITIONS = {
    # thyroid
    "thyroxine": ("E03", "E05"), "levothyroxine": ("E03", "E05"),
    "thyronorm": ("E03", "E05"), "eltroxin": ("E03", "E05"),
    "थायरोनॉर्म": ("E03", "E05"), "थायरॉक्सिन": ("E03", "E05"),
    # diabetes
    "metformin": ("E10", "E11"), "glycomet": ("E10", "E11"),
    "insulin": ("E10", "E11"), "glimepiride": ("E10", "E11"),
    "sitagliptin": ("E10", "E11"), "januvia": ("E10", "E11"),
    "janumet": ("E10", "E11"), "empagliflozin": ("E10", "E11"),
    "मेटफॉर्मिन": ("E10", "E11"), "इंसुलिन": ("E10", "E11"),
    # cardiac / BP / lipids
    "atorvastatin": ("I10", "I20", "I21", "I25", "E78"),
    "rosuvastatin": ("I10", "I20", "I21", "I25", "E78"),
    "aspirin": ("I10", "I20", "I21", "I25", "I63"),
    "ecosprin": ("I10", "I20", "I21", "I25", "I63"),
    "clopidogrel": ("I20", "I21", "I25", "I63"),
    "amlodipine": ("I10",), "amlokind": ("I10",), "telmisartan": ("I10",),
    "telma": ("I10",), "losartan": ("I10",), "metoprolol": ("I10", "I20", "I25", "I48"),
    "atenolol": ("I10",), "ramipril": ("I10", "I50"),
    # respiratory
    "salbutamol": ("J44", "J45"), "asthalin": ("J44", "J45"),
    "budesonide": ("J44", "J45"), "montelukast": ("J45",), "montair": ("J45",),
    "foracort": ("J44", "J45"),
    # infectious disease
    "isoniazid": ("A15",), "rifampicin": ("A15",),
    # oncology
    "tamoxifen": ("C50",),
    # misc
    "warfarin": ("I48", "I26", "I50"), "prednisolone": (), "wysolone": (),
}


def medication_consistency(icd10: str | None, answer: str) -> tuple[str, str] | None:
    """Is a named medication the kind of drug prescribed for this condition?

    Returns (verdict, medication) where verdict is "consistent" or "unexpected",
    or None when nothing can be judged - no medication recognised, no condition
    code, or a drug (like prednisolone) prescribed for so many things that a
    mismatch means nothing.

    WHY FLAG-ONLY, NEVER CHALLENGE. The caller saying "thyroxine" for their
    thyroid is evidence of a REAL diagnosis - it corroborates. A mismatch, like
    "metformin" for a thyroid disclosure, is one of three things: an STT error, a
    caller mixing up drug names (common, elderly-skewed population), or a genuine
    signal the disclosure is confused. All three are for a HUMAN underwriter to
    weigh with the transcript in front of them. A voice bot arguing pharmacology
    with a patient would be wrong often enough to be dangerous and condescending
    the rest of the time - so the verdict goes in the record, not in the call.
    """
    if not icd10 or not answer:
        return None
    text = (answer or "").lower()
    for med, prefixes in _MED_CONDITIONS.items():
        if med in text:
            if not prefixes:
                return None  # prescribed too broadly to judge
            code = icd10.upper()
            ok = any(code.startswith(p) for p in prefixes)
            return ("consistent" if ok else "unexpected", med)
    return None


# Ways of saying "nothing to change" after the summary read-back. The question is
# "anything to correct or add?", so plain NO means correct-as-recorded, and so do
# the everything-is-fine phrasings. Split into exact-tokens and phrases because
# "no" must match alone but not inside "no wait, one more thing" - which starts
# with no and is precisely a correction.
_ALL_CORRECT_TOKENS = {"no", "nope", "nah", "nahi", "nahin", "नहीं", "correct",
                       "right", "sahi", "सही", "done", "bas", "बस"}
_ALL_CORRECT_PHRASES = ("all good", "all correct", "that's right", "thats right",
                        "nothing else", "nothing more", "sab sahi", "sab theek",
                        "sahi hai", "theek hai", "सब सही", "सब ठीक", "ठीक है",
                        "kuch nahi", "कुछ नहीं", "that is all", "that's all")
# APOSTROPHES ARE ERASED, NOT SPACED. is_all_correct blanks punctuation to a
# space, which turned "that's all" into "that s all" - so two entries above
# ("that's right", "that's all") could never match anything, and a caller saying
# the commonest English form of "nothing to change" was routed down the
# correction path. Normalising both sides keeps the list readable.
_APOSTROPHES = str.maketrans({"'": "", "\u2019": "", "\u02bc": "", "`": ""})
_ALL_CORRECT_PHRASES_N = tuple(p.translate(_APOSTROPHES)
                               for p in _ALL_CORRECT_PHRASES)
# A confirmation phrase embedded inside a correction is still a correction.
# These markers deliberately win over _ALL_CORRECT_PHRASES so utterances such as
# "all correct except my weight" and "nothing else, but I have asthma" cannot
# finalize a record the caller is actively changing.
_CORRECTION_MARKER = re.compile(
    r"\b(but|except|except for|actually|wait|however|although|also|instead|"
    r"one more|change|wrong|mistake|mean|meant|add|remove|"
    r"lekin|magar|par|asal mein|galat|badal|jod|hatana)\b|"
    r"लेकिन|मगर|परन्तु|असल में|गलत|बदल|जोड़|हटाना", re.I)


def is_all_correct(utterance: str) -> bool:
    """After the summary read-back: did the caller say the record is right?

    Deliberately the NARROW test. Only a recognisable "no / all correct" closes
    the record, and anything else - including something garbled - is treated as
    an attempted correction and flows down the normal turn path. The failure
    modes are not symmetrical: finalising past a correction writes a record the
    caller just tried to fix, while mistaking a confirmation for a correction
    costs one round of "sorry, I didn't catch that" and the ask is bounded anyway.
    """
    u = re.sub(r"[\u0964\u0965]|[^\w\s\u0900-\u097F]", " ",
               (utterance or "").lower().translate(_APOSTROPHES)).strip()
    u = re.sub(r"\s+", " ", u)
    if not u:
        return False
    if re.fullmatch(r"(?:there is )?(?:nothing|nothing else|no need) to "
                    r"(?:change|add|correct)(?: anything)?", u):
        return True
    if _CORRECTION_MARKER.search(u):
        return False
    tokens = u.split()
    if all(t in _ALL_CORRECT_TOKENS for t in tokens):
        return True
    return any(p in u for p in _ALL_CORRECT_PHRASES_N)


# "Okay, go ahead" in its common spoken forms. NOT the same thing as a yes:
# "ठीक है" said to "do you have cancer?" means "okay, I'm listening", never "yes I
# have cancer", which is why this set deliberately excludes हाँ/yes/haan and the
# affirmation words - those belong to is_bare_affirmation and really do answer.
_ACK_TOKENS = {"ठीक", "है", "theek", "thik", "hai", "achha", "accha", "अच्छा",
               "hmm", "हम्म", "samajh", "समझ", "gaya", "गया", "गई", "understood",
               "chaliye", "चलिए", "boliye", "बोलिए", "poochhiye", "पूछिए",
               "go", "ahead", "carry", "on", "continue", "shuru", "शुरू"}


def is_bare_acknowledgement(utterance: str) -> bool:
    """Did the caller just say "okay, go on" rather than answer?

    Measured on a live call with CLEAN transcription: the agent read the
    conditions-list preamble, the caller said "ठीक है।" - okay, I'm ready - and
    the agent replied "माफ़ कीजिए, मैं ठीक से सुन नहीं पाई" and re-read the whole
    preamble. Twice. Same at the opening, three times. The transcript was
    perfect; the failure was treating "okay" as a failed ANSWER instead of as
    what it is - the caller signalling the agent should continue.

    The response to an acknowledgement is to re-state the pending QUESTION,
    briefly, without apologising for mishearing (nothing was misheard) and
    without burning one of the caller's three attempts (they have not attempted
    anything yet).
    """
    u = re.sub(r"[\u0964\u0965]|[^\w\s\u0900-\u097F]", " ",
               (utterance or "").lower()).strip()
    tokens = u.split()
    if not tokens or len(tokens) > 4:
        return False
    return all(t in _ACK_TOKENS for t in tokens)


def is_bare_affirmation(utterance: str) -> bool:
    """True when the caller said yes and named nothing.

    THIS IS THE DIFFERENCE BETWEEN A PROTOCOL AND A GUESS.

    The thirteen condition screens are yes/no questions, so "yes" is a complete and
    correct answer that nevertheless identifies no condition. Two things went wrong
    with it, and they look opposite while sharing a cause:

      - on a live call the LLM returned no conditionPhrase, so nothing was screened
        and the questionnaire moved to the next item. The caller had just disclosed
        cancer and was asked about heart disease.

      - locally the LLM DID invent a phrase from the bare "yes", and the classifier
        confidently resolved it to D25, leiomyoma of the uterus, then queued that
        protocol and asked when it had been diagnosed. A fabricated diagnosis in an
        underwriting record is worse than a missing one, because it looks answered.

    Both come from treating "yes" as evidence about which condition. It is not. When
    this returns True the condition screening is skipped deliberately and the caller
    is asked which - the one source that actually knows.
    """
    # The danda (।, U+0964) and double danda sit INSIDE the preserved Devanagari
    # range, so "हाँ।" tokenised as "हाँ।" - one character away from the "हाँ" in
    # the set, and invisible in most fonts' rendering of a log line. A live caller
    # said a plain Hindi yes and failed this check because of full-stop
    # punctuation. Stripped explicitly, ahead of the block preservation.
    u = re.sub(r"[\u0964\u0965]|[^\w\s\u0900-\u097F]", " ",
               (utterance or "").lower())
    tokens = [t for t in u.split() if t]
    if not tokens:
        return False
    return all(t in _AFFIRMATION_ONLY for t in tokens)


# Asking us to say it again, in any of the three languages. Kept as phrases rather
# than single words because "what" and "again" appear inside ordinary answers.
# Follow-ups whose answer cannot be a diagnosis: a drug, a date, a lab value, a
# frequency, a yes/no. Condition classification is disabled while one of these is
# the pending question, because the classifier has no "this is not a disease"
# output - it returns its nearest neighbour for any string, so "thyroxine" heard as
# "Hydroxy" came back as Hyperlipidaemia and was read back for confirmation.
#
# Listed explicitly against seed/health-kg.json rather than pattern-matched on the
# id, so a follow-up added later does not silently inherit this behaviour: a new
# question that DOES invite a condition must keep classification on, and being
# absent from this set is the safe default.
_NON_CONDITION_FOLLOWUPS = frozenset({
    "FU_TREATMENT", "FU_CANCER_TREATMENT", "FU_IMMUNOSUPPRESSANT",
    "FU_ART_ADHERENCE", "FU_ON_INSULIN", "FU_DIALYSIS", "FU_INHALER_USE",
    "FU_DIAG_WHEN", "FU_LAST_SEIZURE", "FU_LAST_ONCOLOGY_REVIEW",
    "FU_BP_READING", "FU_HBA1C", "FU_TSH_LAST", "FU_CD4_COUNT", "FU_VIRAL_LOAD",
    "FU_EJECTION_FRACTION", "FU_CKD_STAGE", "FU_CANCER_STAGE",
    "FU_SEIZURE_FREQUENCY", "FU_CONTROLLED", "FU_REMISSION", "FU_RECURRENCE",
    "FU_TB_COMPLETED", "FU_TB_SPUTUM", "FU_TB_DRUG_RESISTANT",
    "FU_SURGERY_DONE", "FU_CARDIAC_INTERVENTION", "FU_TRANSPLANT",

    # Added after auditing all 40 graph follow-ups against this set rather than
    # patching the two that happened to show up in one call's review list. Every
    # one below is a boolean or an enum, so it cannot name a condition:
    "FU_ALCOHOL_LINK",           # was alcohol a contributing factor
    "FU_HEP_TYPE",               # hepatitis B or C - a constrained enum
    "FU_HOSPITALISED_FOR",       # were you ever admitted because of it
    "FU_ORGAN_DAMAGE",           # any effect on heart or kidneys
    "FU_PSYCH_HOSPITALISATION",  # did it require admission
    "FU_RESP_HOSPITALISATION",   # hospital treatment or a nebuliser
    "FU_SELF_HARM",              # a sensitive yes/no
    "FU_SMOKING_LINK",           # do you smoke, or did you

})

# THE DEFAULT PROTOCOL'S STEPS, kept in their own set because they are NOT graph
# questions - seed contracts assert that every id in the set above resolves to a
# real question in health-kg.json, and these three deliberately do not.
#
# This is the highest-volume gap of the lot: the default protocol is applied to
# every code the graph does not govern, which is all 1043 reference-tier codes. Its
# ids are not FU_-prefixed, so a set listing only FU_* names left them open, and
# 'Pantoprazole tablets' was put to the classifier as a candidate disease when it
# was given as the treatment for reference-coded dengue.
_NON_CONDITION_DEFAULT_STEPS = frozenset({
    "diagnosed_when",            # a date
    "current_treatment",         # a drug
    "is_controlled",             # a yes/no
})

# DELIBERATELY LEFT OUT, because the answer can legitimately BE a condition and
# guarding them would discard a real disclosure:
#   FU_ANAEMIA_CAUSE       "did your doctor find a cause?" - thalassaemia
#   FU_DIAB_COMPLICATIONS  "affected your eyes or kidneys?" - retinopathy,
#                          nephropathy, neuropathy are all conditions
#   FU_RESIDUAL_DEFICIT    "any weakness or speech difficulty remained?"
#   FU_FUNCTIONAL_STATUS   free text, so a second condition can surface in it

# THE SAME RULE, FOR FORM ITEMS. The set above only ever applied to protocol
# follow-ups, so the identical question asked as a form item still reached the
# classifier. A live call emailed the customer seventeen "we could not identify
# this" review items, of which sixteen were noise:
#
#   'Cigarettes' 'smoke'          <- A400/A410, the tobacco questions
#   'Beer' 'drink alcohol'        <- A420/A430, the alcohol questions
#   'Pantoprazole, forty mg once a day'  <- A550, a drug list
#   'liver function test' 'enzymes were high'  <- A610, a test and its result
#   'it is fully resolved now'    <- A530, a yes/no about resolution
#   'the medicine'                <- A540, a yes/no about medication
#
# Telling a customer we could not identify "Beer" as a diagnosis is absurd on its
# own, and it buried the ONE real item - dengue fever - among sixteen others. A
# reviewer facing that list stops reading it.
#
# Listed explicitly, exactly as the follow-up set is, so an item added later does
# not silently inherit the behaviour. ABSENT IS THE SAFE DEFAULT: classification
# stays on unless a question is known to be incapable of naming a condition.
#
# DELIBERATELY NOT LISTED, because a condition legitimately appears in them:
#   A510 hospitalisation_reason - "Gallstone surgery" is how K80 was found
#   A910 other_condition_detail - the catch-all; "dengue fever" arrives here
#   A630 family_history_detail  - conditions appear, and per-mention SCOPE
#                                 already excludes the relative's disease
#   A650 prior_insurance_detail - a loading reason can name the condition
#   A200-A320 condition screens - the entire point of them
_NON_CONDITION_ITEMS = frozenset({
    "A100",  # height in cm
    "A110",  # weight in kg
    "A400",  # do you use tobacco - yes/no
    "A410",  # which form, how much per day, how many years
    "A420",  # do you drink alcohol - yes/no
    "A430",  # what do you drink, how much per week, how many years
    "A520",  # when was that - a year
    "A530",  # has that condition fully resolved - yes/no
    "A540",  # are you still taking medication - yes/no
    "A550",  # which medications and what dosage
    "A500",  # have you ever been hospitalised - yes/no. 'hospital mein bharti
             # hui thi' was classified. The REASON is asked next, at A510, which
             # is where a condition legitimately arrives and stays unguarded.
    "A600",  # has any test shown an abnormal result - yes/no. "one test came
             # back abnormal" was classified as R85, read back, and rejected.
    "A610",  # which test, when, and what did it show
    "A920",  # ABHA identifier
    "A990",  # email confirmation
})

# THE AGENT'S OWN INTERJECTIONS ARE NOT DISCLOSURES EITHER. These turns are not
# form items and not protocol follow-ups, so neither set above covered them, and
# a reply to one still reached the classifier: verifying a MEDICATION recorded
# 'salbutamol inhaler' and 'that is the medicine' as unidentifiable conditions.
#
# summaryConfirm is deliberately ABSENT. A caller correcting the summary may name
# a condition there - "no, you missed my asthma" - and that correction has its own
# harvesting path which must keep working.
_NON_CONDITION_KINDS = frozenset({
    "medVerify",          # which drug - a drug is not a diagnosis
    "distressCheck",      # are you alright
    "refusalConfirm",     # are you willing to continue
    "explanationCheck",   # did I understand you
    "explanationClarify",
})

# Disease-definition requests are distinct from repeat requests. A caller asking
# "what is glaucoma?" needs a short explanation, not the same unfamiliar word
# repeated more loudly. Kept deterministic so the request itself never reaches
# the diagnosis classifier as if it were a disclosure.
_EXPLANATION_PATTERNS = (
    "what is that", "what is this", "what does that mean", "what does this mean",
    "what does it mean", "can you explain", "could you explain", "please explain",
    "i don't know what that means", "i dont know what that means",
    "i have never heard of", "never heard of",
    "क्या है", "क्या होता है", "क्या मतलब", "मतलब क्या", "समझाइए", "समझा दीजिए",
    "kya hai", "kya hota hai", "matlab kya", "samjhaiye", "samjha dijiye",
)

# Forms a fixed substring list cannot express, added after a live browser call.
#
# The caller was asked "Hypertension, high blood pressure, or high cholesterol?",
# a spurious "Yes." was transcribed, and they objected: "No, I did not say yes. I
# did not say yes. I did not say yes. I asked you to explain cholesterol." None of
# the substrings above matched. What DID match was _REPEAT_PATTERNS on "i did not
# say", so the utterance was served as a request to repeat the question and the
# agent re-asked the screen twice, never withdrawing the yes and never explaining.
#
# The handling already existed - the branch that flags a screen answer when a
# definition request opens with a denial, written for the Alzheimer's case. Only
# the detector was missing, so this is a detector fix and not a precedence change.
#
# \bexplain\b DELIBERATELY, never a bare "explain" substring: "the doctor
# explained my diabetes" is a disclosure, and a substring match would read it as a
# request and suppress it. The word boundary excludes "explained" because the
# following character is a word character.
_EXPLANATION_REQUEST_RE = re.compile(
    # "I asked you to explain X", "I asked to explain X"
    r"asked\s+(?:you\s+)?to\s+explain"
    # Imperative at the start of the utterance or of a sentence: "Explain X."
    r"|(?:\A|[.!?\u0964]\s*)\s*explain\b"
    # "explain what X is", "explain to me"
    r"|\bexplain\s+(?:what|to\s+me)\b",
    re.I)


# The framing around the disease name in an explanation request. Everything here
# is politeness, question grammar or a pronoun; none of it is ever part of a
# disease's name, which is what makes removing it safe.
#
# WHY THIS EXISTS. _explanation_context_code classified the WHOLE utterance, and
# will only act on an exact or graph-corroborated hit. "मल्टीपल स्क्लेरोसिस" on its
# own resolves to G35 at 1.0000 via neptune_exact_synonym; the same name inside
# "माफ़ कीजिए, मल्टीपल स्क्लेरोसिस क्या है?" cannot match an exact-synonym index at
# all, so it fell to a knn score, failed the bar, and the caller was asked "which
# condition would you like me to explain?" - having just named it. Measured on a
# rehearsal: asked three times, then the screen was re-asked.
#
# TOKEN-BASED, NOT SUBSTRING. Devanagari postpositions like "का" and "की" occur
# inside longer words, so a substring strip would quietly amputate disease names.
# Words are space-separated, so removing whole tokens cannot.
_EXPLANATION_FRAMING = frozenset({
    # English
    "what", "whats", "is", "was", "the", "a", "an", "does", "do", "did", "mean",
    "means", "meaning", "explain", "tell", "me", "about", "sorry", "please",
    "excuse", "that", "this", "it", "you", "can", "could", "would", "know",
    "heard", "of", "never", "dont", "i", "my", "and", "or", "some", "thing",
    # Hindi
    "क्या", "है", "हैं", "था", "थी", "होता", "होती", "मतलब", "समझाइए", "समझा",
    "समझाओ", "दीजिए", "बताइए", "बताओ", "माफ़", "माफ", "कीजिए", "करना", "कृपया",
    "मुझे", "मैं", "मेरा", "मेरी", "यह", "ये", "वो", "वह", "का", "की", "के",
    "को", "में", "से", "कौन", "कौनसी", "कैसी", "कैसा", "नहीं", "अच्छा", "ठीक",
    "और", "या", "कोई",
    # Hinglish
    "kya", "hai", "hota", "hoti", "matlab", "samjhaiye", "samjha", "dijiye",
    "bataiye", "batao", "maaf", "kijiye", "mujhe", "main", "mera", "meri",
    "yeh", "woh", "kaun", "kaisi", "nahi", "nahin", "theek", "accha",
})


# Words about COMPREHENSION. Answering "did you understand?" with one of these is a
# statement about the explanation, never about the caller's health - no reply to "do
# you have multiple sclerosis?" is "I understand now".
_UNDERSTANDING_WORDS = re.compile(
    r"\b(?:understood|understand|understands|got it|makes sense|make sense|"
    r"is clear|clear now|thats clear|that's clear|samajh|samjha|samjhi|"
    r"samajh gaya|samajh gayi)\b"
    r"|समझ|समझा|समझी|समझ़|स्पष्ट", re.I)

# Any negation at all. Deliberately blunt: "yes, I understood" carries none, so its
# presence beside a comprehension word means the caller is saying the opposite, and
# that has to reach the repeat-the-explanation path rather than be read as assent.
_ANY_NEGATION = re.compile(
    r"\b(?:no|not|nope|dont|don't|didnt|didn't|nahi|nahin)\b|नहीं|नही", re.I)


def _confirms_understanding(utterance: str) -> bool:
    """"Yes, I understand now" - about the EXPLANATION, not about their health.

    THE CALL THIS COMES FROM. Having explained multiple sclerosis, the agent asked
    "क्या यह समझ आया? फिर मैं वही सवाल दोबारा पूछूँगी" - did you understand? then I
    will ask the same question again. The caller said "हाँ, अब समझ आया" and the turn
    fell through to the extractor as an answer to the SCREEN, so a neurological
    question was recorded true and the which-condition probe opened. The agent had
    just promised to re-ask and instead recorded a disease.

    is_bare_affirmation could not catch it: it requires every token to be an
    affirmation, and "अब समझ आया" is three content words. They are content words
    about comprehension, which is exactly what makes them safe to read this way.
    """
    u = (utterance or "").strip()
    if not u or not _UNDERSTANDING_WORDS.search(u):
        return False
    return not _ANY_NEGATION.search(u)


def _denies_understanding(utterance: str) -> bool:
    """"No, I still don't follow" - the explanation needs repeating, not an answer.

    The mirror of _confirms_understanding and needed for the same reason: without
    it, "नहीं समझा" also fell through to the extractor and was read as a medical
    reply, so a caller saying she had NOT understood got her non-answer recorded.
    """
    u = (utterance or "").strip()
    if not u or not _UNDERSTANDING_WORDS.search(u):
        return False
    return bool(_ANY_NEGATION.search(u))


def _explanation_subject(utterance: str) -> str:
    """The disease name out of an explanation request, or "" if none is left.

    "माफ़ कीजिए, मल्टीपल स्क्लेरोसिस क्या है?" -> "मल्टीपल स्क्लेरोसिस"
    "Sorry, what is multiple sclerosis?"        -> "multiple sclerosis"
    "what is that?"                             -> ""

    An empty result is the honest answer for a bare "what is that?" - there is no
    subject in it - and the caller keeps the existing clarify question.
    """
    tokens = re.findall(r"[A-Za-z\u0900-\u097F\u093C]+", utterance or "")
    kept = [t for t in tokens if t.lower() not in _EXPLANATION_FRAMING]
    return " ".join(kept).strip()


# "What is multiple sclerosis?" - the commonest way an English speaker asks, and the
# substring list above covers only "what is THAT" and "what is THIS".
#
# THE ASYMMETRY THIS FIXES. Hindi puts the interrogative last, so "मल्टीपल
# स्क्लेरोसिस क्या है?" matched "क्या है" and was explained. The English caller asking
# the identical question matched nothing, so her question went to the extractor as an
# ANSWER, came back unusable, and she was told "That doesn't seem right - I may have
# misheard" and asked the screen again. Measured on four consecutive rehearsals: the
# Hindi script explained multiple sclerosis every time and the English script never
# did.
#
# DELIBERATELY BOUNDED: an optional politeness lead, then the interrogative, then at
# most a short noun phrase to the end of the utterance. A definition request is
# short. This cannot match a sentence that merely contains "what is", and the cost of
# a false positive is one clarifying question rather than a recorded answer, because
# _state_explanation_request never falls through to the extractor.
_WHAT_IS_RE = re.compile(
    r"^\s*(?:sorry|excuse\s+me|please|erm|um|uh|and|so)?[\s,]*"
    r"what(?:'s|s|\s+is|\s+are|\s+was|\s+were)\s+"
    r"(?:a|an|the)?\s*[A-Za-z'’\-\s]{2,40}\?*\s*$", re.I)


def is_explanation_request(utterance: str) -> bool:
    """Whether the caller is asking for a plain-language disease explanation."""
    u = " ".join((utterance or "").lower().split())
    if not u:
        return False
    if any(pattern in u for pattern in _EXPLANATION_PATTERNS):
        return True
    if _EXPLANATION_REQUEST_RE.search(u):
        return True
    return bool(_WHAT_IS_RE.match(u))


_REPEAT_PATTERNS = (
    "repeat", "say that again", "say it again", "again please", "come again",
    "pardon", "what was that", "what did you say", "didn't hear", "didnt hear",
    "did not hear", "can you say", "could you say", "one more time",
    "which disease", "which one was that", "for what", "about what",
    "phir se", "dobara", "dubara", "fir se", "kya kaha", "samajh nahi",
    "फिर से", "दोबारा", "क्या कहा", "समझ नहीं",
    # ---- added from a live Hindi call, where their absence cost a diagnosis ----
    # The caller said "मैंने हाँ कहा, लेकिन किस चीज के लिए हाँ कहा? सूची बताइए."
    # - "I said yes, but yes to WHAT? Tell me the list." None of the patterns above
    # matched, so it fell through to the condition classifier, which returned HIV
    # Disease and read it back for confirmation. A request for clarification became
    # a stigmatised diagnosis one "yes" away from an insurance record.
    #
    # Asking WHICH question is being asked is a repeat request, not an answer.
    "सूची", "लिस्ट", "the list", "kya poocha", "क्या पूछा", "किस चीज",
    "किस बारे", "kis cheez", "kis baare", "किस लिए", "kis liye",
    # Corrections. "I didn't say that" and "no, you asked about cancer" are the
    # caller telling us we have it wrong - which is a request to go back over the
    # question, and emphatically NOT a refusal to continue. On the same call
    # "मैंने नहीं बोला।" was read as a refusal and triggered "would you like to end
    # the call?", which is a bewildering thing to hear when you are trying to
    # correct a machine.
    "मैंने नहीं बोला", "मैंने नहीं कहा", "maine nahi bola", "maine nahi kaha",
    "मैंने ऐसा नहीं", "i didn't say", "i did not say", "that's not what",
    # "बारे में पूछा" rather than "आपने पूछा था": the two halves are separated by
    # whatever they are correcting us about - "आपने कैंसर के बारे में पूछा था" - so a
    # contiguous match on the outer words never fires.
    "बारे में पूछा", "baare mein poocha", "आपने पूछा", "aapne poocha",
    "you asked about", "you were asking",
    # "WHICH disease/medicine are you talking about" - a caller asked this three
    # ways in one call ("किस चीज़ के लिए भाई?", "किस बीमारी के बारे में बात कर
    # रही हैं आप", "किस दवाई...") and only the first form matched.
    "किस बीमारी", "कौन सी बीमारी", "kis bimari", "kaun si bimari",
    "किस दवाई", "kis dawai", "kis dava", "बात कर रही", "baat kar rahi",
    "बात कर रहे", "baat kar rahe",
    # "What is <disease>?" and "what does that mean?". A caller asked
    # "स्कोलियोसिस क्या है?" - what IS scoliosis - and the question was fed to the
    # classifier, which matched Psoriasis and offered to put it on their record.
    # Asking what a word means must never be treated as claiming the disease.
    # Routed to the repeat path as a STOPGAP: repeating the question is not an
    # explanation, but it is honest and safe. The real fix is the explanation
    # feature in docs/REQUIREMENTS-GAP.md, which answers from the graph's own
    # per-condition descriptions.
    "क्या है", "क्या होता है", "क्या था", "मतलब", "kya hai", "kya hota hai",
    "matlab", "what is that", "what does that mean", "what do you mean",
    "never heard of",
)


# Words that independently corroborate a distress signal. Not a detector - the model
# does the detecting - but a second, lexical opinion on whether the WORDS support
# acting immediately. Deliberately broad, because a false corroboration only means
# escalating one turn sooner, which is the safe direction.
_DISTRESS_WORDS = re.compile(
    # Inflected forms matter more than they look. "end my life" missed "ending my
    # life", and "no reason to live" missed "don't want to live anymore" - both real
    # disclosures, both caught only because the test asserts the escalate-immediately
    # direction explicitly. A miss here delays help, which is the one outcome worth
    # over-matching to avoid.
    r"\b(suicid\w*|kill\w* myself|end\w* my life|end\w* it all|tak\w* my life|"
    r"harm\w* myself|hurt\w* myself|self harm|cut myself|overdose|no reason to live|"
    r"(don'?t|do not|dont) want to live|want to die|wish i was dead|"
    r"better off dead|not worth living|give up on life|tired of living|"
    r"jaan de|jaan dene|khudkhushi|atmahatya|marna chahti|marna chahta|"
    r"mar jaun|mar jaaun|zinda nahi rehna|jeena nahi)\b"
    r"|आत्महत्या|खुदकुशी|जान दे|मरना चाहती|मरना चाहता|मर जाऊँ|जीना नहीं", re.I)


def distress_corroborated(utterance: str) -> bool:
    """Do the words themselves support escalating right now?

    Used only to decide between escalating IMMEDIATELY and asking one gentle
    question first. It never suppresses an escalation: a second distress signal
    escalates whatever this returns.

    Exists because a mis-transcription ("I saw him suddenly.") triggered a clinician
    page and halted a questionnaire. The model's judgement is kept - it is better than
    keywords at reading real distress - but a high-consequence, irreversible action
    taken on a single unreliable transcript needs more than one source agreeing.
    """
    return bool(_DISTRESS_WORDS.search(utterance or ""))


def is_repeat_request(utterance: str) -> bool:
    """Is the caller asking us to repeat, rather than answering?

    THIS IS WHY A CALL LOOPED AND THEN RECORDED THE WRONG DISEASE.

    A caller answered the condition-detail probe with "Can you repeat the list?" -
    a completely reasonable thing to say. That sentence was passed to the condition
    classifier, which does what a nearest-neighbour classifier always does and
    returned its closest match, Chronic Ischaemic Heart Disease. The reading was
    parked for confirmation, and because next_question serves a parked confirmation
    ahead of everything else, the call then asked about heart disease repeatedly
    while the caller insisted "no, no, you are asking me about thyroid".

    So the request has to be recognised BEFORE anything downstream treats it as
    content. Two distinct failures come from getting this wrong: the caller cannot
    get the question repeated, and a fabricated diagnosis enters the record.

    Phrase matching, not single keywords: "what" and "again" turn up inside ordinary
    answers ("what I take is metformin"), and a false positive here means refusing to
    accept a real answer.
    """
    u = " ".join((utterance or "").lower().split())
    if not u:
        return False
    return any(p in u for p in _REPEAT_PATTERNS)


def infer_language_from_script(utterance: str) -> str:
    """Last resort: what script did they answer in?

    Better than any fixed default, because it is evidence from this caller rather
    than an assumption about callers in general. Someone who replies in Devanagari
    at all is telling us something, even when the words are not a language name.
    """
    return "hi" if _DEVANAGARI.search(utterance or "") else "en"


# Romanised Hindi that names no language but is unmistakably Hindi being spoken.
# DELIBERATELY NOT the full _AFFIRMATION_ONLY set: "yes", "ok" and "sure" are just
# as likely from someone who wants English, so they stay out and keep their re-ask.
_ROMANISED_HINDI_EVIDENCE = {
    "haan", "haa", "haanji", "hanji", "ji", "jee",
    "bilkul", "sahi", "theek", "thik", "hai",
    "achha", "acha", "boliye", "bolo", "kariye", "karo", "batao", "bataiye",
}


def infer_language_from_reply(utterance: str) -> str | None:
    """Hindi evidence in HOW they answered, when WHAT they said names no language.

    THE CASE THIS EXISTS FOR. Asked "हिंदी, या English?", a live caller said
    "हिंदी" and the recogniser returned "हाँ" - three times - because a ~0.6s clip
    carries so little acoustic evidence that a frequent word can win over the word
    actually spoken. "हाँ" names no language, so the menu re-asked, got "हाँ"
    again, and only then fell back to infer_language_from_script - which read the
    Devanagari and chose Hindi. The caller had answered correctly on the very
    first turn and was asked twice more.

    The evidence was there immediately: they replied IN DEVANAGARI. That is the
    same signal infer_language_from_script already treats as decisive - its own
    docstring says someone replying in Devanagari is telling us something even
    when the words are not a language name - it was simply being consulted two
    turns too late. So it is consulted BEFORE spending a re-ask, not after.

    Returns None rather than guessing when the reply is Latin and carries no Hindi
    word, because Latin script is weak evidence here: a Hinglish speaker's "haan"
    and an English speaker's "yes" look identical to a script test. The re-ask
    still earns its place there, and the "en" default stays exactly where it was,
    as the last resort once the attempts run out.
    """
    u = (utterance or "").strip()
    if not u:
        return None
    # Devanagari is decisive on its own. A caller who wanted English and was
    # transcribed into Devanagari can still say so at any point - that is what
    # parse_language_switch is for - so the downside is one correctable turn,
    # against the two wasted turns this removes.
    if _DEVANAGARI.search(u):
        return "hi"
    tokens = {t.strip(".,;:!?।\"'") for t in u.lower().split()}
    tokens.discard("")
    # ALL tokens must be Hindi evidence. One Hindi word inside an otherwise
    # English sentence is code-mixing, not a language request.
    if tokens and tokens <= _ROMANISED_HINDI_EVIDENCE:
        return "hi"
    return None


def _ask_language(turn_index: int, trace: list, first: bool,
                  note: str | None = None, insurer: str | None = None) -> dict:
    """The language question itself, which also introduces the caller."""
    notes = [note] if note else ["asked which language to continue in"]
    return {
        "agentUtterance": (hvi_prompts.language_choice_prompt(insurer) if first
                           else LANGUAGE_CHOICE_REPROMPT),
        "turnIndex": turn_index,
        "askedKind": "language",
        "askedRef": "language_choice",
        "isComplete": False,
        "escalated": False,
        "stopQuestionnaire": False,
        # The shape of answer this question expects. media/stt.py receives it via
        # set_expected_answer_type as metadata on the turn. STT is Whisper-only, so
        # it no longer drives an answer-type plausibility gate or a second opinion;
        # the field is retained for the surface and for callers that record it.
        "expectedAnswerType": "language",
        # The call is not in a language yet, so a permissive value is reported -
        # the reply could be either language, in either script.
        "language": "hinglish",
        # ...but SYNTHESIS is pinned, and to the Hindi reference. See the reasoning
        # on LANGUAGE_PROMPT_TTS_LANGUAGE in common/hvi_prompts.py. This must match
        # the language the cache builder stored it under or every call pays live
        # synthesis for its opening line.
        "ttsLanguage": hvi_prompts.LANGUAGE_PROMPT_TTS_LANGUAGE,
        "toolTrace": trace,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# choosing what to ask
# --------------------------------------------------------------------------- #
def _caller_condition_term(session: dict, icd10: str) -> str:
    """The caller's OWN word for a declared condition, cleaned to sit inside a
    spoken prompt. Empty when there is nothing usable, so callers fall back to the
    clinical label.

    Exists because an English clinical label ("Hyperlipidaemia") read aloud in the
    middle of a Hindi sentence means nothing to the caller; echoing what they
    actually said ("कोलेस्ट्रॉल") is clearer and truer to them. Looked up by icd10
    so a queue holding two conditions names the right one.
    """
    if not icd10:
        return ""
    phrase = ""
    # Most recent declaration wins, in case the same code was mentioned twice.
    for d in reversed(session.get("declaredConditions") or []):
        if isinstance(d, dict) and d.get("icd10") == icd10 and d.get("patientPhrase"):
            phrase = str(d["patientPhrase"])
            break
    phrase = _strip_disclosure_wrapper(phrase)
    # A condition name is a few words; a whole sentence is not a usable anchor.
    if not phrase or len(phrase) > 40 or len(phrase.split()) > 5:
        return ""
    return phrase


def _strip_disclosure_wrapper(phrase: str) -> str:
    """The condition NAME out of the sentence a caller wraps around it.

    "हाँ, स्किजोफ्रेनिया है मुझे" -> "स्किजोफ्रेनिया"

    Shared by two callers with different purposes, deliberately: naming the
    condition in a spoken prompt (_caller_condition_term), and deciding whether
    two classifications came from the SAME disclosure (_same_disclosure). Both
    need the caller's words reduced to the part that carries the diagnosis, and
    two implementations of that would drift.

    No length guard here - that belongs to the prompt caller, since a phrase too
    long to say aloud is still perfectly usable for an identity comparison.
    """
    phrase = (phrase or "").strip().strip("\u201c\u201d\"'()")
    # Drop a leading affirmation so we never echo "haan cholesterol" as the name.
    phrase = re.sub(
        r"^\s*(हाँ|हां|जी\s*हाँ|जी|haan|haa+n?|ji|yes|yeah)[\s,\u0964\u0965।-]+",
        "", phrase, flags=re.IGNORECASE).strip()
    # Drop a leading "मुझे / mujhe" ("to me, <condition>").
    phrase = re.sub(r"^\s*(मुझे|मुझको|mujhe|mujhko)[\s,\u0964\u0965।-]+",
                    "", phrase, flags=re.IGNORECASE).strip()
    # Drop the copula/possessive a caller wraps a disclosure in - the verb
    # "है/hai" (is) and the pronoun "मुझे/mujhe" (to me) - so
    # "स्किजोफ्रेनिया है मुझे" reads back as the NAME "स्किजोफ्रेनिया", not the
    # whole sentence. A live caller heard "...स्किजोफ्रेनिया है मुझे के लिए?" and
    # could not tell it was still one question. Anchored on whitespace so a
    # condition that merely ends in these letters is left untouched.
    phrase = re.sub(
        r"(?:\s+(?:है|हैं|मुझे|मुझको|को|ना|hai|hain|mujhe|mujhko|na))+"
        r"[\s,\u0964\u0965।?.!]*$",
        "", phrase, flags=re.IGNORECASE).strip()
    return phrase.strip("\u0964\u0965।?.!, ")


def _same_disclosure(phrase_a: str, phrase_b: str) -> bool:
    """Did these two phrases come from the SAME thing the caller said?

    Used to notice that one disclosure has been classified twice under two
    different codes. A live resumed call put BOTH of these on one record:

        F21  Schizotypal disorder   said: "हाँ, स्किजोफ्रेनिया है मुझे"   0.8221
        F20  Schizophrenia          said: "स्किजोफ्रेनिया"                0.8888

    One sentence, one illness, two ICD-10 codes, and the caller was taken through
    the same three follow-ups twice - once per code - then had both read back to
    her in the summary.

    DELIBERATELY EXACT, NOT FUZZY. The temptation is consonant-skeleton matching,
    which _detect_retraction uses a few hundred lines up, and it is the wrong tool
    here because the two mistakes are not equally bad. Failing to spot a duplicate
    costs a spurious code and a repeated question - visible, annoying, recoverable.
    Wrongly merging two phrases DELETES a disease from a medical record used to
    underwrite a policy, and nothing downstream can notice it is gone. So this
    requires the cleaned phrases to be equal, or one to contain the other as a
    whole token run: enough for a caller's copula-wrapped sentence to match the
    bare name inside it, and not enough for "diabetes" to ever match "asthma".
    """
    a = _strip_disclosure_wrapper(phrase_a).casefold()
    b = _strip_disclosure_wrapper(phrase_b).casefold()
    if not a or not b:
        return False
    if a == b:
        return True
    # Containment, on token boundaries so "hyper" cannot match "hypertension".
    short, long = sorted((a, b), key=len)
    if len(short) < 4:
        return False
    short_tokens, long_tokens = short.split(), long.split()
    n = len(short_tokens)
    return any(long_tokens[i:i + n] == short_tokens
               for i in range(len(long_tokens) - n + 1))


def _better_evidenced(new: dict, old: dict) -> bool:
    """Should this NEW classification replace an existing one for the same disclosure?

    Ordered by how much the signal is worth, which is NOT the order of how
    available it is:

      1. corroborated      the curated clinical graph reached the same code from a
                           known synonym. Two independent methods agreeing is the
                           only evidence here that is not just the index's opinion
                           of itself.
      2. confirmedByPatient  the code was read back and the caller said yes. Real
                           evidence, but weaker than it looks: this codebase
                           already records a caller agreeing to CHRONIC VIRAL
                           HEPATITIS on the third guess.
      3. confidence        a similarity score, which is a distance to the nearest
                           indexed vector and not a probability of being right.
                           Last resort, and it is what separated F20 from F21.

    Ties keep the incumbent. Re-minting a code we already hold gains nothing and
    would churn the record's provenance timestamps.
    """
    for key in ("corroborated", "confirmedByPatient"):
        if bool(new.get(key)) != bool(old.get(key)):
            return bool(new.get(key))
    try:
        return float(new.get("confidence") or 0) > float(old.get("confidence") or 0)
    except (TypeError, ValueError):
        return False


def _duplicate_declaration(session: dict, icd10: str,
                           patient_phrase: str | None) -> dict | None:
    """An already-declared condition that came from this same disclosure.

    Returns the existing row, or None. A row carrying the SAME code is not a
    duplicate to resolve - it is the same fact, and _queue_protocol handles that
    separately.
    """
    if not patient_phrase:
        return None
    for row in reversed(session.get("declaredConditions") or []):
        if not isinstance(row, dict) or not row.get("icd10"):
            continue
        if row.get("icd10") == icd10:
            continue
        if _same_disclosure(patient_phrase, str(row.get("patientPhrase") or "")):
            return row
    return None


def _name_condition(fu: dict, language: str, session: dict | None = None) -> str:
    """Put the condition into a follow-up prompt that says only "it".

    Only when the prompt is actually ambiguous - it refers to "it" or "this" and
    names nothing. A prompt that already carries the condition is left alone rather
    than having it stated twice.

    The label comes from the queued follow-up, so it is the condition this protocol
    was opened for and cannot drift to a different one.
    """
    text = (fu.get("prompt") or "").strip()
    label = (fu.get("conditionLabel") or fu.get("label") or "").strip()
    if not text or not label:
        return text
    low = text.lower()
    if label.lower() in low:
        return text
    # Pronouns in ALL THREE LANGUAGES. The first version only looked for English
    # " it"/" this", so every HINDI follow-up - "आप इसके लिए क्या इलाज ले रहे
    # हैं?" - was judged unambiguous and never got the condition named. A live
    # Hindi caller asked "किस चीज़ के लिए भाई?" and then, twice, "किस बीमारी के
    # बारे में बात कर रही हैं आप" - which is this exact gap, experienced from
    # the other end of the phone.
    if not any(w in low for w in (" it", " this", " it?", " this?",
                                  "इसके", "इसका", "इसकी", "यह", "इस ",
                                  "iske", "iska", "iski", "yeh ", "is ")):
        return text
    # In Hindi / Hinglish an English clinical label read aloud ("Hyperlipidaemia"
    # dropped mid-sentence) means nothing to the caller - they said it "doesn't
    # make sense". Prefer the caller's OWN word for the condition and fall back to
    # the clinical label only when there is nothing usable. English keeps the
    # label, which an English-speaking caller expects.
    name = label
    if language in ("hi", "hinglish") and session is not None:
        spoken = _caller_condition_term(session, fu.get("icd10"))
        if spoken:
            name = spoken
    if language == "hi":
        return f"{text.rstrip('?।').strip()} — {name} के लिए?"
    if language == "hinglish":
        return f"{text.rstrip('?').strip()} — {name} ke liye?"
    return f"{text.rstrip('?').strip()}, for your {label}?"


def _anchor_needed(session: dict, fu: dict) -> bool:
    """Is naming the condition still doing any work on THIS follow-up?

    Only on the first question of a protocol, or after the topic has moved. Once
    the caller knows which condition is being discussed, repeating it turns a
    conversation into a form: measured on a live call, four consecutive turns
    ended "— Fracture of Femur or Long Bone के लिए?", and the same call said
    "— Chronic Viral Hepatitis के लिए?" four times before that.

    The original anchoring is still right and stays - a Hindi caller really did
    ask "किस चीज़ के लिए भाई?" when every prompt said only "it". The fix is
    frequency, not the mechanism: say it when the subject changes, which is when a
    person would.
    """
    icd10 = fu.get("icd10")
    if not icd10:
        return False
    answered = session.get("followUpAnswers") or {}
    return not any(
        isinstance(rec, dict) and rec.get("icd10") == icd10
        for rec in answered.values()
    )


def _prompt_for(item: dict, language: str, customer: dict) -> str:
    """Canonical prompt text in the right language, with placeholders filled.

    Delegates to hvi_state so the written transcript quotes exactly the question
    this function spoke. See hvi_state.render_prompt.
    """
    # A resumed call re-confirms identity with a short "before we continue..."
    # prompt rather than the item's full first-call text (A010 otherwise re-greets
    # from the top). The reconfirm descriptor attaches the resolved text here; this
    # is the single funnel every ask-site uses, so it is spoken everywhere the item
    # is asked without touching those sites.
    if item.get("_reconfirmPromptText"):
        return item["_reconfirmPromptText"]
    return st.render_prompt(item, language, customer)


class DialogueState(Enum):
    """A name for what handle_turn is about to do with the caller's next reply.

    THIS DOES NOT CHANGE HOW THE CALL BEHAVES. The pre-LLM overlays (STAGE 2) are
    now dispatched through an ordered registry, _PRE_LLM_STATES, and each row is
    TAGGED with the member it owns - so a member now appears as a LABEL in the turn
    log ("turn N handled in state <value>") and is the read-only projection
    current_dialogue_state() computes. But the enum value is never switched on:
    control flow is driven entirely by the rows' GUARDS (which read the same
    session fields - awaitingConfirmation, pendingConditionDetail, ... - that
    always determined it) and by their list ORDER, which is the priority. Removing
    the labels would not change one bit of what any call does; they exist so a log
    line, a trace entry, or a person reading a transcript can name "where we are"
    without reverse-engineering it from which of ~10 optional fields happens to be
    truthy this turn. AWAITING_CONFIRMATION is deliberately NOT a registry row: a
    yes/no answer needs the one LLM read (STAGE 4), so it stays a post-LLM state.

    That is a deliberate, narrower thing than an FSM refactor. See
    docs/DIALOGUE_STATE.md for why: the short version is that the real state lives
    in DynamoDB so a dropped call resumes on the exact follow-up it still owes, and
    a resumed call can land on a different process or a different box entirely.
    Any change that made THIS enum load-bearing - switched on for control flow -
    would need to reconstruct correctly from a bare session record with no memory
    of how it got there. current_dialogue_state() already has to satisfy that
    (a resumed call has no Python call stack, only the record), which is exactly
    why it is built from the SAME TWO FUNCTIONS handle_turn and resume both already
    call - next_question() and _pending_opening_question() - rather than a third,
    independent reading of the flags that could silently drift from the real
    priority order.
    """

    AWAITING_LANGUAGE = "awaiting_language"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONDITION_DETAIL_PROBE = "condition_detail_probe"
    EXPLANATION_PENDING = "explanation_pending"
    REFUSAL_CONFIRM = "refusal_confirm"
    MED_VERIFY = "med_verify"
    DISTRESS_CHECK = "distress_check"
    SUMMARY_CONFIRM = "summary_confirm"
    PROTOCOL_FOLLOWUP = "protocol_followup"
    FORM_ITEM = "form_item"
    COMPLETE = "complete"


# next_question()'s `kind` and _pending_opening_question()'s `kind` name the same
# ~10 overlays with different strings in places, because they were written at
# different times for different callers. Mapped once here rather than have
# current_dialogue_state() special-case each caller.
_KIND_TO_STATE = {
    "confirmation": DialogueState.AWAITING_CONFIRMATION,
    "conditionDetail": DialogueState.CONDITION_DETAIL_PROBE,
    "explanationCheck": DialogueState.EXPLANATION_PENDING,
    "explanationClarify": DialogueState.EXPLANATION_PENDING,
    "explanationCandidateConfirm": DialogueState.EXPLANATION_PENDING,
    "refusalConfirm": DialogueState.REFUSAL_CONFIRM,
    "medVerify": DialogueState.MED_VERIFY,
    "distressCheck": DialogueState.DISTRESS_CHECK,
    "summaryConfirm": DialogueState.SUMMARY_CONFIRM,
    "followup": DialogueState.PROTOCOL_FOLLOWUP,
    "item": DialogueState.FORM_ITEM,
}


def current_dialogue_state(session: dict, items: list[dict],
                           language: str) -> DialogueState:
    """Name the overlay that will consume the caller's NEXT reply.

    Two real priority orderings exist in this file and this function does not
    invent a third: mid-call, handle_turn's overlay guards run top-to-bottom in
    the order documented at the top of this module; on resume,
    _pending_opening_question runs a differently-ordered subset of the same
    checks (its own docstring explains why the orders differ - a resumed call
    must hear an overlay's own prompt before next_question's ordinary questions,
    which is not the same requirement mid-call). Calling both and preferring
    _pending_opening_question's answer when it fires is what keeps this
    classification honest for a session that has just been loaded fresh, which
    is exactly the situation resume is built for and the situation this function
    is most useful for - describing a session record on its own, with no call
    stack of overlay checks already run against it this turn.

    NOT SUITABLE for classifying the branch a live handle_turn call is actually
    about to take: distressCheckPending and languageConfirmed are resolved inside
    handle_turn itself, ahead of both these functions, using signals (like
    reading.get("distressSignal")) that only exist mid-turn and are not on the
    session record. A caller with a language field already set and no distress
    check pending will therefore classify as FORM_ITEM/PROTOCOL_FOLLOWUP/etc. even
    on a turn where handle_turn is about to open a distress check - correctly, in
    the sense that the SESSION does not yet reflect that decision; it will exist
    the moment this same function is called again next turn.
    """
    if not session.get("languageConfirmed"):
        return DialogueState.AWAITING_LANGUAGE

    resume_kind = None
    opening = _pending_opening_question(session, items, {}, language)
    if opening:
        resume_kind = opening.get("kind")

    kind = resume_kind
    if kind is None:
        asked = next_question(session, items, language)
        kind = asked.get("kind") if asked else None

    if kind is None:
        return DialogueState.COMPLETE
    return _KIND_TO_STATE.get(kind, DialogueState.FORM_ITEM)


# The identity items re-asked, IN ORDER, to re-verify a resumed caller: name then
# date of birth. Both carry TERMINATE_POLITELY (A010 onFalse, A030 onMismatch), so
# routing a resumed call back through them re-proves identity using the SAME
# machinery as a first call - no separate verification or termination path.
_IDENTITY_RECONFIRM_ITEMS = ("A010", "A030")


def _identity_reconfirm_prompt_text(item_id: str, name: str, language: str) -> str:
    """A short "before we continue, can you confirm..." prompt for a resumed call,
    spoken in place of the item's full FIRST-CALL text - A010 otherwise re-greets
    the caller from the top ("Hello, this is a medical information call..."), which
    reads as if the call restarted. A010 also carries a brief "we were cut off"
    acknowledgement, so _first_question suppresses the generic resume opener rather
    than doubling it.
    """
    if item_id == "A010":
        return {
            "hi": (f"हम पहले बीच में रुक गए थे। आगे बढ़ने से पहले, क्या आप पुष्टि करेंगी "
                   f"कि मैं {name} से बात कर रही हूँ?"),
            "hinglish": (f"Hum pehle beech mein ruk gaye the. Aage badhne se pehle, "
                         f"kya aap confirm karengi ki main {name} se baat kar rahi hoon?"),
        }.get(language,
              f"We were cut off earlier. Before we continue, can you confirm "
              f"I'm speaking with {name}?")
    return {
        "hi": "धन्यवाद। और क्या आप अपनी जन्म तिथि की पुष्टि कर सकती हैं?",
        "hinglish": "Dhanyavaad. Aur kya aap apni date of birth confirm kar sakti hain?",
    }.get(language, "Thank you. And can you confirm your date of birth?")


def _identity_reconfirm_descriptor(session: dict, items: list[dict],
                                   language: str) -> dict | None:
    """The identity question a resumed call still owes, or None.

    Returns the head of ``identityReconfirmQueue`` (name first, then DOB) as an
    item descriptor, so both the mid-turn (next_question) and opening-turn
    (_pending_opening_question) paths surface identity ahead of every inherited
    follow-up, overlay and form item. None on a first call and once the queue has
    drained, after which the ordinary question flow takes over. Inert if the form
    somehow lacks the item - it falls through rather than trapping the call.

    The meta is a COPY carrying a resume-specific prompt (_reconfirmPromptText),
    which _prompt_for returns in place of the item's full first-call text. Every
    ask-site funnels through _prompt_for, so the short prompt is spoken wherever
    the item is asked; recording and verifyAgainst/onMismatch read itemId and the
    verification keys from the copy exactly as from the original.
    """
    queue = session.get("identityReconfirmQueue") or []
    if not queue:
        return None
    item = next((i for i in items if i.get("itemId") == queue[0]), None)
    if not item:
        return None
    meta = dict(item)
    meta["_reconfirmPromptText"] = _identity_reconfirm_prompt_text(
        queue[0], session.get("customerName") or "", language)
    return {
        "kind": "item",
        "text": None,
        "answerType": item.get("answerType", "text"),
        "meta": meta,
        "prefillFrom": None,
    }


def next_question(session: dict, items: list[dict], language: str) -> dict | None:
    """
    The single source of truth for what comes next.

    Returns a descriptor: kind is confirmation, followup or item, plus the text
    to ask and the metadata needed to record the answer.
    """
    # A resumed call re-verifies identity before anything it owes - a parked
    # confirmation, an inherited follow-up, or the next form item. Nothing is
    # asked or accepted until the DOB re-matches the CRM.
    reconfirm = _identity_reconfirm_descriptor(session, items, language)
    if reconfirm:
        return reconfirm

    # A parked confirmation comes first, but NOT FOREVER.
    #
    # This used to be unconditional, and a read-back that the caller never resolved
    # then outranked every subsequent question for the rest of the call. On a live
    # call a confirmation created two minutes earlier kept resurfacing while the
    # caller said "no, no, you are asking me about thyroid" - the agent could not get
    # past it, because nothing else was ever reachable.
    #
    # After CONFIRM_MAX_ASKS attempts it is abandoned and the questionnaire moves on.
    # Abandoning is the safe outcome: an unconfirmed classification is simply not
    # recorded, which is what it means to be unconfirmed, and the mention is already
    # in the transcript for the underwriter. Blocking the remaining questions to
    # chase it loses far more.
    # NOT YET BOUNDED, AND IT SHOULD BE.
    #
    # A parked confirmation outranks every other question until it is answered, so on
    # a live call one created two minutes earlier kept resurfacing while the caller
    # said "no, no, you are asking me about thyroid" and could not get past it.
    #
    # An attempt to bound it by the existing repeat counter broke the confirmation
    # flow outright (tests/test_orchestrator.py: the read-back stopped being asked at
    # all, so nothing was ever confirmed and no protocol was queued) and was reverted
    # rather than shipped half-working. The counter is not incremented on the path
    # that asks a confirmation, so reading it here abandons read-backs that were never
    # actually put to the caller.
    #
    # The fix needs its own ask counter on the parked record, incremented where the
    # confirmation is spoken - a change to the write path, not just this read. Left
    # deliberately undone rather than guessed at, because getting it wrong silently
    # discards disclosures.
    pending_conf = session.get("awaitingConfirmation")
    if pending_conf:
        # Bounded by the attempt counter _reask already maintains, keyed exactly as
        # _ref keys a confirmation. That counter is now cleared when a read-back
        # resolves, which is what makes this safe: a first attempt at this bound
        # tripped early because confirmations never reset their count, so it fired on
        # read-backs the caller had not been asked three times.
        ref = f"confirm:{pending_conf.get('icd10')}"
        if int((session.get("repeatCounts") or {}).get(ref) or 0) < MAX_REPEATS:
            return {
                "kind": "confirmation",
                "text": pending_conf.get("question"),
                "answerType": "boolean",
                "meta": pending_conf,
            }
        # Give up on this read-back and let the questionnaire continue. Abandoning is
        # the conservative outcome: an unconfirmed classification is not recorded,
        # which is precisely what "unconfirmed" means, and the caller's own words are
        # still in the transcript for the underwriter. Blocking every remaining
        # question to chase it - which is what happened on a live call, for two
        # minutes, while the caller repeated "you are asking me about thyroid" -
        # loses far more than it protects.
        log.info("abandoning unresolved confirmation of %s after %d attempts",
                 pending_conf.get("icd10"), MAX_REPEATS)

    fu = st.peek_followup(session)
    if fu:
        return {
            "kind": "followup",
            # NAME THE CONDITION. The graph's prompts are written to hang off a
            # condition - "What treatment or medication are you taking for it?" -
            # which reads fine on paper and is genuinely ambiguous on a call. A
            # caller who has just answered "no" to the diabetes screen hears that
            # question next and has no idea which "it" is meant; the live call has
            # them asking "Which disease?" and "For what?", which the agent then
            # could not parse, so it re-asked the same ambiguous question twice more.
            #
            # The condition is already on the queued follow-up, so this costs
            # nothing and removes the ambiguity at source rather than hoping the
            # caller infers it.
            #
            # ONLY WHILE IT IS STILL AMBIGUOUS, though - see _anchor_needed. Once
            # the first question of a protocol has been answered the caller knows
            # the subject, and repeating it on every remaining follow-up is what
            # made four turns in a row end "— Fracture of Femur or Long Bone के
            # लिए?".
            "text": (_name_condition(fu, language, session)
                     if _anchor_needed(session, fu) else (fu.get("prompt") or "")),
            "answerType": fu.get("answerType", "text"),
            "meta": fu,
        }

    # AN OPEN "WHAT IS IT CALLED?" OUTRANKS THE REST OF THE FORM.
    #
    # Set when a disclosure was too weak to assert (see _DIRECT_MIN_SCORE) or when
    # every read-back candidate was rejected. Until this was read here, the probe
    # was written to durable state and then never asked by this function: the
    # remaining form items outranked it and, on the last item, the call went
    # straight to a summary that told the caller they had declared nothing while
    # their volunteered condition sat unnamed. The reply is classified by the
    # pendingConditionDetail branch in handle_turn, which is bounded at three
    # attempts and then flags the mention for review, so this cannot trap a call.
    pending_detail = session.get("pendingConditionDetail") or {}
    if pending_detail:
        return {
            "kind": "conditionDetail",
            "text": pending_detail.get("phrase") or "",
            "answerType": "text",
            "meta": pending_detail,
            "ref": pending_detail.get("ref"),
        }

    item = st.next_item(items, session.get("answers") or {})
    if item:
        return {
            "kind": "item",
            "text": None,          # filled by the caller, which has the customer record
            "answerType": item.get("answerType", "text"),
            "meta": item,
            # Set when a protocol follow-up already collected this item's formField
            # on THIS call. The caller of next_question auto-fills from it instead of
            # asking again - see handle_turn. TEXT ITEMS ONLY: a follow-up answer is
            # free text, and copying it into a boolean or number item would put an
            # uncoerced string where the schema promises a typed value.
            "prefillFrom": (_followup_answer_for_field(session, item.get("formField"))
                            if item.get("answerType", "text") == "text" else None),
        }
    return None


def _answered_on_this_leg(rec: dict, session: dict) -> bool:
    """Was this follow-up answered on the CURRENT leg, or inherited from a resume?

    A resumed call gets a new callId and a new createdAt, while followUpAnswers is
    carried forward wholesale, so the timestamp on the answer is the only thing
    that distinguishes "the caller said this a minute ago" from "the caller said
    this on a call two days ago". answeredAt is written by
    hvi_state.complete_followup on every record, from the same clock as createdAt.

    UNPROVABLE MEANS NO. A missing or unparseable timestamp returns False, so the
    item is asked rather than auto-filled. The two outcomes are not equally bad:
    asking again costs one question the caller may find mildly redundant, while
    carrying a value of unknown age writes an unasked answer into a medical record.
    """
    started, answered = session.get("createdAt"), rec.get("answeredAt")
    if not started or not answered:
        return False
    try:
        return (_dt.datetime.fromisoformat(str(answered))
                >= _dt.datetime.fromisoformat(str(started)))
    except (TypeError, ValueError):
        # Includes the naive-vs-aware comparison, which raises TypeError.
        return False


def _followup_answer_for_field(session: dict, form_field: str | None) -> dict | None:
    """The protocol follow-up answer that already covered this form field, if any.

    THE SAME QUESTION WAS ASKED TWICE ON A LIVE CALL, and the caller noticed:

        graph FU_DIAG_WHEN  "इसका पहली बार निदान कब हुआ था?"   -> "एक साल हो गया"
        form  A520          "यह लगभग कब हुआ था?"                -> "एक साल पहले बोला था तो?"
                                                                    ("I TOLD you - a year ago")

    Both write to formField "since" - the schema and the graph both declare it, which
    means the duplication was already knowable from data; nothing read it. The graph
    protocol runs first because a volunteered condition interrupts the form, and the
    form then resumes with its own child items blind to what the protocol collected.

    Matching on formField rather than on question text or meaning: it is the field
    they both populate, it already exists on both records, and it cannot drift the
    way a semantic comparison would.

    Scoped to the CURRENT call's followUpAnswers deliberately - this is only about
    not asking twice WITHIN EARSHOT. That scoping was documented here from the
    start and was not actually implemented, which cost a record on a live call:

        leg 1  2026-09-01  J45 (asthma) FU_DIAG_WHEN -> "Five years ago"
        leg 2  2026-09-03  A520 "when was that hospitalisation?"
                           <- auto-filled "Five years ago"

    followUpAnswers is carried across a resume, by design, so every inherited
    answer looked like one collected moments ago. The caller was asked A520, said
    something else entirely, and had the asthma diagnosis date from two days
    earlier written into the record as the date of a hospitalisation - then got an
    apology for being misheard, because by the time their words were read the
    questionnaire had already moved to A530.

    Two days is well outside earshot. An inherited answer has none of the property
    this function relies on: that the caller has JUST said the thing and would
    recognise being asked again. So freshness is now checked rather than assumed.

    ONE CONDITION ONLY. Every condition's protocol asks FU_DIAG_WHEN, so a caller
    who discloses two of them has two answers declaring formField "since" - and
    returning the first match means returning whichever the map happened to
    deserialise first. A live call cost exactly that:

        J45 (asthma)     FU_DIAG_WHEN -> "2015"
        K80 (gallstones) FU_DIAG_WHEN -> "In 2019"
        A520 "when was that hospitalisation?" <- auto-filled "2015"

    The hospitalisation was the gallstone surgery, so 2019 was right and the
    asthma year was written into the record instead. Worse, filling A520 silently
    advanced the questionnaire to A530 ("has it resolved?"), so the caller's
    "2019, I told you already" was judged as a yes/no, rejected, and answered with
    an apology - the caller was told they had been misheard while the record
    disagreed with itself about one event.

    Two distinct values is an ambiguity this function cannot resolve: nothing here
    knows which condition the form item is about. So it declines to guess and the
    item is asked, which costs one question and is what the caller expects anyway.
    A single matching value keeps the original behaviour, which is the case the
    duplicate-question fix was written for.
    """
    if not form_field:
        return None
    matches = []
    for rec in (session.get("followUpAnswers") or {}).values():
        # (type compatibility is enforced by the caller: only text items prefill)
        if not isinstance(rec, dict):
            continue
        fu_field = rec.get("formField")
        if fu_field and fu_field == form_field and rec.get("value") not in (None, ""):
            if not _answered_on_this_leg(rec, session):
                continue
            matches.append(rec)
    if not matches:
        return None
    if len({str(rec.get("value")).strip().lower() for rec in matches}) > 1:
        # Ambiguous: ask rather than pick. See the docstring.
        return None
    rec = matches[0]
    return {"value": rec.get("value"), "questionId": rec.get("questionId"),
            "icd10": rec.get("icd10")}


def _current_question(session: dict, items: list[dict], language: str,
                      customer: dict) -> dict | None:
    """Return the unresolved question without consuming or changing state."""
    pending = session.get("pendingConditionDetail")
    if pending:
        return {
            "kind": "conditionDetail",
            "text": pending.get("phrase") or "",
            "answerType": "text",
            "meta": pending,
            "ref": pending.get("ref"),
        }
    asked = next_question(session, items, language)
    if asked and asked["kind"] == "item":
        asked["text"] = _prompt_for(asked["meta"], language, customer)
    if asked:
        asked["ref"] = _ref(asked)
    return asked


def _denies_before_asking(utterance: str) -> bool:
    """True when a definition request opens by denying the previous answer.

    "नहीं मैंने पूछा अल्जाइमर क्या होता है" is one utterance doing two things at
    once: withdrawing the yes just recorded and asking what a word meant. Only a
    LEADING denial counts. A negation later in the sentence retracts nothing -
    "क्या इसका मतलब नहीं है कि..." is a question, not a withdrawal.
    """
    return bool(re.match(
        r"[\s\u0964\u0965]*(no|nope|nah|nahi|nahin|नहीं|नही)[\s,\u0964\u0965]",
        (utterance or "").strip(), re.I))


def _candidate_confirm_text(label: str, language: str) -> str:
    """The "did you mean X?" read-back, in one place.

    Spoken from two call sites - the live turn and the resumed-call overlay - and
    the two must not drift, because a resumed caller who hears a different
    question from the one that was pending will answer a question nobody asked.
    """
    return {
        "hi": f"क्या आपका मतलब {label} से है?",
        "hinglish": f"Kya aapka matlab {label} se hai?",
    }.get(language, f"Did you mean {label}?")


def _explanation_context_code(session: dict, utterance: str,
                              trace: list,
                              notes: list) -> tuple[str | None, dict | None]:
    """Resolve what disease an explanation request refers to, without recording it.

    Returns (code, candidate). ``code`` is safe to act on. ``candidate`` is the
    hit that was good enough to ask about but not good enough to assume, and it
    is only ever populated when ``code`` is None. Splitting the two lets the
    caller decide whether a read-back is appropriate on its path, instead of
    discarding the classifier's answer outright - see _explain_request.
    """
    parked = session.get("awaitingConfirmation") or {}
    if parked.get("icd10"):
        return parked["icd10"], None
    followup = st.peek_followup(session) or {}
    if followup.get("icd10"):
        return followup["icd10"], None
    med = session.get("medCheckPending") or {}
    if med.get("icd10"):
        return med["icd10"], None
    declared = session.get("declaredConditions") or []
    if session.get("summaryAskPending") and len(declared) == 1:
        return declared[0].get("icd10"), None

    # There is no safe referent for bare "what is that?" on a broad screen. If
    # the caller named a disease in the request, allow only an exact/corroborated
    # graph result; an embedding-only guess must never decide what we explain.
    #
    # THE NAME IS CLASSIFIED WITHOUT THE QUESTION WRAPPED AROUND IT. The bar here is
    # an exact synonym or graph corroboration, and a disease name buried in a
    # sentence cannot match an exact-synonym index however plainly it was said. See
    # _explanation_subject. The full utterance is still tried as a fallback, so
    # nothing that used to resolve stops resolving.
    subject = _explanation_subject(utterance)
    c = tools.classify_condition(subject) if subject else {}
    if not (c.get("icd10") and (c.get("source") == "neptune_exact_synonym"
                                or c.get("corroborated"))):
        if subject:
            trace.append({"tool": "classify_condition_for_explanation",
                          "ok": True,
                          "resultSummary": {"subject": subject[:40],
                                            "icd10": c.get("icd10"),
                                            "source": c.get("source"),
                                            "score": c.get("score")}})
        c = tools.classify_condition(utterance)
    trace.append({"tool": "classify_condition_for_explanation", "ok": True,
                  "resultSummary": {"icd10": c.get("icd10"),
                                    "source": c.get("source"),
                                    "score": c.get("score"),
                                    "ambiguous": c.get("ambiguous"),
                                    "corroborated": c.get("corroborated")}})
    if c.get("icd10") and (c.get("source") == "neptune_exact_synonym"
                            or c.get("corroborated")):
        return c["icd10"], None

    # Not trustworthy enough to explain unprompted, but possibly worth asking
    # about. The bar is the one classify_condition uses for confidence: a real
    # score and a neighbourhood that is not a tie, because a tie means there is
    # no single thing to ask about. On call-4475670b this hit was G30 at 0.873
    # and throwing it away cost the caller three identical questions.
    candidate = None
    if (c.get("icd10") and not c.get("deniedCondition")
            and not c.get("ambiguous")
            and float(c.get("score") or 0.0) >= 0.75):
        candidate = {"icd10": c["icd10"], "label_en": c.get("label_en"),
                     "label_hi": c.get("label_hi")}
    notes.append("explanation request had no unambiguous graph-governed referent")
    return None, candidate


def _explain_request(call_id: str, session: dict, utterance: str,
                     current: dict | None, language: str, turn_index: int,
                     trace: list, notes: list,
                     allow_candidate: bool = False,
                     forced_code: str | None = None) -> dict:
    """Explain one governed condition, then wait for understanding confirmation.

    ``allow_candidate`` is set by the retry path. The caller has already been
    asked which condition they meant, so asking again in the same words is the
    one thing that must not happen; offering the best guess back as a yes/no
    question makes progress and still leaves the choice with the caller. Safe
    here specifically because deciding what to EXPLAIN writes nothing to the
    medical record - the strict gate stays in force for anything recorded.
    """
    if forced_code:
        # The caller has already confirmed this code out loud, so re-classifying
        # their "yes" would only find whatever "yes" is nearest to.
        code, candidate = forced_code, None
    else:
        code, candidate = _explanation_context_code(session, utterance, trace,
                                                    notes)

    if not code and allow_candidate and candidate:
        label = ((candidate.get("label_hi") if language == "hi"
                  else candidate.get("label_en"))
                 or candidate.get("label_en") or "")
        if label:
            confirm = _candidate_confirm_text(label, language)
            st.set_explanation_pending(call_id, {
                "awaitingCandidateConfirm": True,
                "icd10": candidate["icd10"],
                "candidateLabel": label,
                "questionText": (current or {}).get("text") or "",
                "askedKind": (current or {}).get("kind") or "item",
                "askedRef": (current or {}).get("ref") or
                            (_ref(current) if current else None),
                "expectedAnswerType": (current or {}).get("answerType"),
            })
            notes.append(f"explanation subject was not an exact match; offering "
                         f"{candidate['icd10']} for confirmation instead of "
                         f"repeating the same question")
            return {
                "agentUtterance": confirm, "turnIndex": turn_index,
                "askedKind": "explanationCandidateConfirm",
                "askedRef": f"explainCandidate:{candidate['icd10']}",
                "expectedAnswerType": "boolean",
                "isComplete": False, "escalated": False,
                "stopQuestionnaire": False, "language": language,
                "toolTrace": trace, "notes": notes,
            }

    if not code:
        clarification = {
            "hi": "ज़रूर। सूची में किस बीमारी का नाम समझना चाहेंगी? केवल उसका नाम बता दीजिए।",
            "hinglish": "Zaroor. List mein kis bimari ka naam samajhna chahengi? Sirf uska naam bata dijiye.",
        }.get(language,
              "Of course. Which condition in the list would you like me to explain? Just tell me its name.")
        st.set_explanation_pending(call_id, {
            "awaitingSubject": True,
            "questionText": (current or {}).get("text") or "",
            "askedKind": (current or {}).get("kind") or "item",
            "askedRef": (current or {}).get("ref") or
                        (_ref(current) if current else None),
            "expectedAnswerType": (current or {}).get("answerType"),
        })
        return {
            "agentUtterance": clarification, "turnIndex": turn_index,
            "askedKind": "explanationClarify", "askedRef": "explanationClarify",
            "isComplete": False, "escalated": False,
            "stopQuestionnaire": False, "language": language,
            "toolTrace": trace, "notes": notes,
        }

    info = tools.get_condition_explanation(code, language)
    trace.append({"tool": "get_condition_explanation", "ok": bool(info.get("found")),
                  "resultSummary": {"icd10": code, "found": info.get("found")}})
    explanation = info.get("explanation")
    if not explanation:
        notes.append(f"no authored explanation returned for {code}; re-asking honestly")
        explanation = {
            "hi": "माफ़ कीजिए, मेरे पास अभी इसकी सरल और सत्यापित व्याख्या नहीं है।",
            "hinglish": "Maaf kijiye, mere paas abhi iski simple verified explanation nahi hai.",
        }.get(language,
              "I'm sorry, I don't have a simple verified explanation for that yet.")

    pending = {
        "icd10": code,
        "explanation": explanation,
        "questionText": (current or {}).get("text") or "",
        "askedKind": (current or {}).get("kind") or "item",
        "askedRef": (current or {}).get("ref") or
                    (_ref(current) if current else None),
        "expectedAnswerType": (current or {}).get("answerType"),
    }
    st.set_explanation_pending(call_id, pending)
    understanding = {
        "hi": "क्या यह समझ आया? फिर मैं वही सवाल दोबारा पूछूँगी।",
        "hinglish": "Kya yeh samajh aaya? Phir main wahi sawaal dobara poochhungi.",
    }.get(language, "Does that make sense? I will then ask the question again.")
    notes.append(f"explained {code} in {language}; awaiting understanding confirmation")
    return {
        "agentUtterance": f"{explanation} {understanding}".strip(),
        "turnIndex": turn_index, "askedKind": "explanationCheck",
        "askedRef": f"explain:{code}", "isComplete": False,
        "escalated": False, "stopQuestionnaire": False,
        "language": language, "toolTrace": trace, "notes": notes,
    }


def _clear_expired_confirmation(call_id: str, session: dict,
                                trace: list, notes: list) -> dict:
    """Remove a read-back whose bounded attempts are exhausted.

    Leaving awaitingConfirmation populated makes protocol_active true forever,
    which blocks deferred conditions even though next_question has stopped asking
    the confirmation. The caller's phrase is retained for human review.
    """
    pending = session.get("awaitingConfirmation") or {}
    if not pending:
        return session
    ref = f"confirm:{pending.get('icd10')}"
    if int((session.get("repeatCounts") or {}).get(ref) or 0) < MAX_REPEATS:
        return session
    st.set_awaiting_confirmation(call_id, None)
    st.clear_repeats(call_id, ref)
    phrase = pending.get("phrase") or ""
    st.record_unclassified_mention(
        call_id, phrase, item_id=None,
        reason=f"classification read-back for {pending.get('icd10')} was not resolved "
               f"after {MAX_REPEATS} attempts")
    trace.append({"tool": "expire_confirmation", "ok": True,
                  "resultSummary": {"icd10": pending.get("icd10")}})
    notes.append(f"unresolved {pending.get('icd10')} read-back expired and was flagged")
    return st.get_session(call_id) or session


# --------------------------------------------------------------------------- #
# one turn
# --------------------------------------------------------------------------- #
def _terminate_identity_unavailable(
    call_id: str,
    session: dict,
    turn_index: int,
    language_hint: str,
) -> dict:
    """Fail closed before soliciting DOB when no authoritative value exists."""
    language = session.get("language") or language_hint
    st.mark_status(call_id, st.STATUS_TERMINATED,
                   note="identity_verification_unavailable")
    closing = {
        "hi": ("धन्यवाद। सुरक्षा और गोपनीयता के लिए हम इस कॉल पर आगे नहीं बढ़ सकते। "
               "हमारी टीम आपसे अलग से संपर्क करेगी।"),
        "hinglish": ("Dhanyavaad. Security aur privacy ke liye hum is call par aage "
                      "nahi badh sakte. Hamari team aapse alag se contact karegi."),
    }.get(language,
          "Thank you. For your security and privacy, we cannot continue this call. "
          "Our team will contact you separately.")
    return {
        "agentUtterance": closing,
        "turnIndex": turn_index,
        "isComplete": False,
        "escalated": False,
        "stopQuestionnaire": True,
        "language": language,
        "toolTrace": [],
        "notes": ["terminated before collecting identity data: "
                  "identity_verification_unavailable"],
    }


# --------------------------------------------------------------------------- #
# The pre-LLM state machine
#
# STAGE 2 of the turn pipeline (see the module docstring and _handle_turn_impl):
# the deterministic overlays that run BEFORE the one expensive LLM call. Each is
# a "we are waiting for a specific kind of reply" state - an explanation the
# caller asked for, a medicine to re-confirm, a which-condition probe - and each
# either consumes the turn or hands it on.
#
# This used to be an if/elif cascade whose priority was its position in the
# source. It is now an ORDERED REGISTRY: _PRE_LLM_STATES below. The order of that
# list IS the priority, as data rather than as line numbers, and every entry
# names the DialogueState it handles, so the dispatch loop logs which state owned
# each turn. That is what "load-bearing" means here - the state value routes
# control flow, it is not just described after the fact.
#
# WHY ONLY THE PRE-LLM OVERLAYS, AND NOT THE WHOLE FUNCTION. The staging is
# deliberate and worth preserving: these overlays are cheap and deterministic, so
# a caller saying "yes" to a medicine check must not cost an LLM inference. The
# post-LLM work (distress, confirmation of a parked classification, volunteered
# conditions, coercion) genuinely needs the semantic reading and is a single
# linear answer-processing path, not a fan-out of states - forcing it into a
# table would be cargo-culting, so it stays as the default path the dispatch loop
# falls through to.


# A handler that examined the turn but does not own it. The dispatch loop tries
# the next state. Distinct from None, which a handler could legitimately want to
# return one day; this is unambiguous.
FALL_THROUGH = object()


@dataclass
class TurnContext:
    """The mutable working set one turn's state handlers share.

    session and language are REASSIGNED by handlers (a handler mutates DynamoDB
    then re-reads the row, or settles the language), so they are attributes rather
    than passed by value - a handler that updates ctx.session is seen by the next
    handler and by the post-LLM pipeline after the dispatch loop writes these
    back. trace and notes are appended to in place, as the inline cascade did.
    """

    call_id: str
    utterance: str
    turn_index: int
    language: str
    customer: dict
    items: list
    session: dict
    trace: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    # Resolved by next_question() and set only for the POST-QUESTION phase
    # (_POST_QUESTION_STATES), whose handlers re-ask the pending question. None
    # during the pre-LLM phase, where next_question has not run yet.
    asked: dict | None = None

    def refresh_session(self) -> dict:
        """Re-read the session after a state helper mutated DynamoDB.

        The cascade did `session = st.get_session(call_id) or session` inline in a
        dozen places; this is that, named, so a handler cannot forget the `or
        session` fallback that keeps a transient read failure from nulling state.
        """
        self.session = st.get_session(self.call_id) or self.session
        return self.session


# Populated at import time, just below, once every handler is defined. The tuple
# is (DialogueState, guard, handler): guard(ctx) says whether this state owns the
# turn - the same condition the inline `if` tested - and handler(ctx) returns a
# reply dict to send, or FALL_THROUGH to try the next state. LIST ORDER IS THE
# PRIORITY.
_PRE_LLM_STATES: list[tuple["DialogueState", Callable[["TurnContext"], bool],
                           Callable[["TurnContext"], Any]]] = []


def _dispatch_pre_llm_states(ctx: TurnContext) -> Any:
    """Run the pre-LLM overlays in priority order; return a reply or FALL_THROUGH.

    The whole of STAGE 2, as one loop over explicit data. The first state whose
    guard owns the turn runs; if it yields FALL_THROUGH the next is tried, exactly
    as reaching the end of an inline block used to fall to the next block.
    """
    for state, guard, handler in _PRE_LLM_STATES:
        if not guard(ctx):
            continue
        result = handler(ctx)
        if result is FALL_THROUGH:
            # The overlay examined the turn and declined it, possibly after
            # mutating state. Carry the mutation forward, keep looking.
            continue
        log.info("turn %d handled in state %s", ctx.turn_index, state.value)
        return result
    return FALL_THROUGH


def _state_explanation_pending(ctx: TurnContext) -> Any:
    """EXPLANATION_PENDING: the caller was mid-way through having a term explained.

    Verbatim from the inline cascade - see DialogueState and the per-branch
    comments for why each path is shaped as it is. Returns a reply, or
    FALL_THROUGH when the caller's reply was substantive and should be processed
    as an answer to the still-pending underwriting question.
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes

    explanation_pending = session.get("explanationPending") or {}
    # "Did you mean X?" - the read-back offered when the clarify answer did
    # not resolve exactly. Yes explains X. Anything else drops the guess and
    # goes back to the underwriting question, because a second guess after a
    # rejected first one is how a caller ends up hearing about a disease they
    # never mentioned.
    if explanation_pending.get("awaitingCandidateConfirm"):
        st.clear_repeats(call_id, "explain:subject")
        current = {
            "kind": explanation_pending.get("askedKind"),
            "ref": explanation_pending.get("askedRef"),
            "text": explanation_pending.get("questionText"),
            "answerType": explanation_pending.get("expectedAnswerType"),
        }
        st.set_explanation_pending(call_id, None)
        if is_bare_affirmation(utterance) or re.search(
                r"\b(yes|yeah|yep|haan|haa|ji|हाँ|हां|जी)\b",
                utterance.lower()):
            notes.append("caller confirmed the explanation subject "
                         f"{explanation_pending.get('icd10')}")
            return _explain_request(
                call_id, session, utterance, current, language, turn_index,
                trace, notes,
                forced_code=explanation_pending.get("icd10"))
        notes.append("caller did not confirm the guessed explanation subject; "
                     "returning to the underwriting question")
        return {
            "agentUtterance": explanation_pending.get("questionText") or "",
            "turnIndex": turn_index,
            "askedKind": explanation_pending.get("askedKind"),
            "askedRef": explanation_pending.get("askedRef"),
            "expectedAnswerType": explanation_pending.get("expectedAnswerType"),
            "isComplete": False, "escalated": False,
            "stopQuestionnaire": False, "language": language,
            "toolTrace": trace, "notes": notes,
        }

    if explanation_pending.get("awaitingSubject"):
        attempts = st.record_repeat(call_id, "explain:subject")
        if attempts >= MAX_REPEATS:
            st.set_explanation_pending(call_id, None)
            st.clear_repeats(call_id, "explain:subject")
            notes.append("could not identify the requested explanation; returning "
                         "to the underwriting question")
            return {
                "agentUtterance": explanation_pending.get("questionText") or "",
                "turnIndex": turn_index,
                "askedKind": explanation_pending.get("askedKind"),
                "askedRef": explanation_pending.get("askedRef"),
                "expectedAnswerType": explanation_pending.get("expectedAnswerType"),
                "isComplete": False, "escalated": False,
                "stopQuestionnaire": False, "language": language,
                "toolTrace": trace, "notes": notes,
            }
        st.set_explanation_pending(call_id, None)
        current = {
            "kind": explanation_pending.get("askedKind"),
            "ref": explanation_pending.get("askedRef"),
            "text": explanation_pending.get("questionText"),
            "answerType": explanation_pending.get("expectedAnswerType"),
        }
        return _explain_request(call_id, session, utterance, current, language,
                                turn_index, trace, notes,
                                allow_candidate=True)

    bare_no = bool(re.fullmatch(
        r"[\s\u0964\u0965]*(no|nope|nahi|nahin|नहीं|na)[\s.!\u0964\u0965]*",
        utterance.strip(), re.I))
    if is_explanation_request(utterance) or bare_no or _denies_understanding(
            utterance):
        attempts = st.record_repeat(
            call_id, f"explain:{explanation_pending.get('icd10')}")
        if attempts < 2:
            understanding = {
                "hi": "मैं इसे एक बार और सरल शब्दों में कहती हूँ। क्या अब यह समझ आया?",
                "hinglish": "Main ise ek baar aur simple shabdon mein kehti hoon. Kya ab yeh samajh aaya?",
            }.get(language,
                  "Let me put it simply once more. Does that make sense now?")
            notes.append("caller remained unsure; repeated the authored explanation once")
            return {
                "agentUtterance": f"{explanation_pending.get('explanation', '')} {understanding}".strip(),
                "turnIndex": turn_index, "askedKind": "explanationCheck",
                "askedRef": f"explain:{explanation_pending.get('icd10')}",
                "isComplete": False, "escalated": False,
                "stopQuestionnaire": False, "language": language,
                "toolTrace": trace, "notes": notes,
            }
    if (is_bare_affirmation(utterance)
            or is_bare_acknowledgement(utterance)
            # "हाँ, अब समझ आया" - an affirmation with content words, so
            # is_bare_affirmation cannot see it. See _confirms_understanding.
            or _confirms_understanding(utterance)
            or bare_no
            or is_explanation_request(utterance)):
        st.set_explanation_pending(call_id, None)
        st.clear_repeats(call_id,
                         f"explain:{explanation_pending.get('icd10')}")
        notes.append("explanation check completed; re-asking the original question")
        return {
            "agentUtterance": explanation_pending.get("questionText") or "",
            "turnIndex": turn_index,
            "askedKind": explanation_pending.get("askedKind"),
            "askedRef": explanation_pending.get("askedRef"),
            "expectedAnswerType": explanation_pending.get("expectedAnswerType"),
            "isComplete": False, "escalated": False,
            "stopQuestionnaire": False, "language": language,
            "toolTrace": trace, "notes": notes,
        }
    # A substantive reply such as "yes, I had it in 2019" confirms
    # understanding and answers the still-pending underwriting question.
    st.set_explanation_pending(call_id, None)
    notes.append("caller answered substantively after the explanation")
    ctx.refresh_session()
    return FALL_THROUGH


def _state_explanation_request(ctx: TurnContext) -> Any:
    """EXPLANATION_PENDING: the caller is ASKING for a term to be explained.

    A new definition request is handled before refusal, summary, medication,
    repeat, extraction, or classification paths. The request itself must never
    become a diagnosis or a replacement medicine answer. Always returns
    _explain_request - a definition request never falls through to be read as an
    answer.
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    items, customer = ctx.items, ctx.customer

    # A denial wrapped around a question. The bare-no branch further down
    # catches a plain "नहीं" to the which-condition probe and flags the screen
    # answer for review, but this path runs first, so "नहीं मैंने पूछा
    # अल्जाइमर क्या होता है" used to leave the probe pending and the yes
    # unqueried: on call-4475670b the record kept A270 true with no condition
    # attached and nothing to tell an underwriter it was doubtful. Same
    # treatment as the bare no - the recorded answer is flagged, never
    # silently flipped - and then the question they actually asked is
    # answered.
    probe = session.get("pendingConditionDetail") or {}
    if probe and _denies_before_asking(utterance):
        st.clear_pending_condition_detail(call_id)
        st.clear_repeats(call_id, f"probe:{probe.get('ref')}")
        st.clear_repeats(call_id, f"probe-repeat:{probe.get('ref')}")
        st.record_unclassified_mention(
            call_id, utterance, item_id=probe.get("ref"),
            reason="said yes to the condition screen, then denied it while "
                   "asking what one of the listed conditions meant; the "
                   "screen answer needs human review")
        notes.append("UNDERWRITER ATTENTION: screen answer denied inside a "
                     "definition request; flagged rather than flipped")
        session = ctx.refresh_session()
    current = _current_question(session, items, language, customer)
    return _explain_request(call_id, session, utterance, current, language,
                            turn_index, trace, notes)


def _state_refusal_confirm(ctx: TurnContext) -> Any:
    """REFUSAL_CONFIRM: did they mean it?

    Answer to the one refusal check. Deliberately conservative in the direction of
    NOT hanging up: only a recognisable yes ends the call, and anything else is
    taken as "carry on", because the reason we are here is that a transcript could
    not be trusted in the first place. Returns a closing dict if the refusal is
    confirmed; otherwise falls through so the pending question is re-asked.
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes

    st.set_refusal_confirm_pending(call_id, False)
    if is_bare_affirmation(utterance) or parse_language_choice(utterance) is None \
            and re.search(r"\b(yes|yeah|yep|haan|ji|हाँ|हां)\b", utterance.lower()):
        st.mark_status(call_id, st.STATUS_DROPPED,
                       note="customer confirmed they wanted to stop")
        closing = {
            "hi": "कोई बात नहीं। हम आपको बाद में कॉल करेंगे। धन्यवाद।",
            "hinglish": "Koi baat nahi. Hum aapko baad mein call karenge. Dhanyavaad.",
        }.get(language, "Of course. We'll call you back another time. Thank you.")
        notes.append("refusal confirmed; ending the call")
        return {
            "agentUtterance": closing, "turnIndex": turn_index,
            "isComplete": False, "escalated": False, "stopQuestionnaire": True,
            "language": language, "toolTrace": trace, "notes": notes,
        }
    notes.append("refusal not confirmed; carrying on")
    # Fall through and re-ask whatever was pending, so the turn is not wasted.
    return FALL_THROUGH


def _state_summary_confirm(ctx: TurnContext) -> Any:
    """SUMMARY_CONFIRM: answer to "anything to correct or add?".

    "No" or any everything-is-right phrasing finalises: submit, email, close.
    Anything else falls through to the NORMAL turn machinery, which is the
    point - a correction is just a disclosure arriving late, so "actually I
    also have asthma" opens the asthma protocol, its follow-ups run, and when
    the queue drains _complete summarises again (bounded at two rounds).
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    customer = ctx.customer

    # A request to hear the summary again is not a correction attempt and must
    # not consume one of the two bounded correction rounds.
    if is_repeat_request(utterance):
        notes.append("customer asked to hear the summary again")
        return _complete(call_id, session, turn_index, trace, notes=notes)

    if is_all_correct(utterance):
        st.clear_summary_ask_pending(call_id)
        # Durably record the declaration itself, not just that the loop
        # ended. The written transcript asserts the customer declared this
        # record complete and accurate, and that sentence has to be backed by
        # a recorded confirmation. See hvi_state.record_declaration.
        try:
            st.record_declaration(call_id)
        except Exception as e:  # noqa: BLE001
            log.error("could not record the declaration for %s: %s", call_id, e)
        notes.append("customer confirmed the summary is accurate; finalising")
        return _complete(call_id, st.get_session(call_id) or session,
                         turn_index, trace,
                         ack=_consistent_ack(None, language), notes=notes,
                         confirmed=True)

    st.clear_summary_ask_pending(call_id)
    notes.append("customer wants to correct or add something; screening the "
                 "correction before finalisation")
    # There is no current form question at this point, so the ordinary turn
    # path would complete without ever interpreting the correction. Use the
    # extractor only to identify explicitly self-attributed conditions. A
    # scalar or otherwise unmappable correction is escalated rather than
    # silently discarded or guessed into the wrong field.
    correction = extract.understand(
        question_text="Is there anything you'd like to correct or add?",
        answer_type="text", utterance=utterance, language=language,
        item={}, customer_name=customer["customerName"])
    trace.append({"tool": "llm_understand", "ok": "error" not in correction,
                  "resultSummary": {
                      "understood": correction.get("understood"),
                      "mentionsCondition": correction.get("mentionsCondition"),
                  }})
    correction_phrases = _current_condition_phrases(correction, utterance)
    for index, correction_phrase in enumerate(correction_phrases):
        current = st.get_session(call_id) or session
        if index > 0 or current.get("pendingFollowUps") \
                or current.get("awaitingConfirmation"):
            st.defer_mention(call_id, correction_phrase)
            notes.append(f"additional summary disclosure '{correction_phrase[:40]}' "
                         "deferred behind the first condition")
        else:
            _handle_condition(call_id, correction_phrase, language, trace, notes,
                              min_score=0.78)
    session = ctx.refresh_session()
    if session.get("awaitingConfirmation") or session.get("pendingFollowUps"):
        return _advance(call_id, turn_index, language,
                        _consistent_ack(None, language), trace, notes,
                        False, False)

    try:
        st.record_unclassified_mention(
            call_id, utterance, item_id="summaryConfirm",
            reason="caller requested a summary correction that could not be "
                   "safely mapped to a structured field; human review required")
    except Exception as exc:  # noqa: BLE001
        log.error("could not record summary correction for %s: %s", call_id, exc)
    escalation = tools.escalate_to_human(
        call_id, "summary_correction_requires_review", utterance[:300])
    trace.append({"tool": "escalate_to_human", "ok": True,
                  "resultSummary": {"reason": "summary_correction_requires_review"}})
    notes.append("unmapped summary correction preserved and escalated; record not finalised")
    closing = {
        "hi": "धन्यवाद। इस सुधार को सही तरह दर्ज करने के लिए हमारी टीम आपसे संपर्क करेगी।",
        "hinglish": "Dhanyavaad. Is correction ko sahi tarah record karne ke liye hamari team aapse contact karegi.",
    }.get(language,
          "Thank you. Our team will contact you to record that correction accurately.")
    return {
        "agentUtterance": closing, "turnIndex": turn_index,
        "isComplete": False, "escalated": True,
        "stopQuestionnaire": bool(escalation.get("stopQuestionnaire", True)),
        "language": language, "toolTrace": trace, "notes": notes,
    }


def _state_med_verify(ctx: TurnContext) -> Any:
    """MED_VERIFY: answer to "are you sure about that medicine?".

    One-shot verification of an unexpected medication (see the pre-record check
    further down). A yes records the original answer, flagged for the
    underwriter with the caller's confirmation on the record. ANYTHING ELSE is
    treated as a fresh answer to the treatment question, which is still at the
    head of the queue precisely because the original was never recorded -
    "no no, it's levothyroxine" simply flows down the normal path below (falls
    through).
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes

    med_pending = session.get("medCheckPending")
    st.set_med_check(call_id, None, mark_asked=med_pending.get("ref"))
    if is_bare_affirmation(utterance):
        st.complete_followup(call_id, med_pending.get("value"))
        trace.append({"tool": "complete_followup", "ok": True,
                      "resultSummary": {"questionId": med_pending.get("ref"),
                                        "medVerified": "caller confirmed"}})
        notes.append(f"UNDERWRITER ATTENTION: caller CONFIRMED "
                     f"'{med_pending.get('med')}' for "
                     f"{med_pending.get('icd10')} although it is not a typical "
                     "treatment; taken at their word, review this turn")
        return _advance(call_id, turn_index, language,
                        _consistent_ack(None, language), trace, notes,
                        False, False)
    notes.append("caller revised the medication answer when asked; treating "
                 "this utterance as the new answer")
    ctx.refresh_session()
    return FALL_THROUGH


def _state_condition_detail_probe(ctx: TurnContext) -> Any:
    """CONDITION_DETAIL_PROBE: the answer to "which condition was it?".

    Ahead of next_question, because the pending probe is not a form item and
    next_question knows nothing about it - it would return the next screen and
    this reply would be read as an answer to that instead, which is how a
    disclosure turns into a wrong answer two questions later.

    "not a language request" matters here. This block runs before the mid-call
    language switch, so without the guard a caller who answers the "which
    condition?" probe with "Hindi" would have that word sent to the classifier as
    a medical phrase - and the classifier always returns its nearest neighbour.

    Repeat-request replay, bare-no retraction, bare-yes retry, and classify. It
    returns a reply on the branches that consume the turn; when the probe is
    pending but the utterance is empty or a language switch, no branch fires and
    it falls through to the deferred-mention drain, exactly as inline.
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    items, customer = ctx.items, ctx.customer

    pending = session.get("pendingConditionDetail")
    if pending and utterance and is_repeat_request(utterance):
        # Replay the screen rather than classifying a repeat request, but keep
        # replay attempts separate from attempts to answer the probe.
        repeat_ref = f"probe-repeat:{pending.get('ref')}"
        attempts = st.record_repeat(call_id, repeat_ref)
        if attempts >= MAX_REPEATS:
            st.clear_pending_condition_detail(call_id)
            st.clear_repeats(call_id, repeat_ref)
            st.clear_repeats(call_id, f"probe:{pending.get('ref')}")
            st.record_unclassified_mention(
                call_id, utterance, item_id=pending.get("ref"),
                reason="yes to the condition screen but only repeat requests "
                       f"were given after {MAX_REPEATS} attempts; needs human review")
            notes.append("UNDERWRITER ATTENTION: condition-detail screen replay "
                         "limit reached; bounded and flagged")
            return _advance(call_id, turn_index, language, "", trace, notes,
                            False, False)

        # The probe alone carries no list context for a caller asking which
        # screen they answered. Recover the original screen from the durable ref.
        original = ""
        ref = pending.get("ref")
        if ref:
            item = next((i for i in items if i.get("itemId") == ref), None)
            if item:
                original = _prompt_for(item, language, customer)
        notes.append("customer asked what the question was; restating the screening "
                     f"question with the probe ({attempts}/{MAX_REPEATS})")
        return {
            "agentUtterance": (f"{original} {pending.get('phrase') or ''}".strip()
                               if original else (pending.get("phrase") or "")),
            "turnIndex": turn_index,
            "askedKind": "conditionDetail",
            "askedRef": pending.get("ref"),
            "isComplete": False, "escalated": False, "stopQuestionnaire": False,
            "language": language, "toolTrace": trace, "notes": notes,
        }

    # A bare yes or okay in answer to "WHICH condition was it?" names nothing and
    # must not reach the classifier, which returns its nearest neighbour for any
    # input: a live caller answered the probe "Yes." and was asked to confirm
    # Transient Ischaemic Attack. The probe stays pending and is re-put briefly.
    # A bare NO to "which condition was it?" is a retraction, not a condition.
    # It used to fall through to the classifier, which returns its nearest
    # neighbour for any string: a live caller's "नहीं" became a Parkinson's
    # Disease read-back, and they spent five turns asking where Parkinson's had
    # come from. The yes stays on the record with a review flag - silently
    # flipping a recorded answer would be worse - but no disease is invented.
    #
    # DELIBERATELY DIFFERENT FROM THE EXPLICIT-DENIAL BRANCH BELOW, which does
    # correct the screen answer to no. A lone "नहीं" here is ambiguous: it can mean
    # "no, I won't name it", "no, I misheard you", or "no, nothing". An explicit
    # "मुझे इनमें से कोई नहीं है" cannot mean any of those, and a caller who says it
    # has answered the screen twice, consistently. So the strength of the denial is
    # what decides whether the form moves, and a bare no is not strong enough.
    if pending and utterance and re.fullmatch(
            r"[\s\u0964\u0965]*(no|nope|nah|nahi|nahin|नहीं|na)[\s.!\u0964\u0965]*",
            utterance.strip(), re.I):
        st.clear_pending_condition_detail(call_id)
        st.clear_repeats(call_id, f"probe:{pending.get('ref')}")
        st.clear_repeats(call_id, f"probe-repeat:{pending.get('ref')}")
        st.record_unclassified_mention(
            call_id, utterance, item_id=pending.get("ref"),
            reason="said yes to the condition screen, then answered the "
                   "which-condition probe with a bare no; needs human review")
        notes.append("UNDERWRITER ATTENTION: yes to the screen but no condition "
                     "named; flagged rather than invented")
        return _advance(call_id, turn_index, language,
                        _consistent_ack(None, language), trace, notes,
                        False, False)

    if pending and utterance and (is_bare_affirmation(utterance)
                                  or is_bare_acknowledgement(utterance)):
        attempts = st.record_repeat(call_id, f"probe:{pending.get('ref')}")
        if attempts >= MAX_REPEATS:
            st.clear_pending_condition_detail(call_id)
            st.clear_repeats(call_id, f"probe:{pending.get('ref')}")
            st.clear_repeats(call_id, f"probe-repeat:{pending.get('ref')}")
            st.record_unclassified_mention(
                call_id, utterance, item_id=pending.get("ref"),
                reason="yes to the condition screen but only bare affirmations "
                       f"were given to the which-condition probe after {MAX_REPEATS} attempts")
            notes.append("UNDERWRITER ATTENTION: condition-detail probe received "
                         "only bare affirmations; bounded and flagged")
            return _advance(call_id, turn_index, language, "", trace, notes,
                            False, False)
        notes.append("the which-condition probe was answered with a bare "
                     f"yes/okay, which names nothing; asking again ({attempts}/{MAX_REPEATS})")
        return {
            "agentUtterance": pending.get("phrase") or "",
            "turnIndex": turn_index,
            "askedKind": "conditionDetail",
            "askedRef": pending.get("ref"),
            "isComplete": False, "escalated": False, "stopQuestionnaire": False,
            "language": language, "toolTrace": trace, "notes": notes,
        }

    if pending and utterance and not parse_language_switch(utterance):
        notes.append(f"condition detail for {pending.get('ref')}: classifying "
                     "what the customer named")
        outcome = _handle_condition(call_id, utterance, language, trace, notes,
                                    min_score=0.78)
        session = ctx.refresh_session()
        # Naming a condition already on the record RESOLVES this probe. See the
        # already_declared branch in _handle_condition for the live call that
        # made this necessary.
        known = (isinstance(outcome, dict)
                 and outcome.get("outcome") in ("already_declared", "elaboration"))

        # THE TOPIC IS LOCKED UNTIL IT IS RESOLVED. The caller said yes to this
        # screen; the conversation is about that condition and nothing else
        # until it is named or attempts run out. Previously a nulled
        # classification (STT garbled "polyp" into "पॉलिट") fell straight
        # through to _advance and the dialogue hopped to heart disease with the
        # caller mid-sentence about their polyp - the exact "switches to a
        # random disease" experience. Classification succeeding means a
        # confirmation or a protocol now exists and takes over naturally;
        # anything else re-asks THIS topic, twice, before giving up loudly.
        resolved = bool(session.get("awaitingConfirmation")
                        or session.get("pendingFollowUps"))

        # THE PROBE ASKED FOR TWO THINGS, SO USE BOTH ANSWERS.
        #
        # It asks "could you tell me what it was, and when you were diagnosed?"
        # and a live caller answered "Asthma 2015." - both halves, in four words.
        # The name opened the asthma protocol and the year was thrown away, so the
        # very next question was "When were you first diagnosed with this, for
        # your Asthma?" and the caller had to say 2015 again. Asking someone to
        # repeat what they just said reads as not listening, and it is the
        # complaint callers actually make.
        #
        # Matched on formField "since" rather than on the question id, the same
        # gate used everywhere else a point in time is handled, so a protocol that
        # orders its follow-ups differently still benefits.
        if resolved and not known:
            fresh = st.get_session(call_id) or session
            head = st.peek_followup(fresh)
            if head and head.get("formField") == "since":
                already = extract.spoken_year(utterance)
                if already:
                    st.complete_followup(call_id, already)
                    notes.append(
                        f"the caller gave the year ({already}) when naming the "
                        f"condition, so {head.get('questionId')} was answered "
                        "from that rather than asked again")
                    session = ctx.refresh_session()

        if resolved or known:
            st.clear_pending_condition_detail(call_id)
            st.clear_repeats(call_id, f"probe:{pending.get('ref')}")
            st.clear_repeats(call_id, f"probe-repeat:{pending.get('ref')}")
            if known:
                label = outcome.get("label") or outcome.get("icd10")
                ack_known = {
                    "hi": f"जी, {label} मैंने पहले ही दर्ज कर लिया है।",
                    "hinglish": f"Ji, {label} maine pehle hi record kar liya hai.",
                }.get(language, f"Yes, I already have {label} on your record.")
                notes.append(f"probe answered with the already-recorded "
                             f"{outcome.get('icd10')}; acknowledged instead of "
                             "re-asking")
                return _advance(call_id, turn_index, language, ack_known,
                                trace, notes, False, False)
        else:
            # A DENIAL HERE IS A RETRACTION OF THE SCREEN, NOT A DISEASE NAME.
            # Only reached when the classifier already found nothing, so it can
            # never swallow a real disclosure (anything ambiguous is flagged for
            # review below, not dropped). Two shapes reach this, both from live
            # Hindi calls that were then re-probed three times for a "name":
            #   - "I didn't say yes" / "नहीं, मैंने हाँ नहीं बोला" (_SCREEN_ANSWER_DENIAL)
            #   - "I don't have any of these" / "नहीं मुझे नहीं है इनमें से कुछ भी"
            #     / "कोई बीमारी नहीं है" / "नहीं है बीमारी"        (_DENIES_POSSESSION)
            # The bare-no branch above misses both: it needs the whole utterance
            # to BE the word "no", and a caller denying a screen says more.
            if (_SCREEN_ANSWER_DENIAL.search(utterance)
                    or _DENIES_POSSESSION.search(utterance)):
                st.clear_pending_condition_detail(call_id)
                st.clear_repeats(call_id, f"probe:{pending.get('ref')}")
                st.clear_repeats(call_id, f"probe-repeat:{pending.get('ref')}")
                # THE FORM FOLLOWS THE CALLER'S LAST WORD.
                #
                # Earlier behaviour left the recorded yes in place and flagged it,
                # on the reasoning stated at the bare-no branch above: on an
                # underwriting form a LOST disclosure is the expensive error, so a
                # recorded yes should not be talked back off the record.
                #
                # Changed deliberately, because that reasoning does not survive
                # contact with the case that actually reaches this branch. Measured
                # on call-63112b8a: the caller's "नहीं" transcribed as "हाँ" on the
                # neuromuscular screen, and when asked which condition she said
                # "नहीं नहीं मुझे नहीं है". The yes was never hers. Keeping it left
                # the form asserting a neuromuscular disease with nothing attached,
                # which can load a premium or attach an exclusion for a disease the
                # customer explicitly denies - a real cost to her, for a yes that
                # was a transcription error.
                #
                # THE ASYMMETRY THAT JUSTIFIES IT. This branch is not a bare "no",
                # which is ambiguous and stays ambiguous. It requires an EXPLICIT
                # denial - _SCREEN_ANSWER_DENIAL ("I didn't say yes") or
                # _DENIES_POSSESSION ("I don't have any of these"). A caller who
                # says that has answered the screen, twice, consistently. Deferring
                # to the transcription over her own correction is the agent not
                # listening. The bare-no branch above keeps its yes precisely
                # because a lone "no" does not carry that much.
                #
                # NOTHING IS LOST. The mention recorded immediately below keeps her
                # verbatim words and says the screen was corrected, so the
                # contradiction still reaches an underwriter and the audit trail
                # still shows a yes was heard first. Pinned by
                # tests/test_screen_denial_corrects_answer.py.
                ref = pending.get("ref")
                if ref:
                    try:
                        tools.save_answer(call_id, ref, False, turn_index,
                                          patient_phrase=utterance)
                        notes.append(f"corrected {ref} to no: the caller explicitly "
                                     "denied it when asked which condition")
                    except Exception as e:  # noqa: BLE001 - never fail a live call
                        log.error("could not correct screen answer %s: %s", ref, e)
                st.record_unclassified_mention(
                    call_id, utterance, item_id=ref,
                    reason="said yes to the condition screen, then explicitly "
                           "denied it when asked which; screen answer corrected "
                           "to no and the contradiction flagged for review")
                notes.append("UNDERWRITER ATTENTION: caller denied a condition on "
                             f"{pending.get('ref')} after the screen yes; flagged "
                             "and moving on rather than pressing for a name")
                ack_denied = {
                    "hi": "माफ़ कीजिए, मैंने ग़लत समझा। आगे बढ़ती हूँ।",
                    "hinglish": "Maaf kijiye, maine galat samajh liya. Aage "
                                "badhti hoon.",
                }.get(language, "My apologies, I misheard. Let me move on.")
                return _advance(call_id, turn_index, language, ack_denied,
                                trace, notes, False, False)

            attempts = st.record_repeat(call_id, f"probe:{pending.get('ref')}")
            if attempts < 3:
                retry_q = {
                    "hi": "माफ़ कीजिए, मैं बीमारी का नाम ठीक से नहीं समझ पाई। "
                          "कृपया सिर्फ़ बीमारी का नाम बताइए?",
                    "hinglish": "Maaf kijiye, main bimari ka naam theek se nahi "
                                "samajh payi. Kripya sirf bimari ka naam bataiye?",
                }.get(language, "Sorry, I didn't catch the condition's name. "
                                "Could you tell me just the name of it?")
                notes.append("could not resolve the named condition; staying on "
                             f"this topic (attempt {attempts})")
                return {
                    "agentUtterance": retry_q, "turnIndex": turn_index,
                    "askedKind": "conditionDetail",
                    "askedRef": pending.get("ref"),
                    "isComplete": False, "escalated": False,
                    "stopQuestionnaire": False, "language": language,
                    "toolTrace": trace, "notes": notes,
                }
            st.clear_pending_condition_detail(call_id)
            st.clear_repeats(call_id, f"probe:{pending.get('ref')}")
            st.clear_repeats(call_id, f"probe-repeat:{pending.get('ref')}")
            st.record_unclassified_mention(
                call_id, utterance, item_id=pending.get("ref"),
                reason="yes to the screen but the condition could not be "
                       "understood after three attempts; needs human review")
            notes.append("UNDERWRITER ATTENTION: condition never resolved on "
                         f"{pending.get('ref')}; flagged, moving on")
        return _advance(call_id, turn_index, language, "", trace, notes,
                        False, False)

    return FALL_THROUGH


_PRE_LLM_STATES.extend([
    (DialogueState.EXPLANATION_PENDING,
     lambda ctx: bool((ctx.session.get("explanationPending") or {}) and ctx.utterance),
     _state_explanation_pending),
    (DialogueState.EXPLANATION_PENDING,
     lambda ctx: bool(ctx.utterance and is_explanation_request(ctx.utterance)),
     _state_explanation_request),
    (DialogueState.REFUSAL_CONFIRM,
     lambda ctx: bool(ctx.session.get("refusalConfirmPending") and ctx.utterance),
     _state_refusal_confirm),
    (DialogueState.SUMMARY_CONFIRM,
     lambda ctx: bool(ctx.session.get("summaryAskPending") and ctx.utterance),
     _state_summary_confirm),
    (DialogueState.MED_VERIFY,
     lambda ctx: bool(ctx.session.get("medCheckPending") and ctx.utterance),
     _state_med_verify),
    (DialogueState.CONDITION_DETAIL_PROBE,
     lambda ctx: bool(ctx.session.get("pendingConditionDetail")),
     _state_condition_detail_probe),
])


# --------------------------------------------------------------------------- #
# STAGE 2b - the POST-QUESTION, pre-LLM overlays.
#
# These run AFTER next_question() has resolved the pending question (ctx.asked)
# and BEFORE the one LLM call. Unlike _PRE_LLM_STATES they need ctx.asked, because
# each re-asks or re-resolves the pending question. Same loop, same FALL_THROUGH
# contract; a separate table only because the phase (and its precondition) differ.
# ctx.asked is guaranteed set and non-None here - the opening-turn and
# asked-is-None cases return before this dispatch runs.
# --------------------------------------------------------------------------- #
def _state_language_switch(ctx: TurnContext) -> Any:
    """AWAITING_LANGUAGE: "actually, Hindi" mid-call.

    Before the LLM, because a language name is not a medical answer and must never
    reach the extractor. A live caller lost the whole call to this: their third,
    correctly-transcribed "Hindi" was parsed as a reply to "Am I speaking with
    <name>?" and the agent carried on in English. Costs nothing when it does not
    apply - a substring check on a short string - and saves an LLM call when it
    does. Re-resolves the pending question in the NEW language rather than
    translating the old text, so the wording comes from the schema or graph exactly
    as it would have if this language had been chosen first.
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    items, customer = ctx.items, ctx.customer

    switch = parse_language_switch(utterance)
    if not (switch and switch != language):
        return FALL_THROUGH
    language = st.set_language(call_id, switch, confirmed=True)
    ctx.language = language
    session = ctx.refresh_session()
    asked = next_question(session, items, language)
    if asked is None:
        return _complete(call_id, session, turn_index, trace)
    if asked["kind"] == "item":
        asked["text"] = _prompt_for(asked["meta"], language, customer)
    ctx.asked = asked
    lead = {
        "hi": "ठीक है, मैं हिंदी में आगे बात करती हूँ। ",
        "hinglish": "Theek hai, main Hindi mein aage baat karti hoon. ",
    }.get(language, "Of course, I'll continue in English. ")
    notes.append(f"customer asked to switch to {switch} mid-call; honoured "
                 "and re-asked the pending question in that language")
    return {
        "agentUtterance": (lead + (asked.get("text") or "")).strip(),
        "turnIndex": turn_index,
        "askedKind": asked["kind"],
        "askedRef": _ref(asked),
        "isComplete": False, "escalated": False, "stopQuestionnaire": False,
        "language": language, "toolTrace": trace, "notes": notes,
    }


def _state_repeat_request(ctx: TurnContext) -> Any:
    """FORM_ITEM: "sorry, what was that?" - replay the pending question.

    Ahead of the LLM, and ahead of the repeat counter. A caller asking us to say it
    again has not failed to answer, so this must not count towards MAX_REPEATS -
    otherwise three polite requests exhaust the item and it is recorded
    "not_captured" without them ever having been asked properly. It also must not
    reach extract.understand, which would try to read it as an answer, nor the
    condition classifier, which returns its nearest neighbour for any input.
    """
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    asked = ctx.asked
    if not is_repeat_request(ctx.utterance):
        return FALL_THROUGH
    notes.append("customer asked for the question again")
    lead = {
        "hi": "ज़रूर। ",
        "hinglish": "Zaroor. ",
    }.get(language, "Of course. ")
    return {
        "agentUtterance": (lead + (asked.get("text") or "")).strip(),
        "turnIndex": turn_index,
        "askedKind": asked["kind"],
        "askedRef": _ref(asked),
        "isComplete": False, "escalated": False, "stopQuestionnaire": False,
        "language": language, "toolTrace": trace, "notes": notes,
    }


def _state_bare_acknowledgement(ctx: TurnContext) -> Any:
    """FORM_ITEM: "okay, go ahead" is not an answer and not a failure.

    A caller who says "ठीक है" after a preamble is signalling readiness, and the
    agent used to apologise for mishearing and re-read the whole thing - three
    times in a row on a live call with perfect transcripts. Nothing was misheard,
    so no apology; nothing was attempted, so no repeat is counted against them.
    BOUNDED, because "okay" can also be a mis-transcription: after two consecutive
    acks on the same question the third falls through to the normal answer
    machinery, which counts attempts and eventually moves on rather than circling.
    """
    call_id, turn_index, language = ctx.call_id, ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    asked = ctx.asked
    if not (is_bare_acknowledgement(ctx.utterance)
            and st.record_repeat(call_id, f"ack:{_ref(asked)}") <= 2):
        return FALL_THROUGH
    notes.append("customer acknowledged; asking the pending question without "
                 "an apology and without burning an attempt")
    return {
        "agentUtterance": (asked.get("text") or "").strip(),
        "turnIndex": turn_index,
        "askedKind": asked["kind"],
        "askedRef": _ref(asked),
        "expectedAnswerType": asked.get("answerType"),
        "isComplete": False, "escalated": False, "stopQuestionnaire": False,
        "language": language, "toolTrace": trace, "notes": notes,
    }


_POST_QUESTION_STATES: list[tuple["DialogueState", Callable[["TurnContext"], bool],
                                  Callable[["TurnContext"], Any]]] = [
    (DialogueState.AWAITING_LANGUAGE,
     lambda ctx: bool(ctx.utterance
                      and parse_language_switch(ctx.utterance)
                      and parse_language_switch(ctx.utterance) != ctx.language),
     _state_language_switch),
    (DialogueState.FORM_ITEM,
     lambda ctx: bool(is_repeat_request(ctx.utterance)),
     _state_repeat_request),
    (DialogueState.FORM_ITEM,
     lambda ctx: bool(is_bare_acknowledgement(ctx.utterance)),
     _state_bare_acknowledgement),
]


def _dispatch_post_question_states(ctx: TurnContext) -> Any:
    """Run the post-question, pre-LLM overlays in priority order.

    Same contract as _dispatch_pre_llm_states, but for the phase after
    next_question resolved ctx.asked.

    The bare-ack row's guard tests only is_bare_acknowledgement and does NOT call
    record_repeat - guards must be side-effect free, since a guard may run for a
    row that does not fire. The <=2 bound lives inside the handler, which
    increments the counter and returns FALL_THROUGH past the cap. That is exactly
    the inline `if is_bare_acknowledgement(u) and record_repeat(...) <= 2:` - the
    counter was incremented only once the ack was recognised, and only then bounded
    - with the increment kept in the handler so it happens once, when the row acts.
    """
    for state, guard, handler in _POST_QUESTION_STATES:
        if not guard(ctx):
            continue
        result = handler(ctx)
        if result is FALL_THROUGH:
            continue
        log.info("turn %d handled in state %s (post-question)",
                 ctx.turn_index, state.value)
        return result
    return FALL_THROUGH


# --------------------------------------------------------------------------- #
# STAGE 4 - the POST-LLM overlays.
#
# These run AFTER the one LLM call (reading = extract.understand) and consume its
# `reading`, so their handlers take (ctx, reading). Same FALL_THROUGH contract as
# the earlier registries. Built incrementally: the clean, turn-OWNING overlays at
# the top of STAGE 4 (distress, ...) become rows; the linear answer path - record,
# height/weight, med-consistency - and the large volunteered-condition harvest stay
# INLINE as the fall-through, exactly as docs/ORCHESTRATOR_CHANGEABILITY_MAP.md
# prescribes. List order is priority.
# --------------------------------------------------------------------------- #
def _state_distress_check(ctx: TurnContext, reading: dict) -> Any:
    """DISTRESS_CHECK: safety first, corroborate before escalating, once.

    Checked before anything else and before any answer is recorded. A disclosure
    of self-harm ends the questionnaire; it does not get noted and worked around.

    A live call produced "I saw him suddenly." from an ordinary answer, the model
    read distress into it, and the caller was told a doctor was being arranged -
    the questionnaire halted and a clinician paged over a transcription error. This
    is the one place where being wrong in EITHER direction does real harm: escalate
    immediately when the WORDS corroborate it, otherwise ask one gentle open
    question before deciding, and a second signal escalates regardless of wording.
    The gentle question asks them to say more (safe either way), not "did you mean
    that?" (which invites a distressed person to retract).
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    asked = ctx.asked

    if reading.get("distressSignal") and not distress_corroborated(utterance) \
            and not session.get("distressCheckPending"):
        st.set_distress_check_pending(call_id, True)
        gentle = {
            "hi": "मैं आपकी बात ठीक से समझना चाहती हूँ। क्या आप मुझे इसके बारे में थोड़ा और बता सकती हैं?",
            "hinglish": "Main aapki baat theek se samajhna chahti hoon. Kya aap mujhe iske baare "
                        "mein thoda aur bata sakti hain?",
        }.get(language,
              "I want to make sure I understood you properly. Could you tell me a "
              "little more about that?")
        notes.append("possible distress signal but the wording does not corroborate "
                     "it; asking once before escalating")
        return {
            "agentUtterance": gentle, "turnIndex": turn_index, "isComplete": False,
            "escalated": False, "stopQuestionnaire": False, "language": language,
            "askedKind": "distressCheck", "askedRef": "distressCheck",
            "toolTrace": trace, "notes": notes,
        }

    if session.get("distressCheckPending") and not reading.get("distressSignal"):
        st.set_distress_check_pending(call_id, False)
        lead = {
            "hi": "स्पष्ट करने के लिए धन्यवाद। अब हम वहीं से जारी रखते हैं। ",
            "hinglish": "Clear karne ke liye dhanyavaad. Ab hum wahi se continue karte hain. ",
        }.get(language, "Thank you for clarifying. Let us continue where we left off. ")
        notes.append("gentle distress check was not corroborated; cleared it and "
                     "re-asked the original question without recording this turn")
        return {
            "agentUtterance": (lead + (asked.get("text") or "")).strip(),
            "turnIndex": turn_index, "askedKind": asked["kind"],
            "askedRef": _ref(asked),
            "expectedAnswerType": asked.get("answerType"),
            "isComplete": False, "escalated": False,
            "stopQuestionnaire": False, "language": language,
            "toolTrace": trace, "notes": notes,
        }

    if reading.get("distressSignal"):
        st.set_distress_check_pending(call_id, False)
        r = tools.escalate_to_human(call_id, "self_harm_disclosure", utterance[:300])
        trace.append({"tool": "escalate_to_human", "ok": True,
                      "resultSummary": {"reason": "self_harm_disclosure"}})
        supportive = {
            "hi": "आपने यह बताया, इसके लिए धन्यवाद। मैं अभी आपके लिए एक डॉक्टर से बात करवा रही हूँ। "
                  "आप अकेले नहीं हैं।",
            "hinglish": "Aapne yeh bataya, iske liye dhanyavaad. Main abhi aapke liye ek doctor se "
                        "baat karwa rahi hoon. Aap akele nahi hain.",
        }.get(language,
              "Thank you for telling me that. I'm arranging for a doctor to speak with you "
              "right now. You're not on your own with this.")
        return {
            "agentUtterance": supportive,
            "turnIndex": turn_index,
            "isComplete": False,
            "escalated": True,
            "stopQuestionnaire": bool(r.get("stopQuestionnaire")),
            "language": language,
            "toolTrace": trace,
            "notes": ["distress detected, questionnaire halted"],
        }

    return FALL_THROUGH


def _state_refusal_detected(ctx: TurnContext, reading: dict) -> Any:
    """REFUSAL_CONFIRM: the LLM read a refusal - confirm once before ending.

    "I'm going to go." ended a live call immediately; the same call produced
    "Seaman.", "Enceladus?" and "And so then." from ordinary answers. Hanging up on
    a caller who did not ask to stop is unrecoverable in a way one extra question is
    not, so an ambiguous refusal is checked once, and only when not already
    confirmed - someone who says it twice, or unmistakably, is not made to argue
    with a machine to get off the phone.

    "I didn't say yes or no" is NOT a request to hang up: a dense-with-negations
    correction the model reported as a refusal. Treated as a correction - re-ask,
    do not count an attempt, flag the turn.
    """
    call_id, session, utterance = ctx.call_id, ctx.session, ctx.utterance
    turn_index, language = ctx.turn_index, ctx.language
    trace, notes = ctx.trace, ctx.notes
    asked = ctx.asked

    if reading.get("isRefusal") and _SCREEN_ANSWER_DENIAL.search(utterance or ""):
        notes.append("UNDERWRITER ATTENTION: caller says the recorded answer was "
                     "not theirs; re-asking rather than treating it as a refusal")
        return _reask(call_id, asked, language, "", turn_index, trace,
                      "caller denies having answered; not a refusal",
                      customer=ctx.customer)

    if reading.get("isRefusal") and not session.get("refusalConfirmPending"):
        st.set_refusal_confirm_pending(call_id, True)
        ask = {
            "hi": "ठीक है। क्या आप अभी कॉल समाप्त करना चाहती हैं?",
            "hinglish": "Theek hai. Kya aap abhi call khatam karna chahti hain?",
        }.get(language, "Of course. Just to check — would you like to stop here?")
        notes.append("possible refusal, confirming once before ending the call")
        return {
            "agentUtterance": ask, "turnIndex": turn_index, "isComplete": False,
            "escalated": False, "stopQuestionnaire": False, "language": language,
            "askedKind": "refusalConfirm", "askedRef": "refusalConfirm",
            "toolTrace": trace, "notes": notes,
        }

    if reading.get("isRefusal"):
        st.mark_status(call_id, st.STATUS_DROPPED, note="customer declined to continue")
        closing = {
            "hi": "कोई बात नहीं, मैं समझती हूँ। हम आपको बाद में कॉल करेंगे। धन्यवाद।",
            "hinglish": "Koi baat nahi, main samajhti hoon. Hum aapko baad mein call karenge. Dhanyavaad.",
        }.get(language, "That's completely fine. We'll call you back another time. Thank you.")
        return {
            "agentUtterance": closing, "turnIndex": turn_index, "isComplete": False,
            "escalated": False, "stopQuestionnaire": True, "language": language,
            "toolTrace": trace, "notes": ["customer declined"],
        }

    return FALL_THROUGH


_POST_LLM_STATES: list[tuple["DialogueState",
                             Callable[["TurnContext", dict], bool],
                             Callable[["TurnContext", dict], Any]]] = [
    (DialogueState.DISTRESS_CHECK,
     lambda ctx, reading: bool(reading.get("distressSignal")
                               or ctx.session.get("distressCheckPending")),
     _state_distress_check),
    (DialogueState.REFUSAL_CONFIRM,
     lambda ctx, reading: bool(reading.get("isRefusal")),
     _state_refusal_detected),
]


def _dispatch_post_llm_states(ctx: TurnContext, reading: dict) -> Any:
    """Run the post-LLM overlays in priority order; return a reply or FALL_THROUGH.

    Same contract as the earlier dispatch loops, but handlers take (ctx, reading)
    because STAGE 4 consumes the LLM reading. A row that falls through lets the
    linear answer path below run, exactly as reaching the end of the inline block
    used to.
    """
    for state, guard, handler in _POST_LLM_STATES:
        if not guard(ctx, reading):
            continue
        result = handler(ctx, reading)
        if result is FALL_THROUGH:
            continue
        log.info("turn %d handled in state %s (post-LLM)",
                 ctx.turn_index, state.value)
        return result
    return FALL_THROUGH


def handle_turn(
    call_id: str,
    utterance: str,
    turn_index: int,
    language_hint: str = "hinglish",
    stt_confidence: float | None = None,
) -> dict:
    """Process one turn and stamp the reply with the state it was answered from.

    A thin wrapper around _handle_turn_impl, which does the actual work and is
    otherwise unchanged - this function exists only so dialogueState can be
    computed from the session exactly as _handle_turn_impl first loaded it,
    without threading that computation through every one of its ~15 return
    statements. See DialogueState's docstring for what the field is and is not:
    it names what this turn was about to consume, purely for logging and
    inspection, and nothing anywhere branches on it.
    """
    session_before = st.get_session(call_id)
    dialogue_state = None
    items_before = None
    if session_before and not st.is_terminal_session(session_before):
        # Fetched here and threaded through to _handle_turn_impl as
        # _prefetched_items, rather than left for it to fetch again: get_form_items
        # is a DynamoDB query, and every turn already pays for it once. Naming the
        # state this turn started in must not tax every single call a second time
        # for the same 38 rows.
        items_before = st.get_form_items(session_before.get("formId") or "")
        language_before = session_before.get("language") or language_hint
        try:
            dialogue_state = current_dialogue_state(
                session_before, items_before, language_before)
        except Exception:  # noqa: BLE001
            # A naming convenience must never be why a real turn fails.
            log.exception("current_dialogue_state failed for %s; omitting "
                          "dialogueState from this reply", call_id)

    result = _handle_turn_impl(call_id, utterance, turn_index, language_hint,
                               stt_confidence, _prefetched_items=items_before)
    if dialogue_state is not None:
        result["dialogueState"] = dialogue_state.value
    return result


def _handle_turn_impl(
    call_id: str,
    utterance: str,
    turn_index: int,
    language_hint: str = "hinglish",
    stt_confidence: float | None = None,
    _prefetched_items: list[dict] | None = None,
) -> dict:
    session = st.get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")

    if st.is_terminal_session(session):
        status = session.get("status")
        language = session.get("language") or language_hint
        closing = {
            "hi": "यह कॉल आगे जारी नहीं रह सकती। हमारी टीम आपसे अलग से संपर्क करेगी।",
            "hinglish": ("Yeh call aage continue nahi ho sakti. Hamari team aapse "
                          "alag se contact karegi."),
        }.get(language,
              "This call cannot continue. Our team will contact you separately.")
        if status == st.STATUS_COMPLETED:
            closing = {
                "hi": "यह प्रश्नावली पहले ही पूरी हो चुकी है। धन्यवाद।",
                "hinglish": "Yeh questionnaire pehle hi complete ho chuka hai. Dhanyavaad.",
            }.get(language, "This questionnaire is already complete. Thank you.")
        return {
            "agentUtterance": closing,
            "turnIndex": turn_index,
            "isComplete": status == st.STATUS_COMPLETED,
            "escalated": status == st.STATUS_ESCALATED,
            "stopQuestionnaire": True,
            "language": language,
            "toolTrace": [],
            "notes": [f"rejected turn for terminal session status={status}"],
        }

    if not session.get("dateOfBirth"):
        return _terminate_identity_unavailable(
            call_id, session, turn_index, language_hint)

    customer = {
        "customerName": session.get("customerName"),
        "customerEmail": session.get("customerEmail"),
        "policyNumber": session.get("policyNumber"),
        "insurerName": session.get("insurerName") or "Meridian Health Cover",
    }
    language = session.get("language") or language_hint
    # Reuse the wrapper's read when it took one - formId is fixed at
    # start_session and nothing mutates it mid-call, so it is still correct for
    # THIS session read a moment later. None only when the wrapper skipped the
    # fetch (a terminal session, handled above and already returned).
    items = (_prefetched_items if _prefetched_items is not None
            else st.get_form_items(session["formId"]))

    trace: list[dict] = []
    notes: list[str] = []
    escalated = False
    stop = False

    # ---- language before anything else -------------------------------------
    # Ahead of next_question because the question text depends on the language,
    # and ahead of the LLM because a language name is not something worth paying
    # an inference for.
    if not session.get("languageConfirmed"):
        if not utterance:
            count = st.record_language_ask(call_id)
            return _ask_language(turn_index, trace, first=(count <= 1),
                                 insurer=customer["insurerName"])

        choice = parse_language_choice(utterance)
        if choice is None:
            # They may have answered without naming a language. Replying in
            # Devanagari IS an answer, and spending a re-ask to rediscover that is
            # what made a live caller say "हिंदी" three times while STT returned
            # "हाँ". See infer_language_from_reply.
            choice = infer_language_from_reply(utterance)
            if choice:
                notes.append(
                    f"no language named, but the reply itself was Hindi; "
                    f"inferred {choice} from how they answered rather than "
                    f"spending a re-ask")
        if choice is None:
            asked_count = int(session.get("languageAskCount") or 0)
            if asked_count < LANGUAGE_ASK_MAX:
                st.record_language_ask(call_id)
                return _ask_language(
                    turn_index, trace, first=False,
                    note=f"could not tell which language was requested "
                         f"(attempt {asked_count} of {LANGUAGE_ASK_MAX})",
                    insurer=customer["insurerName"])
            choice = infer_language_from_script(utterance)
            notes.append(f"no clear choice after {asked_count} attempts; inferred "
                         f"{choice} from the script the customer replied in")
        else:
            notes.append(f"customer chose {choice}")

        language = st.set_language(call_id, choice, confirmed=True)
        session = st.get_session(call_id) or session
        # What they just said was the language CHOICE, not an answer. Falling
        # through would hand "English" to extract.understand as a reply to "Am I
        # speaking with <name>?" - which parses as a plausible-looking no.
        return _first_question(call_id, session, items, customer, language,
                               turn_index, trace, notes)

    # A bounded classification read-back that has exhausted its attempts must be
    # removed from durable state before any priority decision. Otherwise it stops
    # being spoken but continues to block deferred conditions forever.
    session = _clear_expired_confirmation(call_id, session, trace, notes)

    # ---- pre-LLM state machine (STAGE 2) -----------------------------------
    # The deterministic overlays, dispatched in explicit priority order rather
    # than by source position. See _PRE_LLM_STATES and _dispatch_pre_llm_states.
    # A state either consumes the turn (returns a reply) or falls through, and a
    # handler that mutated the session writes it back here for the code below.
    # A resumed call owes an identity re-verification (name, then DOB) before it
    # will run any inherited overlay or classify any deferred mention. The pre-LLM
    # overlays fire on their own persisted flags, so they are skipped wholesale
    # while the queue is non-empty; next_question/_pending_opening_question then
    # surface the owed identity item and the ordinary answer path below verifies
    # it, failing closed on a wrong name/DOB. Emptied as each item re-verifies,
    # after which this turn's successors dispatch overlays normally.
    reconfirm_identity = bool(session.get("identityReconfirmQueue"))

    _ctx = TurnContext(
        call_id=call_id, utterance=utterance, turn_index=turn_index,
        language=language, customer=customer, items=items, session=session,
        trace=trace, notes=notes)
    if not reconfirm_identity:
        _dispatched = _dispatch_pre_llm_states(_ctx)
        session, language = _ctx.session, _ctx.language
        if _dispatched is not FALL_THROUGH:
            return _dispatched

    # ---- pick up conditions that waited their turn ---------------------------
    # The other half of one-condition-at-a-time: mentions deferred while a
    # protocol was running are classified now that nothing is pending, so
    # "aur mujhe sugar bhi hai" said during the cancer questions opens the
    # diabetes protocol HERE - after cancer finished - not over the top of it.
    if not reconfirm_identity and not (
            session.get("pendingFollowUps") or session.get("awaitingConfirmation")):
        deferred = st.pop_deferred_mention(call_id)
        if deferred:
            notes.append(f"returning to the condition mentioned earlier: "
                         f"'{deferred[:40]}'")
            _handle_condition(call_id, deferred, language, trace, notes,
                              min_score=_DEFERRED_MIN_SCORE)
            session = st.get_session(call_id) or session

    # ---- what were we waiting on? -------------------------------------------
    asked = next_question(session, items, language)

    # NEVER ASK WHAT A PROTOCOL ALREADY ANSWERED. When the pending form item's
    # formField was collected by a graph follow-up earlier in this call, the answer
    # is copied across silently and the questionnaire moves to the next item. The
    # caller notices repeats - "एक साल पहले बोला था तो?" was said to us on a live
    # call - and being made to repeat an answer reads as not being listened to.
    #
    # A bounded loop because several consecutive child items can all be prefilled
    # (a hospitalisation block after the protocol asked when/treatment/resolved).
    # Bounded so a cyclic schema cannot spin this forever.
    #
    # KNOWN RESIDUAL, ordering. This runs before the utterance is read, and the
    # question the caller is currently answering was chosen at the END of the
    # previous turn - so an item that was asked can still be auto-filled here, and
    # the caller's reply is then matched against the NEXT item and apologised for.
    # That is what a live call showed: A520 asked, caller answered, the carried
    # value won, and turn 52 opened with "यह सही नहीं लग रहा" about A530.
    #
    # NOT guarded on currentItemId, which looks like the right signal and is not:
    # hvi_state maintains it from save_answer, so it names the last item ANSWERED,
    # never the one last asked. Guarding on it would skip prefills at random.
    # Closing this properly needs the asked item to be checkpointed when it is
    # asked; until then the exposure is bounded by _answered_on_this_leg, which
    # removes the cross-leg case that actually reached a record.
    for _ in range(10):
        if not (asked and asked["kind"] == "item" and asked.get("prefillFrom")):
            break
        pre = asked["prefillFrom"]
        tools.save_answer(call_id, asked["meta"]["itemId"], pre["value"], turn_index,
                          patient_phrase=f"(carried from {pre['questionId']})",
                          stt_confidence=None)
        trace.append({"tool": "save_answer", "ok": True,
                      "resultSummary": {"itemId": asked["meta"]["itemId"],
                                        "carriedFrom": pre["questionId"]}})
        notes.append(f"{asked['meta']['itemId']} auto-filled from protocol "
                     f"follow-up {pre['questionId']}; not asking twice")
        session = st.get_session(call_id) or session
        asked = next_question(session, items, language)

    if asked and asked["kind"] == "item":
        asked["text"] = _prompt_for(asked["meta"], language, customer)

    # ---- opening turn: nothing said yet -------------------------------------
    if not utterance:
        return _first_question(call_id, session, items, customer, language,
                               turn_index, trace, notes)

    if asked is None:
        return _complete(call_id, session, turn_index, trace)

    # ---- post-question, pre-LLM overlays (STAGE 2b) -----------------------
    # Language switch, repeat request, bare acknowledgement - each re-asks the
    # pending question, so they run here (ctx.asked resolved) rather than in the
    # pre-LLM registry. Ordered data, same FALL_THROUGH contract. See
    # _POST_QUESTION_STATES.
    _pq_ctx = TurnContext(
        call_id=call_id, utterance=utterance, turn_index=turn_index,
        language=language, customer=customer, items=items, session=session,
        trace=trace, notes=notes, asked=asked)
    _pq = _dispatch_post_question_states(_pq_ctx)
    session, language, asked = _pq_ctx.session, _pq_ctx.language, _pq_ctx.asked
    if _pq is not FALL_THROUGH:
        return _pq

    # ---- the one LLM call ---------------------------------------------------
    reading = extract.understand(
        question_text=asked["text"] or "",
        answer_type=asked["answerType"],
        utterance=utterance,
        language=language,
        item=asked["meta"],
        customer_name=customer["customerName"],
    )
    trace.append({"tool": "llm_understand", "ok": "error" not in reading,
                  "resultSummary": {
                      "understood": reading.get("understood"),
                      "confidence": reading.get("confidence"),
                      "mentionsCondition": reading.get("mentionsCondition"),
                      "distressSignal": reading.get("distressSignal"),
                  }})

    # AN EXPLICIT CHOICE IS NOT OVERRIDDEN BY CODE-MIXING.
    #
    # This used to follow the customer's detected script unconditionally, which was
    # right when the language came from the CRM - a guess typed by someone else
    # deserves to lose to evidence. It is wrong now that the customer is asked
    # directly. Hinglish is supported as INPUT in both modes, so a caller who chose
    # English and then says "haan mujhe sugar hai" is doing something completely
    # normal; flipping the agent into Hindi at that point would be the system
    # overruling the answer it had just solicited, and it would flap back and forth
    # for the rest of the call as the mixture varied turn to turn.
    #
    # Understanding the customer and answering in their chosen language are separate
    # concerns. This is the second one.
    # The reading may have arrived with its value field mangled and self-repaired
    # from the rest of the same reading. Surfaced rather than silent: the record
    # should show that what was stored is not byte-for-byte what the model returned.
    if reading.get("valueRepairNote"):
        notes.append(reading["valueRepairNote"])

    detected = reading.get("detectedLanguage")
    if session.get("languageConfirmed"):
        if detected in ("en", "hi", "hinglish") and detected != language:
            # Worth recording: a persistent mismatch is the signal that the customer
            # picked a language they are not actually comfortable in.
            notes.append(f"customer is speaking {detected} but chose {language}; "
                         f"honouring their choice")
    elif detected in ("en", "hi", "hinglish") and detected != language:
        language = detected
        notes.append(f"switched to {detected} to match the customer")

    # The acknowledgement is the only model-written text that reaches the
    # customer; the questions themselves come from the schema or the graph. So if
    # its script disagrees with the language in play, drop it rather than speak a
    # mixed-script sentence like "ठीक है, समझा. Aapki height kitni hai?".
    ack = _consistent_ack(reading.get("acknowledgement"), language,
                          customer.get("customerName"))

    # ---- post-LLM overlays (STAGE 4) ---------------------------------------
    # Safety-first / distress is the first row; more will move here. The
    # linear answer path below is the fall-through. See _POST_LLM_STATES.
    _pl_ctx = TurnContext(
        call_id=call_id, utterance=utterance, turn_index=turn_index,
        language=language, customer=customer, items=items, session=session,
        trace=trace, notes=notes, asked=asked)
    _pl = _dispatch_post_llm_states(_pl_ctx, reading)
    session, language, asked = _pl_ctx.session, _pl_ctx.language, _pl_ctx.asked
    if _pl is not FALL_THROUGH:
        return _pl

    # ---- confirmation of a parked classification ---------------------------
    if asked["kind"] == "confirmation":
        value, err = extract.coerce(reading.get("value"), "boolean")
        parked = asked["meta"]
        if err or value is None:
            attempts = st.record_repeat(call_id, _ref(asked))
            if attempts < MAX_REPEATS:
                return _reask(call_id, asked, language, ack, turn_index, trace,
                              f"could not tell whether they agreed ({attempts}/{MAX_REPEATS})",
                              customer=customer)
            session = _clear_expired_confirmation(
                call_id, st.get_session(call_id) or session, trace, notes)
            return _advance(call_id, turn_index, language, ack, trace, notes,
                            escalated, stop)
        st.set_awaiting_confirmation(call_id, None)
        # Reset the attempt counter for this read-back now that it is resolved.
        # It was never cleared before, because this branch returns above the
        # "record the answer" path where clear_repeats normally runs. The count
        # therefore accumulated across every read-back in a call and never reset,
        # which is what broke a first attempt at bounding it: the bound tripped on
        # a confirmation the caller had not actually been asked three times.
        st.clear_repeats(call_id, _ref(asked))
        if value:
            notes.append(f"patient confirmed {parked.get('icd10')}")
            # The parked record kept the phrase and score from when the condition
            # was first heard, so a read-back does not lose the provenance.
            # The caller said yes, which is necessary but not sufficient. Carry
            # the classifier's corroboration verdict through so the record
            # distinguishes "the graph and the embeddings agreed, and she
            # confirmed it" from "we guessed, and she agreed" - the second is
            # what put Chronic Viral Hepatitis on a bronchitis disclosure.
            session = _queue_protocol(call_id, parked.get("icd10"),
                                      parked.get("label"), language, trace, notes,
                                      patient_phrase=parked.get("phrase"),
                                      confidence=parked.get("score"),
                                      corroborated=bool(parked.get("corroborated")),
                                      confirmed_by_patient=True)
        else:
            notes.append(f"patient rejected the {parked.get('icd10')} interpretation")
            # NEVER blacklist a condition the caller has already declared. A
            # rejected read-back of an already-recorded code (which should not
            # be offered at all - see the alternatives filter below) would
            # otherwise make that real condition unnameable for the rest of the
            # call: a live caller rejected a "दमा" re-read while asthma was on
            # their record, and every later "अस्थमा" was then skipped as a
            # rejected code.
            declared_codes = {row.get("icd10") for row in
                              ((st.get_session(call_id) or {})
                               .get("declaredConditions") or [])}
            if parked.get("icd10") in declared_codes:
                notes.append(f"not blacklisting {parked.get('icd10')}: it is "
                             "already declared on this call")
            else:
                st.add_rejected_code(call_id, parked.get("icd10"))

            # THE DISCLOSURE DOES NOT DIE WITH THE GUESS.
            #
            # The graph is deterministic; the ENTRY into it was a single
            # nearest-neighbour guess, and rejecting that guess used to abandon
            # the whole disclosure. A live caller said "कैंसर था पाँच साल पहले",
            # rejected the breast-cancer read-back - correctly, it was a guess on
            # a 0.03 margin - and the call moved to heart disease with their
            # cancer nowhere in the protocol. They noticed: "आपने कैंसर का
            # पूछताछ ही नहीं की." This applies to every condition family, not
            # just cancer.
            #
            # A rejection is INFORMATION: not that code. So walk the classifier's
            # own remaining candidates - already scored, already carrying labels
            # from the graph - and read back the next clinically distinct one.
            # Already-declared codes are excluded as well as rejected ones:
            # asking "was it asthma?" when asthma is already on the record is
            # the question a live caller answered with "नहीं अभी तो बताया ना
            # अस्थमा था" - and their "no" then blacklisted their own condition.
            alts = [a for a in (parked.get("alternatives") or [])
                    if a.get("icd10")
                    and a["icd10"] != parked.get("icd10")
                    and a["icd10"] not in declared_codes
                    and a["icd10"] not in
                    (st.get_session(call_id) or {}).get("rejectedCodes", [])]
            if alts:
                nxt = alts[0]
                label = (nxt.get("label_hi") if language == "hi" else None) \
                    or nxt.get("label_en") or nxt["icd10"]
                q = {
                    "hi": f"समझ गई, वह नहीं। तो क्या यह {label} है?",
                    "hinglish": f"Samajh gayi, woh nahi. Toh kya yeh {label} hai?",
                }.get(language, f"I see, not that. Was it {label}?")
                st.set_awaiting_confirmation(call_id, {
                    "icd10": nxt["icd10"],
                    "label": nxt.get("label_en") or nxt["icd10"],
                    "question": q, "phrase": parked.get("phrase"),
                    "score": nxt.get("score"),
                    "redFlag": parked.get("redFlag"),
                    # NEVER corroborated. These alternatives come from the
                    # embedding candidate list, so by construction the graph did
                    # not independently reach them from a known synonym - if it
                    # had, that code would have been the corroborated top hit. A
                    # yes here is one caller's agreement to a ranked guess, which
                    # is exactly the evidence an underwriter must be able to see.
                    "corroborated": False,
                    "alternatives": alts[1:],
                })
                notes.append(f"offering the next graph candidate "
                             f"{nxt['icd10']} instead of dropping the disclosure")
                return {
                    "agentUtterance": q, "turnIndex": turn_index,
                    "askedKind": "confirmation", "askedRef": f"confirm:{nxt['icd10']}",
                    "expectedAnswerType": "boolean",
                    "isComplete": False, "escalated": escalated,
                    "stopQuestionnaire": stop, "language": language,
                    "toolTrace": trace, "notes": notes,
                }

            # No candidates left: ask THEM, in their own words, via the existing
            # open probe. Their fresh answer re-classifies with the rejected
            # codes excluded, so it cannot circle back to the same wrong guess.
            probe = {
                "hi": "ठीक है। तो कृपया अपने शब्दों में बताइए, यह क्या था?",
                "hinglish": "Theek hai. Toh apne shabdon mein bataiye, yeh kya tha?",
            }.get(language, "Alright. Then in your own words, what was it?")
            st.set_pending_condition_detail(call_id, "rejected-readback", probe)
            notes.append("all read-back candidates rejected; asking in the "
                         "caller's own words rather than dropping the disclosure")
            # Rejected, so the code is definitely not recorded. But silence here
            # would be its own failure: the patient told us about a health
            # condition and we now know for certain we could not code it. That is
            # exactly the "escalate to human" case - the read-back did its job by
            # catching the bad match, and throwing the mention away would waste
            # what it caught.
            #
            # Relying on the free-text answer to carry their words does not hold.
            # Conditions get volunteered while answering something else, and if
            # that something else was a yes/no or a date, the stored value is a
            # boolean and the patient's own phrase survives nowhere an
            # underwriter would look.
            phrase = parked.get("phrase") or ""
            try:
                st.record_unclassified_mention(
                    call_id, phrase, item_id=None,
                    reason=f"patient rejected the {parked.get('icd10')} "
                           f"read-back (similarity "
                           f"{parked.get('score') or 0:.2f}, not corroborated "
                           f"by the clinical graph)")
            except Exception as e:  # noqa: BLE001
                # Never fail a live call over a review annotation, but never let
                # it disappear unnoticed either.
                log.error("could not record rejected mention for %s: %s",
                          call_id, e)
            trace.append({"tool": "record_unclassified_mention", "ok": True,
                          "resultSummary": {"phrase": phrase[:60],
                                            "rejected": parked.get("icd10")}})
            notes.append("mention also flagged for human review")
            return {
                "agentUtterance": probe, "turnIndex": turn_index,
                "askedKind": "conditionDetail", "askedRef": "rejected-readback",
                "isComplete": False, "escalated": escalated,
                "stopQuestionnaire": stop, "language": language,
                "toolTrace": trace, "notes": notes,
            }
        return _advance(call_id, turn_index, language, ack, trace, notes,
                        escalated, stop)

    # ---- volunteered condition, screened before the answer is judged -------
    # Deliberately AHEAD of the "could not understand" branch, because the two
    # events are independent and used to be conflated.
    #
    # People volunteer a condition INSTEAD of answering the question in front of
    # them. Asked for a date of birth, a caller leads with what is actually
    # worrying them: "Haan mujhe sugar hai, das saal se". That genuinely is not a
    # date, so understood=False is right and the question does need re-asking -
    # but returning at that point threw the diabetes declaration away. An
    # unparsed answer and an unheard condition became the same event, and the
    # condition screening below could only ever see utterances that had already
    # parsed cleanly, which is the opposite of when people volunteer things.
    # Measured at 3 of 3 runs dropping the declaration.
    # A bare "yes" to one of the thirteen condition screens is NOT evidence about
    # which condition, so it must not reach the classifier. Measured: from the
    # single word "Yes" the model produced a conditionPhrase that classified to D25,
    # leiomyoma of the uterus, with follow-ups queued for it. The caller is asked
    # instead, further down.
    screen_is_condition = (
        (asked.get("meta") or {}).get("followUpGroup") == "condition_detail")
    # Health classification begins only after consent AND all identity checks.
    # A disclosure volunteered during DOB verification is held until that value
    # has matched the authoritative CRM DOB; no diagnosis may be associated with
    # an unverified caller.
    answers_before_turn = session.get("answers") or {}
    consent_granted = answers_before_turn.get("A020") is True
    if _ref(asked) == "A020":
        consent_value, consent_err = extract.coerce(reading.get("value"), "boolean")
        consent_granted = consent_err is None and consent_value is True

    expected_dob = session.get("dateOfBirth")
    identity_verified = (
        answers_before_turn.get("A010") is True
        and answers_before_turn.get("A020") is True
        and bool(expected_dob)
        and str(answers_before_turn.get("A030")) == str(expected_dob)
    )
    if reading.get("mentionsCondition") and not consent_granted:
        notes.append("health mention was not classified before explicit consent")
    elif reading.get("mentionsCondition") and not identity_verified:
        notes.append("health mention was not classified before identity verification")

    # THE ANSWER TO "WHICH MEDICATION?" IS NOT A DIAGNOSIS.
    #
    # Asked "What treatment or medication are you taking for it?", a caller said
    # "thyroxine". STT heard "Hydroxy.", that went to the condition classifier, and
    # the classifier - which returns its nearest neighbour for any input at all -
    # produced Hyperlipidaemia and read it back for confirmation. A drug name became
    # a disease on an insurance application.
    #
    # These follow-ups ask for a drug, a date, a test value or a frequency. None of
    # those can be a condition, so classification is switched off for them entirely
    # rather than relying on the classifier to be unsure - it never is.
    asks_for_non_condition = (
        asked["kind"] == "followup"
        and (asked.get("meta") or {}).get("questionId")
        in (_NON_CONDITION_FOLLOWUPS | _NON_CONDITION_DEFAULT_STEPS)
    )

    # A SEPARATE, WEAKER SIGNAL: the question cannot itself be ABOUT a condition,
    # but the caller may still volunteer one in their answer.
    #
    # This distinction is the whole lesson of this change. Suppressing
    # classification per question looked right and is wrong: at A400 "no tobacco,
    # but I do have high blood pressure" must still yield I10, and an answer to a
    # GUARDED follow-up - "in 2019, and I also have diabetes" during the cancer
    # protocol - must still defer diabetes. A condition can be volunteered in the
    # answer to any question at all, so nothing here may stop harvesting,
    # deferring or classifying.
    #
    # What it does control is PRESENTATION. When a phrase from one of these
    # questions fails to classify, that failure is not doubt about a diagnosis: it
    # is a drug name, a habit or a test that was never a diagnosis in the first
    # place. Marked here, it stays in the record for the underwriter and is kept
    # out of the customer's "we could not identify this" list.
    non_condition_source = (
        asks_for_non_condition
        or (asked["kind"] == "item" and _ref(asked) in _NON_CONDITION_ITEMS)
        or asked["kind"] in _NON_CONDITION_KINDS
    )

    # Scope is per mention. A mixed sentence such as "my father had diabetes,
    # but I have asthma" must retain asthma while excluding the relative's
    # diabetes. Legacy singular extractor output remains conservative.
    condition_phrases = _current_condition_phrases(reading, utterance)
    hypothetical = (bool(reading.get("mentionsCondition"))
                    and not condition_phrases
                    and is_hypothetical_mention(utterance))
    if hypothetical:
        notes.append("all condition mentions are risk, possibility, family, or "
                     "negated scope; left in the transcript and NOT classified")
    elif isinstance(reading.get("conditionMentions"), list):
        excluded = [m for m in reading["conditionMentions"]
                    if isinstance(m, dict) and m.get("scope") != "self_current"]
        if excluded:
            notes.append(f"excluded {len(excluded)} non-current condition mention(s) "
                         "while retaining the caller's own disclosures")

    # ON A CONDITION SCREEN, A PHRASE IS ONLY TRUSTED IF THE CALLER ACTUALLY SAID
    # IT. The previous gate was a word list of "bare yes" forms, and word lists
    # keep losing to reality: "हाँ।", "Ah, yes." and "Uh-huh." all slipped past
    # it, whereupon the model invented a condition from one word of assent.
    condition_screened = False
    # A transcript BOTH STT engines doubted must never mint a diagnosis.
    doubted = stt_confidence is not None and stt_confidence < 0.5
    if doubted and reading.get("mentionsCondition"):
        notes.append("condition mention ignored: both STT engines doubted this "
                     "transcript, and a doubted transcript must not mint a diagnosis")

    # AN EXPLICIT, NAMED DENIAL OF A DECLARED CONDITION IS A RETRACTION, not a
    # failed answer to the current question. From a live call: a deferred
    # breathlessness phrase was classified Uterine Fibroid, a confused caller
    # confirmed it, and their later "फाइब्रॉइड नहीं है मुझे" had nowhere to go -
    # it read as an unparseable answer to the fibroid follow-up and was re-asked
    # past, three times, while the record kept a condition the caller had just
    # denied. Checked BEFORE the could-not-understand path so the denial acts
    # instead of being apologised at.
    #
    # Deliberately strict: a negation alone never retracts (the stress suites -
    # and real callers - answer bare "no" to boolean follow-ups constantly), and
    # family-scoped sentences ("my father doesn't have cancer") are excluded.
    # The condition must be NAMED, cross-script tolerant, in the same utterance
    # as the negation.
    if not doubted and utterance:
        retracted = _detect_retraction(utterance, session)
        # "I DON'T HAVE IT" NEEDS NO NAME WHEN WE JUST NAMED IT. Asked what
        # treatment they take for Hyperlipidaemia, a live caller answered "नहीं
        # है मुझे" - I don't have it - and the agent moved on to "is it under
        # control?" for a condition that had just been denied. The pronoun is
        # unambiguous because the question supplied the noun.
        #
        # Restricted to NON-BOOLEAN follow-ups: on a yes/no question ("is it
        # controlled?") "नहीं है" is a legitimate answer meaning not controlled,
        # and must never be read as withdrawing the condition.
        if (not retracted and asked.get("kind") == "followup"
                and (asked.get("answerType") or "text") not in
                ("boolean", "yesno", "bool")
                and _DENIES_POSSESSION.search(utterance)):
            active = (asked.get("meta") or {}).get("icd10")
            row = next((d for d in (session.get("declaredConditions") or [])
                        if d.get("icd10") == active), None)
            if row:
                retracted = (active, row.get("label") or active)
                notes.append("caller denied having the condition the current "
                             "question is about; treating it as a retraction")
        if retracted:
            r_icd10, r_label = retracted
            st.retract_condition(call_id, r_icd10, utterance,
                                 reason="caller explicitly denied the condition "
                                        "during the call")
            trace.append({"tool": "retract_condition", "ok": True,
                          "resultSummary": {"icd10": r_icd10}})
            notes.append(f"UNDERWRITER ATTENTION: caller explicitly denied "
                         f"{r_icd10} ({r_label}) after it was declared; moved to "
                         "retractedConditions with their words, review the turn")
            ack_text = {
                "hi": f"माफ़ कीजिए, मैंने {r_label} आपके रिकॉर्ड से हटा दिया है।",
                "hinglish": f"Maaf kijiye, maine {r_label} aapke record se hata "
                            "diya hai.",
            }.get(language, f"My apologies - I have removed {r_label} from "
                            "your record.")
            return _advance(call_id, turn_index, language, ack_text, trace,
                            notes, escalated, stop)

    # ONE CONDITION AT A TIME. The first current disclosure may open a protocol;
    # every additional disclosure is deferred in spoken order so no obligation is
    # overwritten or interleaved with the active topic.
    protocol_active = bool(session.get("pendingFollowUps")
                           or session.get("awaitingConfirmation"))
    # DELIBERATELY NOT GUARDED. Deferring every phrase volunteered mid-protocol is
    # the behaviour that stops a second disclosure being lost, and a guarded
    # follow-up's answer can still carry one: tests/test_underwriting_stress.py
    # drives "in 2019, and I also have diabetes" as the answer to FU_DIAG_WHEN
    # during the cancer protocol and requires diabetes to be deferred. Filtering
    # here broke that. Noise is handled by marking, below, not by dropping.
    if (identity_verified and protocol_active and condition_phrases
            and not doubted and asked["kind"] == "followup"):
        for condition_phrase in condition_phrases:
            st.defer_mention(call_id, condition_phrase)
            notes.append(f"'{condition_phrase[:40]}' mentioned mid-protocol; "
                         "deferred until the current condition's questions are done")

    # Marked BEFORE any classification runs, and it survives the deferred queue,
    # so a phrase drained three turns later is still known to have come from a
    # question that could not have been asking about a diagnosis.
    if non_condition_source and condition_phrases:
        for condition_phrase in condition_phrases:
            try:
                st.mark_noise_phrase(call_id, condition_phrase)
            except Exception as e:  # noqa: BLE001
                log.error("could not mark noise phrase for %s: %s", call_id, e)

    if (identity_verified and condition_phrases and not asks_for_non_condition
            and not doubted and not (protocol_active and asked["kind"] == "followup")):
        for index, condition_phrase in enumerate(condition_phrases):
            current = st.get_session(call_id) or session
            active_now = bool(current.get("pendingFollowUps")
                              or current.get("awaitingConfirmation"))
            if index > 0 or active_now:
                # DEFERRING NEEDS THE SAME GROUNDING TEST AS DECLARING, because a
                # deferred mention is not discarded - it is picked up later and can
                # be declared on its own. This branch had no test at all, so only
                # the FIRST phrase in the list was ever checked against the
                # caller's words.
                #
                # A condition screen's prompt is a list of diseases, which is
                # exactly what makes it a hallucination source. Measured: a bare
                # "Yes" to A200 ("...cancer, tumour, cyst, polyp...") returned
                # conditionMentions ['tumour', 'polyp', 'cyst'] - none of them
                # spoken by the caller, all three lifted from the question. The
                # first was rejected here by grounding; the other two were
                # deferred unchecked, and when the deferred queue was drained
                # 'cyst' resolved to D25 Uterine Fibroid at 0.897, over the 0.85
                # floor, and was declared. The caller acquired a fibroid diagnosis
                # by saying "Yes" to a cancer question, and because the disclosure
                # path had "already handled" the turn, the which-condition probe
                # never opened either.
                #
                # Skipping loses nothing the caller said: an ungrounded phrase is
                # by definition absent from their words, and their actual words
                # are still classified by the direct path below, or asked for by
                # the probe. Intermittent because the extractor is a model and
                # only sometimes copies the question's vocabulary.
                if (screen_is_condition
                        and not _phrase_grounded(condition_phrase, utterance)):
                    notes.append(f"not deferring '{condition_phrase[:40]}': it is "
                                 "not in the caller's words, it is in the question")
                    continue
                st.defer_mention(call_id, condition_phrase)
                notes.append(f"additional condition '{condition_phrase[:40]}' "
                             "deferred behind the current disclosure")
                condition_screened = True
                continue
            # condition_screened means the condition IS CAPTURED, never merely that
            # classifying was attempted. See _condition_captured - setting it on the
            # attempt is what silently lost a lung disclosure on a live call.
            if not screen_is_condition or _phrase_grounded(condition_phrase, utterance):
                _outcome = _handle_condition(
                    call_id, condition_phrase, language, trace, notes,
                    min_score=_DIRECT_MIN_SCORE, direct=True)
                condition_screened = condition_screened or _condition_captured(
                    call_id, _outcome)
            elif not is_bare_affirmation(utterance):
                notes.append("model's condition phrase does not appear in the "
                             "caller's words; classifying their own words instead")
                _outcome = _handle_condition(
                    call_id, utterance, language, trace, notes,
                    min_score=_DIRECT_MIN_SCORE, direct=True)
                condition_screened = condition_screened or _condition_captured(
                    call_id, _outcome)

    # ---- could not understand ---------------------------------------------
    if reading.get("needsRepeat") or (
        not reading.get("understood") and not reading.get("isDontKnow")
    ):
        ref = _ref(asked)
        count = st.record_repeat(call_id, ref)

        # The repeat counter is incremented above whatever happens next, so the
        # give-up path still works and a caller who keeps volunteering conditions
        # cannot loop here forever.
        _screened = st.get_session(call_id) or {}
        if condition_screened and (_screened.get("awaitingConfirmation")
                                   or _screened.get("pendingConditionDetail")
                                   or _screened.get("pendingFollowUps")):
            # Screening parked a read-back, opened the naming probe, or OPENED A
            # PROTOCOL (pendingFollowUps) for a disclosure that classified
            # cleanly. _reask repeats the current question verbatim and never
            # consults next_question, so it would talk straight over any of the
            # three - and worse, lead with "I may have misheard" when the agent
            # heard perfectly and just recorded the condition. Measured: a caller
            # volunteering "I have diabetes" at the height step had E11 recorded
            # but was told "That doesn't seem right - I may have misheard. Your
            # height?", which reads as the machine ignoring them. Advance instead:
            # the item still has no recorded answer, so next_question brings it
            # back once the read-back, probe, or protocol follow-ups resolve.
            notes.append(f"{ref} still unanswered, resolving the volunteered "
                         f"condition first")
            return _advance(call_id, turn_index, language, ack, trace, notes,
                            escalated, stop)

        if count < MAX_REPEATS:
            # A caller who does not understand a protocol follow-up is not helped
            # by hearing it again. On the FIRST miss, explain the condition once
            # and then re-ask; later misses re-ask plainly until the bound. This
            # is the explain -> re-ask -> skip ladder a live schizophrenia call
            # needed, where every "haan" / "what was that?" only bought a repeat.
            if asked["kind"] == "followup" and count == 1:
                return _explain_then_reask(call_id, asked, language,
                                           turn_index, trace, notes)
            return _reask(call_id, asked, language, "", turn_index, trace,
                          f"attempt {count} of {MAX_REPEATS}", customer=customer)
        # Give up on this one rather than looping. Recorded as unanswered, and -
        # because WE gave up rather than the caller declining - flagged for a
        # person to finish. The closing then tells the caller someone will call to
        # complete the questions we could not.
        notes.append(f"{ref} unanswered after {MAX_REPEATS} attempts")
        st.clear_repeats(call_id, ref)
        st.add_human_followup(
            call_id, ref,
            (asked.get("meta") or {}).get("conditionLabel")
            or (asked.get("meta") or {}).get("label"),
            "caller could not answer after an explanation and re-ask")
        _record(call_id, asked, "not_captured", turn_index, utterance,
                stt_confidence, trace)
        return _advance(call_id, turn_index, language, ack, trace, notes,
                        escalated, stop)

    # ---- record the answer -------------------------------------------------
    # Repeat counter NOT cleared here: clearing on "understood" reset it before
    # coercion ran, so clear impossible values (5 Sep 3000, 1500, 2500) reset
    # their own counter each turn and the give-up bound was unreachable - the
    # date question looped forever (stress wave 2). Cleared at _record below.

    if reading.get("isDontKnow"):
        value: Any = "unknown"
        notes.append("customer did not know")
    else:
        meta = asked.get("meta") or {}
        if meta.get("verifyAgainst") == "dateOfBirth":
            # The raw utterance is the identity evidence. The model may only say
            # whether it understood; it may not launder a correction such as
            # "22 March 1987, I mean 23rd" into the first clean date.
            raw_date = extract.normalise_calendar_date(utterance)
            value, err = extract.coerce(raw_date, asked["answerType"], meta)
            if raw_date is None:
                err = "date reply was negated, corrected, or ambiguous"
        else:
            if asked["answerType"] == "email":
                # The closing item reads the address on file aloud and asks the
                # caller to confirm it, so "yes" has to resolve to that address.
                # Passed in rather than looked up inside coerce, which has no
                # session.
                meta = {**meta, "onFileEmail": session.get("customerEmail")}
            value, err = extract.coerce(reading.get("value"), asked["answerType"],
                                        meta)
            if not err and meta.get("formField") == "since":
                # A "since" answer is a point in time, and the caller's own words
                # are the evidence for it. A live call had the caller say "2019, I
                # told you already" and stored 2015. Done here rather than in
                # coerce because only this scope has the utterance.
                #
                # Word-form years first: another live caller answered "Twenty
                # fifteen." and the record kept the words, which cannot be sorted
                # or compared even though the agent had clearly understood it -
                # it said "Okay, 2015" in the same breath.
                value, words_note = extract.normalise_spoken_year(value)
                if words_note:
                    notes.append(words_note)
                grounded, year_note = extract.ground_spoken_year(value, utterance)
                if year_note:
                    notes.append(year_note if grounded != value
                                 else f"UNDERWRITER ATTENTION: {year_note}")
                value = grounded
        if err:
            # Coercion caught something implausible, e.g. a height of 400cm from a
            # mis-hearing. Ask again rather than write it to a medical record.
            count = st.record_repeat(call_id, _ref(asked))
            notes.append(f"rejected value: {err}")
            if count < MAX_REPEATS:
                return _reask(call_id, asked, language, "", turn_index, trace, err,
                              customer=customer)
            value = "not_captured"
            st.add_human_followup(
                call_id, _ref(asked),
                (asked.get("meta") or {}).get("conditionLabel")
                or (asked.get("meta") or {}).get("label"),
                f"value could not be captured after {MAX_REPEATS} attempts: {err}")

    termination_reason = None
    if asked["kind"] == "item":
        meta = asked.get("meta") or {}
        if (meta.get("onFalse") == "TERMINATE_POLITELY"
                and value is not True):
            termination_reason = ("identity_not_confirmed" if _ref(asked) == "A010"
                                  else "consent_not_granted")
            if value is not False:
                notes.append(f"{_ref(asked)} was not affirmatively verified after "
                             "the bounded attempts; failing closed")
        verify_key = meta.get("verifyAgainst")
        expected = session.get(verify_key) if verify_key else None
        if verify_key and meta.get("onMismatch") == "TERMINATE_POLITELY":
            if not expected:
                termination_reason = "identity_verification_unavailable"
                notes.append(f"{_ref(asked)} has no CRM verification value; failing closed")
            elif str(value) != str(expected):
                termination_reason = "identity_verification_mismatch"
                notes.append(f"{_ref(asked)} did not match the CRM verification value")

    # A condition volunteered in the same sentence as a correct DOB must not be
    # lost, but it also must not be classified before the DOB comparison above.
    # Queue it only after successful verification; the normal next-question
    # choke point drains it after A030 is persisted.
    if (_ref(asked) == "A030" and termination_reason is None
            and condition_phrases and not doubted):
        for condition_phrase in condition_phrases:
            st.defer_mention(call_id, condition_phrase)
        notes.append(f"{len(condition_phrases)} condition mention(s) during successful "
                     "DOB verification deferred until identity is checkpointed")

    # ATTEMPTS RESET HERE: understood AND validated, the only honest definition
    # of "the caller answered". Earlier (on understood alone) made the give-up
    # bound unreachable for clear-but-impossible values - three bad dates looped
    # forever. Later, at _record, leaked the counter on every path that returns
    # before recording (medicine verification, the which-condition probe, the
    # summary), so stale attempts followed a caller into their next answer and
    # could give up on them mid-sentence. Both caught by the stress waves.
    st.clear_repeats(call_id, _ref(asked))

    # ---- height and weight have to agree with each other -------------------
    # Each bound is individually correct and the pair can still be impossible. A
    # live call recorded 183cm with a weight of "Thirty", and both passed: A100
    # allows 100-230cm, A110 allows 25-300kg, because 25kg is a real weight for a
    # child applicant. Together they are a BMI of 9, which no living person has.
    #
    # Checked BEFORE the value is written, because the point is to not put it in an
    # underwriting record. Only the just-answered field is re-asked - the other one
    # was plausible on its own and there is no reason to make them repeat it.
    if asked["kind"] == "item" and _ref(asked) in (_HEIGHT_ITEM, _WEIGHT_ITEM):
        answers = dict((session.get("answers") or {}))
        answers[_ref(asked)] = value
        bmi_err = implausible_bmi(answers)
        if bmi_err:
            count = st.record_repeat(call_id, _ref(asked))
            notes.append(f"height/weight combination rejected: {bmi_err}")
            if count < MAX_REPEATS:
                return _reask(call_id, asked, language, "", turn_index, trace,
                              bmi_err, customer=customer)
            value = "not_captured"
            notes.append("could not obtain a plausible height/weight pair")
            st.add_human_followup(
                call_id, _ref(asked),
                (asked.get("meta") or {}).get("conditionLabel")
                or (asked.get("meta") or {}).get("label"),
                "height/weight could not be captured plausibly")

    # ---- does the medication fit the condition? -----------------------------
    # Judged BEFORE the answer is recorded, because an unexpected medicine gets one
    # gentle "just to be sure?" and the whole point of asking is that the answer
    # might change. Three outcomes:
    #
    #   consistent   -> recorded, and the match corroborates the disclosure
    #   unexpected, first time -> the answer is HELD (not recorded), the caller is
    #                   asked once, and the reply is handled at the top of the next
    #                   turn: yes records it flagged, anything else replaces it
    #   unexpected, already asked -> recorded and flagged. The caller is believed;
    #                   nobody defends their prescription to a machine twice.
    med_verdict = None
    if asked["kind"] == "followup" and isinstance(value, str):
        fu_meta = asked.get("meta") or {}
        med_verdict = medication_consistency(fu_meta.get("icd10"), value)
        if med_verdict and med_verdict[0] == "unexpected":
            ref = _ref(asked)
            if ref not in (session.get("medChecksAsked") or []):
                _, med = med_verdict
                st.set_med_check(call_id, {"ref": ref, "med": med,
                                           "icd10": fu_meta.get("icd10"),
                                           "value": value})
                probe_q = {
                    "hi": f"बस पक्का करने के लिए — आप {med} इसी के लिए लेती हैं?",
                    "hinglish": f"Bas confirm karne ke liye — aap {med} isi ke "
                                "liye leti hain?",
                }.get(language, f"Just so I note the medicine correctly — you "
                                f"take {med} for this?")
                notes.append(f"'{med}' is not a typical treatment for "
                             f"{fu_meta.get('icd10')}; verifying once before "
                             "recording")
                return {
                    "agentUtterance": probe_q, "turnIndex": turn_index,
                    "askedKind": "medVerify", "askedRef": ref,
                    "expectedAnswerType": None,
                    "isComplete": False, "escalated": escalated,
                    "stopQuestionnaire": stop, "language": language,
                    "toolTrace": trace, "notes": notes,
                }
    st.clear_repeats(call_id, _ref(asked))

    _record(call_id, asked, value, turn_index, utterance, stt_confidence, trace)

    if termination_reason:
        st.mark_status(call_id, st.STATUS_TERMINATED, note=termination_reason)
        closing = {
            "hi": ("धन्यवाद। सुरक्षा और गोपनीयता के लिए हम इस कॉल पर आगे नहीं बढ़ सकते। "
                   "हमारी टीम आपसे अलग से संपर्क करेगी।"),
            "hinglish": ("Dhanyavaad. Security aur privacy ke liye hum is call par aage "
                          "nahi badh sakte. Hamari team aapse alag se contact karegi."),
        }.get(language,
              "Thank you. For your security and privacy, we cannot continue this call. "
              "Our team will contact you separately.")
        notes.append(f"terminated before health questions: {termination_reason}")
        return {
            "agentUtterance": closing, "turnIndex": turn_index,
            "isComplete": False, "escalated": False,
            "stopQuestionnaire": True, "language": language,
            "toolTrace": trace, "notes": notes,
        }

    # An identity item re-verified on a resumed call (a wrong name/DOB returned
    # above): drain it from the queue. _advance re-reads the session, so once the
    # queue empties the questionnaire continues at whatever it still owes; until
    # then the next owed identity item is surfaced instead.
    if reconfirm_identity and _ref(asked) in (session.get("identityReconfirmQueue") or []):
        remaining = st.pop_identity_reconfirm(call_id, _ref(asked))
        session["identityReconfirmQueue"] = remaining
        notes.append(f"identity item {_ref(asked)} re-verified on resume; "
                     f"{len(remaining)} identity item(s) still to re-confirm")

    if med_verdict:
        state, med = med_verdict
        fu_meta = asked.get("meta") or {}
        trace.append({"tool": "medication_consistency", "ok": True,
                      "resultSummary": {"medication": med,
                                        "icd10": fu_meta.get("icd10"),
                                        "verdict": state}})
        if state == "consistent":
            notes.append(f"medication '{med}' is consistent with "
                         f"{fu_meta.get('icd10')} - corroborates the disclosure")
        else:
            notes.append(f"UNDERWRITER ATTENTION: medication '{med}' is not a "
                         f"typical treatment for {fu_meta.get('icd10')} and the "
                         "caller stood by it when asked; review this turn")

    # ---- "yes" to a condition screen means ASK WHICH ------------------------
    # A caller who answers yes to "have you ever been diagnosed with cancer, a
    # tumour, polyp or cyst?" has disclosed something material and named nothing.
    # Without this the answer was recorded true and the call went straight to the
    # next screen, so the Neptune protocol - six follow-ups for C34, staging,
    # remission, recurrence - never opened. All 13 screens A200-A320 carry
    # followUpGroup="condition_detail" in the schema for exactly this, and nothing
    # read that field.
    #
    # Only when they did not already name it: someone who says "yes, breast cancer
    # in 2019" has been handled by the screening above, and asking "which one?"
    # after that would be the agent not listening.
    if value is True and screen_is_condition and not condition_screened:
        # Ask for the NAME explicitly. The earlier "यह क्या था?" ("what was it?")
        # invited another "haan" - a live caller answered "हाँ" three times to it
        # before the probe gave up and moved on. "कौन सी बीमारी" ("which
        # condition") makes it clear a name is wanted, not another yes. Still two
        # asks (name + when), which the answer parser downstream expects.
        probe = {
            "hi": "आपने हाँ कहा। कृपया बताइए यह कौन सी बीमारी थी, और पहली बार कब पता चली?",
            "hinglish": "Aapne haan kaha. Kripya bataiye yeh kaun si bimari thi, aur pehli baar kab pata chali?",
        }.get(language,
              "You said yes. Which condition was it, and when were you first "
              "diagnosed?")
        st.set_pending_condition_detail(call_id, _ref(asked), probe)
        notes.append(f"{_ref(asked)} answered yes without naming the condition; "
                     "asking which before opening the protocol")
        return {
            "agentUtterance": (ack + " " + probe).strip() if ack else probe,
            "turnIndex": turn_index,
            "askedKind": "conditionDetail",
            "askedRef": _ref(asked),
            "isComplete": False, "escalated": escalated, "stopQuestionnaire": stop,
            "language": language, "toolTrace": trace, "notes": notes,
        }

    # Condition screening already ran above, before the answer was judged, so
    # that a condition volunteered in place of a valid answer is not lost. It is
    # deliberately not repeated here: classify_condition is an embedding call plus
    # a graph traversal, and running it twice per turn would double that cost for
    # no new information.

    meds = reading.get("medications") or []
    if len(meds) >= 2:
        ix = tools.check_medication_interactions(meds)
        trace.append({"tool": "check_medication_interactions", "ok": True,
                      "resultSummary": {"interactionCount": ix.get("interactionCount")}})
        if ix.get("interactionCount"):
            notes.append(f"medication interaction flagged for the underwriter: "
                         f"{ix['interactions'][0]['severity']}")

    return _advance(call_id, turn_index, language, ack, trace, notes,
                    escalated, stop)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def _consistent_ack(ack: str | None, language: str,
                    customer_name: str | None = None) -> str:
    """
    Keep the acknowledgement in the same script as the question it precedes.

    Canonical question text is fixed by the schema, so the safe move when the
    model's acknowledgement is in the wrong script is to drop it. A missing
    "Theek hai." costs nothing; a sentence that switches script mid-way sounds
    broken.
    """
    a = (ack or "").strip()
    if not a:
        return ""
    if language in ("en", "hinglish") and _DEVANAGARI.search(a):
        return ""
    if language == "hi" and not _DEVANAGARI.search(a) and any(c.isalpha() for c in a):
        return ""

    # THE NAME COMES OUT. The acknowledgement is the one piece of model-written
    # text that reaches the caller, and the model puts their name in nearly every
    # one: measured on a live call, eighteen of twenty agent turns opened "ठीक है,
    # महिमा जी।" before the question. Said once that is warm; said every turn it is
    # the clearest tell that nobody is really listening. The greeting and the
    # closing summary still use it - those are template text, and they are where a
    # person would.
    # TWO RULES, because matching the name alone does not work. Speaking Hindi the
    # model transliterates it - the record says "Asha Rao" and the ack
    # says "ठीक है, आशा जी।" - so a substring test against customerName sees
    # nothing. The second rule is script-agnostic: in a short acknowledgement a
    # comma-led "<word> जी/ji" is a vocative, whatever alphabet it is written in.
    # The comma is what makes it safe, since "जी हाँ" opens with the honorific and
    # is a real answer rather than an address.
    original = a
    first = (customer_name or "").strip().split()[0] if customer_name else ""
    if first:
        a = re.sub(rf"[,\u2026]?\s*{re.escape(first)}\s*(जी|ji)?\.?", " ", a,
                   flags=re.IGNORECASE)
    a = re.sub(r"[,\u2026]\s*\S+\s*(जी|ji)\s*[.\u0964!?]?", " ", a)

    if a != original:
        a = re.sub(r"\s{2,}", " ", a).strip()
        a = re.sub(r"^[,.\u0964\s]+", "", a)
        a = re.sub(r"\s+([.\u0964!?])", r"\1", a).strip()
        # Nothing but punctuation left means the whole ack was the address.
        if not (_DEVANAGARI.search(a) or any(c.isalnum() for c in a)):
            return ""
        # The sentence ender goes back on if stripping the vocative took it.
        if not re.search(r"[.\u0964!?]$", a):
            a += "।" if language == "hi" else "."
    return a


def _pending_opening_question(session: dict, items: list[dict], customer: dict,
                              language: str) -> dict | None:
    """Return the preserved overlay that will consume the caller's next reply.

    This ordering mirrors ``handle_turn``. A resumed call must speak the overlay
    before accepting an answer; otherwise the caller hears a form question while
    their response is consumed as an explanation, refusal, medicine check, or
    condition-detail answer.
    """
    # Identity re-verification precedes every inherited overlay on a resumed
    # call: the opening turn must ask the DOB check before anything the prior leg
    # left owed. Cleared the moment A030 re-matches, after which these overlays
    # take over as before.
    reconfirm = _identity_reconfirm_descriptor(session, items, language)
    if reconfirm:
        return reconfirm

    explanation = session.get("explanationPending") or {}
    if explanation:
        if explanation.get("awaitingSubject"):
            text = {
                "hi": "ज़रूर। सूची में किस बीमारी का नाम समझना चाहेंगी? केवल उसका नाम बता दीजिए।",
                "hinglish": "Zaroor. List mein kis bimari ka naam samajhna chahengi? Sirf uska naam bata dijiye.",
            }.get(language,
                  "Of course. Which condition in the list would you like me to explain? Just tell me its name.")
            return {"kind": "explanationClarify", "text": text,
                    "answerType": "text", "meta": explanation,
                    "ref": "explanationClarify"}
        if explanation.get("awaitingCandidateConfirm"):
            # Without this branch a resumed call falls through to the
            # understanding check below and speaks an empty explanation, because
            # a pending candidate has no explanation text yet.
            return {
                "kind": "explanationCandidateConfirm",
                "text": _candidate_confirm_text(
                    explanation.get("candidateLabel") or "", language),
                "answerType": "boolean", "meta": explanation,
                "ref": f"explainCandidate:{explanation.get('icd10')}",
            }
        understanding = {
            "hi": "क्या यह समझ आया? फिर मैं वही सवाल दोबारा पूछूँगी।",
            "hinglish": "Kya yeh samajh aaya? Phir main wahi sawaal dobara poochhungi.",
        }.get(language, "Does that make sense? I will then ask the question again.")
        return {
            "kind": "explanationCheck",
            "text": f"{explanation.get('explanation') or ''} {understanding}".strip(),
            "answerType": "boolean", "meta": explanation,
            "ref": f"explain:{explanation.get('icd10')}",
        }

    if session.get("refusalConfirmPending"):
        text = {
            "hi": "ठीक है। क्या आप अभी कॉल समाप्त करना चाहती हैं?",
            "hinglish": "Theek hai. Kya aap abhi call khatam karna chahti hain?",
        }.get(language, "Of course. Just to check — would you like to stop here?")
        return {"kind": "refusalConfirm", "text": text,
                "answerType": "boolean", "meta": {}, "ref": "refusalConfirm"}

    # AHEAD OF THE SUMMARY, DELIBERATELY. An open "what is this condition
    # called?" is an unresolved obligation, and the summary's job is to read the
    # record back as complete. Behind the summary this probe was queued and then
    # never asked: the caller heard "you haven't declared any conditions" while
    # the condition they had just volunteered sat waiting to be named.
    pending = session.get("pendingConditionDetail") or {}
    if pending:
        original = ""
        ref = pending.get("ref")
        if ref:
            item = next((row for row in items if row.get("itemId") == ref), None)
            if item:
                original = _prompt_for(item, language, customer)
        text = (f"{original} {pending.get('phrase') or ''}".strip()
                if original else (pending.get("phrase") or ""))
        return {"kind": "conditionDetail", "text": text,
                "answerType": "text", "meta": pending, "ref": ref}

    if session.get("summaryAskPending"):
        return {"kind": "summaryConfirm", "text": "", "answerType": "text",
                "meta": {}, "ref": "summaryConfirm"}

    medicine = session.get("medCheckPending") or {}
    if medicine:
        med = medicine.get("med") or "that medicine"
        text = {
            "hi": f"बस पक्का करने के लिए — आप {med} इसी के लिए लेती हैं?",
            "hinglish": f"Bas confirm karne ke liye — aap {med} isi ke liye leti hain?",
        }.get(language, f"Just so I note the medicine correctly — you take {med} for this?")
        return {"kind": "medVerify", "text": text, "answerType": None,
                "meta": medicine, "ref": medicine.get("ref") or "medVerify"}

    if session.get("distressCheckPending"):
        text = {
            "hi": "मैं आपकी बात ठीक से समझना चाहती हूँ। क्या आप मुझे इसके बारे में थोड़ा और बता सकती हैं?",
            "hinglish": "Main aapki baat theek se samajhna chahti hoon. Kya aap mujhe iske baare mein thoda aur bata sakti hain?",
        }.get(language,
              "I want to make sure I understood you properly. Could you tell me a little more about that?")
        return {"kind": "distressCheck", "text": text, "answerType": "text",
                "meta": {}, "ref": "distressCheck"}

    return None


def _first_question(call_id: str, session: dict, items: list[dict], customer: dict,
                    language: str, turn_index: int, trace: list,
                    notes: list) -> dict:
    """Speak the first thing the questionnaire actually wants to ask.

    Reached from two places - the empty opening turn, and immediately after the
    customer picks a language - which is why it is a function. Both need the resume
    acknowledgement and neither may run the extractor, because in both cases the
    customer has not yet answered a question.
    """
    opener = ""
    resumed = int(session.get("resumeCount") or 0) > 0
    reconfirming = bool(session.get("identityReconfirmQueue"))

    # TWO AUDIENCES, ONE CONDITION - WHICH WAS THE BUG. The spoken opener and the
    # audit note used to share this branch, so a resumed call whose reconfirm queue
    # was still full recorded NOTHING about being a resume. That is the normal
    # opening turn of every resumed call, so the single most consequential fact
    # about it - that it inherited another call's answers - was absent from the
    # turn record an underwriter reads.
    #
    # The caller and the record want different things. Suppressing the SPOKEN
    # opener is right: the identity re-confirmation prompt carries its own "we were
    # cut off" lead (see _identity_reconfirm_prompt_text) and saying it twice is
    # clumsy. Suppressing the NOTE is not - nobody hears a note.
    if resumed:
        notes.append(
            f"resumed call, leg {int(session.get('resumeCount') or 0) + 1}, "
            f"inheriting {len(session.get('answers') or {})} answer(s) and "
            f"{len(session.get('pendingFollowUps') or [])} owed follow-up(s)"
            + ("; re-verifying identity before continuing" if reconfirming
               else "; acknowledged the earlier leg to the caller"))
    if resumed and not reconfirming:
        opener = {
            "hi": "हमारी पिछली बात अधूरी रह गई थी, चलिए वहीं से आगे बढ़ें। ",
            "hinglish": "Humari pichhli baat adhoori reh gayi thi, wahin se aage badhte hain. ",
        }.get(language, "We were cut off earlier, so let me carry on from where we stopped. ")

    asked = _pending_opening_question(session, items, customer, language)
    if asked and asked["kind"] == "summaryConfirm":
        result = _complete(call_id, session, turn_index, trace, notes=notes)
        result["agentUtterance"] = (opener + result.get("agentUtterance", "")).strip()
        return result
    if asked is None:
        asked = next_question(session, items, language)
    if asked is None:
        result = _complete(call_id, session, turn_index, trace, notes=notes)
        result["agentUtterance"] = (opener + result.get("agentUtterance", "")).strip()
        return result
    if asked["kind"] == "item":
        asked["text"] = _prompt_for(asked["meta"], language, customer)

    return {
        "agentUtterance": (opener + (asked["text"] or "")).strip(),
        "turnIndex": turn_index,
        "askedKind": asked["kind"],
        "askedRef": asked.get("ref") or _ref(asked),
        # The shape of answer this question expects, reported for the surface and
        # for callers that record it. STT is Whisper-only, so this is metadata and
        # no longer drives a second opinion.
        "expectedAnswerType": asked.get("answerType"),
        "isComplete": False,
        "escalated": False,
        "stopQuestionnaire": False,
        "language": language,
        "toolTrace": trace,
        "notes": notes,
    }


def _ref(asked: dict) -> str:
    m = asked.get("meta") or {}
    return m.get("itemId") or m.get("questionId") or f"confirm:{m.get('icd10')}"


def _record(call_id: str, asked: dict, value: Any, turn_index: int,
            utterance: str, stt_confidence: float | None, trace: list) -> None:
    """Write the answer. Form items go to the schema answers, follow-ups to the queue."""
    # Retire the acknowledgement grace counter with the question it belongs to.
    # It is bookkeeping for THIS question's "okay"/"theek hai" replies; once an
    # answer (including not_captured) is written, a stale entry would only
    # confuse the next reader of repeatCounts. Behaviour was already bounded
    # without this - the item counter carries the hard MAX_REPEATS limit.
    st.clear_repeats(call_id, f"ack:{_ref(asked)}")
    if asked["kind"] == "followup":
        st.complete_followup(call_id, value)
        st.annotate_turn(call_id, turn_index, item_id=_ref(asked),
                         extracted=value, valid=True, text=utterance)
        trace.append({"tool": "complete_followup", "ok": True,
                      "resultSummary": {"questionId": _ref(asked)}})
    else:
        tools.save_answer(call_id, asked["meta"]["itemId"], value, turn_index,
                          patient_phrase=utterance, stt_confidence=stt_confidence)
        trace.append({"tool": "save_answer", "ok": True,
                      "resultSummary": {"itemId": asked["meta"]["itemId"]}})


def _condition_captured(call_id: str, outcome: dict | None) -> bool:
    """Did classifying actually CAPTURE the condition, or open a question about it?

    NOT "was classifying attempted". That distinction is the whole point, and
    getting it wrong lost a disclosure on a live call.

    THE CALL THIS COMES FROM. Asked "अस्थमा, टीबी, सीओपीडी, ब्रोंकाइटिस, या फेफड़ों
    की कोई अन्य बीमारी?" the caller said yes and named her condition, and the STT
    produced "हाँ मुझे आज तो मैं". That is not a condition name, but it is not a bare
    affirmation either - "आज", "तो" and "मैं" are not in _AFFIRMATION_ONLY - so the
    turn took the "classify their own words" branch, the classifier could not place
    the phrase, and condition_screened was set to True regardless. That flag is what
    suppresses the which-condition probe, so A230 was recorded as a bare true, the
    call advanced to the thyroid screen, and the lung disclosure was gone. Three
    turns later she was still trying to say "Asthma" and it landed on the THYROID
    screen's probe, which is how J45 came to be attached to an endocrine question.

    A yes to a condition screen must leave the call in one of exactly two states:
    the condition is on the record, or the caller is being asked which it was.
    Advancing with neither is the defect, and this function is what tells them
    apart.

    WHY THE SESSION IS RE-READ RATHER THAN TRUSTING THE RETURN VALUE. None is
    ambiguous: _handle_condition returns None both when nothing could be done AND
    when it opened its own "what is it called?" naming probe for a weak direct
    disclosure. Those need opposite answers here - the second has already asked the
    caller, so asking again would be the agent not listening. pendingConditionDetail
    distinguishes them, and it cannot be stale: a probe pending at the start of a
    turn is consumed by _state_condition_detail_probe in the pre-LLM dispatch, so
    anything found here was set by this turn.
    """
    if outcome:
        return True
    session = st.get_session(call_id) or {}
    return bool(session.get("pendingConditionDetail")
                or session.get("awaitingConfirmation"))


def _handle_condition(call_id: str, phrase: str, language: str,
                      trace: list, notes: list,
                      min_score: float = 0.0,
                      direct: bool = False) -> dict | None:
    """
    Classify a declared condition and either queue its protocol follow-ups or
    park it for confirmation.

    Red flags and ambiguous matches are never accepted silently - they go into
    awaitingConfirmation so the next turn reads the interpretation back.

    direct=True marks a FIRST disclosure the extractor judged to be the caller's
    own current condition. Two things change: graph corroboration lets the match
    through whatever its similarity score, and a match that fails the floor is
    turned into a question ("what is it called?") instead of being recorded
    silently - the caller is the only one who can settle it. See _DIRECT_MIN_SCORE.
    """
    c = tools.classify_condition(phrase)
    trace.append({"tool": "classify_condition", "ok": True,
                  "resultSummary": {"icd10": c.get("icd10"),
                                    "score": c.get("score"),
                                    "needsConfirmation": c.get("needsConfirmation")}})
    # min_score exists for text that reaches here WITHOUT the LLM having judged
    # that it mentions a condition - the which-condition probe passes the raw
    # utterance. An embedding classifier returns its nearest neighbour for ANY
    # string, and cross-lingual garbage lands in the 0.75-0.80 band: a live
    # caller's "Here they are." (STT noise) came back as Epilepsy, was read
    # back, and a confused "हाँ" put an epilepsy protocol on the call. Real
    # names score higher ("breast cancer" 0.82+) or hit the graph's synonyms at
    # 1.0, so the floor rejects noise without touching genuine disclosures.
    #
    # For a direct disclosure the graph's agreement is the deciding evidence, not
    # the similarity number: a corroborated match passes at any score, and an
    # uncorroborated one must clear the floor. See _DIRECT_MIN_SCORE for the
    # "Cyst." -> I25 call this comes from.
    corroborated = bool(c.get("corroborated")) and not c.get("ambiguous")
    if (min_score and c.get("icd10")
            and c.get("source") != "neptune_exact_synonym"
            and not (direct and corroborated)
            and (c.get("score") or 0) < min_score):
        notes.append(f"classifier's best match {c['icd10']} at "
                     f"{c.get('score'):.2f} is below the {min_score} floor for "
                     + ("an uncorroborated direct disclosure" if direct
                        else "unvetted text") + "; not offering it")

        # A DIRECT DISCLOSURE IS NOT DISCARDED, IT IS ASKED ABOUT. The extractor
        # judged that the caller named a condition of their own; only the mapping
        # is untrustworthy. Reading back the weak guess is what put a cardiac
        # protocol on a live call, and dropping it silently would lose a real
        # disclosure. Asking "what is it called?" does neither, and the answer
        # re-classifies through the bounded probe path (three attempts, then
        # flagged for review).
        if direct:
            probe = {
                "hi": "माफ़ कीजिए, बीमारी का नाम मैं ठीक से नहीं समझ पाई। "
                      "कृपया बीमारी का नाम दोबारा बताइए?",
                "hinglish": "Maaf kijiye, bimari ka naam main theek se nahi samajh "
                            "payi. Kripya bimari ka naam dobara bataiye?",
            }.get(language, "Sorry - I did not catch the name of the condition. "
                            "Could you tell me what it is called?")
            try:
                st.set_pending_condition_detail(call_id, "weak-disclosure", probe)
                notes.append("asking the caller to name the condition rather than "
                             "reading back a guess the graph does not support")
            except Exception as e:  # noqa: BLE001
                log.error("could not open the naming probe for %s: %s", call_id, e)
        # NOT offered, but never silently dropped either: a floor-rejected
        # phrase is still a health mention someone spoke on a medical intake.
        # Durably recorded so a human reviews it - same principle as an
        # unclassifiable phrase, one confidence band higher.
        try:
            st.record_unclassified_mention(
                call_id, phrase, item_id=None,
                reason=f"best match {c['icd10']} at {c.get('score'):.2f} was "
                       f"below the {min_score} floor; needs human review")
        except Exception as e:  # noqa: BLE001
            log.error("could not record floor-rejected mention for %s: %s",
                      call_id, e)
        return None
    if not c.get("icd10"):
        # The patient described something the graph does not cover. Record it
        # DURABLY, not just as a per-turn note: without this the intake finishes
        # with zero declared conditions and the underwriting record is stamped
        # "clear", which is precisely backwards - the mention we could not
        # interpret is the one that most needs a human to look at it.
        notes.append(f"could not classify {phrase!r} - flagged for human review")
        try:
            st.record_unclassified_mention(
                call_id, phrase, item_id=None,
                reason=f"no confident ICD-10 match (best score "
                       f"{c.get('score')})" if c.get("score") else
                       "no ICD-10 match")
        except Exception as e:  # noqa: BLE001
            # Never fail a live call over an audit write, but do not let it pass
            # unnoticed either.
            log.error("could not record unclassified mention for %s: %s", call_id, e)
        trace.append({"tool": "record_unclassified_mention", "ok": True,
                      "resultSummary": {"phrase": phrase[:60],
                                        "bestScore": c.get("score")}})
        return None

    # Already declared? Then stop here.
    #
    # Every utterance is screened for conditions, including the answers to a
    # condition's own follow-ups. Without this guard, "Metformin leti hoon" while
    # working through the diabetes protocol re-classifies as diabetes, re-queues
    # the same six questions, and can re-park the condition for confirmation -
    # a loop the patient experiences as being asked the same things twice.
    session = st.get_session(call_id) or {}
    declared_rows = session.get("declaredConditions") or []
    already = {d.get("icd10") for d in declared_rows}
    if c["icd10"] in already:
        # RESOLUTION, NOT FAILURE. A caller naming a condition that is already
        # on the record has answered correctly - they are confirming, or
        # re-stating after a wrong guess. Returning a bare None made the
        # which-condition probe read this as "I didn't catch the name": a live
        # caller said "अस्थमा" (exact graph synonym, perfectly transcribed)
        # three times and was apologised at three times while asthma sat in
        # declaredConditions. The outcome is reported so callers can tell
        # "already known" apart from "not understood".
        notes.append(f"{c['icd10']} already declared, not re-queued")
        return {"outcome": "already_declared", "icd10": c["icd10"],
                "label": next((d.get("label") for d in declared_rows
                               if d.get("icd10") == c["icd10"]),
                              c.get("label_en") or c["icd10"])}

    # A PHRASE THAT COULD BE THE CONDITION WE ARE ALREADY DISCUSSING IS AN
    # ELABORATION, NOT A NEW DISEASE.
    #
    # From a live call: during the asthma protocol the caller explained when
    # they use the inhaler - "जब सांस फूलती तभी यूज़ करती हूँ". That phrase was
    # deferred and later classified Heart Failure at 0.884, with ASTHMA itself
    # scoring an identical 0.884 in the same candidate list. On a 0.000 margin
    # the agent read back a second, wrong cardiac condition; the caller
    # rejected it, which then poisoned asthma as a "rejected" code and left
    # them unable to name their real condition at all.
    #
    # Only applies to unvetted text (min_score set): deferred mentions, the
    # open probe, summary corrections. A direct, LLM-vetted disclosure is still
    # taken at face value.
    # NARROW ON PURPOSE: only a declared code that is effectively TIED with the
    # best match counts. The live failure was a 0.0003 margin (Heart Failure
    # 0.8843 vs the active Asthma 0.8840) - a coin toss dressed up as a
    # classification. A declared code merely APPEARING further down the
    # candidate list is not ambiguity, and suppressing on that basis swallowed
    # genuine second disclosures ("breast cancer" while a gynaecological
    # condition was on file) in two suites.
    if min_score and already:
        primary_score = float(c.get("score") or 0)
        overlap = sorted(
            code for code, score in (
                (a.get("icd10"), float(a.get("score") or 0))
                for a in (c.get("candidates") or [])[:3])
            if code in already
            and primary_score - score <= _ELABORATION_TIE_MARGIN)
        if overlap:
            notes.append(f"{phrase[:40]!r} scores level with the already-declared "
                         f"{overlap[0]} (best {c['icd10']} at "
                         f"{primary_score:.4f}); treated as detail of that "
                         "condition rather than a second disease")
            try:
                st.record_unclassified_mention(
                    call_id, phrase, item_id=None,
                    reason=f"ambiguous with already-declared {overlap[0]}; "
                           "recorded as elaboration rather than minting a "
                           "second condition",
                    kind="elaboration")
            except Exception as e:  # noqa: BLE001
                log.error("could not record elaboration for %s: %s", call_id, e)
            return {"outcome": "elaboration", "icd10": overlap[0]}

    # A code the caller has already said no to is not offered again. The
    # classifier is a pure function of the phrase, so "cancer" re-stated after a
    # rejected breast-cancer read-back returns breast cancer again forever; the
    # rejected set is what turns that loop into progress through the candidates.
    rejected = set(session.get("rejectedCodes") or [])
    if c["icd10"] in rejected:
        alt = next((a for a in (c.get("candidates") or [])
                    if a.get("icd10") and a["icd10"] not in rejected
                    and a["icd10"] not in already), None)
        if alt:
            notes.append(f"{c['icd10']} was already rejected on this call; "
                         f"taking the next candidate {alt['icd10']}")
            # corroborated MUST be cleared along with the code. It described the
            # original top hit, and carrying it onto a different code would let a
            # substituted candidate inherit an endorsement the graph never gave
            # it - the record would then claim two methods agreed on a code only
            # one of them ever saw.
            c = {**c, "icd10": alt["icd10"],
                 "label_en": alt.get("label_en") or alt["icd10"],
                 "score": alt.get("score"), "needsConfirmation": True,
                 "corroborated": False}
        else:
            notes.append(f"{c['icd10']} was already rejected and no candidate "
                         "remains; recording for human review")
            st.record_unclassified_mention(
                call_id, phrase, item_id=None,
                reason=f"caller rejected {c['icd10']} and the classifier has no "
                       "other graph-corroborated candidate")
            return None

    if c.get("needsConfirmation"):
        # One parked classification at a time. Overwriting a pending read-back
        # would silently discard the earlier one, which for a red flag means it
        # never gets confirmed at all.
        if session.get("awaitingConfirmation"):
            notes.append(f"{c['icd10']} not parked, a confirmation is already pending")
            return None
        label = c.get("label_en") or c["icd10"]
        # ASK IN THE CALLER'S LANGUAGE, RECORD IN ENGLISH. `label` stays English
        # because it goes into the record, where the underwriter needs the
        # canonical name; `spoken` is what is said out loud.
        #
        # This was one read-back in English and the next in Hindi, in the same
        # exchange, because the next-candidate path already preferred label_hi
        # and this one never did: "क्या यह Chronic Obstructive Pulmonary Disease
        # है?" followed by "तो क्या यह गुर्दे की पुरानी बीमारी है?". A Hindi
        # speaker cannot verify an English clinical name, and verification is the
        # entire purpose of reading it back.
        spoken = label
        if language in ("hi", "hinglish"):
            spoken = c.get("label_hi") or label
        # QUOTE THE CALLER'S OWN WORDS in the read-back. A deferred phrase can
        # be classified a full minute after it was spoken; on a live call the
        # agent asked "क्या यह Uterine Fibroid है?" with no hint that it was
        # interpreting "सास फूलती थी मेरी" from four questions earlier, and the
        # disoriented caller confirmed a condition they do not have. The quote
        # is what lets them recognise - and reject - a misclassification.
        #
        # Trailing sentence enders come off the quote: the caller's phrase
        # already ends in one, and the template adds its own, so the live call
        # said 'आपने कहा था "पांच साल पहले ब्रॉन्काइटिस।"।' with two dandas.
        quoted = (phrase or "").strip()[:60].rstrip("।?.! \u0964\u0965")
        question = {
            "hi": (f"आपने कहा था \u201c{quoted}\u201d। रिकॉर्ड सही रखने के लिए "
                   f"पूछ रही हूँ, क्या यह {spoken} है?"),
            "hinglish": (f"Aapne kaha tha \u201c{quoted}\u201d. Record sahi "
                         f"rakhne ke liye poochh rahi hoon, kya yeh {spoken} hai?"),
        }.get(language, (f"You mentioned \u201c{quoted}\u201d. Just so I record "
                         f"it correctly, is that {spoken}?"))
        st.set_awaiting_confirmation(call_id, {
            "icd10": c["icd10"], "label": label, "question": question,
            "phrase": phrase, "score": c.get("score"),
            "redFlag": c.get("redFlag"),
            # Carried so that a yes to this read-back records HOW the code was
            # reached, not merely that it was accepted.
            "corroborated": bool(c.get("corroborated")),
            # The classifier's remaining candidates, carried so a rejected
            # read-back can offer the next graph-corroborated option instead of
            # abandoning the disclosure. Capped: by the third distinct code the
            # open "in your own words" probe is a better question than a fourth
            # guess.
            # SAME BODY SYSTEM ONLY. A rejection means "not that code", not "try
            # any code" - and an embedding index will happily rank a renal and a
            # hepatic condition behind a respiratory one, because it ranks by
            # similarity and knows no anatomy.
            #
            # Measured, on a live call: the caller said "पांच साल पहले
            # ब्रॉन्काइटिस". Offered COPD, she said no. Offered CHRONIC KIDNEY
            # DISEASE, she said no. Offered CHRONIC VIRAL HEPATITIS, and by then
            # she said yes - so B18 went onto her record from the word bronchitis,
            # with four hepatitis follow-ups filed behind it. Three guesses across
            # three unrelated organs is not a clarifying question, it is a
            # guessing game the caller eventually loses.
            #
            # Running out of same-category candidates is the better outcome: it
            # falls through to asking in their own words, which also preserves the
            # phrase for a human instead of converting it into a wrong code.
            "alternatives": [
                {"icd10": a.get("icd10"), "label_en": a.get("label_en"),
                 "label_hi": a.get("label_hi"), "score": a.get("score"),
                 "category": a.get("category")}
                for a in (c.get("candidates") or [])[:4]
                if a.get("icd10") and a.get("icd10") != c["icd10"]
                and (not c.get("category") or not a.get("category")
                     or a.get("category") == c.get("category"))
            ][:2],
        })
        notes.append(f"parked {c['icd10']} for confirmation "
                     f"(redFlag={c.get('redFlag')})")
        return None

    # Reaching here means needsConfirmation was false, which the classifier only
    # allows when the match was corroborated - an uncorroborated top hit always
    # forces a read-back. So this path is recorded as corroborated without a
    # patient confirmation, and the assertion is the classifier's own, not an
    # assumption made here.
    return _queue_protocol(call_id, c["icd10"], c.get("label_en"), language,
                           trace, notes,
                           patient_phrase=phrase, confidence=c.get("score"),
                           corroborated=bool(c.get("corroborated")),
                           confirmed_by_patient=False)


# The minimum an underwriter needs about any chronic condition, used when a code
# resolves but the graph defines no protocol for it. Deliberately the same three
# facts the curated protocols all start with: when, what treatment, controlled or
# not. Trilingual because the caller may be in any of the three modes and a
# fallback that only speaks English would be a worse failure than the gap it fills.
_DEFAULT_PROTOCOL = {
    "en": [
        ("diagnosed_when", "When were you first diagnosed with that?", "text"),
        ("current_treatment",
         "What treatment or medication are you taking for it at the moment?", "text"),
        ("is_controlled", "Is it currently under control?", "boolean"),
    ],
    "hi": [
        ("diagnosed_when", "यह पहली बार कब पता चला था?", "text"),
        ("current_treatment", "इसके लिए अभी आप कौन सी दवा या इलाज ले रहे हैं?", "text"),
        ("is_controlled", "क्या यह अभी नियंत्रण में है?", "boolean"),
    ],
    "hinglish": [
        ("diagnosed_when", "Yeh pehli baar kab pata chala tha?", "text"),
        ("current_treatment", "Iske liye abhi aap kaun si dawa ya treatment le rahe hain?", "text"),
        ("is_controlled", "Kya yeh abhi control mein hai?", "boolean"),
    ],
}


def _default_protocol(language: str) -> list[dict]:
    """Follow-ups for a condition the graph does not govern."""
    rows = _DEFAULT_PROTOCOL.get(language) or _DEFAULT_PROTOCOL["en"]
    return [
        {"questionId": qid, "prompt": prompt, "answerType": atype,
         "mandatory": True, "sensitivity": "normal",
         "clinicalNote": "default protocol; this condition has no curated "
                         "follow-ups in the knowledge graph"}
        for qid, prompt, atype in rows
    ]


def _queue_protocol(call_id: str, icd10: str, label: str | None, language: str,
                    trace: list, notes: list,
                    patient_phrase: str | None = None,
                    confidence: float | None = None,
                    corroborated: bool | None = None,
                    confirmed_by_patient: bool | None = None) -> dict | None:
    """Fetch the protocol-mandated follow-ups and queue them durably."""
    if not icd10:
        return None

    # ONE DISCLOSURE, ONE CODE. Two classifications of the same words are not two
    # diseases, and a live resumed call proved that the hard way: F21 Schizotypal
    # disorder and F20 Schizophrenia both went onto one record from one sentence,
    # the caller was taken through the same three follow-ups twice, and the summary
    # read both diagnoses back to her.
    #
    # Resolved by EVIDENCE rather than by order of arrival, because order of
    # arrival is meaningless here - which code came first depended only on when the
    # classifier index happened to be patched. See _better_evidenced.
    session = st.get_session(call_id) or {}
    duplicate = _duplicate_declaration(session, icd10, patient_phrase)
    if duplicate:
        incumbent = str(duplicate.get("icd10"))
        candidate = {"corroborated": corroborated,
                     "confirmedByPatient": confirmed_by_patient,
                     "confidence": confidence}
        if not _better_evidenced(candidate, duplicate):
            notes.append(
                f"{icd10} is the same disclosure already recorded as {incumbent}; "
                f"keeping {incumbent}, which is better evidenced")
            trace.append({"tool": "dedupe_condition", "ok": True,
                          "resultSummary": {"kept": incumbent, "dropped": icd10}})
            try:
                st.record_unclassified_mention(
                    call_id, patient_phrase or icd10, item_id=incumbent,
                    reason=(f"classified as {icd10} on a later pass but already "
                            f"recorded as {incumbent} from the same words; "
                            f"{incumbent} kept"),
                    kind="duplicate")
            except Exception as e:  # noqa: BLE001 - an audit note never fails a call
                log.error("could not flag duplicate classification %s: %s", icd10, e)
            return session
        notes.append(f"{icd10} supersedes {incumbent} for the same disclosure; "
                     f"{incumbent} moved to supersededConditions")

    fu = tools.get_followup_questions(icd10, language)
    trace.append({"tool": "get_followup_questions", "ok": True,
                  "resultSummary": {"icd10": icd10,
                                    "mandatoryCount": fu.get("mandatoryCount"),
                                    "protocolSource": fu.get("protocolSource")}})
    if not fu.get("found") or not fu.get("followUps"):
        # RESOLVED TO A CODE, BUT THE GRAPH HAS NO PROTOCOL FOR IT.
        #
        # Unreachable from the current seed - all 34 conditions have follow-ups
        # (tests/diag_protocol_coverage.py) - but reachable the moment someone adds
        # a condition at runtime without its follow-ups, which the "items are
        # addable without redeployment" requirement positively invites.
        #
        # The old behaviour was to return None, which meant queue_followups was
        # never called, so the condition never reached declaredConditions and the
        # intake finished looking clean. Same silent-clear failure as an
        # unclassifiable phrase, just one step further along.
        #
        # So fall back to the generic protocol. These three questions are what an
        # underwriter needs for essentially any chronic condition, and asking them
        # is strictly better than recording a bare code. provenance is labelled
        # honestly as a default rather than dressed up as a graph traversal.
        notes.append(f"{icd10} has no graph protocol; applied the default protocol "
                     "and flagged for human review")
        session = st.queue_followups(
            call_id, icd10, label or icd10,
            _default_protocol(language),
            protocol_source=f"default-protocol (no graph entry for {icd10})",
            patient_phrase=patient_phrase, confidence=confidence,
            corroborated=corroborated,
            confirmed_by_patient=confirmed_by_patient)
        try:
            st.record_unclassified_mention(
                call_id, patient_phrase or (label or icd10), item_id=icd10,
                reason=f"resolved to {icd10} but the graph defines no follow-up "
                       "protocol; default protocol applied",
                # NOT "unidentified". This code WAS identified and IS on the
                # record - it simply has no clinician-authored protocol, which is
                # true of every reference-tier code. A live call recorded A97
                # Dengue in declaredConditions and, in the same letter, told the
                # customer we could not identify "dengue fever". The row is for the
                # underwriter; the customer is not asked to correct a disclosure we
                # got right.
                kind="ungoverned")
        except Exception as e:  # noqa: BLE001
            log.error("could not flag ungoverned condition %s: %s", icd10, e)
        return _retire_superseded(call_id, session, duplicate, icd10, trace, notes)

    session = st.queue_followups(call_id, icd10, label or icd10,
                                 fu["followUps"], fu.get("protocolSource", ""),
                                 patient_phrase=patient_phrase,
                                 confidence=confidence,
                                 corroborated=corroborated,
                                 confirmed_by_patient=confirmed_by_patient)
    notes.append(f"queued {len(fu['followUps'])} protocol follow-ups for {icd10}")
    return _retire_superseded(call_id, session, duplicate, icd10, trace, notes)


def _retire_superseded(call_id: str, session: dict, duplicate: dict | None,
                       surviving_icd10: str, trace: list, notes: list) -> dict:
    """Retire the code the new classification just displaced, if there was one.

    Runs AFTER the surviving code has been declared and its protocol queued, so
    the displaced code's answers have somewhere to be copied to and the queue can
    be pruned in one pass. A failure here must not lose the classification that
    just succeeded, so it degrades to a note: the worst case is the pre-fix
    behaviour, two codes on the record, rather than a lost condition.
    """
    if not duplicate or not duplicate.get("icd10"):
        return session
    incumbent = str(duplicate["icd10"])
    try:
        session = st.supersede_condition(
            call_id, incumbent, surviving_icd10,
            reason=(f"same disclosure classified as {surviving_icd10} with better "
                    f"evidence; {incumbent} was recorded from the same words"))
        trace.append({"tool": "supersede_condition", "ok": True,
                      "resultSummary": {"superseded": incumbent,
                                        "survivor": surviving_icd10}})
    except Exception as e:  # noqa: BLE001
        log.error("could not supersede %s with %s: %s", incumbent,
                  surviving_icd10, e)
        notes.append(f"could not retire the duplicate {incumbent}; both it and "
                     f"{surviving_icd10} remain on the record for review")
        trace.append({"tool": "supersede_condition", "ok": False,
                      "resultSummary": {"superseded": incumbent,
                                        "error": type(e).__name__}})
    return session


def _reask(call_id: str, asked: dict, language: str, ack: str, turn_index: int,
           trace: list, why: str, customer: dict | None = None) -> dict:
    """Ask the same question again, using the simpler reprompt text if there is one.

    THE REPROMPT IS SCHEMA TEXT, SO IT CARRIES PLACEHOLDERS. It was being spoken
    raw. Measured on the deployed agent, in a live call:

        "That doesn't seem right - I may have misheard. Sorry, I did not catch
         that. Am I speaking with {{customerName}}?"

    asked["text"] goes through st.render_prompt and so was always filled; the
    reprompt never did, because it is read straight off the item here. On the
    English path meta["reprompt"] EXISTS for the identity item, so it won that
    `or` and reached the caller verbatim - the Hindi path only escaped because the
    schema defines no repromptHi and it therefore fell through to asked["text"].

    customer is optional ONLY so an older call site cannot crash mid-call; without
    it the guard below still refuses to speak an unfilled template.
    """
    meta = asked.get("meta") or {}
    key = {"hi": "repromptHi", "hinglish": "repromptHinglish"}.get(language, "reprompt")
    # NEVER FALL BACK ACROSS LANGUAGES.
    #
    # This used to fall through to meta["reprompt"], which is the ENGLISH reprompt, so
    # a caller who had chosen Hindi was re-asked in English: "माफ़ कीजिए, मैं ठीक से
    # सुन नहीं पाई। Your height, in feet and inches or centimetres?" Twice in a row on
    # a live call, and it is worst exactly when it hurts most - the caller is already
    # struggling to be understood and the retry arrives in a language they did not
    # choose.
    #
    # The form has reprompt on two items and repromptHi/repromptHinglish on none, so
    # that fallback was not an edge case, it was the normal path. Fixed here rather
    # than by adding translations to the schema: asked["text"] is already the question
    # in the right language, so re-asking it verbatim is always correct and no future
    # item can reintroduce the leak by omitting a field.
    text = meta.get(key) or asked.get("text") or ""
    if not text and language in ("en", None):
        text = meta.get("reprompt") or ""
    # Fill the tokens, using the SAME table render_prompt uses so the two cannot
    # diverge. See hvi_state.fill_placeholders.
    if customer is not None:
        text = _fill_placeholders(text, customer)
    # LAST LINE OF DEFENCE. If anything unfilled survived - a token this table does
    # not know, or a call site that passed no customer - fall back to the rendered
    # question rather than reading braces to a caller. Saying the wrong-but-real
    # question is recoverable; saying "{{customerName}}" is not.
    if "{{" in text:
        log.warning("reprompt for %s still had an unfilled placeholder; using the "
                    "rendered question instead: %r",
                    meta.get("itemId"), text[:80])
        text = asked.get("text") or text.replace("{{", "").replace("}}", "")
    # THE APOLOGY MUST BE TRUE. "Sorry, I didn't quite catch that" was said for
    # EVERY re-ask, including when transcription was perfect and the VALUE was
    # impossible: a caller said "50 feet" for height, it transcribed cleanly, the
    # validator rejected 1524cm, and the agent claimed it couldn't hear. So the
    # caller repeated "fifty feet" louder, four times, stuck - the message told
    # them to fix the wrong thing. An implausible value gets told it is
    # implausible; only a genuinely unusable transcript gets the hearing apology.
    #
    # ENUMERATED THE OTHER WAY ROUND, after this recurred. The list used to name
    # the value-is-wrong cases and default to the hearing apology, so every
    # validator added afterwards silently inherited the false message. It had
    # already grown to miss five: the minimum-age bound on a date of birth, both
    # ABHA rejections, the email check, and the not-one-of-these-options check. A
    # caller who said "5th January 2020" was told the agent had not heard them,
    # when it had heard perfectly and was refusing a date that would make them six
    # years old - so they repeated the same wrong year, twice.
    #
    # Naming the SMALL set instead means a new validator defaults to the hedged
    # "may have misheard", which is true either way, rather than to a claim that
    # is flatly false whenever the transcript was fine.
    unusable = any(k in (why or "") for k in
                   ("no value", "only a placeholder", "must include a valid day",
                    "negated, corrected, or ambiguous"))
    if not unusable:
        lead = {
            "hi": "यह सही नहीं लग रहा, शायद मैंने ग़लत समझा। ",
            "hinglish": "Yeh sahi nahi lag raha, shayad maine galat samjha. ",
        }.get(language, "That doesn't seem right — I may have misheard. ")
    else:
        lead = {
            "hi": "माफ़ कीजिए, मैं ठीक से सुन नहीं पाई। ",
            "hinglish": "Maaf kijiye, main theek se sun nahi payi. ",
        }.get(language, "Sorry, I didn't quite catch that. ")
    return {
        "agentUtterance": (lead + text).strip(),
        "turnIndex": turn_index,
        "askedKind": asked["kind"],
        "askedRef": _ref(asked),
        "expectedAnswerType": asked.get("answerType"),
        "isComplete": False, "escalated": False, "stopQuestionnaire": False,
        "language": language, "toolTrace": trace, "notes": [f"re-asked: {why}"],
    }


def _explain_then_reask(call_id: str, asked: dict, language: str,
                        turn_index: int, trace: list, notes: list) -> dict:
    """A confused caller on a protocol follow-up: explain the condition once, then
    re-ask the SAME question in this turn's reply.

    Repeating an unfamiliar question more loudly does not help someone who did not
    understand it. A live caller answered a "when were you diagnosed?" follow-up
    with "haan" and then "what was that?", and was only ever re-asked the same
    words. This speaks the graph's authored, non-diagnostic explanation of the
    condition when there is one (governed conditions carry it) and falls back to a
    plain-language lead when there is not (a reference-only code such as
    schizophrenia has no authored text), so the caller always gets more than the
    same sentence again. The question that follows is unchanged, so the answer
    path and the attempt counter behave exactly as before.
    """
    meta = asked.get("meta") or {}
    icd10 = meta.get("icd10")
    lead = ""
    if icd10:
        try:
            info = tools.get_condition_explanation(icd10, language)
            lead = (info or {}).get("explanation") or ""
            trace.append({"tool": "get_condition_explanation", "ok": True,
                          "resultSummary": {"icd10": icd10, "hadText": bool(lead)}})
        except Exception as e:  # noqa: BLE001 - a nicety must never fail the call
            trace.append({"tool": "get_condition_explanation", "ok": False,
                          "error": str(e)})
            lead = ""
    if not lead:
        lead = {
            "hi": "कोई बात नहीं, मैं आसान शब्दों में पूछती हूँ।",
            "hinglish": "Koi baat nahi, main aasaan shabdon mein poochti hoon.",
        }.get(language, "No problem — let me put that more simply.")
    question = (asked.get("text") or meta.get("prompt") or "").strip()
    notes.append(f"{_ref(asked)}: explained the condition before re-asking; the "
                 "caller did not understand the question")
    return {
        "agentUtterance": (lead.rstrip() + " " + question).strip(),
        "turnIndex": turn_index,
        "askedKind": asked["kind"],
        "askedRef": _ref(asked),
        "expectedAnswerType": asked.get("answerType"),
        "isComplete": False, "escalated": False, "stopQuestionnaire": False,
        "language": language, "toolTrace": trace, "notes": notes,
    }


# THANKING SOMEONE TWICE IN ONE BREATH.
#
# Heard on a live call, and in every run since:
#
#     "Okay, thank you. Thank you. This call is recorded and the information..."
#
# The acknowledgement is generated per turn and the consent prompt is authored
# starting with its own "Thank you.", so both arrive. Fixed where they are joined
# rather than by rewording one prompt, because the collision is structural: any
# prompt that opens politely will hit it, in any of the three languages.
#
# Scoped to thanks specifically. A broader courtesy match would strip "Thank you"
# after an ack of merely "Okay", losing a politeness that was never duplicated.
_THANKS_HEAD = re.compile(
    r"^(?:thank you|thanks|\u0927\u0928\u094d\u092f\u0935\u093e\u0926|"
    r"\u0936\u0941\u0915\u094d\u0930\u093f\u092f\u093e|dhanyavaad|shukriya)"
    r"[,.!\u0964]*\s+", re.I)
_THANKS_ANY = re.compile(
    r"(?:thank you|thanks|\u0927\u0928\u094d\u092f\u0935\u093e\u0926|"
    r"\u0936\u0941\u0915\u094d\u0930\u093f\u092f\u093e|dhanyavaad|shukriya)",
    re.I)


def _dedupe_courtesy(ack: str, text: str) -> str:
    """Drop the question's opening thanks when the acknowledgement already said it."""
    if not ack or not text or not _THANKS_ANY.search(ack):
        return text
    return _THANKS_HEAD.sub("", text, count=1) or text


def _advance(call_id: str, turn_index: int, language: str, ack: str,
             trace: list, notes: list, escalated: bool, stop: bool) -> dict:
    """Move to whatever is next and phrase it."""
    session = st.get_session(call_id)
    session = _clear_expired_confirmation(call_id, session, trace, notes)

    # Deferred mentions drain HERE, at the single choke point every turn passes
    # through on its way to the next question. The drain originally lived at the
    # top of handle_turn, which is one turn too late: the answer that EMPTIES a
    # protocol reaches this function directly, next_question hands out the next
    # form item, and the deferred disease sits waiting for a turn that arrives
    # only after that item is answered - or never, if the form ends first. The
    # stress suite caught diabetes deferred during the cancer protocol and then
    # abandoned.
    # Drain in FIFO order until a real obligation opens or the queue is empty.
    # The first deferred phrase can be an echo of the active condition (the live
    # extractor returned "Malignant Neoplasm of Breast" before "diabetes").
    # _handle_condition correctly ignores that duplicate, but stopping after one
    # pop strands the genuine second disclosure when the form ends. The queue
    # shrinks on every pass; the bound only protects against corrupted state.
    for _ in range(20):
        if session.get("pendingFollowUps") or session.get("awaitingConfirmation"):
            break
        deferred = st.pop_deferred_mention(call_id)
        if not deferred:
            break
        notes.append(f"returning to the condition mentioned earlier: "
                     f"'{deferred[:40]}'")
        _handle_condition(call_id, deferred, language, trace, notes,
                          min_score=_DEFERRED_MIN_SCORE)
        session = st.get_session(call_id)

    items = st.get_form_items(session["formId"])
    customer = {
        "customerName": session.get("customerName"),
        "customerEmail": session.get("customerEmail"),
        "policyNumber": session.get("policyNumber"),
        "insurerName": session.get("insurerName") or "Meridian Health Cover",
    }

    nxt = next_question(session, items, language)
    if nxt is None:
        return _complete(call_id, session, turn_index, trace, ack=ack, notes=notes)

    if nxt["kind"] == "item":
        nxt["text"] = _prompt_for(nxt["meta"], language, customer)

    # Sensitive follow-ups get a softening lead-in rather than being fired off
    # like any other question.
    lead = ""
    if (nxt.get("meta") or {}).get("sensitivity") in ("high", "maximum"):
        lead = {
            "hi": "एक संवेदनशील सवाल है, ",
            "hinglish": "Ek sensitive sawaal hai, ",
        }.get(language, "One sensitive question, ")

    question = _dedupe_courtesy(ack, lead + (nxt["text"] or ""))
    utter = " ".join(p for p in (ack, question) if p).strip()

    return {
        "agentUtterance": utter,
        "turnIndex": turn_index,
        "askedKind": nxt["kind"],
        "askedRef": _ref(nxt),
        "expectedAnswerType": nxt.get("answerType"),
        "isComplete": False,
        "escalated": escalated,
        "stopQuestionnaire": stop,
        "language": language,
        "remainingCount": st.remaining_count(items, session.get("answers") or {}),
        "pendingFollowUps": len(session.get("pendingFollowUps") or []),
        "toolTrace": trace,
        "notes": notes,
    }


# Follow-up answers worth speaking in the read-back, with a spoken lead-in each.
# Two kinds, not all six: a spoken summary is heard once and cannot be skimmed, so
# it carries what an underwriter would read back on a phone call - when it started
# and what is taken for it - and the email carries the rest.
_READBACK_DETAILS = ("FU_DIAG_WHEN", "FU_TREATMENT", "FU_CANCER_TREATMENT")

# THE READ-BACK IS THE VERIFICATION STEP, so it has to be sayable.
#
# A live call read this to the customer, verbatim:
#
#     "Gallstones, since In 2019, treatment Pantoprazole 40 milligrams once a day"
#
# The caller had answered "In 2019" and the read-back adds its own "since", so the
# record was read back to them in broken English at the one moment they are being
# asked to confirm it is right.
#
# Only the prepositions that "since" makes ungrammatical are removed. A hedge is
# kept - "since around 2015" is what the caller said and it is true; reducing it
# to "since 2015" would make an approximate date sound exact, which on a medical
# record is a change of meaning, not a tidy-up.
_SINCE_LEAD = re.compile(r"^(?:in|on|from|since)(?:\s+|$)", re.I)
# The postposition must be its own word. Written as \s* it matched the last two
# letters of "the case" and rewrote it to "the ca".
_SINCE_TRAIL = re.compile(r"(?:^|\s)(?:\u0938\u0947|\u092e\u0947\u0902|se)\s*$", re.I)
_SINCE_HEDGE_HEAD = re.compile(
    r"^(?:around|about|approximately|roughly|circa|maybe|probably|lagbhag|"
    r"karib|qareeb)\b")


def _since_phrase(value: Any, language: str) -> str:
    """Say "since <when>" in the caller's language, without doubling a preposition."""
    text = str(value or "").strip()
    text = _SINCE_LEAD.sub("", text).strip()
    # Hindi marks the relation AFTER the year ("2019 से", "2019 में"), so the
    # duplicate to remove is at the other end.
    text = _SINCE_TRAIL.sub("", text).strip()
    text = re.sub(r"^(?:\u0932\u0917\u092d\u0917|\u0915\u0930\u0940\u092c|"
                  r"\u0915\u093c\u0930\u0940\u092c)\s+", "", text).strip()
    if not text:
        return ""
    # "since Around 2015" - the caller's answer opened a sentence and keeps its
    # capital when it becomes a clause. Only lowered for known hedge words, never
    # for something that might be a proper noun.
    if _SINCE_HEDGE_HEAD.match(text.lower()):
        text = text[0].lower() + text[1:]
    # से and se are POSTpositions: "2019 से", never "से 2019". English leads.
    if language == "hi":
        return f"{text} \u0938\u0947"
    if language == "hinglish":
        return f"{text} se"
    return f"since {text}"


def _spoken_summary(session: dict, language: str, summary: dict) -> str:
    """The disclosures, read back in the CALLER'S language with key details.

    Replaces a read-back that spoke raw English form labels into a Hindi call -
    "मैंने यह दर्ज किया है: cancer tumor, cardiac, hypertension cho..." - which a
    Hindi speaker cannot verify, and verification is the entire point of reading
    it back. Built from declaredConditions (the label the caller confirmed at
    classification time) plus the when/treatment answers from the protocol, so
    what is spoken is what the record actually says.
    """
    declared = session.get("declaredConditions") or []
    fua = session.get("followUpAnswers") or {}
    if declared:
        parts: list[str] = []
        for d in declared[:4]:
            bits = [str(d.get("label") or d.get("icd10") or "")]
            for qid in _READBACK_DETAILS:
                rec = fua.get(f"{d.get('icd10')}::{qid}")
                if isinstance(rec, dict) and rec.get("value"):
                    if qid == "FU_DIAG_WHEN":
                        phrase = _since_phrase(rec["value"], language)
                    else:
                        lead = {"hi": "इलाज", "hinglish": "ilaaj"}.get(language,
                                                                       "treatment")
                        phrase = f"{lead} {rec['value']}"
                    if phrase:
                        bits.append(phrase)
            parts.append(", ".join(bits))
        joined = "; ".join(parts)
        return {
            "hi": f"मैंने यह दर्ज किया है: {joined}। ",
            "hinglish": f"Maine yeh note kiya hai: {joined}. ",
        }.get(language, f"I've recorded: {joined}. ")
    return ""  # fall back to the caller's positives list below


def _complete(call_id: str, session: dict, turn_index: int, trace: list,
              ack: str = "", notes: list | None = None,
              confirmed: bool = False) -> dict:
    """Everything answered: summarise, CONFIRM, then submit, email, close."""
    notes = notes or []
    language = session.get("language") or "hinglish"

    summary = tools.generate_summary(call_id)
    trace.append({"tool": "generate_summary", "ok": True,
                  "resultSummary": {"answeredCount": summary.get("answeredCount"),
                                    "underwritingFlag": summary.get("underwritingFlag")}})

    positives = summary.get("positiveDeclarations") or []
    if positives:
        labels = ", ".join(
            str(p.get("label", "")).replace("_", " ") for p in positives[:4]
        )
        readback = {
            "hi": f"मैंने यह दर्ज किया है: {labels}. ",
            "hinglish": f"Maine yeh note kiya hai: {labels}. ",
        }.get(language, f"I've recorded: {labels}. ")
    else:
        readback = {
            "hi": "आपने कोई बीमारी नहीं बताई। ",
            "hinglish": "Aapne koi bimari declare nahi ki. ",
        }.get(language, "You haven't declared any conditions. ")

    detailed = _spoken_summary(session, language, summary)
    if detailed:
        readback = detailed

    # ---- the record is not final until the caller has heard it --------------
    # Read back what was captured and only close once they confirm. The summary
    # email with its 48-hour window still exists, but THIS call is the one moment
    # the record and the person are together: an error corrected now costs one
    # turn, the same error found in the email costs a callback. Bounded at two
    # rounds so a caller whose corrections cannot be parsed is finalised with
    # what is on record rather than trapped on a call they cannot end.
    asks = int(session.get("summaryAskCount") or 0)
    summary_pending = bool(session.get("summaryAskPending"))
    if not confirmed and (summary_pending or asks < 2):
        # A resumed call may have dropped after hearing the second read-back but
        # before answering it. Re-speak that durable obligation without consuming
        # another attempt; only a parsed correction starts a new bounded round.
        if not summary_pending:
            st.record_summary_ask(call_id, True)
        ask_q = {
            "hi": "क्या इसमें कुछ ठीक करना या जोड़ना चाहेंगी?",
            "hinglish": "Kya isme kuch theek karna ya jodna chahengi?",
        }.get(language, "Is there anything you'd like to correct or add?")
        notes.append("read the summary back; awaiting confirmation before "
                     "submitting to the CRM")
        return {
            "agentUtterance": " ".join(
                p for p in (ack, readback, ask_q) if p).strip(),
            "turnIndex": turn_index, "isComplete": False, "escalated": False,
            "stopQuestionnaire": False, "language": language,
            "askedKind": "summaryConfirm", "askedRef": "summaryConfirm",
            "toolTrace": trace, "notes": notes,
        }

    try:
        sub = tools.submit_to_crm(call_id)
        trace.append({"tool": "submit_to_crm", "ok": True,
                      "resultSummary": {"answerCount": sub.get("answerCount")}})
        # Where the record landed, kept off the trace summary and carried on the
        # reply instead. The trace is an audit artefact; this is a pointer for
        # whoever has to open the record next. Either field may be absent - see
        # crm_writeback._record_id for why the update path has to look it up.
        underwriting_record = {
            "recordId": sub.get("recordId"),
            "recordUrl": sub.get("recordUrl"),
        }
    except Exception as e:
        log.exception("submit failed")
        trace.append({"tool": "submit_to_crm", "ok": False, "error": str(e)})
        notes.append(f"submit failed: {e}")
        failed_closing = {
            "hi": ("माफ़ कीजिए, आपकी जानकारी अभी सुरक्षित रूप से जमा नहीं हो पाई। "
                   "हमारी टीम आपसे दोबारा संपर्क करेगी।"),
            "hinglish": ("Maaf kijiye, aapki jaankari abhi safely submit nahi ho payi. "
                          "Hamari team aapse dobara contact karegi."),
        }.get(language,
              "I'm sorry, I couldn't safely finalize your information just now. "
              "Our team will contact you to complete this.")
        return {
            "agentUtterance": " ".join(p for p in (ack, readback, failed_closing) if p).strip(),
            "turnIndex": turn_index,
            "isComplete": False,
            "escalated": False,
            "stopQuestionnaire": True,
            "language": language,
            "summary": summary,
            "toolTrace": trace,
            "notes": notes,
        }

    email_sent = True
    try:
        tools.send_confirmation_email(call_id)
        trace.append({"tool": "send_confirmation_email", "ok": True})
    except Exception as e:
        email_sent = False
        log.warning("confirmation email failed: %s", e)
        trace.append({"tool": "send_confirmation_email", "ok": False, "error": str(e)})
        notes.append(f"confirmation email failed: {e}")

    # Some questions were skipped after the caller could not answer them even with
    # an explanation and a re-ask (see the give-up ladder). Tell them a person will
    # finish those - honestly, and only when there actually are any.
    human_items = (st.get_session(call_id) or session).get("humanFollowUpItems") or []
    human_line = ""
    if human_items:
        human_line = {
            "hi": "कुछ सवाल अभी पूरे नहीं हो पाए। उन्हें पूरा करने के लिए हमारी टीम का कोई सदस्य जल्द ही आपसे संपर्क करेगा।",
            "hinglish": "Kuch sawaal abhi poore nahi ho paaye. Unhe poora karne ke liye "
                        "hamari team ka koi member jald hi aapse baat karega.",
        }.get(language,
              "A few questions still need to be completed, so someone from our team "
              "will call you shortly to finish them.")

    if email_sent:
        closing = {
            "hi": "एक सारांश ईमेल भेज दिया गया है। अगले 48 घंटों में आप कुछ भी ठीक करा सकते हैं। "
                  "समय देने के लिए धन्यवाद, आपका दिन शुभ हो।",
            "hinglish": "Ek summary email bhej diya gaya hai. Agle 48 ghante mein aap kuch bhi "
                        "correct kara sakte hain. Time dene ke liye dhanyavaad, aapka din shubh ho.",
        }.get(language,
              "A summary email is on its way. You have 48 hours to correct anything in it. "
              "Thank you for your time, and have a good day.")
    else:
        closing = {
            "hi": ("आपकी जानकारी सुरक्षित रूप से दर्ज हो गई है, लेकिन सारांश ईमेल अभी नहीं भेजा जा सका। "
                   "हमारी टीम इसमें मदद करेगी। धन्यवाद।"),
            "hinglish": ("Aapki jaankari safely record ho gayi hai, lekin summary email abhi nahi "
                          "bheja ja saka. Hamari team ismein madad karegi. Dhanyavaad."),
        }.get(language,
              "Your information was safely recorded, but the summary email could not be sent. "
              "Our team can help with that. Thank you.")

    return {
        "agentUtterance": " ".join(
            p for p in (ack, readback, human_line, closing) if p).strip(),
        "turnIndex": turn_index,
        "isComplete": True,
        "escalated": False,
        "stopQuestionnaire": True,
        "language": language,
        "summary": summary,
        "underwritingRecord": underwriting_record,
        "toolTrace": trace,
        "notes": notes,
    }
