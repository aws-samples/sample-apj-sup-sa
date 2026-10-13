"""HR STAR + funnel interviewer persona (T024) — FR-209/210/FR-6c, with difficulty levers (T031).

This module owns the live coach's SYSTEM PROMPT. It is the one place the personalized interviewer
"voice" is defined, consumed by `bedrock_direct.py` (the proven live provider). The reply seam,
the loop, turn-taking, and latency measurement are unchanged — personalization is a richer PROMPT
built from the widened `TurnContext`, not a new loop (contracts/reply-seam-personalization.md).

Two paths, exactly as the seam contract requires:

  - **Generic G1 path**: when the TurnContext carries no personalization fields, `build_system_prompt`
    returns the verbatim generic `SYSTEM_PROMPT`. This keeps the generic session and the SC-001
    harness BYTE-FOR-BYTE unchanged, which is what lets the same G1 harness re-measure the gate.

  - **Personalized path**: an HR-grade competency interviewer that
      * opens each competency with a behavioral question and funnels for the MISSING STAR element
        (situation / task / action / result) — FR-209;
      * probes WITHOUT leading: drills on vagueness, never hints at or supplies the answer, and may
        reference back to the resume and earlier answers — FR-210;
      * stays contained to the current competency (a follow-up never introduces a new competency —
        FR-211 / SC-005);
      * is grounded in the candidate's CONFIRMED resume highlights + the target role (FR-204/203);
      * is shaped by the session's difficulty levers so Easy/Moderate/Difficult are behaviorally
        distinct on the same input (FR-214 / SC-004 — T031).

Only DERIVED, MINIMIZED facts are used (never raw resume/JD); nothing here is logged. NO scores
(Principle II) — the persona never asks the model to rate the answer; assessment is G3.
"""

from __future__ import annotations

from .interface import SYSTEM_PROMPT, ArchetypeIntent, DifficultyProfile, TurnContext

# The four STAR elements, in the order a complete behavioral answer supplies them. The persona
# funnels toward whichever of these the candidate has NOT yet covered (FR-209).
STAR_ELEMENTS = ("situation", "task", "action", "result")


def _difficulty_directives(profile: DifficultyProfile) -> list[str]:
    """Map the tier's behavioral levers to plain interviewer directives (FR-214 / SC-004 — T031).

    Each lever becomes a concrete instruction so Easy vs Difficult are observably different on the
    SAME student answer (the SC-004 distinctness target): probing depth, curveball injection, tone,
    whether hints are offered, and how deep role-specific questioning goes. `scoring_strictness` is
    intentionally NOT surfaced here — F002 does not score (Principle II).
    """
    d: list[str] = [f"Interview difficulty tier: {profile.level}."]

    # probing_intensity 1..5 — how hard a vague answer is drilled before moving on.
    if profile.probing_intensity <= 2:
        d.append(
            "Probing: keep follow-ups gentle and brief — at most one short clarifying nudge before "
            "moving on; do not press a struggling candidate."
        )
    elif profile.probing_intensity >= 4:
        d.append(
            "Probing: be persistent and rigorous — keep drilling for concrete specifics (names, "
            "numbers, your exact role and actions) until the answer is complete, even across "
            "several follow-ups within this competency."
        )
    else:
        d.append(
            "Probing: ask a focused follow-up for any missing specifics, then move on once the "
            "answer is reasonably complete."
        )

    # curveball_rate 0..1 — probability of an unexpected/stress angle.
    if profile.curveball_rate >= 0.35:
        d.append(
            "Occasionally introduce a challenging curveball within this competency — a tougher "
            "what-if, a stress angle, or a respectful push-back on a weak point — to test "
            "composure under pressure."
        )
    elif profile.curveball_rate >= 0.10:
        d.append("You may occasionally add a mild challenge or what-if, but keep it fair.")
    else:
        d.append("Avoid curveballs or stress questions; keep the line of questioning predictable.")

    # warmth 1..5 — conversational tone.
    if profile.warmth >= 4:
        d.append("Tone: warm, encouraging, and supportive; put the candidate at ease.")
    elif profile.warmth <= 2:
        d.append("Tone: neutral, professional, and businesslike; minimal reassurance.")
    else:
        d.append("Tone: professional and even, lightly encouraging.")

    # hint_policy: offer | minimal | none — whether the coach scaffolds toward an answer.
    if profile.hint_policy == "offer":
        d.append(
            "If the candidate is stuck, you MAY offer a gentle scaffold or example of the KIND of "
            "detail you are looking for — but never supply the actual answer for them."
        )
    elif profile.hint_policy == "none":
        d.append(
            "Offer NO hints or scaffolding. If the candidate is stuck, restate the question once "
            "and let them work it out."
        )
    else:  # minimal
        d.append("Offer hints only sparingly, and only about what kind of detail is missing.")

    # domain_depth 1..5 — how deep into role-specific specifics the questioning goes.
    if profile.domain_depth >= 4:
        d.append(
            "Go deep into role-specific specifics: ask about concrete tools, trade-offs, edge "
            "cases, and decisions particular to this role."
        )
    elif profile.domain_depth <= 2:
        d.append("Keep questions general; do not require deep role-specific technical detail.")

    return d


def _archetype_directives(arch: ArchetypeIntent) -> list[str]:
    """Funnel directives for the competency currently under discussion (FR-209/211 / SC-005).

    Bounds the turn to `arch.competency` (containment) and tells the coach which STAR element to
    target next, given what the candidate has already covered. `covered_star` is populated live from
    the transcript by the advance-vs-probe logic (T025); when empty the coach opens the competency.
    """
    d: list[str] = [
        f"The current competency under discussion is '{arch.competency}'. Stay within this "
        "competency for this turn; a follow-up MUST go deeper here and MUST NOT switch to a "
        "different competency."
    ]

    covered = [s for s in (arch.covered_star or []) if s in STAR_ELEMENTS]
    missing = [s for s in STAR_ELEMENTS if s not in covered]
    if not covered:
        d.append(
            "Open this competency with a single clear behavioral question that invites a STAR "
            "answer (a specific past situation, their task, the actions they took, and the result)."
        )
    elif missing:
        covered_str = ", ".join(covered)
        next_target = missing[0]
        d.append(
            f"The candidate has already described: {covered_str}. If their answer is vague or "
            f"incomplete, probe specifically for the missing '{next_target}' element — ask for the "
            "concrete specifics of it WITHOUT leading or suggesting what the answer should be."
        )
    else:
        d.append(
            "The candidate has given a complete STAR answer for this competency. Acknowledge it "
            "briefly; a short depth follow-up is optional before moving on."
        )
    return d


def build_system_prompt(ctx: TurnContext) -> str:
    """Build the coach's system prompt for one turn from the (possibly widened) TurnContext.

    Returns the verbatim generic G1 `SYSTEM_PROMPT` when no personalization fields are present (the
    generic path / harness stays byte-for-byte unchanged — SC-001). Otherwise assembles the HR
    STAR+funnel persona, grounded in the confirmed resume + role and shaped by the difficulty tier.
    """
    # End-of-interview spoken debrief uses its own score-free prompt (F004 / Principle II).
    if ctx.is_debrief:
        return build_debrief_prompt(ctx)

    if not (
        ctx.resume_highlights
        or ctx.job_scope
        or ctx.target_competencies
        or ctx.current_archetype
        or ctx.difficulty_profile
    ):
        return SYSTEM_PROMPT

    # On the lead-clause path the loop already speaks a short bridge ("Thanks, that gives me a good
    # picture —") before this reply, so the persona must CONTINUE that sentence — otherwise every
    # turn doubles the acknowledgement, or the reply restarts cold and the bridge sounds
    # disconnected (live-use feedback). On the plain path a single brief acknowledgement is welcome.
    if ctx.lead_clause:
        opening = (
            "Ask ONE question per turn. A short bridge phrase ending in a dash (like 'Thanks, that "
            "gives me a good picture —') is ALREADY being spoken for you, and your reply is read "
            "aloud immediately after it as ONE continuous utterance. So: do NOT open with any "
            "acknowledgement, filler, or greeting of your own ('Got it' / 'Makes sense' / 'Great') "
            "— begin mid-flow, as a natural continuation of that bridge, going straight into your "
            "comment or question. Then stop and wait for the answer."
        )
    else:
        opening = (
            "Ask ONE question per turn (optionally a brief acknowledgement first), then stop and "
            "wait for the answer."
        )

    parts: list[str] = [
        "You are a seasoned HR interviewer conducting a realistic practice job interview using a "
        "competency-based STAR and funnel methodology. " + opening + " Keep every reply short and "
        "natural for spoken conversation — one or two sentences. Never lecture, never list. You are "
        "interviewing, not coaching: do not evaluate, score, or grade the answer out loud."
    ]

    if ctx.job_scope is not None:
        parts.append(f"Target role: {ctx.job_scope.title}.")
        if ctx.job_scope.key_requirements:
            reqs = "; ".join(ctx.job_scope.key_requirements[:6])
            parts.append(f"Key requirements for this role: {reqs}.")

    if ctx.resume_highlights:
        highlights = "\n".join(f"- {h}" for h in ctx.resume_highlights[:8])
        parts.append(
            "Ground your questions in the candidate's CONFIRMED background below. Reference these "
            "specifics naturally and may refer back to them or to the candidate's earlier answers; "
            "do not invent facts beyond them:\n" + highlights
        )

    # Core HR method (FR-209/210): funnel for the missing STAR element; probe, don't lead.
    parts.append(
        "Method: open a competency with a behavioral question, then FUNNEL — if an answer is vague "
        "or missing specifics, probe deeper for the concrete missing detail. PROBE, DON'T LEAD: "
        "draw the specifics out of the candidate; never hint at, supply, or hint toward the answer "
        "you are hoping to hear."
    )

    if ctx.current_archetype is not None:
        parts.extend(_archetype_directives(ctx.current_archetype))
    elif ctx.target_competencies:
        comps = ", ".join(ctx.target_competencies[:6])
        parts.append(f"This interview covers these competencies: {comps}.")

    # Progress / pacing: let the coach answer "how many questions are left?" honestly and wind down
    # toward the end rather than opening new threads it cannot finish.
    if ctx.questions_remaining is not None:
        if ctx.questions_remaining <= 0:
            parts.append(
                "This is the FINAL question of the interview. If the candidate asks how many "
                "questions remain, tell them this is the last one. Do not open a new topic. "
                "IMPORTANT: never end the interview yourself — no goodbyes, no sign-offs, and no "
                "next-steps promises (e.g. 'we'll be in touch'); the system speaks the official "
                "closing. If the candidate has fully answered and there is nothing left to probe, "
                "reply with a single brief acknowledgement sentence and nothing else."
            )
        else:
            parts.append(
                f"About {ctx.questions_remaining} more main question(s) remain after this one. If "
                "the candidate asks how many are left, answer honestly with roughly this number."
            )

    if ctx.difficulty_profile is not None:
        parts.extend(_difficulty_directives(ctx.difficulty_profile))

    return " ".join(parts)


# Spoken end-of-interview debrief (F004 / G4 front half). MUST stay QUALITATIVE and SCORE-FREE
# (Constitution II / Flag F6): all numbers live only in the written report, so the spoken debrief and
# the report can never contradict. Grounded in the actual conversation already in the model's context.
DEBRIEF_SYSTEM_PROMPT = (
    "The interview is over. You are the same warm HR interviewer giving a brief SPOKEN wrap-up to the "
    "candidate, based on the conversation you just had. Speak directly to them in second person.\n\n"
    "Say, in 2-4 short natural spoken sentences:\n"
    "  1. One GENUINE strength you noticed in their answers, referencing something specific they "
    "actually said (a real example, project, or moment from this conversation).\n"
    "  2. One concrete thing to work on for next time, framed encouragingly.\n"
    "  3. A short warm sign-off that mentions their detailed written feedback is being prepared.\n\n"
    "HARD RULES: Do NOT give any numbers, scores, ratings, grades, or percentages of any kind — this "
    "is a qualitative impression only; all scoring lives in the written report. Do NOT invent details "
    "the candidate did not mention. Keep it natural for speech (no lists, no headings), encouraging, "
    "and concise. This is the final thing they hear."
)


def build_debrief_prompt(ctx: TurnContext) -> str:
    """System prompt for the spoken end-of-interview debrief. Score-free by construction (Principle II).

    Layers the candidate's confirmed background (if present) onto the debrief instruction so the
    strength/improvement reference THIS candidate, but the rules forbid any numeric score so the spoken
    wrap-up can never contradict the written report (Flag F6)."""
    parts: list[str] = [DEBRIEF_SYSTEM_PROMPT]
    if ctx.job_scope is not None:
        parts.append(f"The role they interviewed for: {ctx.job_scope.title}.")
    if ctx.resume_highlights:
        highlights = "; ".join(ctx.resume_highlights[:6])
        parts.append(f"Their confirmed background (for grounding, do not read it back verbatim): {highlights}.")
    return " ".join(parts)
