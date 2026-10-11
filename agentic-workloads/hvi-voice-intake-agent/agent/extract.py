"""
The single narrow LLM call per turn.

The model is given one job: read the customer's utterance against the question
that was just asked, and report what it means as structured data. It does not
decide what to ask next, it does not choose which tools to call, and it does not
control the protocol. All of that lives in orchestrator.py.

Why this shape. When the model owned the protocol it skipped steps - classifying
a condition and then talking instead of checkpointing the answer and fetching the
mandated follow-ups. That is not something to fix with a firmer prompt, because
the audit trail then depends on model compliance rather than on code. Narrowing
the task also brings it within reach of the in-region model, which keeps
inference inside ap-south-1 and preserves the DPDP position.

Structured output comes back through tool use rather than free-text JSON, so it
is schema-validated by Bedrock instead of parsed hopefully.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import time
from typing import Any

import boto3

log = logging.getLogger("hvi-agent.extract")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "mistral.mistral-large-3-675b-instruct")

bedrock = boto3.client("bedrock-runtime", region_name=DATA_REGION)

# Deliberately permissive on the local part and strict about the shape: one @, a
# dot-bearing domain, no spaces. Validating an address properly is a losing game,
# but "True" and "हाँ" must not pass, which is what this is for. See coerce().
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")

# The closing item reads as a yes/no question - "I will email a summary to X, is
# that the right address?" - so both answers arrive constantly, and they mean
# opposite things. Kept apart: yes resolves to the address already on file, no
# means that address is wrong and the caller must say the right one. Devanagari
# and Hinglish included because a caller who chose English still answers "हाँ".
_EMAIL_AFFIRM = {
    "true", "yes", "yeah", "yep", "yes it is", "that is right", "that's right",
    "correct", "right", "confirmed", "haan", "haan ji", "ji", "ji haan",
    "theek hai", "sahi hai", "bilkul",
    "\u0939\u093e\u0901", "\u0939\u093e\u0902", "\u091c\u0940",
    "\u0939\u093e\u0901 \u091c\u0940", "\u0939\u093e\u0902 \u091c\u0940",
    "\u091c\u0940 \u0939\u093e\u0901", "\u091c\u0940 \u0939\u093e\u0902",
    "\u0920\u0940\u0915 \u0939\u0948",
    "\u0938\u0939\u0940 \u0939\u0948", "\u092c\u093f\u0932\u0915\u0941\u0932",
}
_EMAIL_DENY = {
    "false", "no", "nope", "not right", "wrong", "that is wrong", "nahi",
    "nahin", "galat", "\u0928\u0939\u0940\u0902", "\u0928\u0939\u0940",
    "\u0917\u0932\u0924", "\u0917\u0932\u0924 \u0939\u0948",
}

# A model reporting its own uncertainty as though it were the caller's answer.
# These carry no information and must not reach a medical record.
_LIST_PLACEHOLDER = re.compile(
    r"(?:duration|amount|quantity|frequency|dosage|years?|period)?\s*"
    r"(?:not\s+(?:specified|mentioned|stated|provided|given|available)"
    r"|unspecified|unknown\s+(?:duration|amount|quantity|period)"
    r"|none\s+specified|n/?a|not\s+applicable)\s*",
    re.I)

# ABHA - Ayushman Bharat Health Account, the national health identifier.
#
# 14 digits, per the National Health Authority. A caller reading it aloud pauses,
# and speech recognition renders those pauses as spaces or dashes, so separators
# are stripped before counting rather than treated as invalid.
#
# Every ABHA number also has a default ADDRESS of the form 14-digit@abdm or @sbx,
# and some people know themselves by that instead, so it is accepted too.
_ABHA_SEPARATORS = re.compile(r"[\s\-\u2013\u2014.]")
_ABHA_NUMBER = re.compile(r"^\d{14}$")
_ABHA_ADDRESS = re.compile(r"^[A-Za-z0-9._]{3,}@(abdm|sbx)$", re.I)

# An optional field the caller is invited to skip. This must NOT be routed through
# the extractor's isRefusal signal, which means "declined to answer OR wants to
# stop the call" and makes the agent offer to end the call - the wrong response to
# someone declining one optional identifier.
_ABHA_DECLINE = {
    "skip", "skip it", "no", "nope", "none", "no thanks", "not now",
    "i don't have one", "i dont have one", "i do not have one", "dont have",
    "don't have", "nahi", "nahin", "nahi hai", "nahi h", "no abha",
    "\u0928\u0939\u0940\u0902", "\u0928\u0939\u0940\u0902 \u0939\u0948",
    "\u0928\u0939\u0940\u0902 \u092c\u0928\u093e", "\u0938\u094d\u0915\u093f\u092a",
    "chhod do", "chhodo", "rehne do", "\u0930\u0939\u0928\u0947 \u0926\u094b",
    "\u091b\u094b\u0921\u093c \u0926\u094b", "pata nahi", "yaad nahi",
    "\u092f\u093e\u0926 \u0928\u0939\u0940\u0902",
    "\u092a\u0924\u093e \u0928\u0939\u0940\u0902",
}

# A decline stated as a sentence rather than a bare word: "No, I do not have an
# ABHA number." Applied ONLY when the utterance contains no digits at all, so it
# can never swallow a real identifier.
_ABHA_NEGATION = re.compile(
    r"\b(no|not|none|never|dont|doesnt|havent|haven|nope|nahi|nahin|nai)\b"
    r"|n[o']t\b|\u0928\u0939\u0940|\u0928\u093e", re.I)

EXTRACT_TOOL = [{
    "toolSpec": {
        "name": "report_understanding",
        "description": "Report what the customer's reply means. Always call this exactly once.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "understood": {
                    "type": "boolean",
                    "description": "True if the reply answers the question that was asked.",
                },
                "value": {
                    "description": (
                        "The answer in the expected type. boolean for yes/no, "
                        "number for quantities, string for free text, array of "
                        "strings for lists. Omit if not understood."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": "0 to 1, how clear the reply was.",
                },
                "isDontKnow": {
                    "type": "boolean",
                    "description": "True if they said they do not know or cannot remember.",
                },
                "isRefusal": {
                    "type": "boolean",
                    "description": "True if they declined to answer or want to stop the call.",
                },
                "needsRepeat": {
                    "type": "boolean",
                    "description": "True if they could not hear, asked you to repeat, or the reply was unintelligible.",
                },
                "mentionsCondition": {
                    "type": "boolean",
                    "description": (
                        "True if they named ANY medical condition, symptom or "
                        "medication, even in passing and even while answering a "
                        "different question."
                    ),
                },
                "conditionPhrase": {
                    "type": "string",
                    "description": (
                        "Legacy first condition phrase. Prefer conditionMentions. "
                        "The customer's own words, verbatim and untranslated."
                    ),
                },
                "conditionMentions": {
                    "type": "array",
                    "description": (
                        "Every distinct condition, symptom, or medication mention "
                        "in the reply, preserving order and scope."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "phrase": {
                                "type": "string",
                                "description": "The caller's exact words, untranslated.",
                            },
                            "scope": {
                                "type": "string",
                                "enum": ["self_current", "family", "hypothetical", "negated"],
                                "description": (
                                    "self_current only when the caller says they currently "
                                    "have or were diagnosed with it."
                                ),
                            },
                        },
                        "required": ["phrase", "scope"],
                    },
                },
                "medications": {
                    "type": "array", "items": {"type": "string"},
                    "description": (
                        "Every medication name the caller said, verbatim. Return [] "
                        "when no medication was named. A non-empty medications array "
                        "must always accompany mentionsCondition=true."
                    ),
                },
                "distressSignal": {
                    "type": "boolean",
                    "description": (
                        "True if they express self-harm, suicidal thoughts, an "
                        "emergency happening now, or clear distress."
                    ),
                },
                "detectedLanguage": {
                    "type": "string",
                    "enum": ["en", "hi", "hinglish"],
                    "description": (
                        "Language and script actually used. 'hi' only for "
                        "Devanagari script; romanised Hindi is 'hinglish'."
                    ),
                },
                "acknowledgement": {
                    "type": "string",
                    "description": (
                        "A short, natural acknowledgement in the customer's "
                        "language and script, under 8 words, e.g. 'Theek hai.' or "
                        "'Got it, thank you.' No question, no markdown."
                    ),
                },
            },
            # These are semantic contract fields, not optional decoration. Requiring
            # explicit false/[] values prevents a structurally valid tool call from
            # silently omitting medication, scope, refusal, repeat, or distress data.
            "required": [
                "understood", "confidence", "isDontKnow", "isRefusal",
                "needsRepeat", "mentionsCondition", "conditionMentions",
                "medications", "distressSignal", "detectedLanguage",
                "acknowledgement",
            ],
        }},
    }
}]

SYSTEM = """
You interpret one reply from a customer on a medical-history phone call for an
Indian health insurer. You do not conduct the call and you do not decide what to
ask next. Report what the reply means by calling report_understanding exactly
once.

Rules:
- Answer only about the reply you are given. Do not infer from anything else.
- Yes/no questions: map affirmatives to true and negatives to false, in any
  language. "haan", "ji haan", "bilkul", "yes" are all true. "nahi", "nahin",
  "no", "kabhi nahi" are all false.
- Heights and weights: convert to the unit named in the question. Five foot five
  is 165 centimetres. Sixty two kilos is 62.
- Report what they actually said, even when it is implausible. If someone says
  four hundred centimetres, report 400. Do not correct it, clamp it, or
  substitute a sensible-looking number. Plausibility is checked elsewhere, and
  quietly fixing a mis-heard value is how a wrong figure ends up in a medical
  record. Lower confidence instead.
- Set mentionsCondition whenever a condition, symptom or medicine is named, even
  if it was not what you asked about. Someone answering "any hospital stay?" with
  "haan, sugar ke liye" has mentioned a condition.
- Put EVERY distinct mention in conditionMentions, in spoken order, using the
  caller's exact untranslated words. Mark scope self_current only for the caller's
  own current or previously diagnosed condition; use family, hypothetical, or
  negated for the other scopes. For "my father had diabetes but I have asthma",
  return both mentions with family and self_current respectively. Keep
  conditionPhrase as the first phrase for backward compatibility.
- detectedLanguage reflects the script they actually used. Roman letters means
  hinglish even if the words are Hindi.
- acknowledgement is a brief human acknowledgement in their language and script.
  Never put a question in it.
- Set distressSignal for any mention of self-harm, wanting to die, or an
  emergency in progress. Err towards true here.
"""


def _type_hint(answer_type: str, item: dict) -> str:
    if answer_type == "boolean":
        return "A yes/no answer. value must be true or false."
    if answer_type == "date":
        return ("A complete calendar date. Normalize value to ISO YYYY-MM-DD, "
                "preserving the day, month, and year the customer said.")
    if answer_type == "number":
        unit = item.get("unit")
        v = item.get("validation") or {}
        bits = [f"A number{' in ' + unit if unit else ''}."]
        if "min" in v or "max" in v:
            bits.append(f"Plausible range {v.get('min','?')} to {v.get('max','?')}.")
        return " ".join(bits)
    if answer_type == "enum":
        return f"One of exactly these: {item.get('options')}. value must be one of them."
    if answer_type == "list":
        return ("A list. value must be an array of strings, containing ONLY what "
                "the customer actually said. If they did not give one of the "
                "things asked for, leave it out - do NOT add an element such as "
                "'duration not specified'.")
    if answer_type == "email":
        return "An email address, or true if they confirm the one read to them."
    if answer_type == "abha":
        return ("A 14-digit ABHA health ID number, or an ABHA address like "
                "name@abdm. Return the digits verbatim in value, keeping any "
                "spaces they said. If they decline or say they have none, return "
                "value as the words they used - do NOT invent a number and do NOT "
                "return true or false.")
    return "Free text. value is a short string capturing what they said."


# Structural JSON characters. A caller does not say a brace, so their presence at
# the end of a free-text value is generation debris rather than something spoken.
_VALUE_DEBRIS = re.compile(r'[{}\[\]"\\]+\s*$')


def _repair_glitched_value(out: dict, utterance: str) -> str | None:
    """Repair a `value` the model mangled while reporting it correctly elsewhere.

    Returns the note to append when a repair was made, or None.

    THE MEASURED GLITCH. Asked "आप इसके लिए क्या इलाज या दवा ले रहे हैं?" a caller
    answered "पैंटोप्राजोल गोली" and K80::FU_TREATMENT was recorded as
    "पैंटोप्राजोल गो{" - the final "ली" (U+0932 U+0940) replaced by one U+007B. It
    reached the underwriting record and was then read back to the caller for
    confirmation in that state, in the closing summary, which is the one moment the
    record is supposed to be verified.

    NOT A TRUNCATION, which is what it looks like and what it was first blamed on.
    Reproduced 2 times in 20 at temperature 0: stopReason is "tool_use", output is
    155-177 tokens against a 500 cap, and THE SAME RESPONSE carries the string
    correctly three more times - in conditionPhrase, in medications, and in
    conditionMentions[].phrase. Only `value` is corrupt. Raising the token cap
    cannot fix a glitch that never hit it.

    So the repair uses that corroboration rather than guessing. A candidate is
    accepted only when the debris-stripped value is a genuine PREFIX of it, which
    is what makes this a repair of an interrupted write and not a substitution of
    one answer for another. The caller's own utterance is a candidate too, and the
    last resort, on the same principle the date path already follows: prefer what
    the caller actually said over what the model rendered.

    Conservative by construction. With no debris it does nothing, and if nothing
    corroborates the fragment the value is left alone for the ordinary validation
    and human-review paths to catch - inventing a completion would be worse than
    recording a visibly broken one.
    """
    value = out.get("value")
    if not isinstance(value, str) or not value.strip():
        return None
    stripped = _VALUE_DEBRIS.sub("", value).strip()
    if stripped == value.strip() or not stripped:
        return None  # no debris to explain, so nothing to repair

    candidates: list[str] = []
    phrase = out.get("conditionPhrase")
    if isinstance(phrase, str):
        candidates.append(phrase)
    meds = out.get("medications")
    if isinstance(meds, list):
        candidates.extend(m for m in meds if isinstance(m, str))
    mentions = out.get("conditionMentions")
    if isinstance(mentions, list):
        candidates.extend(m.get("phrase") for m in mentions
                          if isinstance(m, dict) and isinstance(m.get("phrase"), str))
    # The caller's own words last: a corroborating field is the model's considered
    # rendering of the answer, while the utterance may carry surrounding speech.
    candidates.append(utterance or "")

    for candidate in candidates:
        candidate = (candidate or "").strip()
        if candidate.startswith(stripped) and len(candidate) > len(stripped):
            out["value"] = candidate
            return (f"model's value field was cut short and left JSON debris "
                    f"({value.strip()!r}); repaired to {candidate!r}, which the "
                    f"same reading reported intact")
    return None


def understand(
    question_text: str,
    answer_type: str,
    utterance: str,
    language: str = "hinglish",
    item: dict | None = None,
    customer_name: str | None = None,
) -> dict:
    """
    Interpret one reply. Returns the structured reading, never raises.

    On any failure the caller gets understood=False with needsRepeat set, which
    the orchestrator handles as "ask again" - a safe default on a live call.
    """
    item = item or {}
    prompt = (
        f"Question asked: {question_text}\n"
        f"Expected answer: {_type_hint(answer_type, item)}\n"
        f"Customer's preferred language: {language}\n"
        + (f"Customer's name: {customer_name}\n" if customer_name else "")
        + f"\nCustomer said: {utterance!r}"
    )

    try:
        resp = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM.strip()}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={
                "tools": EXTRACT_TOOL,
                # Force the structured call. Without this the model sometimes
                # answers conversationally, which is useless here.
                "toolChoice": {"tool": {"name": "report_understanding"}},
            },
            # 500 is ample, and measured: a Hindi turn carrying an acknowledgement,
            # a value, condition mentions with verbatim phrases and a medication
            # list came in at 155-177 output tokens over 20 samples. Left as it is
            # rather than raised on suspicion - see _repair_glitched_value for the
            # corruption this was briefly and wrongly blamed for.
            inferenceConfig={"maxTokens": 500, "temperature": 0.0},
        )
    except Exception as e:
        log.warning("extraction call failed: %s", e)
        return {
            "understood": False, "needsRepeat": True, "confidence": 0.0,
            "detectedLanguage": language,
            "acknowledgement": "", "error": str(e),
        }

    # A TRUNCATED READING IS NOT A READING. This is not the fix for the glitch in
    # _repair_glitched_value - that one reports stopReason "tool_use" and is not a
    # truncation at all - but if the cap IS ever hit, the tool input is whatever
    # partial JSON had accumulated, and trusting it would put a half-written drug
    # name on the record. One extra turn against that is not a close call.
    if resp.get("stopReason") == "max_tokens":
        log.warning("extraction hit the token cap; discarding a partial reading")
        return {
            "understood": False, "needsRepeat": True, "confidence": 0.0,
            "detectedLanguage": language, "acknowledgement": "",
            "error": "reading truncated at the token cap",
        }

    for c in resp["output"]["message"].get("content", []):
        if "toolUse" in c and c["toolUse"]["name"] == "report_understanding":
            out = dict(c["toolUse"].get("input") or {})
            out.setdefault("understood", False)
            out.setdefault("confidence", 0.0)
            out.setdefault("detectedLanguage", language)
            out.setdefault("acknowledgement", "")

            # Repaired here rather than in the orchestrator, unlike the year
            # helpers, because this is a defect in the READING itself and every
            # caller of understand() deserves it fixed - not only the one path that
            # remembers to ask.
            repair_note = _repair_glitched_value(out, utterance)
            if repair_note:
                log.warning("%s", repair_note)
                out["valueRepairNote"] = repair_note

            # Enforce contradictions the model sometimes emits even at temperature
            # zero. Scope correction is deliberately conservative and uses only the
            # model's verbatim phrase: if that phrase contains an explicit negation,
            # it cannot truthfully remain self_current. This prevents statements such
            # as "मधुमेह नहीं है" from minting diabetes while preserving other
            # mentions in a mixed sentence. Likewise, naming a medication is by the
            # schema's own definition a medical mention.
            explicit_negation = re.compile(
                r"\b(?:do not|don't|dont|never|not|no longer|nahi|nahin)\b"
                r"|नहीं|नही", re.I)
            mentions = out.get("conditionMentions")
            if isinstance(mentions, list):
                valid_mentions = [m for m in mentions if isinstance(m, dict)]
                for mention in valid_mentions:
                    phrase = str(mention.get("phrase") or "")
                    if explicit_negation.search(phrase):
                        mention["scope"] = "negated"
                # Some tool calls shorten the phrase to the disease name and drop
                # the nearby negation. Correct from the whole utterance only when
                # there is exactly one mention and the construction explicitly says
                # the caller does not have it; this intentionally excludes generic
                # forms such as "I don't know".
                single_mention_negation = re.compile(
                    r"\b(?:do not have|don't have|dont have|never had|"
                    r"not diagnosed|nahi hai|nahin hai|nahi tha|nahin tha)\b"
                    r"|नहीं\s+(?:है|था|थी|हूँ)|नही\s+(?:है|था|थी|हूँ)", re.I)
                if (len(valid_mentions) == 1
                        and single_mention_negation.search(utterance or "")):
                    valid_mentions[0]["scope"] = "negated"
            if out.get("medications"):
                out["mentionsCondition"] = True

            # Prefer deterministic parsing of the caller's raw date over a
            # model-normalized value only when the model independently judged the
            # reply to be a usable answer. The parser must never turn a refusal,
            # negation, or correction into affirmative identity evidence.
            if (answer_type == "date" and out.get("understood") is True
                    and not out.get("needsRepeat") and not out.get("isRefusal")):
                raw_date = normalise_calendar_date(utterance)
                if raw_date:
                    out["value"] = raw_date

            # HEIGHT IN FEET, RECOVERED FROM THE RAW WORDS. The model's unit
            # conversion is not deterministic: the identical transcript
            # "5 feet." was reported as not-understood on one attempt and
            # converted correctly on the next, so a caller was apologised at
            # for an answer that was perfectly clear. If the item declares
            # feet/inches coercion and the raw utterance parses as a height,
            # that parse stands - the model's uncertainty is about units, and
            # units are ours to compute.
            if (answer_type == "number"
                    and ((item or {}).get("validation") or {})
                    .get("coerceFromFeetInches")
                    and not out.get("isRefusal")
                    and not out.get("isDontKnow")):
                raw_cm = _feet_inches_to_cm(utterance)
                if raw_cm is not None:
                    validation = (item or {}).get("validation") or {}
                    low = float(validation.get("min", 0))
                    high = float(validation.get("max", 10 ** 9))
                    if low <= raw_cm <= high:
                        if not out.get("understood") or out.get("needsRepeat"):
                            log.info("recovered height %.1fcm from %r after the "
                                     "model reported no usable value",
                                     raw_cm, utterance[:40])
                        out.update({"understood": True, "needsRepeat": False,
                                    "value": raw_cm})
            out["usage"] = resp.get("usage", {})
            return out

    log.warning("model did not call report_understanding")
    return {
        "understood": False, "needsRepeat": True, "confidence": 0.0,
        "detectedLanguage": language, "acknowledgement": "",
    }


# Month spellings, three families per month:
#   1. English ("september", "sep")
#   2. Hindi calendar names ("सितंबर", "सितम्बर")
#   3. PHONETIC DEVANAGARI OF THE ENGLISH WORD ("सप्तेंबर", "सप्टेंबर").
# The third family exists because a live caller answered "5 September 2000" and
# STT - correctly - wrote the English month in Devanagari script, twice, in two
# regional spellings. Neither was in this table, so a correct DOB failed
# verification three times and the call terminated on the caller. STT writes
# what it hears in the script of the surrounding sentence; the table has to
# meet it there.
_MONTH_NUMBERS = {
    "january": 1, "jan": 1, "जनवरी": 1, "जान्युअरी": 1, "जेनुअरी": 1,
    "february": 2, "feb": 2, "फरवरी": 2, "फेब्रुअरी": 2, "फेब्रुवारी": 2,
    "march": 3, "mar": 3, "मार्च": 3,
    "april": 4, "apr": 4, "अप्रैल": 4, "एप्रिल": 4, "अप्रिल": 4,
    "may": 5, "मई": 5, "मे": 5,
    "june": 6, "jun": 6, "जून": 6,
    "july": 7, "jul": 7, "जुलाई": 7, "जुलै": 7,
    "august": 8, "aug": 8, "अगस्त": 8, "ऑगस्ट": 8, "अगस्ट": 8,
    "september": 9, "sep": 9, "sept": 9, "सितंबर": 9, "सितम्बर": 9,
    "सप्तेंबर": 9, "सप्टेंबर": 9, "सेप्टेंबर": 9, "सेप्टेम्बर": 9,
    "सतंबर": 9, "सतम्बर": 9, "सेटम्बर": 9, "सब्टेंबर": 9,
    "october": 10, "oct": 10, "अक्टूबर": 10, "ऑक्टोबर": 10, "अक्तूबर": 10,
    "november": 11, "nov": 11, "नवंबर": 11, "नवम्बर": 11, "नोव्हेंबर": 11,
    "december": 12, "dec": 12, "दिसंबर": 12, "दिसम्बर": 12, "डिसेंबर": 12,
    # FOURTH FAMILY: URDU / ARABIC SCRIPT. Both engines sometimes transcribe
    # Hindi audio in Urdu script - a live caller's "5 September 2000" came back
    # as "5 ستمبر 2020", and the month token was invisible to a matcher that
    # only knew Latin and Devanagari.
    "جنوری": 1, "فروری": 2, "مارچ": 3, "اپریل": 4, "مئی": 5, "جون": 6,
    "جولائی": 7, "اگست": 8, "ستمبر": 9, "اکتوبر": 10, "نومبر": 11, "دسمبر": 12,
}

# Hindi number words, for dates spoken the way people actually say them:
# "पाँच सितंबर दो हजार" carries no digit at all, so a digit-only parser sees no
# day and no year and rejects a perfectly clear date of birth. Composed with the
# सौ / हजार multipliers below, this covers 1-31 for days and the 1900-2010 range
# for birth years.
_HINDI_NUMBER_WORDS = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5,
    "छह": 6, "छः": 6, "छे": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "पन्द्रह": 15, "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19,
    "बीस": 20, "इक्कीस": 21, "बाईस": 22, "बाइस": 22, "तेईस": 23, "तेइस": 23,
    "चौबीस": 24, "पच्चीस": 25, "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28,
    "उनतीस": 29, "तीस": 30, "इकतीस": 31, "इक्तीस": 31,
    "बत्तीस": 32, "तैंतीस": 33, "चौंतीस": 34, "पैंतीस": 35, "छत्तीस": 36,
    "सैंतीस": 37, "अड़तीस": 38, "उनतालीस": 39, "चालीस": 40, "इकतालीस": 41,
    "बयालीस": 42, "तैंतालीस": 43, "चवालीस": 44, "पैंतालीस": 45, "छियालीस": 46,
    "सैंतालीस": 47, "अड़तालीस": 48, "उनचास": 49, "पचास": 50, "इक्यावन": 51,
    "बावन": 52, "तिरपन": 53, "चौवन": 54, "पचपन": 55, "छप्पन": 56,
    "सत्तावन": 57, "अट्ठावन": 58, "उनसठ": 59, "साठ": 60, "इकसठ": 61,
    "बासठ": 62, "तिरसठ": 63, "चौंसठ": 64, "पैंसठ": 65, "छियासठ": 66,
    "सड़सठ": 67, "अड़सठ": 68, "उनहत्तर": 69, "सत्तर": 70, "इकहत्तर": 71,
    "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74, "पचहत्तर": 75, "छिहत्तर": 76,
    "सतहत्तर": 77, "अठहत्तर": 78, "उन्यासी": 79, "अस्सी": 80, "इक्यासी": 81,
    "बयासी": 82, "तिरासी": 83, "चौरासी": 84, "पचासी": 85, "छियासी": 86,
    "सत्तासी": 87, "सतासी": 87, "अट्ठासी": 88, "नवासी": 89, "नब्बे": 90,
    "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94, "पंचानवे": 95,
    "छियानवे": 96, "सत्तानवे": 97, "अट्ठानवे": 98, "निन्यानवे": 99,
}
_HINDI_HUNDRED = {"सौ"}
_HINDI_THOUSAND = {"हजार", "हज़ार"}

# VIRAMA-FOLDED MONTH LOOKUP. STT places the halant almost at random inside a
# transliterated month: a live caller's January came back as "जन्वरी" (ज-न-्-व-र-ी)
# against the table's "जनवरी", and one invisible combining mark cost a correct
# date of birth. Folding it out of BOTH sides makes the whole family of
# halant-shifted spellings match without enumerating them.
_VIRAMA = "\u094d"
_MONTH_LOOKUP = {name.replace(_VIRAMA, ""): number
                 for name, number in _MONTH_NUMBERS.items()}


def _hindi_words_to_digits(text: str) -> str:
    """Rewrite spoken Hindi numbers as digits, leaving everything else alone.

    "पाँच सितंबर दो हजार"  -> "5 सितंबर 2000"
    "उन्नीस सौ सत्तासी"     -> "1987"
    "दो हजार बीस"          -> "2020"
    """
    tokens = text.split()
    out: list[str] = []
    index = 0
    while index < len(tokens):
        total = current = 0
        consumed = 0
        matched = False
        while index + consumed < len(tokens):
            token = tokens[index + consumed].strip(".,;।!?")
            if token in _HINDI_NUMBER_WORDS:
                current += _HINDI_NUMBER_WORDS[token]
            elif token in _HINDI_HUNDRED:
                current = (current or 1) * 100
            elif token in _HINDI_THOUSAND:
                total += (current or 1) * 1000
                current = 0
            else:
                break
            matched = True
            consumed += 1
        if matched:
            out.append(str(total + current))
            index += consumed
        else:
            out.append(tokens[index])
            index += 1
    return " ".join(out)

_ROMAN_HINDI_DATE_NUMBERS = {
    "bais": 22, "baais": 22, "bayis": 22,
    "unnees": 19, "unnis": 19, "unis": 19,
    "sattasi": 87, "sataasi": 87,
}

# ---- fuzzy month recovery ---------------------------------------------------
# Enumerating Devanagari spellings of English month names is a losing game: a
# single live call produced सप्तेंबर, सप्टेंबर, रिटम्बर, सब्टेंबर and सेटम्बर
# for one caller saying "September" five times over a choppy browser leg. STT
# invents a new phonetic rendering per attempt, so unknown-but-unambiguous
# spellings are recovered structurally instead: reduce the token to a consonant
# skeleton (matras and vowels carry the least information and take the most
# damage) and accept it ONLY if exactly one month is clearly closest.
#
# The uniqueness margin is the safety property. A garble equidistant from two
# months (रिटम्बर sits 2 edits from both September and December) stays
# rejected and the caller is re-asked - identical to today's behaviour. And a
# wrongly-recovered month on the DOB item cannot corrupt anything: it fails
# verification against the CRM date of birth and the call fails closed.
_DEVANAGARI_CONSONANTS = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "च": "c", "छ": "c", "ज": "j",
    "झ": "j", "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n", "त": "t",
    "थ": "t", "द": "d", "ध": "d", "न": "n", "प": "p", "फ": "f", "ब": "b",
    "भ": "b", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "s",
    "ष": "s", "स": "s", "ह": "h",
}


def _consonant_skeleton(token: str) -> str:
    """Latin consonant skeleton of a Devanagari or Latin token."""
    out = []
    for ch in token.lower():
        if ch in _DEVANAGARI_CONSONANTS:
            out.append(_DEVANAGARI_CONSONANTS[ch])
        elif ch.isascii() and ch.isalpha() and ch not in "aeiou":
            out.append("k" if ch == "c" else ch)
    return "".join(out)


_MONTH_SKELETONS = {
    name: number for name, number in (
        ("january", 1), ("february", 2), ("march", 3), ("april", 4),
        ("may", 5), ("june", 6), ("july", 7), ("august", 8),
        ("september", 9), ("october", 10), ("november", 11), ("december", 12),
    )
}


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_month(token: str) -> int | None:
    """Recover a month from a garbled token, or None when unsure.

    Accepts only when the best month is within a bounded edit budget AND
    strictly better than the runner-up. Ties and distant matches return None,
    which keeps the caller on the re-ask path rather than guessing.
    """
    skeleton = _consonant_skeleton(token)
    if len(skeleton) < 3:
        return None
    # The first consonant survives garbling far better than the middle of the
    # word (every live September garble kept its opening स), and it is what
    # separates near-skeletons like सतंबर -> stbr from october's ktbr. Weighted
    # TWO edits, not one: at +1 "सतंबर" tied september (distance 2, first letter
    # right) against october (distance 1, first letter wrong) and the tie-break
    # rejected a correct date of birth three times on a live call.
    scored = sorted(
        (_edit_distance(skeleton, _consonant_skeleton(name))
         + (2 if skeleton[0] != _consonant_skeleton(name)[0] else 0), number)
        for name, number in _MONTH_SKELETONS.items()
    )
    best_distance, best_month = scored[0]
    budget = max(1, len(_consonant_skeleton(
        next(n for n, m in _MONTH_SKELETONS.items() if m == best_month))) // 3)
    if best_distance > budget:
        return None
    if scored[1][0] - best_distance < 1:
        return None  # ambiguous between two months: never guess
    return best_month

_ENGLISH_DAY_NUMBERS = {
    "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
    "seven": 7, "seventh": 7, "eight": 8, "eighth": 8, "nine": 9,
    "ninth": 9, "ten": 10, "tenth": 10, "eleven": 11, "eleventh": 11,
    "twelve": 12, "twelfth": 12, "thirteen": 13, "thirteenth": 13,
    "fourteen": 14, "fourteenth": 14, "fifteen": 15, "fifteenth": 15,
    "sixteen": 16, "sixteenth": 16, "seventeen": 17, "seventeenth": 17,
    "eighteen": 18, "eighteenth": 18, "nineteen": 19, "nineteenth": 19,
    "twenty": 20, "twentieth": 20, "twenty one": 21, "twenty first": 21,
    "twenty two": 22, "twenty second": 22, "twenty three": 23,
    "twenty third": 23, "twenty four": 24, "twenty fourth": 24,
    "twenty five": 25, "twenty fifth": 25, "twenty six": 26,
    "twenty sixth": 26, "twenty seven": 27, "twenty seventh": 27,
    "twenty eight": 28, "twenty eighth": 28, "twenty nine": 29,
    "twenty ninth": 29, "thirty": 30, "thirtieth": 30,
    "thirty one": 31, "thirty first": 31,
}


def _day_candidates(text: str) -> list[int]:
    """Return every standalone numeric or spoken value that could be a day."""
    candidates = [int(token) for token in re.findall(r"\b\d{1,2}\b", text)
                  if 1 <= int(token) <= 31]
    candidates.extend(
        number
        for word, number in _ROMAN_HINDI_DATE_NUMBERS.items()
        if number <= 31
        for _match in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", text)
    )

    # Prefer the longest non-overlapping English number phrase so "twenty third"
    # is one candidate, not separate matches for "twenty" and "third".
    english_text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)
    occupied: list[tuple[int, int]] = []
    for phrase, number in sorted(
            _ENGLISH_DAY_NUMBERS.items(), key=lambda row: len(row[0]), reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", english_text):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            candidates.append(number)
    return candidates


def normalise_calendar_date(value: Any) -> str | None:
    """Normalize one unambiguous affirmative calendar date to YYYY-MM-DD."""
    text = str(value or "").strip().lower().replace(",", " ").translate(
        str.maketrans("०१२३४५६७८९", "0123456789"))
    text = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", text)
    text = re.sub(r"\s+", " ", text)
    # Spoken Hindi numbers become digits before anything else looks for a day or
    # a year. Runs after Devanagari-digit translation so both forms are handled.
    text = _hindi_words_to_digits(text)
    # Fold the halant away; see _MONTH_LOOKUP.
    text = text.replace(_VIRAMA, "")

    # Identity evidence cannot contain a repudiation, correction, or alternative.
    # Include contractions explicitly: word boundaries alone split ``isn't`` and
    # previously allowed the repudiated date to authenticate the caller.
    if re.search(
        r"\b(no|not|nahi|nahin|sorry|actually|rather|instead|correction|"
        r"mean|or|cannot|wrong|incorrect|doubt|unsure|maybe)\b|\bi\s+mean\b|"
        r"\b\w+n['’]?t\b|"
        r"नहीं|नही|माफ|सुधार",
        text,
    ):
        return None

    spoken_year_pattern = r"\b(unnees|unnis|unis)\s+sau\s+(sattasi|sataasi)\b"
    year_matches = list(re.finditer(r"\b\d{4}\b", text))
    spoken_year_matches = list(re.finditer(spoken_year_pattern, text))
    month_matches = [
        (match, number)
        for name, number in _MONTH_LOOKUP.items()
        for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text)
    ]
    if not month_matches:
        # Unknown spelling: try the structural recovery, still requiring that
        # exactly one token in the answer resolves to a month. See _fuzzy_month.
        # [A-Za-z] plus the full Devanagari block: \w excludes combining
        # matras (Mn/Mc), so a \w-based token pattern silently splits
        # "सब्टेंबर" at every mark and the matcher never sees the word.
        # Latin + Devanagari + Arabic/Urdu: STT writes the month in whichever
        # script it decided the sentence was in, and all three have been seen
        # for the same spoken word on live calls.
        fuzzy = [
            (match, month)
            for match in re.finditer(
                r"[A-Za-z\u0900-\u097F\u0600-\u06FF]+", text)
            if len(match.group(0)) >= 4
            and (month := _fuzzy_month(match.group(0))) is not None
        ]
        if len(fuzzy) == 1:
            month_matches = fuzzy

    iso_matches = list(re.finditer(
        r"\b(\d{4})[-/]([01]?\d)[-/]([0-3]?\d)\b", text))
    dmy_matches = list(re.finditer(
        r"\b([0-3]?\d)[-/]([01]?\d)[-/](\d{4})\b", text))
    numeric_matches = [(match, "iso") for match in iso_matches]
    numeric_matches.extend((match, "dmy") for match in dmy_matches)

    if len(numeric_matches) > 1:
        return None
    if numeric_matches:
        match, order = numeric_matches[0]
        if month_matches or len(year_matches) != 1 or spoken_year_matches:
            return None
        remainder = text[:match.start()] + " " + text[match.end():]
        if _day_candidates(remainder):
            return None
        if order == "iso":
            year, month, day = map(int, match.groups())
        else:
            day, month, year = map(int, match.groups())
    else:
        if len(month_matches) != 1:
            return None
        if len(year_matches) + len(spoken_year_matches) != 1:
            return None

        month_match, month = month_matches[0]
        if year_matches:
            year_match = year_matches[0]
            year = int(year_match.group(0))
        else:
            year_match = spoken_year_matches[0]
            century, suffix = year_match.groups()
            year = (_ROMAN_HINDI_DATE_NUMBERS[century] * 100
                    + _ROMAN_HINDI_DATE_NUMBERS[suffix])

        # Remove the one month and one year, then require exactly one plausible
        # day token. This rejects shortened alternatives such as ``... or 23rd``
        # even when the second candidate omits the month and year.
        spans = sorted((month_match.span(), year_match.span()), reverse=True)
        remainder = text
        for (start, end) in spans:
            remainder = remainder[:start] + " " + remainder[end:]
        day_candidates = _day_candidates(remainder)
        if len(day_candidates) != 1:
            return None
        day = day_candidates[0]

    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


_WORD_SMALL_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "half": 0.5,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5,
    "panch": 5, "chhe": 6, "che": 6, "saat": 7, "aath": 8,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6,
    "छे": 6, "सात": 7, "आठ": 8,
}

_FEET_UNIT = r"(?:feet|foot|ft|फीट|फुट|')"
_INCH_UNIT = r"(?:inches|inch|in|इंच|\")"


def _feet_inches_to_cm(value: Any) -> float | None:
    """Deterministically convert a feet/inches answer to centimetres.

    Returns None when the value does not read as feet/inches, so plain
    centimetre answers pass through untouched. A bare number in the human
    feet range (3-8) counts as feet: on an item whose unit is cm, "5" can
    only mean five feet - no adult is five centimetres tall.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value) * 30.48, 1) if 3 <= float(value) <= 8 else None

    text = str(value or "").strip().lower()
    if not text:
        return None
    for word, number in _WORD_SMALL_NUMBERS.items():
        text = re.sub(rf"(?<![\w\u0900-\u097F]){re.escape(word)}"
                      rf"(?![\w\u0900-\u097F])", str(number), text)

    match = re.search(
        rf"(\d+(?:\.\d+)?)\s*{_FEET_UNIT}\s*(?:(\d+(?:\.\d+)?)\s*{_INCH_UNIT}?)?",
        text)
    if match:
        feet = float(match.group(1))
        inches = float(match.group(2) or 0)
        if 0 < feet <= 8 and 0 <= inches < 12:
            return round(feet * 30.48 + inches * 2.54, 1)
        return None

    # Bare small number as text ("5", "पाँच" after word mapping): feet range.
    bare = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\.?", text)
    if bare and 3 <= float(bare.group(1)) <= 8:
        return round(float(bare.group(1)) * 30.48, 1)
    return None


def _spoken_years(text: str) -> list[int]:
    """Every plausible four-digit year in a piece of speech, in order said."""
    flat = str(text or "").translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    # STT writes years with thousands separators ("2,019"), which breaks \b\d{4}\b.
    flat = re.sub(r"(?<=\d),(?=\d)", "", flat)
    this_year = time.gmtime().tm_year
    out = []
    for token in re.findall(r"(?<!\d)(\d{4})(?!\d)", flat):
        year = int(token)
        # Deliberately narrower than the date branch's bounds. This runs over free
        # text, where a four-digit number can be a dosage ("2000 mg"), so only
        # years a living person could be describing an event in are considered.
        if 1900 <= year <= this_year:
            out.append(year)
    return out


_YEAR_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_YEAR_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,
              "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
              "ninety": 90}


def _under_hundred(tokens: list[str]) -> int | None:
    """'eighty seven' -> 87, 'fifteen' -> 15, 'twenty' -> 20."""
    if not tokens:
        return None
    if len(tokens) == 1:
        token = tokens[0]
        return _YEAR_UNITS.get(token, _YEAR_TENS.get(token))
    if len(tokens) == 2 and tokens[0] in _YEAR_TENS:
        unit = _YEAR_UNITS.get(tokens[1])
        if unit is not None and 1 <= unit <= 9:
            return _YEAR_TENS[tokens[0]] + unit
    return None


def spoken_year(text: Any) -> str | None:
    """
    The year in an answer, as four digits, whether it was said in digits or words.

    A live caller answered "when were you first diagnosed?" with "Twenty fifteen."
    and the record kept the words - a medical history saying a condition began in
    "Twenty fifteen" cannot be sorted, compared, or underwritten.

    Speech recognition writes what it hears. Asked for a year, people say
    "twenty fifteen", "nineteen eighty seven", or "two thousand and five", and
    only sometimes "2015".
    """
    digits = _spoken_years(text)
    if digits:
        return str(digits[0])
    words = re.sub(r"[^a-z\s]", " ", str(text or "").lower()).split()
    this_year = time.gmtime().tm_year

    # "two thousand", "two thousand five", "two thousand and fifteen"
    for i in range(len(words) - 1):
        if words[i] == "two" and words[i + 1] == "thousand":
            rest = words[i + 2:i + 5]
            if rest and rest[0] == "and":
                rest = rest[1:]
            value = _under_hundred(rest[:2])
            if value is None:
                value = _under_hundred(rest[:1])
            year = 2000 + (value or 0)
            if 1900 <= year <= this_year:
                return str(year)

    # "nineteen eighty seven", "twenty fifteen", "twenty twenty"
    for i, word in enumerate(words):
        century = {"nineteen": 19, "twenty": 20}.get(word)
        if century is None:
            continue
        for span in (2, 1):
            value = _under_hundred(words[i + 1:i + 1 + span])
            # A single digit after the century is rejected: "twenty five" is
            # twenty-five of something, not the year 2005, which is said "two
            # thousand five" or "twenty oh five". Guessing here would turn a
            # quantity into a date.
            if value is not None and value >= 10:
                year = century * 100 + value
                if 1900 <= year <= this_year:
                    return str(year)
    return None


def years_said(text: str) -> list[int]:
    """Every year a caller could be heard to have said - digits OR words.

    WHY THIS EXISTS, from a live transcript that contradicted itself in two
    consecutive notes on ONE turn:

        recorded the spoken year 'Two thousand fifteen se' as 2015
        UNDERWRITER ATTENTION: year 2015 was not spoken by the caller

    Both notes came from this module and only one can be true. normalise_spoken_year
    understands word-form years, so it correctly turned "Two thousand fifteen" into
    2015; ground_spoken_year then checked that against _spoken_years, which only
    finds FOUR-DIGIT tokens, found none, and reported the year as un-spoken. The
    record was right and the flag was wrong - which is the worse direction, because
    UNDERWRITER ATTENTION is the mechanism for routing a doubtful answer to a human
    and it is worth nothing if it fires on correct ones.

    Digits first and unchanged, so a caller who said "2015" behaves exactly as
    before. The word-form is consulted only when there are no digits at all: that
    covers the reported case without letting spoken_year's single-year answer
    silently override a digit year in the same sentence.
    """
    digits = _spoken_years(text)
    if digits:
        return digits
    spoken = spoken_year(text)
    return [int(spoken)] if spoken else []


def normalise_spoken_year(value: Any) -> tuple[Any, str | None]:
    """Rewrite a word-form year as digits, leaving everything else alone."""
    if not isinstance(value, str) or not value.strip():
        return value, None
    if _spoken_years(value):
        return value, None      # already carries a usable four-digit year
    year = spoken_year(value)
    if not year:
        # A relative answer ("two years ago") or no year at all. Both are the
        # caller's own words and stay exactly as they are.
        return value, None
    return year, f"recorded the spoken year {value.strip()!r} as {year}"


def ground_spoken_year(value: Any, utterance: str) -> tuple[Any, str | None]:
    """
    Replace a year in a stored answer with the year the caller actually said.

    THE SYMPTOM THAT PROMPTED THIS, from a live call's stored record:

        turn 59, caller: "2019, I told you already"
        A520 stored:     "2015"

    A520 asks when a hospitalisation was, and the record kept 2015 while the same
    call stored K80::FU_DIAG_WHEN = "In 2019" - the record contradicting itself
    about one event, which is the one thing a medical history used for
    underwriting must never do.

    NOT THE ROOT CAUSE OF THAT PARTICULAR TURN, and the distinction matters to
    anyone reading this later. 2015 was not a model error: it was the asthma
    diagnosis year, copied into A520 by the protocol auto-fill, which picked one
    of three conditions' "since" answers arbitrarily. That is fixed where it
    belongs, in orchestrator._followup_answer_for_field.

    This function is the second line: free text is the only answer type with no
    arithmetic to check, so a year that IS mis-heard or invented has nothing
    standing between it and the record. The caller's words are the evidence, and
    on this point they are unambiguous - a four-digit year is not something
    speech recognition mishears into a different valid year.

    ONLY APPLIED TO ANSWERS THE SEED MARKS formField "since", the two questions
    that ask for a point in time (A520 and FU_DIAG_WHEN). Applying it to every
    free-text answer would corrupt dosages: "Metformin 2000 mg" said alongside
    "since 2019" would have its dose rewritten to the year.

    Substitutes only when the caller said exactly one distinct year. Two years
    ("2015 or 2016") is a genuine ambiguity and the model's reading of it is
    better than an arbitrary pick, so it is left alone and reported instead.

    WORD-FORM YEARS COUNT AS SPOKEN. This originally scanned the utterance for
    four-digit tokens only, so a caller who said "Two thousand fifteen" - whose
    year normalise_spoken_year had just correctly recorded as 2015 - was reported
    as never having said it. Two notes on one turn, flatly contradicting each
    other, and the FLAG was the wrong one. Now it also lets a mis-heard year be
    corrected to a word-form one, which the digits-only version could not do.
    """
    if not isinstance(value, str) or not value.strip():
        return value, None
    # years_said, NOT _spoken_years: the caller may have said the year in words,
    # and this function runs AFTER normalise_spoken_year has turned those words
    # into digits on the value side. Comparing digits against a digits-only scan
    # of the utterance made every word-form year look invented. See years_said.
    said = years_said(utterance)
    kept = _spoken_years(value)
    if not kept:
        # A relative answer ("two years ago", "बीस साल पहले") carries no year to
        # check. Nothing to do - and nothing to add, because the caller did not
        # give one.
        return value, None
    distinct = sorted(set(said))
    if not distinct:
        # The model produced a year out of nothing the caller said. Cannot repair
        # it without inventing a second time; flag it for the human reviewer.
        return value, (f"year {kept[0]} was not spoken by the caller")
    if set(kept) <= set(distinct):
        return value, None
    if len(distinct) > 1:
        return value, (f"year {kept[0]} differs from the years spoken "
                       f"{distinct}")
    spoken = distinct[0]
    grounded = re.sub(r"(?<!\d)\d{4}(?!\d)",
                      lambda m: (str(spoken)
                                 if 1900 <= int(m.group()) <= time.gmtime().tm_year
                                 else m.group()),
                      value)
    return grounded, (f"corrected year {kept[0]} to the spoken {spoken}"
                      if grounded != value else None)


def coerce(value: Any, answer_type: str, item: dict | None = None) -> tuple[Any, str | None]:
    """
    Validate and coerce an extracted value in code.

    Deliberately not left to the model. A number outside a plausible range, or an
    enum value that is not in the option list, is caught here so a mis-hearing
    cannot become a medical record entry.
    """
    item = item or {}
    if value is None:
        return None, "no value"

    if answer_type == "date":
        # A YEAR NO HUMAN CAN HAVE. A live caller said "30th September 3000" as a
        # date of birth and it went straight onto the record, because dates were
        # typed "text" and text has no notion of impossible. STT mangles years
        # more than most tokens (this one was probably "2000"), so the check is a
        # transcription guard as much as an input one. Bounds are generous - the
        # oldest verified human was 122 - and the future is excluded outright:
        # nobody being underwritten today was born tomorrow.
        # ANY four-digit token is judged as a year. The first version enumerated
        # "plausible-looking" year shapes (18xx-21xx, 3000+), which meant 1500
        # sailed through untouched - a caller demonstrated exactly that. If a
        # four-digit number appears in a date answer, it is the year, and the
        # bounds below are the arbiter of possible; the regex must not be a
        # second, sloppier arbiter.
        # Normalised before matching: STT writes years as "1,500" (the comma
        # broke \b\d{4}\b and let 1500 through a second time) and Hindi
        # transcripts can carry Devanagari digits.
        canonical = normalise_calendar_date(value)
        if not canonical:
            return None, "date must include a valid day, month, and four-digit year"
        year = int(canonical[:4])
        this_year = time.gmtime().tm_year
        if year > this_year:
            return None, f"year {year} is in the future"
        if year < this_year - 120:
            return None, f"year {year} is more than 120 years ago"
        # Item-specific age floor. A caller's "3019" was transcribed as
        # "30 September 2019", which passes the human-lifetime bounds and
        # would have recorded a seven-year-old policyholder. The item says
        # who can plausibly be answering it: dob_verify carries
        # minAgeYears 18, because a minor cannot hold this policy.
        min_age = ((item or {}).get("validation") or {}).get("minAgeYears")
        if min_age:
            born = _dt.date.fromisoformat(canonical)
            today = _dt.date.today()
            age = today.year - born.year - ((today.month, today.day)
                                            < (born.month, born.day))
            if age < int(min_age):
                return None, (f"date {canonical} would make the policyholder under "
                              f"{min_age}")
        return canonical, None

    if answer_type == "boolean":
        if isinstance(value, bool):
            return value, None
        s = str(value).strip().lower()
        if s in ("true", "yes", "haan", "1", "y"):
            return True, None
        if s in ("false", "no", "nahi", "nahin", "0", "n"):
            return False, None
        return None, f"not a yes/no value: {value!r}"

    if answer_type == "number":
        v = item.get("validation") or {}
        # HEIGHT IN FEET IS CONVERTED HERE, IN CODE. The extraction prompt asks
        # the model to convert ("Five foot five is 165 centimetres") and on a
        # live call the same "5 feet." answer failed twice and succeeded on the
        # third attempt - LLM unit conversion is not deterministic. The schema
        # has declared validation.coerceFromFeetInches on the height item all
        # along; this is the code that finally honours it.
        if v.get("coerceFromFeetInches"):
            converted = _feet_inches_to_cm(value)
            if converted is not None:
                value = converted
        try:
            n = float(value)
        except (TypeError, ValueError):
            return None, f"not a number: {value!r}"
        if "min" in v and n < float(v["min"]):
            return None, f"{n} below plausible minimum {v['min']}"
        if "max" in v and n > float(v["max"]):
            return None, f"{n} above plausible maximum {v['max']}"
        return (int(n) if n == int(n) else n), None

    if answer_type == "enum":
        opts = item.get("options") or []
        s = str(value).strip().lower()
        for o in opts:
            if s == str(o).lower():
                return o, None
        return None, f"{value!r} not one of {opts}"

    if answer_type == "list":
        # PLACEHOLDERS ARE NOT ANSWERS. A live call answered the tobacco detail
        # question with "Cigarette toys a week" - the caller's words, badly
        # transcribed - and the model returned
        # ['cigarette', 'toys a week', 'duration not specified'].
        #
        # That third element was never said by anybody. The question asks for three
        # things (form, amount, duration) and the model filled the missing one in
        # with a note about its own uncertainty, which then sat in a medical record
        # looking like content. An underwriter reading the row cannot tell an
        # invented element from a spoken one.
        #
        # Dropping them is safe: if every element is a placeholder the list comes
        # out empty and the question is re-asked, which is the honest outcome.
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()
                     and not _LIST_PLACEHOLDER.fullmatch(str(x).strip())]
            return (items or None), (None if items else
                                     "no answer given, only placeholders")
        s = str(value).strip()
        if s and _LIST_PLACEHOLDER.fullmatch(s):
            return None, "no answer given, only a placeholder"
        return ([s], None) if s else (None, "empty list")

    # EMAIL HAD NO BRANCH, so anything at all was accepted into an email field. A
    # live call closed with A990 - answerType "email" - holding the string "True",
    # because the closing item reads as a yes/no question ("I will email a summary
    # to X, is that right?") and the model answered the question rather than
    # returning the address. A confirmation is not an address, and an address that
    # is not one is worse than a missing one: the summary silently goes nowhere.
    if answer_type == "email":
        s = str(value).strip().strip("<>").rstrip(".,;।")
        if _EMAIL.fullmatch(s):
            return s, None
        normalised = " ".join(s.lower().split())
        # A YES MEANS "USE THE ADDRESS YOU JUST READ TO ME", AND IT MUST RESOLVE
        # TO THAT ADDRESS.
        #
        # Treating a confirmation as an error was the previous behaviour, added
        # because the model once returned the word "True" and it was stored as if
        # it were an address. But the item reads as a yes/no question - "I will
        # email a summary to X, is that the right address?" - so yes is the
        # commonest correct answer, and rejecting it re-asked three times and then
        # stored not_captured. Measured on a live call: the caller said "Yes"
        # three times, was apologised at three times, and the summary address
        # ended up empty - exactly the silent-nowhere outcome the old comment set
        # out to prevent.
        #
        # The address is known: it was read aloud from the customer record. The
        # orchestrator passes it in as onFileEmail, so the confirmation resolves
        # to a real address rather than to the word "yes" or to nothing.
        if normalised in _EMAIL_AFFIRM:
            on_file = str(item.get("onFileEmail") or "").strip()
            if _EMAIL.fullmatch(on_file):
                return on_file, None
            # Nothing on file to confirm, so the confirmation is meaningless and
            # the caller has to say the address.
            return None, ("confirmed an address, but there is none on file to "
                          "confirm")
        # A NO is not an address either, but it means something different: the one
        # on file is wrong. Re-asking is right, and the reason says why.
        if normalised in _EMAIL_DENY:
            return None, ("the address on file was rejected; the caller needs to "
                          "say the correct one")
        return None, f"{value!r} is not an email address"

    # ABHA HAD NO TYPE, so it was free text and free text accepts anything. A live
    # call closed with A920 - the national health ID field - holding
    # 'हुई थी क्या मेरी?', which is the caller asking "was mine done?". They
    # answered the question with a question and it was filed as their health
    # identifier.
    #
    # An identifier field has to validate its identifier. Three outcomes only:
    # a real ABHA, an explicit decline on a field the prompt itself calls
    # optional, or a rejection that re-asks. After the bounded attempts the
    # orchestrator stores "not_captured", which is an honest empty and infinitely
    # better than a Hindi question sitting in an ID field.
    if answer_type == "abha":
        raw = str(value).strip()
        if raw.lower() in {"true", "false"}:
            # The model answered the yes/no shape of "do you have one?" instead of
            # returning the identifier. That is not an ID, and it is not a decline.
            return None, f"{value!r} is a confirmation, not an ABHA number"
        flat = _ABHA_SEPARATORS.sub("", raw)
        if _ABHA_ADDRESS.fullmatch(flat):
            return flat.lower(), None
        # Nobody recites a bare identifier. Real callers wrap it - "haan, mera
        # number hai 1234 5678 9012 34" - so the number is EXTRACTED from the
        # sentence rather than required to be the whole of it. Separators are
        # collapsed only BETWEEN digits, so surrounding words cannot fuse into the
        # run and inflate its length.
        collapsed = re.sub(r"(?<=\d)[\s\-\u2013\u2014.]+(?=\d)", "", raw)
        runs = re.findall(r"\d+", collapsed)
        exact = [r for r in runs if _ABHA_NUMBER.fullmatch(r)]
        if len(exact) == 1:
            return exact[0], None
        # Declines are recorded as a decline, not re-asked and not escalated. The
        # prompt offers an escape word, so honouring that is the contract. It
        # offers "no" rather than "skip" - the question is a yes/no question, so
        # "no" is what a caller reaches for - but BOTH stay accepted here, because
        # the prompt wording is free to change and a caller who says either one
        # has answered.
        # Checked AFTER the digits, so "no wait, it's 1234..." is read as the
        # number the caller corrected themselves into, not as a refusal.
        normalised = " ".join(raw.lower().replace("?", " ").split())
        if normalised in _ABHA_DECLINE or not normalised:
            return "declined", None
        if not runs and _ABHA_NEGATION.search(normalised):
            # "No, I do not have an ABHA number" is a decline stated in a full
            # sentence. Requiring the bare word "no" would re-ask a caller who
            # already answered clearly. Safe only because there are no digits to
            # mistake for an identifier.
            return "declined", None
        if runs:
            longest = max(runs, key=len)
            return None, (f"{longest!r} has {len(longest)} digits; an ABHA number "
                          f"has exactly 14")
        return None, f"{value!r} is not an ABHA number or ABHA address"

    s = str(value).strip()
    return (s, None) if s else (None, "empty string")
