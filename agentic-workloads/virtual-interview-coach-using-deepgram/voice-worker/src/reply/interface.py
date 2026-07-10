"""The swappable reply-generator interface (T013).

This is the single most important architectural seam in G1: it makes "AgentCore vs
direct-Bedrock" a config swap, so a latency failure of the primary provider is a one-line
change, not a re-architecture (research R4; contracts/reply-generator-interface.md).

The loop, turn-taking, and latency measurement MUST be identical regardless of which
implementation answers a turn (FR-014). For G1 the TurnContext is generic — NO PII,
no resume, no job scope (Constitution III).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class Utterance:
    """One prior turn in the running generic transcript."""

    speaker: str  # "student" | "coach"
    text: str


# --- G2 (F002) personalization payloads (all optional on TurnContext) --------------------
# These widen the seam additively so personalization is a richer PROMPT, not a new loop. They
# carry only DERIVED, MINIMIZED facts the live turn needs — never the raw resume/JD, never any
# score (Principle II / III; FR-218). Raw PII stays in RDS/S3; nothing raw is logged.


@dataclass
class JobScope:
    """The target role for this session (FR-203). Derived at session prep from the JD."""

    title: str
    key_requirements: list[str] = field(default_factory=list)


@dataclass
class ArchetypeIntent:
    """The competency archetype currently being interviewed + its live STAR/funnel state.

    `archetype_id`/`competency` bound a dynamic follow-up (FR-211 / SC-005 containment): a probe
    MUST stay within `competency` and is persisted with this `archetype_id`. `covered_star`
    tracks which STAR elements the student has already supplied so the persona can probe the
    MISSING one (FR-209) rather than re-asking.
    """

    archetype_id: str
    competency: str
    prompt_template: str
    follow_up_prompts: list[str] = field(default_factory=list)
    covered_star: list[str] = field(default_factory=list)  # subset of situation|task|action|result


@dataclass
class DifficultyProfile:
    """The session tier's behavioral levers (FR-214), injected into the persona (T031).

    Mirrors the difficulty_profile row; only the behavioral levers are carried live —
    scoring_strictness is intentionally omitted (F002 does not score; Principle II).
    """

    level: str  # easy | moderate | difficult
    probing_intensity: int
    curveball_rate: float
    warmth: int
    hint_policy: str
    domain_depth: int


@dataclass
class TurnContext:
    """Everything the reply generator needs for one turn.

    G1 baseline: the running interview transcript + the just-finalized student utterance.
    G2 widens this with optional personalization fields; when they are absent the generator
    behaves exactly like the generic G1 path (so existing call sites and the generic harness
    keep working unchanged — contracts/reply-seam-personalization.md). NO raw PII, NO scores.
    """

    session_id: str
    student_text: str
    history: list[Utterance] = field(default_factory=list)

    # --- G2 additions (all optional; absent -> generic G1 behavior) ---
    resume_highlights: list[str] | None = None  # salient confirmed facts (FR-204), minimized
    job_scope: JobScope | None = None  # title + key requirements (FR-203)
    target_competencies: list[str] | None = None  # the session blueprint's competencies (FR-211)
    current_archetype: ArchetypeIntent | None = None  # archetype now being interviewed
    difficulty_profile: DifficultyProfile | None = None  # the tier levers (FR-214)
    # When the loop prepends a spoken lead-in backchannel (lead-clause path), the persona must NOT
    # also open with its own acknowledgement, or every turn doubles up ("Okay, That's great. ...").
    lead_clause: bool = False
    # Roughly how many MAIN questions remain before wrap-up, so the coach can pace and answer
    # "how many questions are left?" honestly. None on the generic path. 0 means this is the last one.
    questions_remaining: int | None = None
    # When true, this turn is the end-of-interview SPOKEN DEBRIEF (F004): the reply generator uses the
    # score-free debrief prompt instead of the interviewer persona. Qualitative only (Principle II).
    is_debrief: bool = False


@runtime_checkable
class ReplyGenerator(Protocol):
    """Yield the coach's reply as a token/clause stream, first chunk ASAP.

    Contract (enforced by both implementations and relied on by the pipeline):
    - MUST yield the first chunk as early as possible (streaming first-token); MUST NOT
      buffer the whole reply before yielding. The caller forwards each chunk to TTS
      immediately (sentence-level chunking — R1), so first-audio overlaps generation.
    - MUST be cancellable: if the caller stops iterating (barge-in — R3), the
      implementation MUST abort any in-flight provider call promptly and release resources.
      In asyncio this surfaces as GeneratorExit / CancelledError on the async generator.
    - MUST yield plain text suitable for TTS (no markup, no role tags).
    - SHOULD bound its own time-to-first-token; the caller measures reply_ttft_ms.
    - MUST NOT persist raw context anywhere durable (Constitution III).
    """

    def stream(self, ctx: TurnContext) -> AsyncIterator[str]: ...


# Generic G1 interviewer framing. No personalization (that is G2).
SYSTEM_PROMPT = (
    "You are a friendly, professional job interviewer conducting a practice interview. "
    "Ask one clear, generic interview question or give a brief acknowledgement, then a "
    "follow-up question. Keep replies short and natural for speech. Be encouraging and "
    "confidence-building. Do not reference any resume or specific job; ask generic questions."
)

# Fixed opening line so the walking skeleton has a deterministic first turn.
OPENING_QUESTION = "Thanks for joining. To start, tell me a little about yourself."


def build_provider(provider: str, config) -> "ReplyGenerator":
    """Factory: construct the configured reply generator. This is the ONLY place provider
    selection happens — every other code path calls ReplyGenerator.stream(...) only.
    """
    if provider == "agentcore":
        from .agentcore import AgentCoreReplyGenerator

        return AgentCoreReplyGenerator(config)
    if provider == "bedrock_direct":
        from .bedrock_direct import BedrockDirectReplyGenerator

        return BedrockDirectReplyGenerator(config)
    raise ValueError(f"Unknown REPLY_PROVIDER: {provider!r}")
