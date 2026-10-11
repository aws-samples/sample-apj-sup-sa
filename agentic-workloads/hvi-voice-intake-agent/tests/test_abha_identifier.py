#!/usr/bin/env python3
"""An identifier field must validate its identifier.

THE FAILURE THIS FIXES, from a live call's stored record:

    A920 = 'हुई थी क्या मेरी?'        ("was mine done?")

A920 is the ABHA field - the Ayushman Bharat Health Account number, India's
national health identifier. The caller answered the question with a question, and
because the item was typed "text" it was filed as their health ID. "text" is
extract.coerce's permissive fallback: it accepts any non-empty string, which is
right for a free-text answer and wrong for an identifier.

WHY NOT A GENERAL QUESTION DETECTOR, which was the first instinct: it would have
to judge every free-text answer in the form, and a false positive there rejects a
real answer. Validating one identifier against its own documented format is
narrower, deterministic, and cannot misfire on unrelated items.

FORMAT, per the National Health Authority: 14 digits. Callers reading it aloud
pause, and speech recognition renders pauses as spaces or dashes, so separators
are stripped before counting. Every ABHA number also has a default address of the
form 14-digit@abdm or @sbx, and some people know themselves by that instead.

DECLINES ARE NOT REFUSALS. The prompt offers an escape word, so that word has to
work - and it must NOT route through the extractor's isRefusal signal, which means
"declined to answer OR wants to stop the call" and makes the agent offer to end
the call. Declining one optional identifier is not hanging up.

The prompt now offers "no" rather than "skip", because the question is phrased as
a yes/no question and a live call answered it with नहीं twice. Both words are
still accepted: see test_abha_prompt_offers_a_word_the_extractor_accepts, which
pins the invariant that whatever the prompt offers must be something coerce()
actually maps to a decline.

Run:  ./.venv/bin/python tests/test_abha_identifier.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))

import extract  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:170]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:  # noqa: PLR0915
    print("[1] the exact value that broke the live record")
    val, err = extract.coerce("\u0939\u0941\u0908 \u0925\u0940 \u0915\u094d"
                              "\u092f\u093e \u092e\u0947\u0930\u0940?", "abha")
    check("a question is REJECTED, not stored as a health ID",
          val is None and bool(err), f"value={val!r} err={err!r}")
    check("and the rejection says why",
          bool(err) and "ABHA" in str(err), str(err))

    print("\n[2] genuine ABHA numbers are accepted, however they were spoken")
    for said, want in (
            ("12345678901234", "12345678901234"),
            ("1234 5678 9012 34", "12345678901234"),
            ("12-34-5678-9012-34", "12345678901234"),
            ("  91 1234 5678 9012  ", "911234567890 12".replace(" ", "")),
    ):
        val, err = extract.coerce(said, "abha")
        check(f"{said!r} -> 14 digits", val == want and err is None,
              f"value={val!r} err={err!r}")

    print("\n[3] ABHA addresses are accepted too")
    for said in ("12345678901234@abdm", "priya.sharma@sbx", "PRIYA_S@ABDM"):
        val, err = extract.coerce(said, "abha")
        check(f"{said!r} accepted", val is not None and err is None,
              f"value={val!r} err={err!r}")
    val, _ = extract.coerce("PRIYA_S@ABDM", "abha")
    check("addresses are normalised to lower case", val == "priya_s@abdm", str(val))

    print("\n[4] declining is honoured, on a field the prompt calls optional")
    for said in ("skip", "no", "nahi hai", "\u0928\u0939\u0940\u0902",
                 "I don't have one", "yaad nahi", "\u0938\u094d\u0915\u093f\u092a"):
        val, err = extract.coerce(said, "abha")
        check(f"{said!r} -> declined, no reprompt",
              val == "declined" and err is None, f"value={val!r} err={err!r}")

    print("\n[4b] declines stated as a sentence, which is how people actually talk")
    # tests/test_positive_disclosures.py scripts exactly the first of these. A
    # validator that only matched the bare word "no" would re-ask a caller who had
    # already answered plainly.
    for said in ("No, I do not have an ABHA number",
                 "no I don't have an ABHA",
                 "Sorry, I have not made one yet",
                 "nahi, mera ABHA nahi bana hai",
                 "\u0928\u0939\u0940\u0902, \u092e\u0947\u0930\u093e ABHA "
                 "\u0928\u0939\u0940\u0902 \u0939\u0948"):
        val, err = extract.coerce(said, "abha")
        check(f"{said[:38]!r} -> declined", val == "declined" and err is None,
              f"value={val!r} err={err!r}")

    print("\n[4c] a number embedded in a sentence is still the number")
    for said, want in (
            ("haan, mera number hai 1234 5678 9012 34", "12345678901234"),
            ("My ABHA number is 12345678901234.", "12345678901234"),
            ("it's 91-1234-5678-9012 I think", "911234567890 12".replace(" ", "")),
            # A self-correction opens with a negation AND carries the identifier.
            # Digits must win, or the agent discards what the caller just gave it.
            ("no wait, it is 1234 5678 9012 34", "12345678901234"),
    ):
        val, err = extract.coerce(said, "abha")
        check(f"{said[:38]!r} -> {want}", val == want and err is None,
              f"value={val!r} err={err!r}")

    print("\n[5] near-misses are rejected rather than half-stored")
    for said, why in (("123456789", "too few digits"),
                      ("123456789012345678", "too many digits"),
                      ("my abha is somewhere at home", "prose, not an identifier"),
                      ("priya@gmail.com", "an email, not an ABHA address")):
        val, err = extract.coerce(said, "abha")
        check(f"{said[:30]!r} rejected ({why})", val is None and bool(err),
              f"value={val!r} err={err!r}")
    _, err = extract.coerce("123456789", "abha")
    check("a wrong-length number says how many digits it had",
          bool(err) and "9 digits" in str(err), str(err))

    print("\n[6] a yes/no is neither an ID nor a decline")
    # The item reads as "do you have one?", so the model sometimes answers the
    # shape of the question. That is the same defect A990 had, where answerType
    # "email" received the string "True".
    for said in ("True", "true", "False"):
        val, err = extract.coerce(said, "abha")
        check(f"{said!r} rejected as a confirmation",
              val is None and "confirmation" in str(err),
              f"value={val!r} err={err!r}")

    print("\n[7] the form and the contract agree")
    form = json.loads((ROOT / "seed" / "form-arogya-sanjeevani-v1.json").read_text())
    a920 = next(i for i in form["items"] if i["itemId"] == "A920")
    check("A920 is typed abha, not text", a920.get("answerType") == "abha",
          str(a920.get("answerType")))
    check("A920 is still optional", bool(a920.get("optional")), str(a920))
    src = (ROOT / "tests" / "test_seed_contracts.py").read_text()
    check("the seed contract permits the new type", '"abha"' in src,
          "SUPPORTED_ANSWER_TYPES was not updated, so seeding would reject A920")
    hint = extract._type_hint("abha", a920)  # noqa: SLF001
    check("the model is told the format", "14" in hint and "abdm" in hint.lower(),
          hint[:120])

    print("\n[7b] the prompt offers a word the extractor actually honours")
    # A live call sat in 17 seconds of silence here. The prompt's only offered
    # escape was "skip", while the question itself is a yes/no question - and the
    # caller answered the question, with नहीं, twice. The prompt now offers "no".
    #
    # The invariant is the general one, not the wording: whatever word a prompt
    # tells the caller they may say, coerce() has to map to a decline. Offering
    # "pass" or "later" without teaching the extractor would fail here, which is
    # the failure this is for.
    OFFERED = {
        "prompt": ("no", "en"),
        "promptHi": ("\u0928\u0939\u0940\u0902", "hi"),
        "promptHinglish": ("nahi", "hinglish"),
    }
    for field, (word, language) in OFFERED.items():
        text = a920.get(field) or ""
        check(f"A920 {field} offers {word!r} to the caller", word in text.lower(),
              f"{language} prompt does not contain it: {text!r}")
        val, err = extract.coerce(word, "abha")
        check(f"and coerce({word!r}) -> declined", val == "declined" and err is None,
              f"value={val!r} err={err!r}")

    # Changing the offered word must not withdraw the old one. Anyone who learned
    # to say "skip" from an earlier call, or reads it in the SMS, still gets heard.
    for said in ("skip", "skip it", "\u0938\u094d\u0915\u093f\u092a"):
        val, err = extract.coerce(said, "abha")
        check(f"{said!r} is still honoured after the reword",
              val == "declined" and err is None, f"value={val!r} err={err!r}")

    # The positive branch has to be actionable too: the old prompt never told the
    # caller they could read the number out, which is the other half of a 17-second
    # silence - not knowing what the alternative was.
    check("A920 prompt says what to do if they DO have one",
          "read it out" in (a920.get("prompt") or "").lower(),
          a920.get("prompt"))

    print("\n[8] no other item silently relies on text for an identifier")
    # A cheap guard against the same mistake returning: any item whose label looks
    # like an identifier should not be free text.
    suspects = [i for i in form["items"]
                if i.get("answerType") == "text"
                and any(w in (i.get("label") or "").lower()
                        for w in ("number", "id", "abha", "aadhaar", "policy"))]
    check("no identifier-looking item is still typed text",
          not suspects, str([i["itemId"] for i in suspects]))

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} ABHA checks passed.")
    print("  A national health ID field now accepts an ID, honours a skip, and")
    print("  refuses everything else instead of filing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
