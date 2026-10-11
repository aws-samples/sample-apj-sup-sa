#!/usr/bin/env python3
"""Echo of the agent's own speech must not be answered as if the caller said it.

WHY THIS EXISTS
On a live call the agent apologised and re-asked nine times in ninety seconds. The
transcripts were not mishearings of the caller; they were the agent's own sentence
returning on the inbound leg about two seconds after it began speaking:

    agent  "Okay, Anita. Just so I record it correctly, is that Chronic Kidney..."
    stt    "My code."          <- "...I record it correctly..."
    agent  "...What treatment or medication are you taking for it?"
    stt    "Why coding?"       <- "...medication are you taking..."
    stt    "White Golden."     -> classified, confidently, as Chronic Kidney Disease

PSTN hybrid echo, or a handset speaker feeding its own microphone. It compounded
twice: the orchestrator could not parse the fragments so it re-asked, and the same
echo tripped the barge-in path and cut the agent off mid-sentence, which is what the
caller experienced as the call getting stuck.

Every case below is a real string from that call's log, so this is a regression test
against something that actually happened rather than an invented scenario.

THE ASYMMETRY THAT DRIVES THE TUNING
Discarding an echo costs one re-ask. Discarding a real answer writes a wrong value
into a medical record, or silently loses a disclosure. So the checks that a genuine
answer survives matter more than the ones that an echo is caught, and they are
listed second on purpose - if a trade-off is ever needed, that is the direction.

Run:  ./.venv/bin/python tests/test_echo_rejection.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HVI_TTS", "omnivoice")
os.environ.setdefault("AGENT_RUNTIME_ARN",
                      "arn:aws:bedrock-agentcore:ap-south-1:0:runtime/test")

# Suppression is OFF in production, because capturing the inbound leg proved it
# carries the caller only - no echo to suppress on this deployment, and a
# suppressor that cannot help can still discard a real answer. Forced ON here so
# the mechanism itself stays tested and correct for a line that does echo.
os.environ["ECHO_SUPPRESSION"] = "1"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    from media.agent_bridge import AgentCoreBridge

    bridge = AgentCoreBridge(call_id="test-echo")

    def judge(agent_text: str, heard: str, *, speaking: bool = True) -> bool:
        bridge._last_agent_text = agent_text  # noqa: SLF001
        bridge._bot_speaking = speaking  # noqa: SLF001
        bridge._bot_spoke_at = None  # noqa: SLF001
        return bridge._is_echo(heard)  # noqa: SLF001

    RECORD = "Okay, Anita. Just so I record it correctly, is that Chronic Kidney Disease?"
    TREAT = "Sorry, I didn't quite catch that. What treatment or medication are you taking for it?"
    CONTROLLED = "I see. Is it well controlled at the moment?"

    print("[1] Fragments of our own speech, verbatim from the failed call")
    check("'My code.' during the 'record it correctly' sentence",
          judge(RECORD, "My code."))
    check("'Why coding?' during the 'medication are you taking' sentence",
          judge(TREAT, "Why coding?"))
    check("'Hi, Golden.' during the same sentence",
          judge(RECORD, "Hi, Golden.") or judge(RECORD, "Golden."))

    print("\n[2] Real answers MUST survive, including ones that echo the question")
    # This is the dangerous direction. A caller answering in the words of the
    # question is normal speech, not echo, and dropping it loses a real answer.
    check("'yes' is never treated as echo",
          not judge(CONTROLLED, "yes"))
    check("'no' is never treated as echo",
          not judge(CONTROLLED, "no"))
    check("'2025' survives", not judge("When were you first diagnosed with this?",
                                       "2025"))
    check("'yes, well controlled' survives",
          not judge(CONTROLLED, "yes it is well controlled"),
          "long enough to be a person talking")
    check("a real condition disclosure survives",
          not judge(RECORD, "I have chronic kidney disease and diabetes"))
    check("'Hypertension, cholesterol, blood pressure, etc.' survives",
          not judge("Hypertension, high blood pressure, or high cholesterol?",
                    "Hypertension, cholesterol, blood pressure, etc."),
          "this one transcribed CORRECTLY on the live call")

    print("\n[3] Nothing is suppressed once the agent has been quiet")
    # Outside the window there is no echo left to reject, so the caller may reuse
    # the question's words freely.
    import time as _t

    bridge._last_agent_text = CONTROLLED  # noqa: SLF001
    bridge._bot_speaking = False  # noqa: SLF001
    bridge._bot_spoke_at = _t.monotonic() - 60  # noqa: SLF001
    check("a long-past utterance cannot be echoed",
          not bridge._is_echo("well controlled"))  # noqa: SLF001

    print("\n[4] Degenerate input")
    check("empty transcript is not echo", not judge(RECORD, ""))
    # With the agent silent and nothing on record as having been said, there is
    # nothing that could have echoed, so the transcript must pass through.
    check("agent silent and nothing said means nothing is echo",
          not judge("", "My code.", speaking=False))
    # But while the agent IS speaking, short unintelligible input is suppressed
    # whether or not the text was captured - not knowing what we are saying is no
    # reason to accept our own voice as an answer.
    check("agent speaking suppresses it even with no text recorded",
          judge("", "My code.", speaking=True))

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("The agent no longer answers itself, and real answers still get through.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
