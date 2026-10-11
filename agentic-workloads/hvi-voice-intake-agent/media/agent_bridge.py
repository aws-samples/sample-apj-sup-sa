"""Pipecat processor that drives one call turn against the AgentCore runtime.

WHERE THE DIVISION OF LABOUR SITS
---------------------------------
The deterministic orchestrator stays on Bedrock AgentCore, unchanged: it owns
question sequencing, DynamoDB per-turn checkpointing, the Neptune-mandated
follow-up queue, the red-flag read-back, and the resume-after-drop logic. This
module is only a bridge - it turns a transcript into an HTTP call and the reply
into speech.

That split is deliberate. Agent logic belongs somewhere durable and auditable;
media belongs next to the RTP. Moving the orchestrator into the media process
would have meant reimplementing checkpointing in a component that dies with the
call, which is precisely backwards for a system whose main requirement is
surviving a dropped call.

BARGE-IN IS TRIGGERED HERE, AND IT IS NOT AUTOMATIC
---------------------------------------------------
Pipecat 1.7.0 constructs InterruptionFrame in exactly two places, neither of
which is the VAD processor or the input transport. Voice activity alone does not
interrupt the bot. So this processor watches for UserStartedSpeakingFrame and
calls broadcast_interruption() itself when the bot is mid-utterance. Assuming the
framework did it would have produced a pipeline that looks correct and never
barges in.

The guard matters: interruption is only broadcast when the bot is actually
speaking. Firing it on every utterance would reset processor queues needlessly
between turns.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import unicodedata

import boto3
from botocore.config import Config
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("hvi.media.agent")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
AGENT_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")

# How long to wait for the opening utterance to be reported as delivered before
# ECHO REJECTION TUNING. All three are deliberately conservative, because the two
# errors are not equally bad: discarding an echo costs nothing, while discarding a
# real answer puts a wrong value in a medical record or loses a disclosure.
#
# How long after the agent stops talking its own voice may still arrive. Echo lags
# the speech that caused it, and a caller answering promptly overlaps that tail.
ECHO_WINDOW_S = float(os.environ.get("ECHO_WINDOW_SECS", "1.5"))
# Only short transcripts are considered. Real answers to these questions are short
# too, which is why the overlap test below has to carry the weight rather than
# length alone; but a long utterance sharing the question's vocabulary is a person
# talking, not a hybrid.
ECHO_MAX_WORDS = int(os.environ.get("ECHO_MAX_WORDS", "4"))
# Fraction of the transcript's words that must appear in what the agent just said.
# At 1.0 nothing would ever match, because echo is lossy and returns approximations
# rather than the original words.
ECHO_OVERLAP = float(os.environ.get("ECHO_OVERLAP", "0.6"))
# Off unless explicitly enabled. See AgentCoreBridge._is_echo for why: the inbound
# capture proved the line carries the caller only, so on this deployment echo
# suppression can only discard real answers.
ECHO_SUPPRESSION = os.environ.get("ECHO_SUPPRESSION", "0") not in ("0", "", "false")

# Short utterances that ARE plausible answers, and so survive even mid-utterance.
# This is the barge-in carve-out: a caller who cuts in does so with one of these,
# whereas echo arrives as something that answers nothing. Generous on purpose - a
# word wrongly listed here costs one re-ask, a word wrongly missing costs a real
# answer.
_ANSWER_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "no", "nope", "nah", "not", "never",
    "haan", "haa", "ha", "ji", "nahi", "nahin", "bilkul", "sahi",
    "हाँ", "हां", "नहीं", "जी", "बिल्कुल",
    "dont", "don", "know", "unsure", "maybe", "sometimes", "pata", "malum",
    "stop", "wait", "repeat", "again", "sorry", "pardon", "phir", "dobara",
    "hindi", "english", "हिंदी", "अंग्रेजी",
    "controlled", "insulin", "tablets", "tablet", "medicine", "dawa", "goli",
}

# listening anyway. Only reached when BotStoppedSpeakingFrame never arrives, e.g.
# a TTS failure. Set generously - a measured greeting is ~3.3s to synthesise plus
# ~11s to play - because the cost of being early is ignoring the customer, while
# the cost of being late is a few seconds of a call that is already degraded.
OPENING_GRACE_MAX_S = float(os.environ.get("HVI_OPENING_GRACE_MAX_S", "30"))

# How many times the questionnaire schema may ride along on a turn before we stop
# re-sending it unacknowledged.
#
# Bounded rather than unlimited because the schema is ~31KB - 38 items in three
# languages - and a 40-turn call that re-sent it every turn would push well over a
# megabyte down a data channel on a 2GB t4g.small that is simultaneously running
# VAD, STT and the call. Three attempts covers the failure this exists for (the
# opening message being written before the channel is open); if three turns have
# gone by unacknowledged the channel is broken rather than slow, and the browser
# reports that itself.
MAX_FORM_SCHEMA_SENDS = int(os.environ.get("HVI_MAX_FORM_SCHEMA_SENDS", "3"))


class AgentCoreBridge(FrameProcessor):
    """Transcript in, agent speech out, one turn at a time."""

    def __init__(
        self,
        *,
        call_id: str,
        stt_service=None,
        tts_service=None,
        agent_arn: str | None = None,
        caller_phone: str | None = None,
        on_reply=None,
        include_form_state: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        # Optional observer for a surface that renders the call as well as
        # speaking it - see media/bot_web.py. Both default off, so the phone
        # pipeline builds exactly the bridge it always did and its replies are
        # unchanged: no form state requested, no callback invoked.
        self._on_reply = on_reply
        self._include_form_state = include_form_state
        # THE SCHEMA IS TRACKED BY WHETHER THE SURFACE HAS IT, NOT BY WHETHER WE
        # ASKED FOR IT.
        #
        # It used to be a single flag latched the moment the agent returned the
        # items, which quietly assumed that a message written to the data channel
        # arrives. It need not: pipecat queues app messages until the channel
        # opens, but caps that queue at MAX_MESSAGE_QUEUE_SIZE and, once
        # DATA_CHANNEL_TIMEOUT_SECS (10s) has passed without an open channel,
        # discards them silently - its own docstring says so. The opening turn is
        # exactly the message at risk, because it is written before the channel is
        # necessarily open, and it is the ONLY one carrying the schema. Lose it and
        # the flag was already latched, so nothing ever asked again and the
        # questionnaire panel stayed empty for the whole call while answers landed
        # against labels it could not draw.
        #
        # So the browser acknowledges the schema once it has rendered it (see
        # note_form_schema_delivered), and until that ack arrives the schema is
        # re-sent - capped, because if several turns have not got through then the
        # channel is broken rather than slow, and the surface says so itself
        # instead of us re-sending 31KB on every turn of a 40-turn call.
        self._form_schema_acked = False
        self._form_schema_sends = 0
        # Starts as the AudioSocket media id. The orchestrator swaps it for the
        # real business callId on the first reply, because only it can create or
        # resume a session (it owns DynamoDB; this host has no access to it).
        self._call_id = call_id
        self._caller_phone = caller_phone
        self._stt = stt_service
        # The TTS service needs the language too, and for a DIFFERENT reason than
        # STT: it selects which reference clip to clone from. Omitting it meant the
        # endpoint supported per-language references while every live call was
        # synthesised from whatever HVI_DEFAULT_LANGUAGE happened to be - so a Hindi
        # caller heard the English reference and nothing said so.
        self._tts = tts_service
        self._agent_arn = agent_arn or AGENT_ARN
        self._turn_index = 0
        self._bot_speaking = False
        # Timing for the stale-answer gate: when the current bot utterance began
        # playing, and when the most recent caller speech burst began.
        self._bot_utterance_started_at: float | None = None
        self._user_speech_started_at: float | None = None
        self._complete = False
        # Pre-roll gate. Transcripts are ignored until the opening utterance has
        # actually reached the caller, because carrier and PBX announcements
        # ("This call is not being recorded.") otherwise arrive as customer turns
        # and can talk the agent into ending the call before it has said anything.
        self._opening_pushed = False
        self._opening_delivered = False
        self._opening_pushed_at: float | None = None
        # Language the orchestrator has settled on, echoed back on each turn so
        # it keeps following the caller instead of re-defaulting to hinglish.
        self._language: str | None = None
        # One turn in flight at a time. Without this, a caller who talks over the
        # agent could have two turns racing to write the same checkpoint.
        self._turn_lock = asyncio.Lock()
        self._client = boto3.client(
            "bedrock-agentcore", region_name=DATA_REGION,
            config=Config(read_timeout=30, connect_timeout=5,
                          retries={"max_attempts": 1}),
        )
        self._latencies: list[int] = []
        # What the agent last said, and when it stopped, so its own speech coming
        # back on the line can be recognised. See _is_echo.
        self._last_agent_text = ""
        self._bot_spoke_at: float | None = None

    def _is_echo(self, text: str) -> bool:
        """Is this transcript our own last utterance coming back at us?

        Word overlap against what the agent just said, not string equality: the echo
        path is lossy, so what returns is a short garbled fragment rather than a
        clean substring. "My code." never appears verbatim in "Just so I record it
        correctly", yet it is plainly that phrase after a round trip through a
        telephone hybrid.

        Only while the bot is speaking or immediately after. Once the agent has been
        quiet for ECHO_WINDOW_S there is nothing left to echo, and a caller is
        perfectly entitled to answer using the words of the question - "is it well
        controlled?" answered "yes, well controlled" must not be discarded.

        Short transcripts only. A long utterance that happens to share vocabulary
        with the question is someone talking, and dropping a real answer is worse
        than accepting an occasional echo: the echo costs one re-ask, a dropped
        answer costs a wrong medical record.

        WORD OVERLAP DOES NOT WORK, which is worth recording because it is the
        obvious first idea. "My code." shares not one word with "Just so I record it
        correctly" - the resemblance is phonetic, not lexical, because the echo path
        garbles sounds rather than substituting synonyms. Overlap scored 0.0 on every
        real case from the call.

        What does separate them is INTELLIGIBILITY. Echo arrives as something that is
        not a possible answer to anything - "My code.", "Why coding?", "White
        Golden.", "Hi, Golden." A genuine short interruption is a recognisable
        answer: yes, no, haan, a number, a language name. So while the agent is
        actually speaking, a short transcript that is not a recognisable answer is
        treated as echo, and one that IS recognisable gets through - which keeps
        barge-in working for the answers people actually interrupt with.

        The word-overlap test is still applied in the tail after the agent stops,
        where a lexical repeat is the likelier artefact.
        """
        # DISABLED BY DEFAULT, BECAUSE THE THEORY BEHIND IT WAS DISPROVED.
        #
        # This was written from timestamp evidence: transcripts arriving ~2s after
        # the agent began speaking, reading like fragments of its own sentence
        # ("My code." for "...I record it correctly..."). Plausible, and wrong.
        #
        # Capturing the inbound leg and transcribing it settled it: the leg carries
        # the CALLER ONLY. None of the agent's questions appear anywhere in 227
        # seconds of audio. The garbage came from the double-WAV corruption fixed in
        # media/stt.py, which put an 85%-of-full-scale click before every first
        # phoneme; once that was removed the same caller transcribed cleanly
        # ("It's resolved now. I'm not taking any medication.").
        #
        # Kept rather than deleted because the mechanism is sound if a genuinely
        # echoey line ever turns up, and the reasoning is worth not repeating from
        # scratch. Off by default because it can only cost accuracy now: a real
        # four-word answer that is not in _ANSWER_WORDS would be discarded, and
        # losing an answer is far worse than the problem it was built for.
        if not ECHO_SUPPRESSION:
            return False

        words = [w for w in re.sub(r"[^\w\s]", " ", (text or "").lower()).split() if w]
        if not words or len(words) > ECHO_MAX_WORDS:
            return False

        if self._bot_speaking:
            # Mid-utterance. Anything short and unintelligible here is our own voice;
            # the caller has not been given a question to answer yet.
            return not self._is_recognisable_answer(words)

        if (self._bot_spoke_at is None
                or time.monotonic() - self._bot_spoke_at > ECHO_WINDOW_S):
            return False

        mine = set(re.sub(r"[^\w\s]", " ", self._last_agent_text.lower()).split())
        if not mine:
            return False
        overlap = sum(1 for w in words if w in mine) / len(words)
        return overlap >= ECHO_OVERLAP

    @staticmethod
    def _is_recognisable_answer(words: list[str]) -> bool:
        """Could this short utterance be a real answer to a medical question?

        Kept deliberately generous. Every word admitted here is a word that will be
        passed through mid-utterance, and passing through an echo costs one re-ask
        while blocking a real answer costs an answer. Numbers, yes/no in three
        languages, don't-know, and the language names all qualify.
        """
        for w in words:
            if w.isdigit():
                return True
            if w in _ANSWER_WORDS:
                return True
        return False

    _BARE_YES_NO = {
        "yes", "no", "yeah", "nope", "ok", "okay",
        "haan", "han", "ha", "ji", "nahi", "nahin", "na",
        "हाँ", "हा", "जी", "नहीं", "नही", "ना",
    }

    @classmethod
    def _is_bare_yes_no(cls, text: str) -> bool:
        """A transcript that is ONLY an affirmation or negation.

        Used by the stale-answer gate: these are the only tokens ambiguous
        enough to bind to the wrong question, because they carry no content of
        their own. Anything with substance stays, wherever it lands.

        Punctuation is stripped by Unicode CATEGORY, not by [^\\w\\s]: Python's
        \\w does not match Devanagari matras or nasalisation marks (Mc/Mn), so
        the regex approach silently mangles "हाँ" into "ह" and the gate never
        fires for Hindi - the language most of these calls are in.
        """
        cleaned = "".join(" " if unicodedata.category(ch).startswith("P")
                          else ch for ch in (text or "").lower())
        words = cleaned.split()
        return bool(words) and all(w in cls._BARE_YES_NO for w in words)

    # ---- lifecycle --------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            # When the CURRENT question started playing. A bare yes/no whose
            # speech began before this instant is answering something else -
            # see the stale-answer gate below.
            self._bot_utterance_started_at = time.monotonic()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            # Echo outlives the speech that caused it, so the window starts here.
            self._bot_spoke_at = time.monotonic()
            if self._opening_pushed and not self._opening_delivered:
                self._opening_delivered = True
                log.info("opening utterance delivered, now listening to the caller")
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speech_started_at = time.monotonic()
            # The barge-in trigger. Silero has detected speech; if the bot is
            # talking, cut it off now rather than after the current utterance.
            if self._bot_speaking:
                log.info("caller interrupted mid-utterance, broadcasting interruption")
                await self.broadcast_interruption()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            # THE AGENT MUST NOT ANSWER ITSELF.
            #
            # Measured on a live call, where the agent apologised and re-asked nine
            # times in ninety seconds. The transcripts were not mishearings of the
            # caller, they were fragments of the agent's own sentence coming back
            # on the inbound leg roughly two seconds after it started speaking:
            #
            #   agent "...Just so I record it correctly, is that Chronic..."
            #   stt   "My code."          <- "...I record it correctly..."
            #   agent "...What treatment or medication are you taking for it?"
            #   stt   "Why coding?"       <- "...medication are you taking..."
            #
            # PSTN hybrid echo, or a handset speaker feeding its own microphone.
            # Either way the VAD is right that there is speech and the STT is right
            # about roughly what it says; the mistake is attributing it to the
            # caller. It compounds twice over: the orchestrator cannot parse the
            # fragment so it re-asks, and UserStartedSpeakingFrame above treats the
            # same echo as a barge-in and cuts the agent off mid-sentence, which is
            # what a caller experiences as the call getting stuck.
            #
            # Rejected at the TEXT level rather than by muting the microphone while
            # the bot speaks, because muting would kill barge-in - and barge-in is
            # the feature that makes this feel like a conversation instead of an
            # IVR. A genuine interruption does not repeat the agent's own words
            # back, so it still gets through.
            if self._is_echo(frame.text):
                log.info("dropped echo of our own speech: %r", frame.text[:60])
                return

            # A BARE YES/NO THAT PREDATES THE QUESTION IS NOT ITS ANSWER.
            #
            # Measured on a live browser-harness call with a headset: the caller
            # answered "controlled?" with a real हाँ, then a stray vocalisation
            # (breath / hmm at a close-talking mic) in the inter-question gap was
            # transcribed as a second "हाँ". Its speech started ~100ms BEFORE the
            # next question began playing, yet it was consumed as that question's
            # answer - a yes to "do you know your HbA1c?" the caller never heard,
            # let alone answered. Nobody can answer a question that has not been
            # asked, so a BARE affirmation/negation whose speech began before the
            # current utterance started is discarded.
            #
            # Deliberately narrow, so genuine barge-in survives untouched:
            #   - only bare yes/no tokens; substantive answers ("blood cancer",
            #     numbers, dates) pass, because content that predates the question
            #     is still a real disclosure worth hearing
            #   - only when the timing is knowable; if either timestamp is
            #     missing, nothing is dropped
            #   - a caller who interrupts DURING the question still lands after
            #     its start, so their yes/no is honoured
            if (self._is_bare_yes_no(frame.text)
                    and self._user_speech_started_at is not None
                    and self._bot_utterance_started_at is not None
                    and self._user_speech_started_at
                    < self._bot_utterance_started_at):
                log.info("dropped stale %r - speech began before the current "
                         "question started playing, so it cannot be its answer",
                         frame.text[:40])
                return

            # NOTHING THE LINE CARRIES BEFORE WE HAVE FINISHED GREETING IS AN
            # ANSWER TO US.
            #
            # A live call died on exactly this. The caller's side played
            # "This call is not being recorded." as pre-roll, and because
            # OmniVoice needs ~3.3s to synthesise the greeting, that announcement
            # was transcribed while the agent had not yet said a word. It arrived
            # as three separate utterances - "This call is not", "being recorded.",
            # "Hello." - which the orchestrator counted as three customer turns.
            # "This call is not" reads like a rejection, so by the third turn the
            # model concluded the customer was declining and closed with
            # "That's completely fine. We'll call you back another time." The call
            # ended 1.2 seconds after the greeting was queued, so the caller heard
            # a moment of speech, then a hangup, and reported it as silence.
            #
            # Carrier and PBX pre-roll is not optional and not predictable -
            # recording notices, network announcements, ringback fragments - so the
            # agent has to be deaf until it has actually spoken. Waiting for the
            # opening to be DELIVERED rather than merely started matters, because
            # the whole problem is the gap between deciding to speak and the audio
            # existing.
            #
            # This does not disable barge-in. Interruption is driven by
            # UserStartedSpeakingFrame above and stays live throughout; the cost
            # here is only that a caller who talks over the greeting itself has
            # that first utterance ignored, and is then asked the question again.
            # Being asked twice is a far better failure than being hung up on.
            if not self._opening_delivered:
                # Failsafe. _opening_delivered is normally set by
                # BotStoppedSpeakingFrame, but if TTS errors or the frame is lost
                # the gate would never open and the agent would be deaf for the
                # entire call - turning a fix for pre-roll into a far worse bug
                # than the one it replaced. Elapsed time is a coarse signal, so it
                # is set well beyond any plausible greeting rather than tuned.
                waited = time.monotonic() - (self._opening_pushed_at or 0)
                if self._opening_pushed_at and waited > OPENING_GRACE_MAX_S:
                    log.warning("opening never reported as delivered after %.1fs, "
                                "opening the gate anyway so the caller is heard",
                                waited)
                    self._opening_delivered = True
                else:
                    log.info("ignoring %r - arrived before the opening finished "
                             "(carrier pre-roll, not an answer)", frame.text[:60])
                    return
            await self._on_transcript(frame)
            return

        await self.push_frame(frame, direction)

    # ---- opening ----------------------------------------------------------
    async def speak_opening(self) -> None:
        """Make the agent talk first.

        This is an OUTBOUND intake call - the customer did not dial us - so
        waiting for them to speak guarantees an awkward "hello?". The
        orchestrator returns its opening line when handed an empty utterance,
        which is also where it decides whether this is a fresh call or a resumed
        leg and prefixes the "we were cut off earlier" acknowledgement.
        """
        async with self._turn_lock:
            reply = await self._invoke_agent("")
        if reply is None:
            await self.push_frame(TTSSpeakFrame(
                "Sorry, I'm having a technical problem. We'll call you back."
            ))
            await self.push_frame(EndFrame())
            return

        self._absorb(reply)
        # Before the audio is queued, so the UI shows the opening line as the
        # caller starts hearing it rather than a beat behind.
        await self._notify(reply)

        utterance = (reply.get("agentUtterance") or "").strip()
        if utterance:
            # Set before pushing, so the BotStoppedSpeakingFrame this produces
            # cannot be observed before the flag it is meant to satisfy.
            self._opening_pushed = True
            self._opening_pushed_at = time.monotonic()
            await self.push_frame(TTSSpeakFrame(utterance))
        else:
            # Nothing to deliver, so there is nothing to wait for. Listening must
            # not stay disabled or the call is deaf for its whole duration - a
            # guard against pre-roll must not become a guard against the customer.
            self._opening_delivered = True
            log.warning("no opening utterance to speak, listening immediately")

    def _absorb(self, reply: dict) -> None:
        """Take the state the orchestrator owns back off its reply.

        Several things travel back on every turn and all must be adopted, which is
        why this is one function called from both paths rather than duplicated.
        """
        # What we are about to say, kept so that hearing it again is recognised as
        # echo rather than answered as if the caller had said it. See _is_echo.
        said = (reply.get("agentUtterance") or "").strip()
        if said:
            self._last_agent_text = said

        language = reply.get("language")
        if language:
            self._language = language
            if self._stt is not None:
                self._stt.set_expected_language(language)

        # The shape of answer the NEXT utterance should have. Pushed down for the
        # same reason as the language: the orchestrator is the only thing that knows
        # what it just asked, and the STT needs it to tell an unusable transcript
        # from a real one. Set unconditionally, including to None, so a free-text
        # question clears a stale boolean and cannot make "tell me what it was"
        # look implausible.
        if self._stt is not None and hasattr(self._stt, "set_expected_answer_type"):
            self._stt.set_expected_answer_type(reply.get("expectedAnswerType"))

        # TTS language is SEPARATE from the session language, and the difference is
        # not pedantic: the language-selection question itself is bilingual, so it
        # has to be synthesised from the Hindi reference (Devanagari read from an
        # English reference is degraded for exactly the callers who need that half
        # of the sentence) while the session is not yet in any language at all.
        #
        # ttsLanguage is the orchestrator saying "synthesise THIS utterance as X".
        # Absent, it falls back to the session language, which is the normal case.
        if self._tts is not None:
            tts_language = reply.get("ttsLanguage") or language
            setter = getattr(self._tts, "set_language", None)
            if tts_language and callable(setter):
                setter(tts_language)

        # THE ORCHESTRATOR OWNS THE TURN COUNTER.
        # It writes the customer turn at turnIndex and its own reply at
        # turnIndex+1, then returns the agent's index. Counting locally instead
        # meant the next customer turn reused the index the agent reply had just
        # been written to, overwriting half the transcript in the checkpoint
        # table - which is the exact record call-drop resume reads back.
        returned = reply.get("turnIndex")
        if isinstance(returned, int) and returned > self._turn_index:
            self._turn_index = returned

        # Adopt the business callId the orchestrator resolved for this caller.
        # Without this every turn would look like a brand-new call and bootstrap
        # a fresh session, so the questionnaire would restart on each utterance
        # and the checkpoint table would fill with one-turn sessions.
        resolved = reply.get("callId")
        if resolved and resolved != self._call_id:
            log.info("call %s adopted orchestrator callId %s",
                     self._call_id, resolved)
            self._call_id = resolved

    # ---- one turn ---------------------------------------------------------
    async def _on_transcript(self, frame: TranscriptionFrame) -> None:
        if self._complete:
            return
        text = (frame.text or "").strip()
        if not text:
            return

        low_conf = bool((getattr(frame, "result", None) or {}).get("lowConfidence"))
        async with self._turn_lock:
            reply = await self._invoke_agent(text, low_confidence=low_conf)

        if reply is None:
            # The agent is unreachable. Say something rather than leaving the
            # caller in silence wondering whether the line dropped.
            await self.push_frame(TTSSpeakFrame(
                "Sorry, I'm having a technical problem. Let me call you back shortly."
            ))
            await self.push_frame(EndFrame())
            return

        # Keep the STT's script guard aligned with the language the orchestrator
        # has settled on - it switches when the caller switches.
        self._absorb(reply)
        # `text` is the transcript this turn was admitted on, so the surface shows
        # the caller and the agent as one ordered exchange.
        await self._notify(reply, text)

        utterance = (reply.get("agentUtterance") or "").strip()
        if utterance:
            await self.push_frame(TTSSpeakFrame(utterance))

        if reply.get("escalated"):
            log.warning("call %s escalated to a human", self._call_id)

        if reply.get("isComplete") or reply.get("stopQuestionnaire"):
            self._complete = True
            log.info("questionnaire finished for %s after %d turns",
                     self._call_id, self._turn_index)
            # Let the closing words play out before tearing the pipeline down.
            # EndFrame is not a SystemFrame, so it queues behind the audio
            # already in flight rather than truncating it.
            await self.push_frame(EndFrame())

    async def _invoke_agent(self, utterance: str,
                            low_confidence: bool = False) -> dict | None:
        """Invoke the AgentCore runtime for one turn."""
        self._turn_index += 1
        # FIELD NAMES ARE THE CONTRACT. agent/app.py reads body["userUtterance"];
        # sending "utterance" meant every turn arrived EMPTY, and an empty
        # utterance is the orchestrator's signal for "opening turn", so the bot
        # would have re-asked its first question forever while appearing healthy.
        payload = {
            "callId": self._call_id,
            "userUtterance": utterance,
            "turnIndex": self._turn_index,
        }
        if low_confidence:
            # The STT marked this transcript low-confidence. The orchestrator uses
            # this to avoid ACTING on it - a doubted "Jardiance." for a height
            # answer went to the condition classifier and became an Osteoarthritis
            # read-back, which is how one bad transcript turned into a phantom
            # diagnosis dialogue. (Whisper-only STT does not currently emit this
            # flag, so this path is dormant; the handling is kept for callers that
            # do set sttConfidence.)
            payload["sttConfidence"] = 0.2
        # Let the orchestrator keep following the caller's language rather than
        # defaulting to hinglish on every turn.
        if self._language:
            payload["language"] = self._language
        # Sent on every turn, not just the first. It costs nothing and means a
        # retry after a transient failure can still resolve the session.
        if self._caller_phone:
            payload["callerPhone"] = self._caller_phone
        if self._include_form_state:
            payload["includeFormState"] = True
            # The 38-item schema in three languages is the bulky half and it does
            # not change mid-call, so it is not sent on every turn. It is asked for
            # until the SURFACE confirms it has it, rather than once, because a
            # single dropped message would otherwise cost the UI its labels for the
            # rest of the call. See _form_schema_acked.
            if (not self._form_schema_acked
                    and self._form_schema_sends < MAX_FORM_SCHEMA_SENDS):
                payload["includeFormSchema"] = True
        started = time.perf_counter()
        try:
            # boto3 is synchronous, so it goes to a thread. Blocking the event
            # loop here would stall the audio path and stutter playback.
            resp = await asyncio.to_thread(
                self._client.invoke_agent_runtime,
                agentRuntimeArn=self._agent_arn,
                runtimeSessionId=self._session_id(),
                payload=json.dumps(payload).encode(),
            )
            body = resp["response"].read()
            reply = json.loads(body)
        except Exception:  # noqa: BLE001 - never kill the call on one bad turn
            log.exception("agent invocation failed for %s turn %d",
                          self._call_id, self._turn_index)
            return None

        elapsed = int((time.perf_counter() - started) * 1000)
        self._latencies.append(elapsed)
        log.info("turn %d agent %dms state=%s: %s", self._turn_index, elapsed,
                 reply.get("dialogueState") or "?",
                 (reply.get("agentUtterance") or "")[:70])
        # Counted only once the schema has actually come BACK, not when it was
        # requested, so a turn that failed on the way back does not burn an attempt.
        # This counts SENDS, not success - success is the browser's ack.
        if isinstance(reply.get("formState"), dict) and reply["formState"].get("items"):
            self._form_schema_sends += 1
            if self._form_schema_sends >= MAX_FORM_SCHEMA_SENDS and not self._form_schema_acked:
                log.warning("sent the form schema %d times without an ack from the "
                            "surface; giving up. The questionnaire panel will stay "
                            "empty and the page should be saying so.",
                            self._form_schema_sends)
        return reply

    def note_form_schema_delivered(self) -> None:
        """The surface has the questionnaire schema and drew it.

        Called by media/bot_web.py when the browser acknowledges over the data
        channel. Until this arrives the schema rides along on each turn, because
        the alternative - assuming the first send landed - is what left the form
        blank for an entire call.
        """
        if not self._form_schema_acked:
            log.info("surface acknowledged the form schema after %d send(s)",
                     self._form_schema_sends)
        self._form_schema_acked = True

    async def notify_recording_stored(self) -> None:
        """Tell the agent this call's audio is in S3 now, so it can be linked.

        NOT A TURN: it carries no utterance, gets no reply spoken, and arrives after
        the pipeline has torn down. It exists because of an ownership split. The
        audio only exists in this process, and this host deliberately has no
        DynamoDB permissions, so it cannot write session.recordingUri itself. The
        agent can - but the agent archives when the call ENDS, which is strictly
        before this upload finishes, so its own lookup always missed and every
        call ended up with a transcript and no linked recording.

        Sends only the callId. The S3 key is deterministic, so there is nothing
        here the agent cannot verify for itself, and it does verify.

        Never raises: this runs while the call is being torn down and a failure to
        link audio must not become a failure of the call.
        """
        if not self._call_id:
            return
        payload = {"action": "recordingStored", "callId": self._call_id}
        try:
            resp = await asyncio.to_thread(
                self._client.invoke_agent_runtime,
                agentRuntimeArn=self._agent_arn,
                runtimeSessionId=self._session_id(),
                payload=json.dumps(payload).encode(),
            )
            body = json.loads(resp["response"].read() or b"{}")
            if body.get("found"):
                log.info("agent linked the recording for %s (crmUpdated=%s)",
                         self._call_id, body.get("crmUpdated"))
            else:
                log.warning("agent found no recording to link for %s: %s",
                            self._call_id, body.get("error") or "absent")
        except Exception:  # noqa: BLE001 - teardown must not fail over this
            log.exception("could not tell the agent the recording was stored for %s",
                          self._call_id)

    async def _notify(self, reply: dict, utterance: str = "") -> None:
        """Hand one turn to the surface, if a surface asked for them.

        The caller's words travel WITH the agent's reply rather than being left to
        the transport's own transcription event, for two reasons.

        Ordering. One message carrying both halves of a turn cannot arrive out of
        order with itself. Two independent messages can, and did.

        Truthfulness. The transcription event fires for every transcript the STT
        produces, including the ones this bridge then discards as the agent's own
        speech echoing back (see _is_echo). Those would appear on screen as things
        the caller said and the agent ignored. What is shown now is exactly what
        the agent acted on.

        Wrapped because this is a rendering concern on a live medical call. A UI
        that throws - a closed data channel, a serialisation error - must not take
        the call down with it, so the failure is logged and the turn proceeds.
        """
        if self._on_reply is None:
            return
        try:
            await self._on_reply(reply, utterance)
        except Exception:  # noqa: BLE001
            log.exception("reply observer failed on turn %d; call continues",
                          self._turn_index)

    def _session_id(self) -> str:
        """AgentCore requires a session id of at least 33 characters.

        The call UUID alone is 36, but a resumed call must land on the same
        session so the orchestrator sees continuous history, so the id is derived
        from the call id rather than randomised per connection.
        """
        return f"hvi-{self._call_id}"

    @property
    def call_id(self) -> str:
        """The call id to file this call's artefacts under.

        Read AFTER the first reply, never before: this starts as the transport's
        local session uuid and is replaced by the orchestrator's business callId
        on the first reply (see _adopt above), and only the latter matches the
        transcript already archived at calls/<callId>/. Keying a recording on the
        pre-adoption value would file the audio under an id nothing else in the
        system uses, which is indistinguishable from having lost it.
        """
        return self._call_id

    @property
    def stats(self) -> dict:
        if not self._latencies:
            return {"turns": 0}
        ordered = sorted(self._latencies)
        return {
            "turns": len(ordered),
            "p50_ms": ordered[len(ordered) // 2],
            "max_ms": ordered[-1],
        }
