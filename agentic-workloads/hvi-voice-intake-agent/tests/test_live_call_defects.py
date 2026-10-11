#!/usr/bin/env python3
"""Defects found by reading the transcript of one English voice call.

Call call-00000000-0000-0000-0000-000000000000, form arogya-sanjeevani-demo-v1,
23 answers. Four things in the stored record were wrong. Each is pinned here with
the caller's actual words, because every one of them looked plausible in the
record and only the transcript showed it was not.

1. A990, THE EMAIL CONFIRMATION, ENDED not_captured.
   Turns 84-89: the agent read the address on file and asked "is that the right
   address?". The caller said "Yes". Three times. Each time the agent apologised -
   "Sorry, I didn't quite catch that" - and after the third the record stored
   not_captured. The item was correctly refusing to file the word "yes" as an
   address, but it had nothing to put in its place, so a question the caller
   answered on the first attempt ended up blank.

2. A520, THE HOSPITALISATION YEAR, DISAGREED WITH THE RECORD'S OWN FOLLOW-UP.
   Turn 59, caller: "2019, I told you already". Stored: 2015. The same call stored
   K80::FU_DIAG_WHEN = "In 2019", so the record contradicted itself about a single
   event. answerType "text" has no arithmetic to check, so nothing noticed.

3. A410, THE TOBACCO DETAIL, CONTAINED AN ELEMENT NOBODY SAID.
   Stored: ['cigarette', 'toys a week', 'duration not specified']. The first two
   are the caller's words as transcribed. The third is the model reporting its own
   uncertainty, filed as though the caller had said it. An underwriter reading the
   row cannot tell an invented element from a spoken one.

4. A920, THE ABHA FIELD, STORED THE WORD 'Skip' VERBATIM.
   The abha answerType added for the previous live-call defect handles this - it
   resolves a skip to "declined". The demo form was built before that type existed
   and was never rebuilt, so the fix was live in the production form and absent
   from the form the demo actually calls.

Run:  ./.venv/bin/python tests/test_live_call_defects.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))

import extract  # noqa: E402
import orchestrator  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:170]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


ON_FILE = {"onFileEmail": "priya.sharma@example.com"}


def test_email_confirmation() -> None:
    print("[1] the exact exchange that ended in not_captured")
    val, err = extract.coerce("Yes", "email", ON_FILE)
    check("'Yes' resolves to the address that was read out",
          val == ON_FILE["onFileEmail"] and err is None, f"value={val!r} err={err!r}")

    print("\n[1b] every way a caller confirms, including a Hindi yes on an English call")
    for said in ("Yes", "yes", "yeah", "yep", "correct", "that's right",
                 "Confirmed", "  YES  ", "true", "True",
                 "haan", "haan ji", "ji haan", "theek hai", "bilkul",
                 "\u0939\u093e\u0901", "\u091c\u0940 \u0939\u093e\u0901",
                 "\u0920\u0940\u0915 \u0939\u0948"):
        val, err = extract.coerce(said, "email", ON_FILE)
        check(f"{said!r} -> the on-file address",
              val == ON_FILE["onFileEmail"] and err is None,
              f"value={val!r} err={err!r}")

    print("\n[1c] a real address still wins over anything on file")
    for said, want in (
            ("user@example.com", "user@example.com"),
            ("  <other@example.co.in>  ", "other@example.co.in"),
            ("priya.sharma@example.com.", "priya.sharma@example.com"),
    ):
        val, err = extract.coerce(said, "email", ON_FILE)
        check(f"{said.strip()!r} -> {want}", val == want and err is None,
              f"value={val!r} err={err!r}")

    print("\n[1d] a NO is not the on-file address - it means that one is wrong")
    for said in ("no", "No", "nope", "wrong", "that is wrong", "nahi",
                 "\u0928\u0939\u0940\u0902", "\u0917\u0932\u0924"):
        val, err = extract.coerce(said, "email", ON_FILE)
        check(f"{said!r} -> rejected, and not silently the on-file one",
              val is None and bool(err), f"value={val!r} err={err!r}")
    _, err = extract.coerce("no", "email", ON_FILE)
    check("and the reason says the caller must give the right one",
          bool(err) and "correct one" in str(err), str(err))

    print("\n[1e] confirming when there is nothing on file cannot invent an address")
    for meta in ({}, {"onFileEmail": None}, {"onFileEmail": ""},
                 {"onFileEmail": "not-an-address"}):
        val, err = extract.coerce("yes", "email", meta)
        check(f"onFileEmail={meta.get('onFileEmail')!r} -> rejected, not fabricated",
              val is None and bool(err), f"value={val!r} err={err!r}")

    print("\n[1f] the original defect this branch was added for still cannot recur")
    # Before the email branch existed the string "True" was stored as an address.
    # It must never be stored as one - resolving to the on-file address is fine,
    # but the literal must not survive.
    val, _ = extract.coerce("True", "email", ON_FILE)
    check("'True' is never stored as the address itself", val != "True", repr(val))
    for said in ("hello", "priya at example dot com", "42", "yes please send it"):
        val, err = extract.coerce(said, "email", ON_FILE)
        check(f"{said!r} rejected as not an address",
              val is None and bool(err), f"value={val!r} err={err!r}")


def test_spoken_year() -> None:
    print("\n[2] the exact wrong year, from turn 59")
    val, note = extract.ground_spoken_year("2015", "2019, I told you already")
    check("the caller's 2019 replaces the model's 2015", val == "2019", repr(val))
    check("and the correction is recorded in the notes",
          bool(note) and "2019" in str(note), str(note))

    print("\n[2b] the year is corrected in place, leaving the caller's phrasing")
    for value, utterance, want in (
            ("Around 2015", "it was 2019", "Around 2019"),
            ("In 2015", "2019", "In 2019"),
            ("surgery in 2015, I think", "no it was 2019", "surgery in 2019, I think"),
            # Devanagari digits, and a thousands separator STT likes to insert.
            ("2015", "\u0968\u0966\u0967\u0669 mein", "2019"),
            ("2015", "2,019", "2019"),
    ):
        got, _ = extract.ground_spoken_year(value, utterance)
        check(f"{value!r} + {utterance!r} -> {want!r}", got == want, repr(got))

    print("\n[2c] agreement is left completely alone")
    for value, utterance in (
            ("2019", "2019, I told you already"),
            ("In 2019", "it was in 2019"),
            ("Around 2015", "around 2015 I think"),
            ("2015", "2015 or maybe 2015"),
    ):
        got, note = extract.ground_spoken_year(value, utterance)
        check(f"{value!r} unchanged when the caller said the same year",
              got == value and note is None, f"got={got!r} note={note!r}")

    print("\n[2d] a relative answer has no year to check and must not gain one")
    for value, utterance in (
            ("two years ago", "about two years ago"),
            ("\u092c\u0940\u0938 \u0938\u093e\u0932 \u092a\u0939\u0932\u0947",
             "\u092c\u0940\u0938 \u0938\u093e\u0932 \u092a\u0939\u0932\u0947"),
            ("last year", "last year, 2019 was fine"),
            ("recently", "2019"),
    ):
        got, note = extract.ground_spoken_year(value, utterance)
        check(f"{value!r} unchanged", got == value, f"got={got!r} note={note!r}")

    print("\n[2e] genuine ambiguity is reported, not resolved by guessing")
    got, note = extract.ground_spoken_year("2017", "2015 or 2016, I am not sure")
    check("two spoken years leave the value alone", got == "2017", repr(got))
    check("and say what the caller actually said",
          bool(note) and "2015" in str(note) and "2016" in str(note), str(note))

    print("\n[2f] a year the caller never said at all is flagged for a human")
    got, note = extract.ground_spoken_year("2015", "quite a while back")
    check("value kept (nothing to replace it with)", got == "2015", repr(got))
    check("and flagged as unspoken",
          bool(note) and "not spoken" in str(note), str(note))

    print("\n[2g] four-digit numbers that are not years are not touched")
    this_year = time.gmtime().tm_year
    for value, utterance in (
            ("Metformin 2000 mg", "metformin 2000 mg since 2019"),
            ("9999", "2019"),
            ("1500 units", "1500 units in 2019"),
    ):
        got, _ = extract.ground_spoken_year(value, utterance)
        if value == "9999":
            check(f"{value!r} is out of year range, left alone", got == value, repr(got))
        else:
            check(f"{value!r} keeps its dosage", got == value, repr(got))
    check("the year window ends at the current year", this_year >= 2026, str(this_year))

    print("\n[2h] non-strings and empties are handled without raising")
    for value in (None, "", "   ", 2015, ["2015"]):
        got, note = extract.ground_spoken_year(value, "2019")
        check(f"{value!r} passes through untouched", got == value,
              f"got={got!r} note={note!r}")


def test_list_placeholders() -> None:
    print("\n[3] the exact fabricated element, from A410")
    val, err = extract.coerce(
        ["cigarette", "toys a week", "duration not specified"], "list")
    check("the invented element is dropped",
          val == ["cigarette", "toys a week"] and err is None,
          f"value={val!r} err={err!r}")

    print("\n[3b] the shapes a model uses to report its own uncertainty")
    for placeholder in ("duration not specified", "not specified", "unspecified",
                        "amount not mentioned", "quantity not stated",
                        "years not provided", "frequency not given", "N/A", "n/a",
                        "not applicable", "none specified", "unknown duration",
                        "  Duration Not Specified  "):
        val, _ = extract.coerce(["cigarette", placeholder], "list")
        check(f"{placeholder!r} dropped", val == ["cigarette"], repr(val))

    print("\n[3c] a list that is nothing but placeholders is no answer at all")
    val, err = extract.coerce(["duration not specified", "N/A"], "list")
    check("empty result, so the question is re-asked",
          val is None and bool(err), f"value={val!r} err={err!r}")
    val, err = extract.coerce("not specified", "list")
    check("a bare placeholder string is also no answer",
          val is None and bool(err), f"value={val!r} err={err!r}")

    print("\n[3d] real answers are untouched, including ones that merely contain 'not'")
    for said, want in (
            (["cigarettes", "10 a day", "15 years"],
             ["cigarettes", "10 a day", "15 years"]),
            (["beer", "four bottles a week", "ten years"],
             ["beer", "four bottles a week", "ten years"]),
            # "not" appears but the element carries real information, so a
            # substring match would have destroyed it. fullmatch is why it does not.
            (["salbutamol, not daily"], ["salbutamol, not daily"]),
            (["duration not specified by doctor"], ["duration not specified by doctor"]),
            (["Pantoprazole 40mg once a day"], ["Pantoprazole 40mg once a day"]),
            ("gutkha", ["gutkha"]),
    ):
        val, err = extract.coerce(said, "list")
        check(f"{said!r} preserved", val == want and err is None,
              f"value={val!r} err={err!r}")

    print("\n[3e] the model is also told not to invent elements")
    hint = extract._type_hint("list", {})  # noqa: SLF001
    check("the list type hint forbids padding",
          "not specified" in hint.lower() and "only" in hint.lower(), hint[:150])


def test_since_gate() -> None:
    print("\n[4] the seed marks exactly the questions that ask for a point in time")
    # ground_spoken_year is only safe where a four-digit number must be a year.
    # That gate is formField "since", which the seed already declares. If a
    # quantity question ever gains it, this test fails rather than the record
    # quietly gaining a rewritten dosage.
    form = json.loads((ROOT / "seed" / "form-arogya-sanjeevani-v1.json").read_text())
    kg = json.loads((ROOT / "seed" / "health-kg.json").read_text())
    since_items = [i for i in form["items"] if i.get("formField") == "since"]
    since_fus = [q for q in kg["followUpQuestions"] if q.get("formField") == "since"]
    check("A520 is the form's since field",
          [i["itemId"] for i in since_items] == ["A520"],
          str([i["itemId"] for i in since_items]))
    check("FU_DIAG_WHEN is the graph's since field",
          [q["id"] for q in since_fus] == ["FU_DIAG_WHEN"],
          str([q["id"] for q in since_fus]))
    check("every since field is free text, which is what needs grounding",
          all(i.get("answerType") in ("text", "date") for i in since_items)
          and all(q.get("answerType") in ("text", "date") for q in since_fus),
          str([(i["itemId"], i.get("answerType")) for i in since_items]))

    quantity = [i["itemId"] for i in form["items"]
                if i.get("formField") == "since"
                and i.get("answerType") in ("number", "list")]
    check("no quantity question is marked since", not quantity, str(quantity))

    src = (ROOT / "agent" / "orchestrator.py").read_text()
    check("the orchestrator gates grounding on that field",
          'formField") == "since"' in src and "ground_spoken_year" in src,
          "grounding is not wired to the since gate")


def test_readback_phrasing() -> None:
    print("\n[6] the read-back the customer has to verify must be sayable")
    # Read to a live customer, verbatim, at the moment they were asked to confirm
    # the record was correct:
    #     "Gallstones, since In 2019, treatment Pantoprazole 40 milligrams..."
    sys.path.insert(0, str(ROOT / "agent"))
    import orchestrator as orch  # noqa: PLC0415

    check("the exact broken phrase is gone",
          orch._since_phrase("In 2019", "en") == "since 2019",  # noqa: SLF001
          orch._since_phrase("In 2019", "en"))  # noqa: SLF001

    print("\n[6b] English: one preposition, never two")
    for said, want in (
            ("In 2019", "since 2019"),
            ("in 2019", "since 2019"),
            ("2019", "since 2019"),
            ("from 2015", "since 2015"),
            ("since 2015", "since 2015"),
            ("on 5 May 2015", "since 5 May 2015"),
    ):
        got = orch._since_phrase(said, "en")  # noqa: SLF001
        check(f"{said!r} -> {want!r}", got == want, repr(got))

    print("\n[6c] a hedge is kept, because dropping it makes a guess sound exact")
    for said, want in (
            ("Around 2015", "since around 2015"),
            ("around 2015", "since around 2015"),
            ("About 2015", "since about 2015"),
            ("approximately 2015", "since approximately 2015"),
            ("two years ago", "since two years ago"),
    ):
        got = orch._since_phrase(said, "en")  # noqa: SLF001
        check(f"{said!r} -> {want!r}", got == want, repr(got))

    print("\n[6d] Hindi: से is a postposition, so the year comes first")
    for said, want in (
            ("2019", "2019 \u0938\u0947"),
            ("2019 \u0938\u0947", "2019 \u0938\u0947"),
            ("2019 \u092e\u0947\u0902", "2019 \u0938\u0947"),
            ("\u0932\u0917\u092d\u0917 2015", "2015 \u0938\u0947"),
    ):
        got = orch._since_phrase(said, "hi")  # noqa: SLF001
        check(f"{said!r} -> {want!r}", got == want, repr(got))
    check("Hindi never emits the English word order",
          not orch._since_phrase("2019", "hi").startswith("\u0938\u0947"),  # noqa: SLF001
          orch._since_phrase("2019", "hi"))  # noqa: SLF001

    print("\n[6e] Hinglish likewise")
    for said, want in (("2019", "2019 se"), ("2019 se", "2019 se"),
                       ("In 2019", "2019 se")):
        got = orch._since_phrase(said, "hinglish")  # noqa: SLF001
        check(f"{said!r} -> {want!r}", got == want, repr(got))

    print("\n[6e2] the postposition strip must not eat the end of a real word")
    # "\\s*(?:se)\\s*$" matched the last two letters of "the case" and rewrote it
    # to "the ca", because \\s* happily matches nothing at all.
    for said, want_tail in (("the case", "the case"),
                            ("remission since the relapse", "the relapse"),
                            ("2015, in the course", "the course")):
        got = orch._since_phrase(said, "en")  # noqa: SLF001
        check(f"{said!r} keeps its last word", got.endswith(want_tail), repr(got))

    print("\n[6f] an empty value contributes nothing rather than a bare preposition")
    for said in (None, "", "   ", "in ", "\u0938\u0947"):
        got = orch._since_phrase(said, "en")  # noqa: SLF001
        check(f"{said!r} -> '' (omitted from the read-back)", got == "", repr(got))


def test_no_double_courtesy() -> None:
    print("\n[10] the agent must not thank the caller twice in one breath")
    # Heard on every call: "Okay, thank you. Thank you. This call is recorded..."
    # The per-turn acknowledgement and the authored consent prompt both open with
    # a thanks.
    sys.path.insert(0, str(ROOT / "agent"))
    import orchestrator as orch  # noqa: PLC0415

    consent = "Thank you. This call is recorded and the information is used only"
    got = orch._dedupe_courtesy("Okay, thank you.", consent)  # noqa: SLF001
    check("the doubled thanks is dropped",
          got == "This call is recorded and the information is used only", repr(got))

    print("\n[10b] in all three languages")
    for ack, text, want in (
            ("Okay, thank you.", "Thank you. This call is recorded.",
             "This call is recorded."),
            ("\u0920\u0940\u0915 \u0939\u0948, \u0927\u0928\u094d\u092f\u0935"
             "\u093e\u0926\u0964",
             "\u0927\u0928\u094d\u092f\u0935\u093e\u0926\u0964 \u092f\u0939 "
             "\u0915\u0949\u0932 \u0930\u093f\u0915\u0949\u0930\u094d\u0921 "
             "\u0939\u094b \u0930\u0939\u0940 \u0939\u0948\u0964",
             "\u092f\u0939 \u0915\u0949\u0932 \u0930\u093f\u0915\u0949\u0930"
             "\u094d\u0921 \u0939\u094b \u0930\u0939\u0940 \u0939\u0948\u0964"),
            ("Theek hai, dhanyavaad.", "Dhanyavaad. Yeh call record ho rahi hai.",
             "Yeh call record ho rahi hai."),
    ):
        got = orch._dedupe_courtesy(ack, text)  # noqa: SLF001
        check(f"{ack[:22]!r} + {text[:22]!r}", got == want, repr(got))

    print("\n[10c] a thanks that is NOT duplicated must survive")
    for ack, text, why in (
            ("Okay.", "Thank you. This call is recorded.",
             "the ack did not thank, so the prompt's thanks is the only one"),
            ("", "Thank you. This call is recorded.", "no ack at all"),
            ("Got it.", "Thank you. Next question.", "ack has no thanks"),
    ):
        got = orch._dedupe_courtesy(ack, text)  # noqa: SLF001
        check(f"kept: {why}", got == text, repr(got))

    print("\n[10d] and a question that never opened with thanks is untouched")
    for ack, text in (
            ("Okay, thank you.", "And your current weight in kilograms?"),
            ("Thank you.", "Do you use tobacco in any form?"),
            ("Okay, thank you.", ""),
    ):
        got = orch._dedupe_courtesy(ack, text)  # noqa: SLF001
        check(f"{text[:34]!r} unchanged", got == text, repr(got))


def test_spoken_word_years() -> None:
    print("\n[9] a year said in words is still a year")
    # A live caller answered "when were you first diagnosed?" with "Twenty
    # fifteen." and the record kept the words, even though the agent said
    # "Okay, 2015" in the same breath - so it had understood and stored the
    # words anyway. A history saying a condition began in "Twenty fifteen"
    # cannot be sorted, compared, or underwritten.
    for said, want in (
            ("Twenty fifteen", "2015"),
            ("twenty fifteen.", "2015"),
            ("Nineteen eighty seven", "1987"),
            ("nineteen ninety", "1990"),
            ("twenty twenty", "2020"),
            ("Two thousand fifteen", "2015"),
            ("two thousand and five", "2005"),
            ("two thousand", "2000"),
            ("in twenty fifteen I think", "2015"),
            ("2015", "2015"),
            ("In 2019.", "2019"),
    ):
        got = extract.spoken_year(said)
        check(f"{said!r} -> {want}", got == want, repr(got))

    print("\n[9b] things that are not years must not become years")
    for said, why in (
            ("twenty five", "a quantity - 2005 is said 'two thousand five'"),
            ("ten a day", "a rate"),
            ("two years ago", "relative, no year named"),
            ("about twice a week", "a frequency"),
            ("", "nothing said"),
            ("Salbutamol inhaler", "a medicine"),
            ("sixty two", "a weight"),
            ("nineteen", "a bare number, no century pair"),
    ):
        got = extract.spoken_year(said)
        check(f"{said!r} -> no year ({why})", got is None, repr(got))

    print("\n[9c] the stored value is rewritten, and only when it should be")
    for said, want, note_expected in (
            ("Twenty fifteen", "2015", True),
            ("2015", "2015", False),
            ("In 2019", "In 2019", False),
            ("two years ago", "two years ago", False),
            ("Salbutamol inhaler", "Salbutamol inhaler", False),
    ):
        got, note = extract.normalise_spoken_year(said)
        check(f"{said!r} -> {want!r}", got == want, f"got={got!r}")
        check(f"  note {'given' if note_expected else 'withheld'}",
              bool(note) == note_expected, f"note={note!r}")

    print("\n[9d] and the probe's year is reused instead of asked again")
    # "Asthma 2015." answers both halves of "what was it, and when?".
    src = (ROOT / "agent" / "orchestrator.py").read_text()
    check("the probe branch reads a year from the naming utterance",
          "spoken_year(utterance)" in src and "peek_followup" in src,
          "the probe no longer reuses the year the caller already gave")
    check("and it is gated on the since field, not a question id",
          'head.get("formField") == "since"' in src, "gate missing")
    check("'Asthma 2015.' yields the year", extract.spoken_year("Asthma 2015.") == "2015",
          repr(extract.spoken_year("Asthma 2015.")))
    check("'Asthma' alone yields nothing to reuse",
          extract.spoken_year("Asthma") is None,
          repr(extract.spoken_year("Asthma")))


def test_prefill_never_guesses_which_condition() -> None:
    print("\n[8] the ROOT CAUSE of the wrong year: prefill picked a condition at random")
    # A520 ("when was that hospitalisation?") is auto-filled from a protocol
    # follow-up that already collected formField "since", so the caller is not
    # asked the same thing twice. But EVERY condition's protocol asks FU_DIAG_WHEN,
    # and the lookup returned the first match in map order. On the live call:
    #
    #     J45 (asthma)     -> "2015"
    #     K80 (gallstones) -> "In 2019"     <- the hospitalisation was this one
    #     A520             <- "2015"
    #
    # Filling A520 also advanced the questionnaire to A530, so the caller's
    # "2019, I told you already" was judged as a yes/no, rejected, and answered
    # with an apology. One arbitrary pick produced a wrong year, a self-
    # contradicting record, and the caller being told they had been misheard.
    sys.path.insert(0, str(ROOT / "agent"))
    import orchestrator as orch  # noqa: PLC0415

    # The leg this notional call started on, and a time a few minutes into it.
    # An auto-fill only carries an answer the caller gave on THIS leg - see
    # orch._answered_on_this_leg and the [8f] block below.
    LEG_START = "2026-09-03T10:00:00+00:00"
    THIS_LEG = "2026-09-03T10:05:00+00:00"
    PRIOR_LEG = "2026-09-01T13:26:49+00:00"

    def fu(icd10, value, qid="FU_DIAG_WHEN", field="since", at=THIS_LEG):
        return {"questionId": qid, "icd10": icd10, "value": value,
                "formField": field, "answeredAt": at}

    def sess(answers):
        return {"createdAt": LEG_START, "followUpAnswers": answers}

    live = {"J45::FU_DIAG_WHEN": fu("J45", "2015"),
            "K80::FU_DIAG_WHEN": fu("K80", "In 2019")}
    got = orch._followup_answer_for_field(sess(live), "since")  # noqa: SLF001
    check("two different years -> declines to guess, so A520 is asked",
          got is None, f"picked {got!r}")

    print("\n[8b] the case the auto-fill was built for still works")
    one = {"K80::FU_DIAG_WHEN": fu("K80", "In 2019")}
    got = orch._followup_answer_for_field(sess(one), "since")  # noqa: SLF001
    check("a single condition still auto-fills, so nobody is asked twice",
          got is not None and got.get("value") == "In 2019", repr(got))
    check("and it names the follow-up it came from",
          bool(got) and got.get("questionId") == "FU_DIAG_WHEN", repr(got))

    print("\n[8c] agreement is not ambiguity")
    same = {"J45::FU_DIAG_WHEN": fu("J45", "2019"),
            "K80::FU_DIAG_WHEN": fu("K80", "2019")}
    got = orch._followup_answer_for_field(sess(same), "since")  # noqa: SLF001
    check("two conditions that agree still auto-fill",
          got is not None and got.get("value") == "2019", repr(got))
    spaced = {"a": fu("J45", " 2019 "), "b": fu("K80", "2019")}
    got = orch._followup_answer_for_field(sess(spaced), "since")  # noqa: SLF001
    check("whitespace and case are not a disagreement", got is not None, repr(got))

    print("\n[8d] the empty and malformed cases")
    for session, why in (
            (sess({}), "no follow-ups at all"),
            ({}, "no session key"),
            (sess({"x": fu("J45", "")}), "an empty value"),
            (sess({"x": fu("J45", "2015", field=None)}),
             "a follow-up with no formField"),
            (sess({"x": "not a dict"}), "a malformed record"),
    ):
        got = orch._followup_answer_for_field(session, "since")  # noqa: SLF001
        check(f"{why} -> no prefill", got is None, repr(got))
    check("asking for no field at all returns nothing",
          orch._followup_answer_for_field(sess(live), None) is None,  # noqa: SLF001
          "returned a prefill for a null formField")

    print("\n[8e] three conditions, which is what the demo script discloses")
    three = {"I10::FU_DIAG_WHEN": fu("I10", "2020"),
             "J45::FU_DIAG_WHEN": fu("J45", "2015"),
             "K80::FU_DIAG_WHEN": fu("K80", "2019")}
    got = orch._followup_answer_for_field(sess(three), "since")  # noqa: SLF001
    check("three different years -> declines, measured as A520='2019' end to end",
          got is None, f"picked {got!r}")

    print("\n[8f] AN INHERITED ANSWER IS NOT 'WITHIN EARSHOT'")
    # The live sequel to the case above, two days later on a RESUMED leg:
    #
    #     leg 1  2026-09-01  J45 (asthma) FU_DIAG_WHEN -> "Five years ago"
    #     leg 2  2026-09-03  A520 "when was that hospitalisation?"
    #                        <- auto-filled "Five years ago"
    #
    # followUpAnswers is carried across a resume by design, so an answer given on
    # a previous call looked exactly like one given moments ago. The caller was
    # asked A520, said something else entirely, and had the asthma diagnosis date
    # written into the record as the date of a hospitalisation - then apologised
    # to for being misheard, because by then the form had moved on to A530.
    inherited = {"J45::FU_DIAG_WHEN": fu("J45", "Five years ago", at=PRIOR_LEG)}
    got = orch._followup_answer_for_field(sess(inherited), "since")  # noqa: SLF001
    check("an answer from a prior leg does NOT prefill", got is None,
          f"carried {got!r} across a resume")

    mixed = {"J45::FU_DIAG_WHEN": fu("J45", "Five years ago", at=PRIOR_LEG),
             "K80::FU_DIAG_WHEN": fu("K80", "In 2019", at=THIS_LEG)}
    got = orch._followup_answer_for_field(sess(mixed), "since")  # noqa: SLF001
    check("a stale answer is ignored rather than counted as a disagreement",
          got is not None and got.get("value") == "In 2019", repr(got))

    for stamp, why in ((None, "no answeredAt at all"),
                       ("not-a-date", "an unparseable answeredAt"),
                       ("2026-09-03T10:05:00", "a naive answeredAt")):
        rec = fu("K80", "In 2019")
        if stamp is None:
            rec.pop("answeredAt")
        else:
            rec["answeredAt"] = stamp
        got = orch._followup_answer_for_field(sess({"x": rec}), "since")  # noqa: SLF001
        check(f"{why} -> asks rather than carries", got is None, repr(got))
    check("a session with no createdAt cannot prove freshness, so it asks",
          orch._followup_answer_for_field(  # noqa: SLF001
              {"followUpAnswers": one}, "since") is None)


def test_declaration_no_means_nothing_to_change() -> None:
    print("\n[7] 'no' to \"anything to correct?\" is a confirmation, not a rejection")
    # Recorded here because reading the live transcript, this looked like a defect:
    # the caller said नहीं at the read-back and the call completed with the
    # declaration recorded. The question asked was "Is there anything you'd like to
    # correct or add?", so नहीं means "nothing to correct" - a confirmation. The
    # behaviour is right, and this pins it so nobody "fixes" it into a bug.
    sys.path.insert(0, str(ROOT / "agent"))
    import orchestrator as orch  # noqa: PLC0415

    for said in ("\u0928\u0939\u0940\u0902", "no", "nahi", "nope", "nothing else",
                 "sab theek", "\u0938\u092c \u0920\u0940\u0915",
                 "\u0915\u0941\u091b \u0928\u0939\u0940\u0902"):
        check(f"{said!r} closes the record", orch.is_all_correct(said), "returned False")

    print("\n[7a] the apostrophe forms, which could not match before")
    # "that's all" was blanked to "that s all" by the punctuation pass, so the two
    # apostrophe entries in the phrase list were unreachable and the commonest
    # English way of saying "nothing to change" went down the correction path.
    for said in ("that's all", "that\u2019s all", "thats all",
                 "that's right", "that\u2019s right", "thats right",
                 "That's all, thank you"):
        check(f"{said!r} closes the record", orch.is_all_correct(said), "returned False")

    print("\n[7b] but a 'no' that carries a correction must NOT close it")
    for said in ("no that is wrong",
                 "\u0928\u0939\u0940\u0902, \u0917\u0932\u0924 \u0939\u0948",
                 "no, my asthma is missing",
                 "no, you missed my diabetes",
                 "nahi, yeh galat hai"):
        check(f"{said!r} is treated as a correction",
              not orch.is_all_correct(said), "returned True - would finalise past "
              "a correction the caller was trying to make")

    print("\n[7c] and a bare yes is not a confirmation of 'nothing to change'")
    # "yes" answers a question that was not asked; it flows down the correction
    # path, which costs one bounded round rather than closing the record wrongly.
    for said in ("yes", "haan", "\u0939\u093e\u0901"):
        check(f"{said!r} does not close the record",
              not orch.is_all_correct(said), "returned True")


def test_demo_form_matches_production() -> None:
    print("\n[5] the LIVE demo form carries the same answer types as production")
    # A920 stored the literal 'Skip' because the live demo form still typed it
    # "text". The abha type had been added to the production seed and the demo
    # form - a derived subset, rebuilt by tools/form_admin.py - was never rebuilt,
    # so the fix was live in the form nobody demos and absent from the one they do.
    #
    # Checked against DynamoDB, not against the seed. Comparing the seed to
    # itself would be a tautology: the demo form is generated FROM the production
    # seed, so their types always agree on disk. The whole defect was that the
    # table had not been regenerated.
    prod_path = ROOT / "seed" / "form-arogya-sanjeevani-v1.json"
    prod = {i["itemId"]: i for i in json.loads(prod_path.read_text())["items"]}

    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import form_admin  # noqa: PLC0415
        keep = form_admin.DEMO_KEEP
    except Exception as exc:  # noqa: BLE001
        check("the demo keep-list is readable", False, repr(exc))
        return
    missing = [i for i in keep if i not in prod]
    check("every demo item exists in the production seed", not missing, str(missing))

    try:
        import hvi_state as st  # noqa: PLC0415
        live = st.get_form_items("arogya-sanjeevani-demo-v1", include_inactive=True)
    except Exception as exc:  # noqa: BLE001
        check("the live demo form could be read from DynamoDB", False, repr(exc))
        return
    if not live:
        check("the live demo form is seeded", False,
              "hvi-form-schema has no arogya-sanjeevani-demo-v1 items; run "
              "tools/form_admin.py demo-form")
        return
    print(f"       live demo form has {len(live)} items")

    mismatched = [(i["itemId"], i.get("answerType"),
                   prod[i["itemId"]].get("answerType"))
                  for i in live
                  if i["itemId"] in prod
                  and i.get("answerType") != prod[i["itemId"]].get("answerType")]
    check("no live demo item has drifted from production's answerType",
          not mismatched, f"(itemId, demo, production) -> {mismatched}")

    live_by_id = {i["itemId"]: i for i in live}
    for item_id, want in (("A920", "abha"), ("A990", "email"), ("A030", "date")):
        if item_id in live_by_id:
            check(f"{item_id} is typed {want} in the live demo form",
                  live_by_id[item_id].get("answerType") == want,
                  str(live_by_id[item_id].get("answerType")))

    print("\n[5b] and a skip on the ABHA field resolves rather than being stored")
    for said in ("Skip", "skip", "  SKIP  "):
        val, err = extract.coerce(said, "abha")
        check(f"{said!r} -> declined, not stored verbatim",
              val == "declined" and err is None, f"value={val!r} err={err!r}")


def test_denial_plus_definition_request() -> None:
    """A caller withdrawing a yes while asking what the word meant.

    FROM A LIVE BROWSER CALL. The agent asked "Hypertension, high blood pressure,
    or high cholesterol?", a spurious "Yes." was transcribed, and the caller
    objected:

        "No, I did not say yes. I did not say yes. I did not say yes.
         I asked you to explain cholesterol."

    The agent answered by re-asking the screen with the probe appended, twice, and
    never withdrew the yes or explained anything.

    The handling for this already existed - orchestrator.py flags the screen answer
    for review when a definition request opens with a denial, written for the
    Alzheimer's case. It never ran, because is_explanation_request() did not
    recognise "I asked you to explain". The utterance DID match _REPEAT_PATTERNS on
    "i did not say", so it was served as a request to repeat the question, and the
    two later branches that would have caught it were never reached.

    Both halves are asserted, because either alone leaves the defect: the request
    has to read as a definition request, AND the leading denial has to register, or
    the yes is not flagged.
    """
    print("[11] denial wrapped around a definition request")

    live = ("No, I did not say yes. I did not say yes. I did not say yes. "
            "I asked you to explain cholesterol.")

    check("the live utterance reads as a definition request",
          orchestrator.is_explanation_request(live),
          f"is_explanation_request({live[:40]!r}...) was False, so the branch that "
          "flags the screen answer never runs")

    check("its leading denial registers, so the yes gets flagged",
          orchestrator._denies_before_asking(live))

    # The plain forms, without the denial. A caller may simply ask.
    for phrasing in ("I asked you to explain cholesterol.",
                     "Please explain cholesterol.",
                     "Can you explain what cholesterol is?",
                     "Explain cholesterol to me.",
                     "I asked you to explain it."):
        check(f"{phrasing!r} reads as a definition request",
              orchestrator.is_explanation_request(phrasing))

    # A definition request must not be mistaken for a disclosure. This is the
    # same class as the Alzheimer's loop: a question becoming a diagnosis.
    check("asking to explain is not itself an answer of yes",
          not orchestrator.is_bare_affirmation(
              "I asked you to explain cholesterol."))

    # And the guard must stay narrow. These are answers, not requests, and must
    # not be swallowed by the new patterns.
    #
    # The "explained" cases are the reason the pattern uses a word boundary rather
    # than a bare "explain" substring. Each of these is a DISCLOSURE. Reading one
    # as a request for a definition would answer a question the caller did not ask
    # and drop the condition they just declared.
    for answer in ("Yes.", "No.", "I have high cholesterol.",
                   "Yes, high cholesterol since 2019.",
                   "The doctor explained my diabetes to me.",
                   "It was explained as high cholesterol.",
                   "My cardiologist explained the stent procedure."):
        check(f"{answer!r} is NOT a definition request",
              not orchestrator.is_explanation_request(answer))


def test_glitched_value_repair() -> None:
    """A mangled `value` is repaired from the rest of the same reading, never guessed.

    MEASURED, NOT HYPOTHETICAL. Asked for her gallstone treatment a caller said
    "पैंटोप्राजोल गोली" and the record kept "पैंटोप्राजोल गो{" - the final "ली"
    (U+0932 U+0940) replaced by one U+007B. It went onto the underwriting record and
    was read back to her in the closing summary in that state, which is the one turn
    where the record is supposed to be verified.

    Reproduced 2-3 times in 20 at temperature 0. It is NOT a token-cap truncation:
    stopReason is "tool_use", output is 155-177 tokens against a 500 cap, and the
    same response reports the string intact in conditionPhrase, in medications and
    in conditionMentions[].phrase. Only `value` is corrupt, which is what makes a
    repair possible without inventing anything.

    Pure function, so this pins the behaviour deterministically with no model call.
    """
    print("[14] a glitched value field is repaired from its own reading")

    # The live case, verbatim.
    out = {
        "value": "पैंटोप्राजोल गो{",
        "conditionPhrase": "पैंटोप्राजोल गोली",
        "medications": ["पैंटोप्राजोल गोली"],
        "conditionMentions": [{"phrase": "पैंटोप्राजोल गोली",
                               "scope": "self_current"}],
    }
    note = extract._repair_glitched_value(out, "पैंटोप्राजोल गोली")  # noqa: SLF001
    check("the live corruption is repaired", out["value"] == "पैंटोप्राजोल गोली",
          repr(out["value"]))
    check("and the repair is reported, not silent", bool(note), repr(note))

    # A clean value must be left exactly alone - no debris, nothing to explain.
    out = {"value": "पैंटोप्राजोल गोली", "medications": ["कुछ और"]}
    note = extract._repair_glitched_value(out, "पैंटोप्राजोल गोली")  # noqa: SLF001
    check("a clean value is untouched", out["value"] == "पैंटोप्राजोल गोली",
          repr(out["value"]))
    check("and reports no repair", note is None, repr(note))

    # THE IMPORTANT NEGATIVE. A candidate that is not a continuation of the
    # fragment must never be substituted, or a repair becomes a wrong answer.
    out = {"value": "मेटफॉर्मिन{", "medications": ["पैंटोप्राजोल गोली"]}
    note = extract._repair_glitched_value(out, "पैंटोप्राजोल गोली")  # noqa: SLF001
    check("a different medicine is NOT substituted in",
          out["value"] == "मेटफॉर्मिन{", repr(out["value"]))
    check("and no repair is claimed", note is None, repr(note))

    # Nothing corroborates the fragment: leave it visibly broken for the ordinary
    # validation and human-review paths rather than completing it by guesswork.
    out = {"value": "पैंटोप्राजोल गो{"}
    note = extract._repair_glitched_value(out, "")  # noqa: SLF001
    check("an uncorroborated fragment is not invented into an answer",
          out["value"] == "पैंटोप्राजोल गो{" and note is None, repr(out["value"]))

    # The caller's own words are an acceptable source when no field corroborates.
    out = {"value": "पैंटोप्राजोल गो{"}
    note = extract._repair_glitched_value(out, "पैंटोप्राजोल गोली")  # noqa: SLF001
    check("the utterance is used as the last resort",
          out["value"] == "पैंटोप्राजोल गोली", repr(out["value"]))

    # Non-strings and blanks are none of this function's business.
    for value in (True, False, 42, None, "", "   "):
        out = {"value": value, "medications": ["पैंटोप्राजोल गोली"]}
        note = extract._repair_glitched_value(out, "x")  # noqa: SLF001
        check(f"{value!r} is left alone", out["value"] == value and note is None,
              repr(out["value"]))

    # Latin-script debris too - the glitch is about the write being cut short, not
    # about Devanagari.
    out = {"value": 'Salbutamol inh"', "medications": ["Salbutamol inhaler"]}
    note = extract._repair_glitched_value(out, "Salbutamol inhaler")  # noqa: SLF001
    check("Latin-script debris is repaired the same way",
          out["value"] == "Salbutamol inhaler", repr(out["value"]))


def test_trailing_punctuation_does_not_change_the_code() -> None:
    """[15] A trailing danda must not move the classifier to a different disease.

    MEASURED ON call-47ce35b0. The caller answered the lung screen "हाँ, अस्थमा।" and
    it resolved to J46 Status asthmaticus - a life-threatening asthma attack - at
    0.8707 by embedding alone. The identical words without the trailing danda resolve
    to J45 Asthma at 0.8798 WITH graph corroboration.

    The consequences ran well past the wrong label. J46 is a reference-tier code with
    no follow-up protocol, so the DEFAULT protocol ran; the code was later superseded
    by J45, whose real protocol was then queued on top. The caller was asked when she
    was diagnosed twice, answered "हाँ" to the repeat, and each "हाँ" to a date
    question tripped explain-then-reask - producing both live complaints, "it would
    not take 2015" and "it explains diseases I did not ask about", from one
    punctuation mark.

    Tested at the boundary rather than end to end: the assertions are that
    _for_matching normalises correctly AND that classify_condition actually routes
    both the graph lookup and the embedding through it. Stubs stand in for Neptune,
    Bedrock and OpenSearch so this stays a fast deterministic unit test - the live
    scores are measured by tests/diag_asthma_classification.py instead.
    """
    import tools as agent_tools

    # ---- the normaliser itself ----
    cases = [
        ("हाँ, अस्थमा।", "हाँ, अस्थमा"),          # the danda that broke the call
        ("हाँ, अस्थमा॥", "हाँ, अस्थमा"),          # double danda
        ("asthma.", "asthma"),
        ("Yes, I fractured my leg.", "Yes, I fractured my leg"),
        ("मुझे अस्थमा है  ।  ", "मुझे अस्थमा है"),  # spacing around it
        ("अस्थमा?!", "अस्थमा"),
        ("अस्थमा", "अस्थमा"),                      # nothing to strip
    ]
    for said, want in cases:
        check(f"[15] _for_matching({said!r}) -> {want!r}",
              agent_tools._for_matching(said) == want,
              f"got {agent_tools._for_matching(said)!r}")

    # INTERNAL punctuation must survive. The comma in "नहीं, मुझे अस्थमा है" separates
    # a negation from the condition, and the embedding is entitled to see it.
    keep = "नहीं, मुझे अस्थमा है"
    check("[15] internal punctuation is preserved",
          agent_tools._for_matching(keep) == keep,
          f"got {agent_tools._for_matching(keep)!r}")

    # A phrase that is ONLY punctuation must not become an empty query.
    check("[15] a punctuation-only phrase is returned unchanged, not emptied",
          agent_tools._for_matching("।") == "।",
          f"got {agent_tools._for_matching('।')!r}")

    # ---- and that the classifier actually uses it ----
    seen: dict[str, str] = {}

    class _Body:
        @staticmethod
        def read() -> bytes:
            return json.dumps({"embeddings": [[0.0, 0.0]]}).encode()

    class _FakeBedrock:
        @staticmethod
        def invoke_model(modelId: str, body: str):  # noqa: N803
            seen["embed"] = json.loads(body)["texts"][0]
            return {"body": _Body()}

    def _fake_graph(action: str, **kw):
        seen["graph"] = kw.get("phrase", "")
        return {"found": False, "matches": []}

    def _fake_os(method: str, path: str, body=None):
        # One governed-looking hit, so classify_condition takes the kNN path and
        # returns without needing anything else stubbed.
        return {"hits": {"hits": [{"_score": 0.88, "_source": {
            "icd10": "J45", "label_en": "Asthma", "label_hi": "अस्थमा",
            "category": "respiratory", "severityTier": 2, "sensitivity": None,
            "surfaceForm": "अस्थमा", "termType": "ColloquialTerm",
            "redFlag": False}}]}}

    orig = (agent_tools._graph, agent_tools._bedrock, agent_tools._os_request)
    agent_tools._graph, agent_tools._bedrock, agent_tools._os_request = (
        _fake_graph, _FakeBedrock, _fake_os)
    try:
        out = agent_tools.classify_condition("हाँ, अस्थमा।")
    finally:
        (agent_tools._graph, agent_tools._bedrock,
         agent_tools._os_request) = orig

    check("[15] the graph is asked WITHOUT the trailing danda",
          seen.get("graph") == "हाँ, अस्थमा", f"graph saw {seen.get('graph')!r}")
    check("[15] the embedding is built WITHOUT the trailing danda",
          seen.get("embed") == "हाँ, अस्थमा", f"embed saw {seen.get('embed')!r}")
    # The RECORD keeps the caller's words. Normalisation is for matching only; the
    # read-back quotes this field back to them, so it must be what they said.
    check("[15] the returned phrase is still the caller's verbatim words",
          out.get("phrase") == "हाँ, अस्थमा।", f"got {out.get('phrase')!r}")


def main() -> int:
    test_email_confirmation()
    test_spoken_year()
    test_list_placeholders()
    test_since_gate()
    test_readback_phrasing()
    test_no_double_courtesy()
    test_spoken_word_years()
    test_prefill_never_guesses_which_condition()
    test_declaration_no_means_nothing_to_change()
    test_demo_form_matches_production()
    test_denial_plus_definition_request()
    test_glitched_value_repair()
    test_trailing_punctuation_does_not_change_the_code()

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} live-call defect checks passed.")
    print("  A confirmed email resolves to the address on file, a spoken year")
    print("  outranks a modelled one, invented list elements are dropped, and the")
    print("  demo form cannot drift behind production's answer types.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
