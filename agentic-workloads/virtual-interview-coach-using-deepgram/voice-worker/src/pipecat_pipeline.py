"""Pipecat pipeline assembly (Feature 007, T017) — replaces the VoiceLoop orchestration role.

Wires the verified Pipecat 1.3 components + our custom processors into the interview loop:

    transport.input()
      -> TurnGateProcessor            (ptt vs hands-free; data-channel control + barge-in)
      -> STT (DeepgramSTTService)
      -> user context aggregator      (+ Silero VAD: end-of-speech + voice-activated barge-in)
      -> LeadClauseProcessor          (backchannel ahead of the LLM; "processor" strategy only)
      -> LLM (ReplyGeneratorLLMService adapter over our tuned bedrock_direct/agentcore)
      -> TTS (DeepgramTTSService)
      -> RecordingProcessor           (consent-gated taps; async S3 upload off the gap clock)
      -> LatencyObserver              (response_gap contract -> turn_latency + CloudWatch)
      -> transport.output()
      -> assistant context aggregator
    + DeadlineProcessor               (independent wall-clock wrap-up)

Everything maps to the immutable contracts (metrics-contract.md, the /offer contract, G6 recording).
The lead-clause vs native strategy is selected by config.lead_clause_strategy for the A/B
(contracts/latency-strategy-ab.md). The opening question + session-start handoff are driven by the
server once the connection is ready (it owns the token verify + plan minimization).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, InterruptionFrame, STTMuteFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_turn_strategies import (
    UserTurnStrategies,
    default_user_turn_stop_strategies,
)
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from .turn_start_grace import GraceWindowMinWordsUserTurnStartStrategy
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from .config import Config
from .metrics import SessionStats
from .processors import (
    DeadlineProcessor,
    LatencyObserver,
    LatencyProbe,
    LeadClauseProcessor,
    RecordingProcessor,
    TurnGateProcessor,
)
from .processors.interview_director import InterviewDirector
from .processors.persistence_writer import CoachTurnTap, PersistenceWriter, StudentTurnTap
from .reply.interface import OPENING_QUESTION, TurnContext, Utterance, build_provider
from .reply.pipecat_adapter import ReplyGeneratorLLMService

log = logging.getLogger("voice_worker")

_SAMPLE_RATE = 16000  # inbound + outbound PCM are 16kHz mono linear16, matching audio_record/G1.


def _build_stt(config: Config) -> DeepgramSTTService:
    """Deepgram STT with our tuned endpointing/keyword settings (FR-004 hands-free patience)."""
    settings = DeepgramSTTService.Settings(
        model="nova-2",
        language="en",
        interim_results=True,
        punctuate=True,
        endpointing=config.stt_endpointing_ms,
        utterance_end_ms=str(config.stt_utterance_end_ms),
        keywords=[f"{kw}:{config.stt_keyword_intensifier}" for kw in config.stt_keywords] or None,
    )
    return DeepgramSTTService(
        api_key=config.deepgram_api_key or "",
        encoding="linear16",
        sample_rate=_SAMPLE_RATE,
        settings=settings,
    )


def _build_tts(config: Config) -> DeepgramTTSService:
    """Deepgram Aura TTS (16kHz linear16 to match the transport + recording)."""
    return DeepgramTTSService(
        api_key=config.deepgram_api_key or "",
        sample_rate=_SAMPLE_RATE,
        encoding="linear16",
        settings=DeepgramTTSService.Settings(voice="aura-2-thalia-en", language="en"),
    )


def _build_vad(config: Config) -> SileroVADAnalyzer:
    """Silero VAD — on-audio end-of-speech (replaces the DTX watchdog) + barge-in trigger."""
    return SileroVADAnalyzer(
        sample_rate=_SAMPLE_RATE,
        params=VADParams(
            confidence=config.vad_confidence,
            start_secs=config.vad_start_secs,
            stop_secs=config.vad_stop_secs,
        ),
    )


def _flatten_text(content) -> str:
    """OpenAI message content may be a list of parts; join the text parts into a plain string."""
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")
        ).strip()
    return ""


def _derive_budget_s(config: Config, session_plan) -> float | None:
    """The interview's wall-clock budget in seconds, in priority order (ported from
    pipeline.py:236-243 — the chosen duration must be honored as the hard backstop):
      1. SESSION_BUDGET_MS env override (tests / ops), if set;
      2. the student's CHOSEN duration (authoritative): duration_minutes*60 + duration_grace_s;
      3. fallback to the question-count estimate (n * seconds_per_question) for a plan with no
         recorded duration.
    None only for a generic session with no plan AND no override (no bound — generic G1 path)."""
    if config.session_budget_ms:
        return config.session_budget_ms / 1000.0
    if session_plan is not None and getattr(session_plan, "duration_minutes", None):
        return session_plan.duration_minutes * 60.0 + config.duration_grace_s
    if session_plan is not None and getattr(session_plan, "plan_rows", None):
        return float(len(session_plan.plan_rows) * config.seconds_per_question)
    return None


def _build_user_turn_strategies(config: Config) -> UserTurnStrategies:
    """Decide when a student turn STARTS (which, mid-coach-speech, is voice barge-in).

    ISSUE 1 fix: the Pipecat default starts a turn on raw VAD energy
    (`VADUserTurnStartStrategy(enable_interruptions=True)`), so on a live mic breathing / room noise /
    the coach's own audio echoing back fires an interruption mid-reply and the coach's substantive
    answer is cancelled (one word, then silence). We instead require REAL transcribed speech to
    interrupt:
      - `VADUserTurnStartStrategy(enable_interruptions=False)`: VAD still detects speech-start for the
        NORMAL (coach-not-speaking) case, but does NOT raise an interruption frame on raw energy.
      - `GraceWindowMinWordsUserTurnStartStrategy(min_words=N, grace_secs=G)`: a turn (and,
        mid-coach-speech, a barge-in) only starts after N actually-transcribed words — silence/noise
        produce no transcript, so no false barge-in. N is config-driven (`VOICE_BARGE_IN_MIN_WORDS`,
        default 3). ADDITIONALLY, for G seconds after the coach starts speaking
        (`VOICE_BARGE_IN_GRACE_SECS`, default 1.5) it ignores barge-in transcripts entirely, so a
        LATE/stale transcript of the student's own just-finished turn (common when STT lags) cannot
        cancel the coach's reply. A genuine barge-in (the student keeps talking past the window)
        still interrupts.
    Push-to-talk button barge-in (TurnGateProcessor.on_control -> InterruptionFrame) is unaffected —
    only VOICE-triggered barge-in is gated. Stop strategies keep the Pipecat default (Issue 3 revisits
    the Smart-Turn end-of-turn latency separately).
    """
    return UserTurnStrategies(
        start=[
            VADUserTurnStartStrategy(enable_interruptions=False),
            GraceWindowMinWordsUserTurnStartStrategy(
                min_words=config.voice_barge_in_min_words,
                grace_secs=config.voice_barge_in_grace_secs,
            ),
            TranscriptionUserTurnStartStrategy(),
        ],
        stop=default_user_turn_stop_strategies(),
    )


def build_transport(connection: SmallWebRTCConnection, vad: SileroVADAnalyzer) -> SmallWebRTCTransport:
    """Self-hosted WebRTC transport over the (already-initialized) connection. Direct browser<->worker
    media (FR-013); audio in/out enabled at 16kHz."""
    params = TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=_SAMPLE_RATE,
        audio_out_sample_rate=_SAMPLE_RATE,
        audio_in_passthrough=True,
    )
    return SmallWebRTCTransport(webrtc_connection=connection, params=params)


def make_turn_context_enricher(session_plan, planner_holder):
    """Build the per-turn TurnContext enricher that widens the reconstructed context with the
    session's minimized grounding payload (G2) — worker-local, never into the shared LLMContext.

    `planner_holder` is the SHARED dict the InterviewDirector writes each turn ({"plan": TurnPlan,
    "questions_remaining": int}); the enricher reads it so the prompt is grounded on the CURRENT
    archetype (advance-vs-probe) and the coach knows how many questions remain — restoring the
    structured-interview behavior (Issue 2b). None plan -> identity (generic G1)."""
    if session_plan is None:
        return None

    def enrich(ctx: TurnContext) -> TurnContext:
        ctx.resume_highlights = session_plan.resume_highlights or None
        ctx.job_scope = session_plan.job_scope
        ctx.target_competencies = session_plan.target_competencies or None
        ctx.difficulty_profile = session_plan.difficulty_profile
        ctx.lead_clause = True
        # The director (runs just before the LLM) stashed this turn's plan; ground the prompt on it.
        plan = planner_holder.get("plan")
        if plan is not None:
            ctx.current_archetype = plan.intent
        qr = planner_holder.get("questions_remaining")
        if qr is not None:
            ctx.questions_remaining = qr
        return ctx

    return enrich


class InterviewPipeline:
    """Owns the assembled Pipeline + PipelineWorker for one session, plus the handles the server needs
    (the LLM context, the turn-gate for control messages, the recording processor for flush, and the
    deadline callback wiring)."""

    def __init__(self, *, config: Config, connection: SmallWebRTCConnection, session_id: str,
                 metrics, persistence, session_plan=None, on_deadline=None,
                 turn_mode: str = "auto", opening_question: str = OPENING_QUESTION) -> None:
        self.config = config
        self.session_id = session_id
        self._metrics = metrics
        self._persistence = persistence
        self._connection = connection
        self._opening_question = opening_question
        self._session_plan = session_plan
        self._opened = False
        self._wrapped_up = False
        self._runner: WorkerRunner | None = None
        self._run_task = None
        # Per-session product-quality counters (turns, fallbacks, barge-ins); the server emits
        # them as ONE summary metric batch at teardown (the evidence loop). Counts only, no PII.
        self.stats = SessionStats()
        # WORKER-LOCAL FALLBACK end_reason, used only when the DB cannot supply the authoritative
        # value (the server's finalize_session resolves the real one — backend student_ended /
        # wrap-up completed / this fallback — so the metric dimension matches RDS). Wrap-up sets
        # "completed" only AFTER its sequence succeeds; a pipeline crash sets "error".
        self.end_reason = "dropped"
        # Once-only guard for the teardown summary emission (set by the server).
        self.summary_emitted = False

        vad = _build_vad(config)
        self.transport = build_transport(connection, vad)
        self.turn_gate = TurnGateProcessor(mode=turn_mode)
        self.stt = _build_stt(config)
        self.tts = _build_tts(config)

        reply = build_provider(config.reply_provider, config)
        self._reply = reply  # reused for the score-free debrief at wrap-up (F004)
        # Shared holder: the InterviewDirector (pre-LLM) writes this turn's plan; the enricher (inside
        # the LLM adapter, runs later) reads it to ground the prompt + set questions_remaining.
        self._planner_holder: dict = {}
        enrich = make_turn_context_enricher(session_plan, self._planner_holder)
        self._fallback_count = 0  # rotation index for contained-fallback probes (NOT the turn index)
        self.llm = ReplyGeneratorLLMService(
            reply,
            session_id=session_id,
            enrich_turn_context=enrich,
            turn_budget_s=(config.turn_budget_ms / 1000.0) if config.turn_budget_ms else None,
            fallback_text=self._contained_fallback,
        )
        # ISSUE 2b: the director advances the FunnelPlanner per student turn, bounds the interview by
        # question count, and triggers wrap-up when the plan is exhausted / a budget is hit.
        self.director = InterviewDirector(
            session_plan=session_plan,
            config=config,
            planner_holder=self._planner_holder,
            on_wrap_up=self._wrap_up,
        )

        self.lead_clause = LeadClauseProcessor(strategy=config.lead_clause_strategy)
        # G6 consent gate (FR-001/FR-002): recording is ON only when the student consented for THIS
        # session (session_plan.record_audio == consent_store_materials). A generic session (no plan)
        # has no consent -> no audio. Fail-closed.
        consent = bool(session_plan is not None and getattr(session_plan, "record_audio", False))
        self.recording = RecordingProcessor(
            config=config,
            session_id=session_id,
            consent=consent,
            on_audio_uri=self._link_audio_uri,
        )
        self.latency = LatencyObserver(
            reply_provider=config.reply_provider,
            stt_finalization_ms=config.stt_finalization_ms,
            on_turn=self._record_turn,
        )
        # Two probes feed the observer the sub-component instants it can't see downstream of TTS. Both
        # are the same LatencyProbe class; each marks only the frames that actually reach its position
        # (the mark_* hooks are idempotent — first-occurrence-per-turn — so a frame seen by both probes
        # is recorded once):
        #   - pre-LLM probe (after lead-clause, before LLM): LLMContextFrame (reply requested) + the
        #     lead-in TTSSpeakFrame (tts requested) pass through here first.
        #   - post-LLM probe (after LLM, before TTS): the LLM's first LLMTextFrame (reply first token)
        #     is ONLY visible here, before TTS consumes it. (The lead-in TTSSpeakFrame also passes here
        #     but its mark_tts_requested is already a no-op — the pre-LLM probe set it.)
        self.latency_probe = LatencyProbe(self.latency)
        self.latency_probe_post = LatencyProbe(self.latency)

        # Incremental per-turn persistence (FR-003) + recording flush (G6). Two taps feed a shared
        # writer because the student transcript and the coach text are visible at different pipeline
        # positions (see persistence_writer). StudentTurnTap sits before the user aggregator;
        # CoachTurnTap sits after the LLM (before TTS). The writer reads the planner_holder so a
        # planned coach turn carries its archetype facts (FR-212a), and reports each persisted coach
        # turn_id back so the turn's LatencyRecord can FK onto it (the turn_latency row).
        self._pending_latency_rec = None
        self._pending_coach_turn_id: str | None = None
        self.persistence_writer = PersistenceWriter(
            session_id=session_id,
            persistence=persistence,
            recording=self.recording,
            planner_holder=self._planner_holder,
            on_coach_persisted=self._on_coach_persisted,
            stats=self.stats,
        )
        self.student_turn_tap = StudentTurnTap(self.persistence_writer)
        self.coach_turn_tap = CoachTurnTap(self.persistence_writer)

        # ISSUE 2a: derive the wall-clock budget so the chosen duration is honored as a hard backstop
        # (the deadline was disabled — budget_s was None because SESSION_BUDGET_MS defaults to 0 and
        # session_plan.duration_minutes was never read). Priority order ported from pipeline.py:236-243.
        budget_s = _derive_budget_s(config, session_plan)
        self.deadline = DeadlineProcessor(
            budget_s=budget_s, on_deadline=on_deadline or self._wrap_up, stats=self.stats
        )

        self.context = LLMContext()
        aggregators = LLMContextAggregatorPair(
            self.context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=vad,
                user_turn_strategies=_build_user_turn_strategies(config),
            ),
        )

        self.pipeline = Pipeline(
            [
                self.transport.input(),
                self.turn_gate,
                self.stt,
                self.student_turn_tap,   # before the user aggregator (only place TranscriptionFrame is visible)
                aggregators.user(),
                self.director,           # advance the funnel + bound the interview (may suppress the context frame)
                self.lead_clause,
                self.latency_probe,
                self.llm,
                self.coach_turn_tap,     # after the LLM (only place LLMTextFrame is visible, before TTS)
                self.latency_probe_post,
                self.tts,
                self.recording,
                self.latency,
                self.transport.output(),
                aggregators.assistant(),
                self.deadline,
            ]
        )
        self.worker = PipelineWorker(
            self.pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
                audio_in_sample_rate=_SAMPLE_RATE,
                audio_out_sample_rate=_SAMPLE_RATE,
            ),
        )

    def _contained_fallback(self) -> str:
        """A safe, contained probe spoken when the live reply degrades (FR-221), so the coach never
        goes silent. Reuses the resident-plan fallback; generic here (no planner state on this path).

        NB: _fallback_count is ONLY a rotation index for picking a different probe each time — it is
        NOT the persisted turn index (PersistenceWriter owns that). The fallback text flows as an
        LLMTextFrame through CoachTurnTap and is persisted there like any other coach turn."""
        from .blueprint import contained_fallback_reply

        self._fallback_count += 1
        self.stats.fallbacks += 1
        return contained_fallback_reply(None, self._fallback_count)

    async def _record_turn(self, rec) -> None:
        """LatencyObserver callback: emit the turn_latency record (metrics-contract.md).

        CloudWatch + the gate log fire HERE, at first audio (measurement never depends on the DB).
        The durable turn_latency row needs the coach turn's FK, which does not exist yet at
        first-audio time (the LLM is still streaming) — so the record is stashed and the row is
        written by _on_coach_persisted once PersistenceWriter has the coach turn_id.

        For a local gate run with no RDS, set GATE_LATENCY_LOG to a path: each turn's record is also
        appended as one JSON line there (the same fields aggregate.py reads), so the SC-001 verdict can
        be computed without the DB — the equivalent of the durable turn_latency rows."""
        if self._pending_latency_rec is not None:
            # The prior measured turn never persisted a coach row (e.g. barge-in dropped the reply,
            # which CoachTurnTap intentionally does not persist) — its row is dropped with it.
            log.info("session %s unattached turn_latency record dropped", self.session_id)
        self._pending_latency_rec = rec
        try:
            # Empty turn_id: MetricsSink emits CloudWatch only and skips the DB row (written later).
            await self._metrics.record_turn(self.session_id, "", rec, network_path=None)
        except Exception as exc:  # noqa: BLE001 - measurement must never crash the pipeline
            log.warning("record_turn failed (%s)", type(exc).__name__)
        gate_log = os.environ.get("GATE_LATENCY_LOG")
        if gate_log:
            try:
                row = {
                    "response_gap_ms": rec.response_gap_ms,
                    "stt_finalization_ms": rec.stt_finalization_ms,
                    "reply_ttft_ms": rec.reply_ttft_ms,
                    "tts_first_audio_ms": rec.tts_first_audio_ms,
                    "orchestration_ms": rec.orchestration_ms,
                }
                sub = self.latency.substantive_reply_ms()
                if sub is not None:
                    row["substantive_reply_ms"] = sub
                with open(gate_log, "a") as f:
                    f.write(json.dumps(row) + "\n")
            except Exception as exc:  # noqa: BLE001 - gate logging must never crash the pipeline
                log.warning("gate latency log write failed (%s)", type(exc).__name__)

    async def _link_audio_uri(self, turn_id: str, uri: str) -> None:
        if self._persistence is not None:
            try:
                await self._persistence.set_turn_audio_uri(turn_id, uri)
            except Exception as exc:  # noqa: BLE001
                log.warning("set_turn_audio_uri failed (%s)", type(exc).__name__)

    async def _on_coach_persisted(self, turn_id: str) -> None:
        """PersistenceWriter callback: a coach turn row now exists, so the stashed LatencyRecord can
        be written as its durable turn_latency row (the FK the gate/aggregate.py reads). Off the gap
        clock — this runs as the turn completes, after first audio already flowed."""
        rec, self._pending_latency_rec = self._pending_latency_rec, None
        if rec is None or self._persistence is None:
            # No measured gap for this coach turn (e.g. the opening question / closing line, which
            # are unmeasured by design) — nothing to attach.
            return
        try:
            await self._persistence.record_latency(
                self.session_id, turn_id, rec, datetime.now(timezone.utc)
            )
        except Exception as exc:  # noqa: BLE001 - measurement must never crash the pipeline
            log.warning("record_latency failed (%s)", type(exc).__name__)

    _CLOSING = (
        "That's everything I wanted to cover — thank you for walking me through your experience. "
        "That wraps up our interview for today. Take care, and best of luck!"
    )

    async def _wrap_up(self) -> None:
        """The single wrap-up path, called by BOTH the InterviewDirector (question/competency bound)
        and the DeadlineProcessor (wall-clock backstop). Idempotent.

        Sequence (the fix for the live-test "kept asking questions after wrap-up" bug):
          1. MUTE STT immediately so NO further student speech can start a new turn / generate another
             question while we wrap up (the live worker kept generating because new turns kept firing);
          2. speak a generated, SCORE-FREE qualitative debrief (F004) when a plan + transcript exist,
             else degrade to the fixed closing line (never silence);
          3. record the session end (end_reason) in RDS;
          4. EndFrame to terminate the pipeline.
        """
        if self._wrapped_up:
            return
        self._wrapped_up = True
        log.info("session %s wrapping up", self.session_id)
        # 1) Stop the interview from taking any more turns RIGHT NOW (before the slow debrief call),
        #    and CANCEL any in-flight coach reply. The live-session bug this fixes: a long student
        #    answer splits into VAD segments, each kicking the LLM; the segment that crosses the
        #    question bound triggers wrap-up while the PREVIOUS segment's reply is still streaming —
        #    that stale reply (generated under the "final question" directive, so the model tends to
        #    improvise its own goodbye, e.g. "we'll be in touch next week") then plays back-to-back
        #    with the real closing: two conflicting endings. The InterruptionFrame kills the stale
        #    stream + its queued audio exactly like a barge-in; the closing is queued after it.
        self.stats.wrap_up_started = True  # DeadlineProcessor: don't count this as a barge-in
        try:
            await self.worker.queue_frame(STTMuteFrame(mute=True))
            await self.worker.queue_frame(InterruptionFrame())
        except Exception:  # noqa: BLE001 - muting/interrupting must never block wrap-up
            pass
        # 2) Generate the debrief (qualitative only).
        debrief = ""
        if self.config.wrap_up_debrief and self._session_plan is not None:
            try:
                debrief = await self._generate_debrief()
            except Exception as exc:  # noqa: BLE001 - never let a debrief failure end in silence
                log.warning("session %s debrief failed (%s); fixed closing",
                            self.session_id, type(exc).__name__)
        # The debrief is LLM output and MUST be a statement-only wrap (the live session caught the
        # model continuing the interview with a fresh question instead). A real debrief per its
        # prompt never asks anything — reject question-like output and fall back to the fixed line
        # rather than asking the candidate something and then hanging up on them.
        if debrief and "?" in debrief:
            log.warning("session %s debrief rejected (question-like); fixed closing", self.session_id)
            debrief = ""
        closing = f"{debrief} {self._CLOSING}".strip() if debrief else self._CLOSING
        ended_cleanly = await self.wrap_up_and_end(closing)
        # The session COMPLETED only if the closing/EndFrame actually went out — a wrap-up that
        # died mid-sequence is not a completed interview and must not be reported as one.
        if ended_cleanly:
            self.end_reason = "completed"
        # 3) Record the session end so it is not left open (end_reason was never written before).
        if ended_cleanly and self._persistence is not None:
            try:
                await self._persistence.end_session(self.session_id, "completed")
            except Exception as exc:  # noqa: BLE001
                log.warning("session %s end_session failed (%s)", self.session_id, type(exc).__name__)

    async def _generate_debrief(self) -> str:
        """Generate the score-free debrief via the reply seam (port of pipeline.py:_speak_debrief).
        Builds a TurnContext over the running transcript with is_debrief=True so the provider uses the
        debrief prompt (qualitative only — no scores, Principle II). Returns the text (caller speaks +
        persists it via wrap_up_and_end). Bounded by debrief_budget_ms."""
        history = []
        for m in self.context.get_messages():
            role = m.get("role")
            content = m.get("content")
            text = content if isinstance(content, str) else _flatten_text(content)
            if not text or role not in ("user", "assistant"):
                continue
            history.append(Utterance(speaker="student" if role == "user" else "coach", text=text))
        ctx = TurnContext(session_id=self.session_id, student_text="", history=history, is_debrief=True)
        if self._session_plan is not None:
            ctx.resume_highlights = self._session_plan.resume_highlights or None
            ctx.job_scope = self._session_plan.job_scope
        parts: list[str] = []
        agen = self._reply.stream(ctx)
        try:
            async def _drain():
                async for tok in agen:
                    if tok:
                        parts.append(tok)
            await asyncio.wait_for(_drain(), timeout=self.config.debrief_budget_ms / 1000.0)
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        return "".join(parts).strip()

    # --- lifecycle -----------------------------------------------------------------------

    async def run(self) -> None:
        """Run the pipeline to completion (blocks until EndFrame/cancel). Spawn this in a task.

        Registers the connection-ready handler that speaks the opening question only AFTER DTLS/ICE
        is up (the G1 'opening line chopped off mid-word' bug — synthesizing before the browser can
        receive audio truncates it). Idempotent on the connected event."""

        @self._connection.event_handler("connected")
        async def _on_connected(_conn):  # noqa: ANN001
            # Anchor the session-duration clock at media-up (not /offer time): ICE negotiation
            # takes seconds and must not count toward the spoken-session duration metric.
            if not self._opened:
                self.stats.mark_started()
            await self._speak_opening()

        self._runner = WorkerRunner(handle_sigint=False)
        await self._runner.add_workers(self.worker)
        await self._runner.run()

    async def _speak_opening(self) -> None:
        """Speak the fixed opening question once the media plane is ready (not a measured turn)."""
        if self._opened:
            return
        self._opened = True
        try:
            # Spoken via TTSSpeakFrame (not the LLM), so it never crosses CoachTurnTap's LLM-response
            # boundary — persist it directly (parity with the old _emit_coach_turn: the transcript and
            # G3 scoring need the first question). Unplanned: no archetype facts, no latency record.
            await self.persistence_writer.on_coach_turn(self._opening_question, planned=False)
            # append_to_context=True so the assistant aggregator records the opening question as the
            # coach's first turn (the running transcript the reply LLM grounds subsequent turns on).
            await self.worker.queue_frame(
                TTSSpeakFrame(self._opening_question, append_to_context=True)
            )
            log.info("session %s opening question queued", self.session_id)
        except Exception as exc:  # noqa: BLE001 - never block the session on the opener
            log.warning("session %s opening question failed (%s)", self.session_id, type(exc).__name__)

    async def on_control(self, msg: dict) -> None:
        """Route a data-channel control message (mode / push-to-talk) to the turn gate."""
        await self.turn_gate.on_control(msg)

    async def wrap_up_and_end(self, closing: str | None = None) -> bool:
        """Speak a closing line (if given) and end the pipeline — the default deadline handler.
        Returns True when the EndFrame was queued (the session reached its natural end)."""
        try:
            if closing:
                # The fixed closing line is spoken via TTSSpeakFrame (not the LLM), so it does not pass
                # through the CoachTurnTap's LLM-response boundary; persist it directly so the transcript
                # records the sign-off (parity with the old _emit_coach_turn). Its on_coach_turn ->
                # flush_turn drains an EMPTY coach buffer here (the audio is synthesized only after the
                # frame below is queued), so the closing line's audio is not recorded — by design, the
                # session is ending (same as the old loop).
                await self.persistence_writer.on_coach_turn(closing, planned=False)
                await self.worker.queue_frame(TTSSpeakFrame(closing, append_to_context=True))
            await self.worker.queue_frame(EndFrame())
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("session %s wrap-up failed (%s)", self.session_id, type(exc).__name__)
            return False

    async def stop(self) -> None:
        """Tear the session down: cancel the worker and clean up the connection."""
        try:
            await self.worker.cancel(reason="session closed")
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._connection.disconnect()
        except Exception:  # noqa: BLE001
            pass
