"""Custom Pipecat FrameProcessors carrying the load-bearing logic that has no native Pipecat
equivalent (Feature 007). See specs/007-pipecat-adoption/plan.md."""

from .deadline import DeadlineProcessor
from .interview_director import InterviewDirector
from .latency_observer import LatencyObserver, LatencyProbe
from .lead_clause import (
    LEAD_INS,
    STRATEGY_NATIVE,
    STRATEGY_PROCESSOR,
    LeadClauseProcessor,
)
from .recording import RecordingProcessor
from .turn_gate import MODE_AUTO, MODE_PTT, TurnGateProcessor

__all__ = [
    "DeadlineProcessor",
    "InterviewDirector",
    "LatencyObserver",
    "LatencyProbe",
    "LeadClauseProcessor",
    "LEAD_INS",
    "STRATEGY_NATIVE",
    "STRATEGY_PROCESSOR",
    "RecordingProcessor",
    "TurnGateProcessor",
    "MODE_AUTO",
    "MODE_PTT",
]
