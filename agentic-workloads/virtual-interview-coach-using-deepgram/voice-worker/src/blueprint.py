"""In-memory interview question queue (T020) — FR-208 / FR-212.

The session-prep blueprint (assembled in the backend prep window) hands the voice worker the ordered
question plan at session start. This module turns that plan into a resident in-memory queue the live
loop walks: MAIN questions are served from the queue in order, with NO live bank query, NO embedding,
and NO DB on the response_gap clock (the whole point of prep — SC-003 / latency invariant).

The advance-vs-follow-up decision itself is made live from the running transcript (FR-212a, US2);
this module just provides the mechanism: `current()` is the archetype under discussion, `advance()`
moves to the next planned MAIN question, and a follow-up simply does NOT advance (it stays on the
current archetype so the persisted turn keeps the same archetype_id — SC-005 containment).

Each queue item carries the archetype's competency + prompt_template + follow_up_prompts (loaded with
the plan) so a contained follow-up has its seed probes available without any new query. No scores
are carried (Principle II / F002).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .reply.interface import ArchetypeIntent, DifficultyProfile

# STAR elements in the order a behavioral answer supplies them; the funnel walks this sequence and
# probes for the next one not yet credited (FR-209).
STAR_SEQUENCE = ("situation", "task", "action", "result")

# Minimum salient word count for a student answer to be treated as substantive (covers the element
# currently being probed). A thinner answer is "vague": the funnel re-probes the SAME element rather
# than advancing (this is what turns a deliberately vague answer into a probing follow-up — the US2
# independent test). A structural heuristic over the transcript, NOT a score (FR-212a / Principle II).
_SUBSTANTIVE_MIN_WORDS = 6

# Fallback follow-up budget per archetype when the session carries no difficulty profile (generic).
_DEFAULT_MAX_FOLLOWUPS = 2


@dataclass
class PlannedQuestion:
    """One archetype in the plan — the unit the live loop interviews on."""

    archetype_id: str
    competency: str
    question_type: str
    prompt_template: str
    follow_up_prompts: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict) -> "PlannedQuestion":
        """Build from a retrieval/prep row (extra keys like scoring_guidance/distance are ignored —
        scoring_guidance is for G3, not loaded into the live queue)."""
        return cls(
            archetype_id=str(row["id"]) if "id" in row else str(row["archetype_id"]),
            competency=row["competency"],
            question_type=row.get("question_type", "behavioral"),
            prompt_template=row["prompt_template"],
            follow_up_prompts=list(row.get("follow_up_prompts") or []),
        )


class BlueprintQueue:
    """The resident, ordered MAIN-question queue for one session.

    Walked by the live loop: `current()` is the archetype being interviewed; `advance()` steps to the
    next planned main question (used when the loop decides to move on); a follow-up does not advance.
    No I/O — everything is in memory (FR-208).
    """

    def __init__(self, questions: list[PlannedQuestion], opening_archetype_id: str | None = None) -> None:
        self._questions = questions
        self._index = 0
        # If an opening archetype was chosen at prep, start the walk there so the first main question
        # matches the opening selection; otherwise start at the top of the ranked plan.
        if opening_archetype_id is not None:
            for i, q in enumerate(questions):
                if q.archetype_id == opening_archetype_id:
                    self._index = i
                    break

    @classmethod
    def from_plan(cls, rows: list[dict], opening_archetype_id: str | None = None) -> "BlueprintQueue":
        """Build the queue from the prep plan rows (ordered_archetype_ids order)."""
        return cls([PlannedQuestion.from_row(r) for r in rows], opening_archetype_id)

    def __len__(self) -> int:
        return len(self._questions)

    @property
    def position(self) -> int:
        return self._index

    @property
    def is_exhausted(self) -> bool:
        """True once every planned main question has been served (advanced past the last)."""
        return self._index >= len(self._questions)

    def current(self) -> PlannedQuestion | None:
        """The archetype currently being interviewed (the question a follow-up stays within)."""
        if self.is_exhausted:
            return None
        return self._questions[self._index]

    def peek_next(self) -> PlannedQuestion | None:
        """The next planned main question without advancing (None if this is the last)."""
        nxt = self._index + 1
        return self._questions[nxt] if nxt < len(self._questions) else None

    def advance(self) -> PlannedQuestion | None:
        """Move to the next planned MAIN question and return it (None when the plan is exhausted).

        A follow-up must NOT call this — it keeps `current()` so the turn stays within the same
        competency and is persisted under the same archetype_id (SC-005)."""
        self._index += 1
        return self.current()

    def competencies_remaining(self) -> list[str]:
        """Distinct competencies from the current position onward (for blueprint-coverage checks)."""
        seen: list[str] = []
        for q in self._questions[self._index :]:
            if q.competency not in seen:
                seen.append(q.competency)
        return seen


@dataclass
class TurnPlan:
    """The plan for ONE coach turn: which archetype, whether it is a follow-up, and which STAR
    element it targets. The structural facts here (archetype_id, is_followup, targeted_star_element)
    are exactly what persistence writes to conversation_turn (FR-212a) and what the SC-005 containment
    check reads. NO scores (Principle II)."""

    intent: ArchetypeIntent | None  # the archetype to ground this turn (None when the plan is exhausted)
    is_followup: bool
    targeted_star_element: str | None
    advanced: bool  # True if this turn moved to a NEW planned main question
    exhausted: bool  # True if the plan ran out (no current archetype) — caller wraps up / degrades


class FunnelPlanner:
    """Decides advance-vs-probe for each coach turn from the running transcript (FR-212a).

    This is the adaptive-follow-up brain, and it is deliberately a small DETERMINISTIC state machine
    over the transcript — NOT a real-time scoring subsystem (the contract forbids one) and nothing it
    does touches the response_gap clock (it is in-memory, microseconds). The actual follow-up TEXT is
    generated live by the persona behind the seam; this planner only decides WHETHER the next turn is
    a new main question (advance the queue) or a contained probe (stay on the current archetype), and
    which STAR element that turn targets.

    The funnel model: each archetype opens with a behavioral MAIN question (targets `situation`). Each
    substantive student answer credits the next STAR element in sequence; a vague answer credits
    nothing, so the coach RE-PROBES the same element (this is what turns a deliberately vague answer
    into a probing follow-up — the US2 independent test). The archetype is done — and the queue
    advances to the next main question — once all STAR elements are covered OR the difficulty tier's
    follow-up budget is spent (so Easy moves on sooner than Difficult — SC-004). A probe never
    advances, so `current()` and therefore the persisted `archetype_id` stay the originating main
    question's (FR-211 / SC-005 containment).
    """

    def __init__(
        self,
        queue: BlueprintQueue,
        difficulty_profile: DifficultyProfile | None = None,
        followup_ceiling: int | None = None,
        followups_per_question: int | None = None,
    ) -> None:
        self._queue = queue
        # probing_intensity (1..5) is the per-archetype follow-up budget: Easy=2 drills lightly and
        # moves on; Difficult=5 keeps drilling for specifics. Generic (no profile) uses a small default.
        budget = (
            difficulty_profile.probing_intensity if difficulty_profile is not None else _DEFAULT_MAX_FOLLOWUPS
        )
        # Optional hard ceiling on follow-ups per competency, on TOP of the tier budget. Unset (None)
        # by default so the difficulty tiers — and the SC-004 Easy-vs-Difficult distinctness eval —
        # are unchanged. A deployment may set it (env) to keep a single competency from being drilled
        # too long in a real interview without re-tuning the tier levers.
        if followup_ceiling is not None:
            budget = min(budget, followup_ceiling)
        # Session-length bound: when a per-question follow-up factor is passed (a duration-bounded
        # session), cap each archetype's follow-ups to it so the total-turn budget spreads across the
        # planned mains rather than being burned on the first competency. Gated on the arg being
        # passed, so the SC-004 harness (which builds the planner WITHOUT it) is byte-for-byte
        # unchanged. Only ever LOWERS the cap; never raises it above the tier budget.
        if followups_per_question is not None:
            budget = min(budget, followups_per_question)
        self._max_followups = budget
        self._asked: set[str] = set()  # archetype_ids whose opening main question has been asked
        self._covered: dict[str, list[str]] = {}  # archetype_id -> STAR elements credited so far
        self._followups: dict[str, int] = {}  # archetype_id -> follow-ups issued on it

    @staticmethod
    def _is_substantive(student_text: str) -> bool:
        """Structural (not scored) check: did the answer carry enough content to credit a STAR step?

        A thin answer ("um, not sure", "it went well") leaves the funnel on the same element so the
        coach re-probes; a fuller answer advances it. Word-count heuristic only — no quality judgement
        (Principle II)."""
        words = [w for w in (student_text or "").split() if len(w) >= 2]
        return len(words) >= _SUBSTANTIVE_MIN_WORDS

    def _intent_for(self, q: PlannedQuestion, covered: list[str]) -> ArchetypeIntent:
        return ArchetypeIntent(
            archetype_id=q.archetype_id,
            competency=q.competency,
            prompt_template=q.prompt_template,
            follow_up_prompts=list(q.follow_up_prompts),
            covered_star=list(covered),
        )

    def next_turn(self, student_text: str) -> TurnPlan:
        """Plan the upcoming coach turn given the student's most recent answer.

        Called once per student turn. The first time an archetype is seen it emits that archetype's
        opening MAIN question (the student_text — an answer to the prior turn — is NOT credited to the
        new archetype). On subsequent turns the student_text is an answer WITHIN the current archetype:
        it is credited and the planner decides advance-vs-probe."""
        current = self._queue.current()
        if current is None:
            return TurnPlan(intent=None, is_followup=False, targeted_star_element=None, advanced=False, exhausted=True)

        aid = current.archetype_id
        if aid not in self._asked:
            # First contact with this archetype: ask its opening behavioral (main) question.
            self._asked.add(aid)
            self._covered.setdefault(aid, [])
            self._followups.setdefault(aid, 0)
            return TurnPlan(
                intent=self._intent_for(current, self._covered[aid]),
                is_followup=False,
                targeted_star_element=STAR_SEQUENCE[0],  # an opener primarily elicits the situation
                advanced=False,
                exhausted=False,
            )

        # The student just answered within the current archetype. Credit a STAR step if substantive.
        covered = self._covered.setdefault(aid, [])
        if self._is_substantive(student_text) and len(covered) < len(STAR_SEQUENCE):
            covered.append(STAR_SEQUENCE[len(covered)])

        star_complete = len(covered) >= len(STAR_SEQUENCE)
        budget_spent = self._followups.get(aid, 0) >= self._max_followups
        if star_complete or budget_spent:
            nxt = self._queue.advance()
            if nxt is None:
                return TurnPlan(
                    intent=None, is_followup=False, targeted_star_element=None, advanced=True, exhausted=True
                )
            self._asked.add(nxt.archetype_id)
            self._covered.setdefault(nxt.archetype_id, [])
            self._followups.setdefault(nxt.archetype_id, 0)
            return TurnPlan(
                intent=self._intent_for(nxt, self._covered[nxt.archetype_id]),
                is_followup=False,
                targeted_star_element=STAR_SEQUENCE[0],
                advanced=True,
                exhausted=False,
            )

        # Stay on the current archetype: a contained probe for the next missing STAR element (SC-005).
        self._followups[aid] = self._followups.get(aid, 0) + 1
        target = STAR_SEQUENCE[len(covered)] if len(covered) < len(STAR_SEQUENCE) else STAR_SEQUENCE[-1]
        return TurnPlan(
            intent=self._intent_for(current, covered),
            is_followup=True,
            targeted_star_element=target,
            advanced=False,
            exhausted=False,
        )


# A small set of competency-agnostic probes used ONLY as the degradation fallback (FR-221 / R8): if
# the live substantive reply stalls past the turn budget or errors, the coach speaks one of these
# instead of going quiet. They reference the current competency by name so the fallback stays
# CONTAINED (SC-005 preserved even on degradation) and never leads toward an answer (FR-210).
_CONTAINED_FALLBACK_PROBES = (
    "Could you walk me through a specific example of that?",
    "What was your role in that, specifically?",
    "Can you tell me more about how you approached it?",
    "What was the outcome, and how did you measure it?",
)


def contained_fallback_reply(plan: "TurnPlan | None", turn_index: int) -> str:
    """A safe, CONTAINED probe to speak when live generation degrades (FR-221 / R8).

    Stays within the current competency (so SC-005 holds even on the fallback path) and probes for
    the turn's targeted STAR element without leading. When the plan is exhausted/absent (generic G1
    or end of plan) it degrades to a neutral, non-leading probe. Deterministic — no I/O, no LLM."""
    probe = _CONTAINED_FALLBACK_PROBES[turn_index % len(_CONTAINED_FALLBACK_PROBES)]
    if plan is not None and plan.intent is not None:
        competency = plan.intent.competency
        return f"Staying on {competency} for a moment — {probe[0].lower() + probe[1:]}"
    return probe
