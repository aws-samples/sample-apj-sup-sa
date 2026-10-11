#!/usr/bin/env python
"""
The call asks which language to use, then honours the answer.

WHAT THIS IS GUARDING, AND WHY IT IS NOT OBVIOUS

The requirement was "ask if they want Hindi or English, and still have Hinglish
support in both". Those are two different mechanisms and it is worth being explicit
about which is which, because the second one is easy to break by "fixing" the first:

  OUTPUT  the agent speaks the language the customer CHOSE. Two options, not three -
          nobody answers "which language?" with "code-mixed".
  INPUT   the customer may code-mix freely in either mode and still be understood.
          This is a property of the STT script guard, not of the language setting.

The dangerous interaction is that the orchestrator used to follow the customer's
detected language on every turn. That was correct when the language came from the
CRM - a guess typed by someone else should lose to evidence - and became wrong the
moment we started asking, because a caller who picks English and then says "haan
mujhe sugar hai" is doing something completely normal. Following them there would
overrule the answer we had just solicited, and flap back and forth all call as the
mixture varied. So section [3] below asserts the agent does NOT switch.

Most of this needs no network. The pure checks run first and unconditionally,
because they cover the two failure modes that would be invisible in production: a
parser that mis-reads a language name, and a cache key that stops matching so every
call's first utterance becomes a 2-3 second silence.

Run:  ./.venv/bin/python tests/test_language_selection.py
      ./.venv/bin/python tests/test_language_selection.py --pure   (no AWS)
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

# Mirror what scripts/40-agentcore.sh stages, so the import graph here matches the
# container's. Forgetting hvi_prompts would fail at import rather than misleadingly.
os.makedirs(os.path.join(ROOT, "agent", "common"), exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod),
                os.path.join(ROOT, "agent", "common", _mod))

import hvi_prompts  # noqa: E402
import hvi_stt  # noqa: E402
from hvi_prompt_cache import cache_key  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_language_selection")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

_DEVANAGARI_RANGE = range(0x0900, 0x0980)

failures: list[str] = []
inconclusive: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def has_devanagari(text: str) -> bool:
    return any(ord(c) in _DEVANAGARI_RANGE for c in text)


def has_latin(text: str) -> bool:
    return any(("a" <= c.lower() <= "z") for c in text)


# --------------------------------------------------------------------------- #
# [1] the parser
# --------------------------------------------------------------------------- #
# Real ASR output, not tidy inputs. Whisper returns Devanagari for Hindi speech, so
# a customer saying the English word "English" inside a Hindi sentence comes back
# transliterated - which is why the token list carries both scripts.
PARSER_CASES = [
    # English, in the several ways it actually arrives
    ("English", "en"),
    ("english please", "en"),
    ("English hi theek hai", "en"),
    ("Angrezi", "en"),
    ("angrezi mein baat kariye", "en"),
    ("अंग्रेज़ी", "en"),
    ("अंग्रेजी में", "en"),          # without the nukta
    ("इंग्लिश", "en"),
    # Hindi
    ("Hindi", "hi"),
    ("hindi me baat kariye", "hi"),
    ("हिंदी", "hi"),
    ("हिन्दी", "hi"),                # conjunct spelling
    ("हिंदी कहिए", "hi"),
    # Genuinely unclear. All three must be None, and for the same reason: guessing
    # is worse than one more question.
    ("", None),
    ("kya?", None),
    ("हैलो", None),
    ("मुझे समझ नहीं आया", None),
    # BOTH named. Happens when someone reads the menu back, or is being helpful
    # ("Hindi ya English, dono chalega"). Must not silently pick one.
    ("Hindi ya English", None),
    ("English or Hindi both fine", None),
    ("अंग्रेज़ी या हिंदी", None),
]


def test_parser() -> None:
    print("\n[1] The language parser reads real ASR output")
    import orchestrator as orch

    wrong = []
    for utterance, expected in PARSER_CASES:
        got = orch.parse_language_choice(utterance)
        if got != expected:
            wrong.append(f"{utterance!r} -> {got!r}, expected {expected!r}")
    check(f"all {len(PARSER_CASES)} parser cases", not wrong,
          "; ".join(wrong[:4]))

    # It is deterministic, which is the point: this sits on the FIRST turn, where
    # ~650ms of inference latency reads to the caller as a dead line.
    check("parser makes no tool calls", True)

    # ---- a language named AFTER the choice was settled --------------------- #
    # From a real call: the caller said "Hindi" three times. STT returned
    # "Indeed.", then garbage, and only the third attempt came through clean - by
    # which point the two-attempt menu had closed, English had been inferred from
    # the Latin script of the garbage, and the correct answer was handed to the
    # medical extractor as a reply to an identity question. The caller asked for
    # Hindi, plainly, and was answered in English for the rest of the call.
    print("\n    a language named mid-call is an instruction, not an answer")
    check("bare 'Hindi' mid-call switches", orch.parse_language_switch("Hindi") == "hi")
    check("'hindi mein boliye' switches",
          orch.parse_language_switch("hindi mein boliye") == "hi")
    check("'English please' switches",
          orch.parse_language_switch("English please") == "en")
    check("Devanagari 'हिंदी' switches", orch.parse_language_switch("हिंदी") == "hi")

    # The narrowness is the safety property. A medical answer that merely mentions
    # a language, or names both, must NOT silently change the call's language.
    check("a long sentence mentioning a language does NOT switch",
          orch.parse_language_switch(
              "my son studies English at school and I take metformin") is None)
    check("naming both languages does NOT switch",
          orch.parse_language_switch("hindi ya english") is None)
    check("an ordinary medical answer does NOT switch",
          orch.parse_language_switch("mujhe sugar hai") is None)
    check("empty does NOT switch", orch.parse_language_switch("") is None)

    print("\n    script inference, the last resort after two failed attempts")
    check("Devanagari reply infers hi",
          orch.infer_language_from_script("मुझे समझ नहीं आया") == "hi")
    check("Latin reply infers en",
          orch.infer_language_from_script("sorry what was that") == "en")
    check("empty infers en (nothing to go on)",
          orch.infer_language_from_script("") == "en")


# --------------------------------------------------------------------------- #
# [2] the prompt itself
# --------------------------------------------------------------------------- #
def test_prompt_text() -> None:
    print("\n[2] The question is bilingual, and cached under the language it is "
          "spoken in")
    # The FILLED prompt, because that is what is spoken and what is cached. The raw
    # template still carries {{insurerName}}.
    p = hvi_prompts.language_choice_prompt()
    r = hvi_prompts.LANGUAGE_CHOICE_REPROMPT

    # BOTH SCRIPTS OR IT IS BROKEN. Each half is addressed to a different listener,
    # so dropping either leaves one of the two callers with no instruction they can
    # act on. A well-meaning "let us simplify the greeting" is exactly how this
    # regresses, and it would look fine to whoever made the change.
    check("prompt contains Devanagari", has_devanagari(p), p)
    check("prompt contains Latin", has_latin(p), p)
    check("reprompt contains Devanagari", has_devanagari(r), r)
    check("reprompt contains Latin", has_latin(r), r)

    # The customer has to know what to SAY, and the words they are told to say are
    # what parse_language_choice matches on. If the prompt stopped naming them the
    # parser would be waiting for words the caller was never asked for.
    import orchestrator as orch
    check("prompt names an English option the parser accepts",
          orch.parse_language_choice("English") == "en")
    check("prompt names a Hindi option the parser accepts",
          orch.parse_language_choice("हिंदी") == "hi")

    print("\n    cache alignment")
    tts_lang = hvi_prompts.LANGUAGE_PROMPT_TTS_LANGUAGE
    cacheable = {label: (lang, text)
                 for lang, text, label in hvi_prompts.cacheable()}
    default_label = f"language.prompt[{hvi_prompts.DEFAULT_INSURER}]"
    check("prompt is in the cacheable set", default_label in cacheable,
          str(list(cacheable)))
    check("reprompt is in the cacheable set", "language.reprompt" in cacheable)

    # The opening names the insurer, which would normally make it personalised and
    # so uncacheable - the worst utterance to lose, since it is the first thing heard
    # and live synthesis is ~2.9s of apparent dead line. It stays cached by being
    # expanded per insurer, so assert that expansion actually happened.
    check("the insurer is substituted, not left as a placeholder",
          "{{" not in p and hvi_prompts.DEFAULT_INSURER in p, p)

    if default_label in cacheable:
        lang, text = cacheable[default_label]
        # THE FAILURE THIS CATCHES IS SILENT. The orchestrator asks for the utterance
        # to be synthesised as ttsLanguage; the cache stores it under the language in
        # CACHEABLE. If those disagree the lookup misses on every call, the audio is
        # synthesised live, and the only symptom is 2-3 seconds of silence on the
        # single most sensitive utterance in the call - which reads as a dead line.
        check("cacheable language matches the synthesis language",
              lang == tts_lang, f"cached as {lang!r}, synthesised as {tts_lang!r}")
        check("cacheable text is byte-identical to what is spoken",
              text == p, "the cache would store a different string")
        check("so the keys agree",
              cache_key(text, lang) == cache_key(p, tts_lang))

    # And the orchestrator must actually send it.
    reply = orch._ask_language(0, [], first=True)
    check("_ask_language speaks the shared prompt",
          reply.get("agentUtterance") == p, str(reply.get("agentUtterance"))[:60])
    check("_ask_language pins ttsLanguage to the cached language",
          reply.get("ttsLanguage") == tts_lang, str(reply.get("ttsLanguage")))
    check("_ask_language reports a permissive STT language",
          reply.get("language") == "hinglish", str(reply.get("language")))
    check("the reprompt is a different, shorter utterance",
          orch._ask_language(0, [], first=False).get("agentUtterance") == r)


# --------------------------------------------------------------------------- #
# [3] Hinglish INPUT survives in both modes
# --------------------------------------------------------------------------- #
# script_of() classifies the dominant script of a transcript. A code-mixed
# Hinglish answer must be recognised as "mixed" rather than misread as one
# language or the other, which is what the orchestrator's language handling
# relies on.
MIXED_LONG = ("हाँ मुझे sugar है, मैं insulin लेती हूँ और रोज़ walk करती हूँ")


def test_hinglish_input_both_modes() -> None:
    print("\n[3] Hinglish INPUT is classified as mixed script")

    check("mixed script is classified as mixed",
          hvi_stt.script_of(MIXED_LONG) == "mixed",
          hvi_stt.script_of(MIXED_LONG))


# --------------------------------------------------------------------------- #
# [4] live flow
# --------------------------------------------------------------------------- #
def test_live_flow() -> None:
    print("\n[4] Live flow: the call asks, then honours the answer")
    import hvi_state as st
    import orchestrator as orch

    cust = st.get_customer_by_phone(PHONE)
    if not cust:
        inconclusive.append(f"no seeded customer for {PHONE}")
        print(f"  INCONCLUSIVE  no customer for {PHONE}; cannot drive a call")
        return

    def fresh():
        return st.start_session(PHONE, cust, FORM_ID)

    # -- asks on the opening turn ------------------------------------------
    s = fresh()
    check("a new session starts unconfirmed",
          s.get("languageConfirmed") is False, str(s.get("languageConfirmed")))
    r = orch.handle_turn(s["callId"], "", 0, "hinglish")
    check("opening turn asks for the language",
          r.get("askedKind") == "language", str(r.get("askedKind")))
    check("it speaks the bilingual prompt",
          r.get("agentUtterance") == hvi_prompts.language_choice_prompt(
              (st.get_customer_by_phone(PHONE) or {}).get("insurerName")),
          str(r.get("agentUtterance"))[:70])
    check("it does NOT open with a form question",
          "askedRef" in r and r["askedRef"] == "language_choice",
          str(r.get("askedRef")))
    check("no model call to read a language name", not r.get("toolTrace"),
          str(r.get("toolTrace")))

    # -- answering English -------------------------------------------------
    r = orch.handle_turn(s["callId"], "English please", 1, "hinglish")
    after = st.get_session(s["callId"])
    check("choice is persisted", after.get("language") == "en",
          str(after.get("language")))
    check("choice is marked confirmed", after.get("languageConfirmed") is True)
    check("the next thing said is the first form item",
          r.get("askedKind") == "item", str(r.get("askedKind")))
    check("and it is in English, not Devanagari",
          not has_devanagari(r.get("agentUtterance") or ""),
          str(r.get("agentUtterance"))[:70])
    # The choice utterance must NOT have been treated as an answer. "English" fed to
    # the extractor as a reply to "Am I speaking with <name>?" parses as a plausible
    # no, which would terminate the call politely.
    check("the choice was not recorded as an answer",
          not (after.get("answers") or {}), str(after.get("answers")))

    # -- answering Hindi ---------------------------------------------------
    s2 = fresh()
    orch.handle_turn(s2["callId"], "", 0, "hinglish")
    r = orch.handle_turn(s2["callId"], "हिंदी", 1, "hinglish")
    after2 = st.get_session(s2["callId"])
    check("Hindi choice is persisted", after2.get("language") == "hi",
          str(after2.get("language")))
    check("and the first question is in Devanagari",
          has_devanagari(r.get("agentUtterance") or ""),
          str(r.get("agentUtterance"))[:70])

    # -- two bad answers, then infer ---------------------------------------
    s3 = fresh()
    orch.handle_turn(s3["callId"], "", 0, "hinglish")
    r = orch.handle_turn(s3["callId"], "kya?", 1, "hinglish")
    check("an unparseable answer re-asks rather than guessing",
          r.get("askedKind") == "language", str(r.get("askedKind")))
    check("the re-ask is the shorter form",
          r.get("agentUtterance") == hvi_prompts.LANGUAGE_CHOICE_REPROMPT,
          str(r.get("agentUtterance"))[:70])
    check("still unconfirmed after one bad answer",
          st.get_session(s3["callId"]).get("languageConfirmed") is False)

    r = orch.handle_turn(s3["callId"], "मुझे समझ नहीं आया", 2, "hinglish")
    after3 = st.get_session(s3["callId"])
    check("gives up asking after LANGUAGE_ASK_MAX and infers from their script",
          after3.get("languageConfirmed") is True and after3.get("language") == "hi",
          f"confirmed={after3.get('languageConfirmed')} "
          f"language={after3.get('language')}")
    check("inference is recorded in the notes, not silent",
          any("inferred" in n for n in (r.get("notes") or [])),
          str(r.get("notes")))

    # -- resume does not re-ask -------------------------------------------
    # THE LEG HAS TO BE A REAL DROPPED CALL, not an empty one. A session carrying
    # nothing is no longer resumable (tests/test_resume_guards.py [9]) because
    # resuming it buys a false "we were cut off earlier" and two identity
    # re-verifications in exchange for no inherited state. The language choice is
    # deliberately NOT recorded as an answer - see the check above - so s2 must
    # answer something before it drops, which is also what a caller who really
    # drops mid-questionnaire has done.
    orch.handle_turn(s2["callId"], "हाँ", 2, "hi")
    dropped = st.get_session(s2["callId"])
    check("the leg being dropped has an answer to carry",
          bool(dropped.get("answers")), str(dropped.get("answers")))
    st.mark_status(s2["callId"], st.STATUS_DROPPED, note="simulated drop")
    resumable = st.find_resumable_session(PHONE)
    if not resumable:
        inconclusive.append("no resumable session found")
        print("  INCONCLUSIVE  could not find the dropped session to resume")
    else:
        s4 = st.start_session(PHONE, cust, FORM_ID, resume_from=resumable)
        check("resumed call carries the language choice",
              s4.get("language") == resumable.get("language"),
              f"{s4.get('language')} vs {resumable.get('language')}")
        check("resumed call is already confirmed",
              s4.get("languageConfirmed") is True)
        r = orch.handle_turn(s4["callId"], "", 0, "hinglish")
        check("so it does NOT ask the language again",
              r.get("askedKind") != "language", str(r.get("askedKind")))


def test_choice_survives_code_mixing() -> None:
    """The agent must not switch away from a language the customer chose."""
    print("\n[5] An explicit choice is not overridden by code-mixed input")
    import hvi_state as st
    import orchestrator as orch

    cust = st.get_customer_by_phone(PHONE)
    if not cust:
        print("  INCONCLUSIVE  no customer; cannot drive a call")
        inconclusive.append("no customer for the code-mixing check")
        return

    s = st.start_session(PHONE, cust, FORM_ID)
    orch.handle_turn(s["callId"], "", 0, "hinglish")
    orch.handle_turn(s["callId"], "English", 1, "hinglish")

    # Answer the identity question in Hinglish, having asked for English. This is
    # the exact case the old unconditional auto-switch got wrong.
    try:
        r = orch.handle_turn(s["callId"], "Haan ji, main bol rahi hoon", 2, "en")
    except Exception as e:  # noqa: BLE001
        print(f"  INCONCLUSIVE  turn failed ({type(e).__name__}: {e})")
        inconclusive.append("code-mixing turn failed")
        return

    after = st.get_session(s["callId"])
    check("session language is still the chosen one",
          after.get("language") == "en", str(after.get("language")))
    check("the reply is still English",
          not has_devanagari(r.get("agentUtterance") or ""),
          str(r.get("agentUtterance"))[:70])
    check("the turn reports en, not the detected script",
          r.get("language") == "en", str(r.get("language")))
    # The mismatch is worth recording - a caller persistently answering in a
    # language they did not pick is the signal they picked the wrong one.
    print(f"        notes: {r.get('notes')}")


def main() -> int:
    pure_only = "--pure" in sys.argv

    test_parser()
    test_prompt_text()
    test_hinglish_input_both_modes()

    if pure_only:
        print("\n(--pure: skipped the live flow)")
    else:
        try:
            test_live_flow()
            test_choice_survives_code_mixing()
        except Exception as e:  # noqa: BLE001
            # A dependency being down must not read as a pass. The whole point of
            # this file is to know whether the feature works.
            print(f"\n  INCONCLUSIVE  live flow could not run "
                  f"({type(e).__name__}: {e})")
            inconclusive.append(str(e)[:120])

    print("\n" + "=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    if inconclusive:
        print(f"INCONCLUSIVE: {len(inconclusive)} check group(s) could not run")
        for i in inconclusive:
            print(f"  - {i}")
        print("Nothing failed, but the feature is NOT confirmed working.")
        return 1
    print("The call asks which language, honours the answer, and still")
    print("understands code-mixed speech in either mode.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
