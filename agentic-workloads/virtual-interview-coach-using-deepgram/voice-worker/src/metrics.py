"""Latency measurement + CloudWatch emission (T014 base, T022 capture).

Defines the response_gap clocks (contracts/metrics-contract.md), captures the three
sub-components per coach turn, writes a turn_latency row, and emits the same values to
CloudWatch as custom metrics. Measurement is a first-class concern (Constitution I/II).

The clock is monotonic and lives entirely in the worker (single clock, no cross-host skew).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .persistence import LatencyRecord, Persistence

log = logging.getLogger("voice_worker")

# Metric names (also the CloudWatch metric names).
M_RESPONSE_GAP = "response_gap_ms"
M_STT = "stt_finalization_ms"
M_REPLY_TTFT = "reply_ttft_ms"
M_TTS_FIRST_AUDIO = "tts_first_audio_ms"
M_ORCHESTRATION = "orchestration_ms"

# Per-session product-quality metrics (the evidence loop: how often do sessions degrade to the
# fallback probe, how often do students barge in, how long/dense are sessions, how do they end).
M_SESSION_COUNT = "session_count"
M_SESSION_DURATION = "session_duration_s"
M_SESSION_TURNS = "session_turns"
M_SESSION_FALLBACKS = "session_fallbacks"
M_SESSION_BARGE_INS = "session_barge_ins"


@dataclass
class SessionStats:
    """Per-session counters incremented by the pipeline as events happen (counts only — no PII).

    student_turns/coach_turns are the persisted-turn counts (PersistenceWriter increments them);
    fallbacks counts contained-fallback probes (reply degradation, FR-221); barge_ins counts
    interruptions that landed while the coach was actually speaking (true barge-ins, not every
    push-to-talk press)."""

    student_turns: int = 0
    coach_turns: int = 0
    fallbacks: int = 0
    barge_ins: int = 0
    # Set by _wrap_up before it injects its InterruptionFrame (which cancels any in-flight coach
    # reply); DeadlineProcessor checks it so the wrap-up's own interruption is never counted as a
    # student barge-in.
    wrap_up_started: bool = False
    # Construction time is only a fallback anchor — the pipeline re-anchors on the WebRTC
    # 'connected' event (mark_started) so duration_s measures the SPOKEN session, not the
    # seconds of ICE negotiation between /offer and media-up.
    started_monotonic: float = field(default_factory=time.monotonic)

    @property
    def turns_total(self) -> int:
        return self.student_turns + self.coach_turns

    def mark_started(self) -> None:
        """Re-anchor the duration clock at media-connect (idempotent by construction: callers
        invoke it once, from the connection's 'connected' handler)."""
        self.started_monotonic = time.monotonic()

    def duration_s(self) -> int:
        return int(round(time.monotonic() - self.started_monotonic))


def _now_ms() -> float:
    return time.monotonic() * 1000.0


@dataclass
class TurnTimer:
    """Captures the instants that define response_gap and its sub-components for one coach turn.

    Usage within the loop:
        timer = TurnTimer.start_at_end_of_speech()
        ... STT yields usable final transcript ...
        timer.mark_stt_final()
        ... reply request issued ...
        timer.mark_reply_requested()
        ... first reply token ...
        timer.mark_reply_first_token()
        ... first reply chunk handed to TTS ...
        timer.mark_tts_requested()
        ... first audio frame to transport ...
        timer.mark_first_audio()
        rec = timer.to_record(reply_provider)
    """

    t_end_of_speech: float
    t_stt_final: float | None = None
    t_reply_requested: float | None = None
    t_reply_first_token: float | None = None
    t_tts_requested: float | None = None
    t_first_audio: float | None = None

    @classmethod
    def start_at_end_of_speech(cls) -> "TurnTimer":
        return cls(t_end_of_speech=_now_ms())

    def mark_stt_final(self) -> None:
        self.t_stt_final = _now_ms()

    def mark_reply_requested(self) -> None:
        self.t_reply_requested = _now_ms()

    def mark_reply_first_token(self) -> None:
        self.t_reply_first_token = _now_ms()

    def mark_tts_requested(self) -> None:
        self.t_tts_requested = _now_ms()

    def mark_first_audio(self) -> None:
        self.t_first_audio = _now_ms()

    def to_record(self, reply_provider: str) -> LatencyRecord:
        if self.t_first_audio is None:
            raise ValueError("first_audio not marked; turn incomplete")
        gap = int(round(self.t_first_audio - self.t_end_of_speech))

        stt = (
            int(round(self.t_stt_final - self.t_end_of_speech))
            if self.t_stt_final is not None
            else 0
        )
        # reply TTFT = reply request -> first token
        if self.t_reply_requested is not None and self.t_reply_first_token is not None:
            ttft = int(round(self.t_reply_first_token - self.t_reply_requested))
        else:
            ttft = 0
        # TTS first audio = first reply chunk handed to TTS -> first audio frame
        if self.t_tts_requested is not None:
            tts = int(round(self.t_first_audio - self.t_tts_requested))
        else:
            tts = 0

        # Stages overlap (R1); orchestration is the residual that makes a blown budget
        # diagnosable. It can be small/near-zero and may be negative under heavy overlap —
        # clamp at 0 for the diagnostic.
        orchestration = max(0, gap - stt - ttft)

        return LatencyRecord(
            response_gap_ms=max(0, gap),
            stt_finalization_ms=max(0, stt),
            reply_ttft_ms=max(0, ttft),
            tts_first_audio_ms=max(0, tts),
            orchestration_ms=orchestration,
            reply_provider=reply_provider,
        )


class MetricsSink:
    """Writes a turn_latency row and emits CloudWatch custom metrics (T022).

    The CloudWatch client is optional: if boto3/credentials are unavailable (e.g. local
    harness runs without AWS), emission is skipped but the durable DB row is still written,
    which is what the gate verdict aggregates.
    """

    def __init__(
        self,
        persistence: Persistence | None,
        namespace: str = "InterviewCoach/G1",
        cw_client=None,
    ) -> None:
        self._p = persistence
        self._namespace = namespace
        self._cw = cw_client
        # In-flight fire-and-forget emission tasks (strong refs so they aren't GC'd mid-flight).
        self._bg_tasks: set[asyncio.Task] = set()

    async def record_turn(
        self,
        session_id: str,
        turn_id: str,
        rec: LatencyRecord,
        network_path: str | None = None,
    ) -> None:
        measured_at = datetime.now(timezone.utc)
        # The durable turn_latency row needs persistence AND a real turn_id (the FK to
        # conversation_turn). When the DB is disabled/unreachable the gate measurement still
        # emits to CloudWatch and is logged by the loop — measurement never depends on the DB.
        if self._p is not None and turn_id:
            await self._p.record_latency(session_id, turn_id, rec, measured_at)
        self._emit_cloudwatch(rec, network_path)

    async def record_session(self, stats: SessionStats, end_reason: str, reply_provider: str) -> None:
        """Emit the per-session summary batch (the product-evidence metrics) at teardown.

        One call per session, dimensioned by end_reason (completed | student_ended | dropped |
        error) + reply_provider, so dashboards can answer: what fraction of sessions complete vs
        drop, how often the coach degrades to a fallback probe, how interactive sessions are.
        Also logged (counts only) so the log stream carries the same evidence without CloudWatch.
        """
        log.info(
            "session summary: end_reason=%s turns=%d (student=%d coach=%d) fallbacks=%d "
            "barge_ins=%d duration_s=%d",
            end_reason, stats.turns_total, stats.student_turns, stats.coach_turns,
            stats.fallbacks, stats.barge_ins, stats.duration_s(),
        )
        if self._cw is None:
            return
        dims = [
            {"Name": "end_reason", "Value": end_reason},
            {"Name": "reply_provider", "Value": reply_provider},
        ]
        metric_data = [
            {"MetricName": M_SESSION_COUNT, "Value": 1, "Unit": "Count", "Dimensions": dims},
            {"MetricName": M_SESSION_DURATION, "Value": stats.duration_s(), "Unit": "Seconds",
             "Dimensions": dims},
            {"MetricName": M_SESSION_TURNS, "Value": stats.turns_total, "Unit": "Count",
             "Dimensions": dims},
            {"MetricName": M_SESSION_FALLBACKS, "Value": stats.fallbacks, "Unit": "Count",
             "Dimensions": dims},
            {"MetricName": M_SESSION_BARGE_INS, "Value": stats.barge_ins, "Unit": "Count",
             "Dimensions": dims},
        ]
        try:
            # put_metric_data is a sync boto3 call (~ms); run it off the event loop to be safe.
            await asyncio.to_thread(
                self._cw.put_metric_data, Namespace=self._namespace, MetricData=metric_data
            )
        except Exception as exc:  # noqa: BLE001 - metrics must never crash the loop
            log.warning("session summary emission failed (%s)", type(exc).__name__)

    def _emit_cloudwatch(self, rec: LatencyRecord, network_path: str | None) -> None:
        if self._cw is None:
            return
        dims = [{"Name": "reply_provider", "Value": rec.reply_provider}]
        if network_path:
            dims.append({"Name": "network_path", "Value": network_path})
        metric_data = [
            {"MetricName": M_RESPONSE_GAP, "Value": rec.response_gap_ms, "Unit": "Milliseconds",
             "Dimensions": dims},
            {"MetricName": M_STT, "Value": rec.stt_finalization_ms, "Unit": "Milliseconds",
             "Dimensions": dims},
            {"MetricName": M_REPLY_TTFT, "Value": rec.reply_ttft_ms, "Unit": "Milliseconds",
             "Dimensions": dims},
            {"MetricName": M_TTS_FIRST_AUDIO, "Value": rec.tts_first_audio_ms,
             "Unit": "Milliseconds", "Dimensions": dims},
        ]
        if rec.orchestration_ms is not None:
            metric_data.append(
                {"MetricName": M_ORCHESTRATION, "Value": rec.orchestration_ms,
                 "Unit": "Milliseconds", "Dimensions": dims}
            )
        # LATENCY RULE: record_turn fires inside the LatencyObserver's frame task at first-audio
        # time. put_metric_data is a blocking HTTPS call (~20-100ms, a full botocore timeout when
        # the network misbehaves) — running it inline stalls every frame queued behind this
        # processor, i.e. the outbound audio whose latency is being measured. Fire-and-forget on
        # a worker thread; emission failures are logged, never awaited on this path.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sync context (offline harness): no audio path to stall, blocking is fine.
            try:
                self._cw.put_metric_data(Namespace=self._namespace, MetricData=metric_data)
            except Exception:  # noqa: BLE001 - metrics must never crash the loop
                pass
            return
        task = loop.create_task(self._put_off_loop(metric_data))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _put_off_loop(self, metric_data: list) -> None:
        try:
            await asyncio.to_thread(
                self._cw.put_metric_data, Namespace=self._namespace, MetricData=metric_data
            )
        except Exception as exc:  # noqa: BLE001 - metrics must never crash the loop
            log.warning("turn metric emission failed (%s)", type(exc).__name__)
