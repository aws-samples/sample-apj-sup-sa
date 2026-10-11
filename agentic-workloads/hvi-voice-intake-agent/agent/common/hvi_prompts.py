"""Fixed agent utterances that are NOT part of the form schema.

WHY THIS MODULE EXISTS
Almost everything the agent says comes from the form schema in DynamoDB, which is
what lets a health item be added at runtime without a redeployment. The
language-selection question cannot live there: it is asked BEFORE the questionnaire
starts, it is not an intake answer, and putting it in the form would put it in the
answers map, the remaining-item count and the underwriting record - three places it
has no business being.

But it also cannot just be a constant inside the orchestrator, because two separate
processes need the identical string:

  agent/orchestrator.py      speaks it on the first turn
  tools/build_prompt_cache.py  pre-synthesises it so that first turn is not a
                               2-3 second silence while a GPU warms up

If those two drift by one character the cache key stops matching and the miss is
invisible - the call still works, just with the dead air this cache exists to
remove, on the single most sensitive utterance in the call. So the text lives here,
once, and both import it.

Shipped to the agent container by scripts/40-agentcore.sh, alongside hvi_state.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# language selection
#
# BILINGUAL, AND NEITHER HALF IS A TRANSLATION OF THE OTHER. Each half addresses a
# different listener: the English sentence is for someone who does not read
# Devanagari, the Hindi one for someone who does not speak English. Translating one
# into the other would leave one of those two callers with no instruction they can
# act on. "Namaste" opens because both understand it.
#
# TWO OPTIONS, NOT THREE. Hinglish is deliberately absent from the menu - nobody
# answers "which language?" with "code-mixed". It remains fully supported as INPUT
# in either mode; see the note in agent/orchestrator.py.
# --------------------------------------------------------------------------- #
# IT INTRODUCES THE CALLER BEFORE ASKING ANYTHING.
#
# The first version opened straight into "For English, please say English", which a
# live caller correctly objected to: someone who has just answered an unknown number
# is owed who is calling before being asked to do anything. An unexplained menu from
# an unidentified number is what a scam sounds like, and on a health-insurance call
# that costs trust it cannot get back.
#
# The introduction is in Hindi and the instruction pair is bilingual, which is not
# an oversight: the company name is a proper noun and lands identically in either
# language, so an English speaker still hears WHO is calling and then hears an
# English instruction telling them what to say.
#
# Kept to roughly 20 words / ~8s. It is dead air from the caller's point of view
# until it finishes, and tests/diag_language_prompt_audio.py warns past 9s.
#
# "नमस्ते" IS SPELT IN DEVANAGARI DELIBERATELY. Written "Namaste" it synthesised as
# "नमस्ट" - clipped - because a Latin-spelt Hindi word is read as though it were
# English. Caught by tests/diag_language_prompt_audio.py.
# SYMMETRIC. THE INTRODUCTION IS SAID IN BOTH LANGUAGES, NOT JUST HINDI.
#
# The first version introduced the insurer in Hindi only and then offered a
# bilingual instruction pair. The defence for that was that a company name is a
# proper noun and lands the same in either language, and a live caller correctly
# rejected it: the name may survive, but the SENTENCE around it does not. An English
# speaker heard a Hindi sentence, understood none of it, and only then got told they
# could ask for English - which is precisely the "unexplained call from an unknown
# number" experience the introduction exists to avoid.
#
# So each caller now hears who is calling and why in their own language, before
# being asked to choose. The name is repeated rather than shared between the two
# halves; that costs about a second and buys a greeting that works for both.
# The Hindi half is a FULL SENTENCE with a verb. An earlier revision compressed it
# to "{{insurerName}} से, आपके हेल्थ इंश्योरेंस के बारे में।" - a verbless
# headline that a listener rightly called bad Hindi. Nobody talks in noun phrases,
# and the greeting is the one utterance every single caller hears.
LANGUAGE_CHOICE_PROMPT = (
    "नमस्ते। मैं {{insurerName}} से बात कर रही हूँ, "
    "आपके हेल्थ इंश्योरेंस एप्लीकेशन के बारे में। "
    "This is {{insurerName}}, about your health insurance application. "
    # ONE SHORT QUESTION THAT CANNOT BE ANSWERED "YES". This ending has been
    # rewritten three times, each fixing the previous failure: instruction pairs
    # made whichever language came last sound like the request, and the longer
    # "क्या आप हिंदी में बात करना चाहेंगे या English में?" is grammatically a
    # yes/no question - callers answered "हाँ", which names no language, which
    # forced a re-ask, which is the "why does it ask twice" complaint. Naming
    # just the two options invites naming one back and nothing else.
    "हिंदी, या English?"
)

# Shorter: by the time this is heard the caller has had the long version and knows
# who is calling, so the likely problem is the line or the accent, not the wording.
# A QUESTION, mirroring the greeting's own ending - "Please say English" read as an
# instruction to speak English, which a caller reasonably objected to.
LANGUAGE_CHOICE_REPROMPT = "हिंदी या English?"

# Matches the orchestrator's fallback. Shared so the cache builder and the runtime
# fill the template identically - if they disagreed by one character the opening
# utterance would miss the cache on every call and be synthesised live.
DEFAULT_INSURER = "Meridian Health Cover"


def language_choice_prompt(insurer_name: str | None = None) -> str:
    """The opening question, with the insurer filled in."""
    return LANGUAGE_CHOICE_PROMPT.replace(
        "{{insurerName}}", (insurer_name or DEFAULT_INSURER).strip())

# Synthesised from the HINDI reference clip, not the English one.
#
# The utterance is mixed-script, so one of the two references has to render text it
# was not cloned from. Hindi is the right one to keep: Devanagari spoken by a voice
# cloned from an English reference is degraded for precisely the callers who need
# that half, whereas the English half spoken by the Hindi-referenced voice merely
# comes out Indian-accented - which is correct for an Indian insurer anyway.
LANGUAGE_PROMPT_TTS_LANGUAGE = "hi"

def cacheable(insurer_names: list[str] | None = None) -> list[tuple[str, str, str]]:
    """Everything here that is fixed, shareable text, for the cache builder.

    The opening question carries an insurer name, which would normally make it
    PERSONALISED and therefore uncacheable - and that would be the worst possible
    utterance to synthesise live, since it is the first thing a caller hears and
    live synthesis is ~2.9s of what sounds like a dead line.
    
    It stays cacheable because the cache is CONTENT-ADDRESSED: once the name is
    substituted the text is stable per insurer, so each insurer is simply its own
    cache entry. Expanding over the known names here costs one object each and keeps
    the opening instant. An insurer not listed degrades to live synthesis for that
    one utterance rather than breaking.

    A function rather than a constant so adding a prompt above without caching it is
    a visible omission rather than a silent regression to dead air.
    """
    names = insurer_names or [DEFAULT_INSURER]
    out: list[tuple[str, str, str]] = []
    for name in dict.fromkeys(n for n in names if n):  # de-dupe, keep order
        out.append((LANGUAGE_PROMPT_TTS_LANGUAGE, language_choice_prompt(name),
                    f"language.prompt[{name}]"))
    out.append((LANGUAGE_PROMPT_TTS_LANGUAGE, LANGUAGE_CHOICE_REPROMPT,
                "language.reprompt"))
    return out
