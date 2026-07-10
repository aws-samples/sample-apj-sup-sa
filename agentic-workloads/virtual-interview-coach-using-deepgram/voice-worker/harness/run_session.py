"""Scripted session driver for measurement (T028).

Drives a deterministic ~10-turn session through the REAL VoiceLoop and writes a run JSON that
harness/aggregate.py turns into the SC-001 verdict. The loop is provider-agnostic; this driver
injects the audio sink and TTS-speak callback so a session can be measured.

Two modes:
  --live    : use the configured reply provider (agentcore|bedrock_direct) + Deepgram Aura TTS.
              Captures TRUE reply-TTFT and TTS-first-audio latency against the live services.
              STT finalization is added as a measured constant (--stt-finalization-ms, default
              280ms — see note below) because a scripted driver has no live mic to endpoint;
              the dominant, riskiest components (reply + TTS) are measured for real.
  (default) : DRY mode — deterministic simulated stage latencies so the harness and verdict
              pipeline are exercisable end-to-end WITHOUT credentials. DRY numbers are clearly
              labeled and MUST NOT be used as a gate decision.

The response_gap clock (metrics-contract.md) runs end-of-speech -> coach first audio. For the
FIRST audio frame this path is sequential: STT-final -> reply-first-token -> TTS-first-audio.
The live driver therefore composes: gap = stt_finalization (measured constant) + reply_ttft
(live) + tts_first_audio (live). This is honest per Principle II: each component is a real
measurement, the STT constant is stated and overridable, and no turn is trimmed.

  --personalized : re-measure SC-001 on the ENRICHED loop (T028). Each TurnContext is widened with
                   a SYNTHETIC grounded HR persona (resume highlights + job scope), the FunnelPlanner
                   advance-vs-probe walk, and the chosen difficulty tier's levers — exactly the
                   payload VoiceLoop.on_student_turn folds in for a real personalized session. The
                   provider therefore streams the REAL enriched prompt, so the gate verdict reflects
                   the product loop rather than the generic G1 path. The grounding is synthetic and
                   carries NO PII (Constitution III); --difficulty picks the tier (default moderate).

Run:
    python -m harness.run_session --turns 10 --out runs/agentcore.json
    python -m harness.run_session --turns 10 --live --out runs/bedrock.json
    # SC-001 on the enriched loop (the T028 gate path):
    python -m harness.run_session --turns 10 --live --lead-clause --personalized \
        --difficulty moderate --out runs/personalized.json
    python -m harness.aggregate runs/personalized.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

# NOTE: heavy imports (src.pipeline / src.metrics / Deepgram / asyncpg) are done lazily inside
# _run_live so DRY mode runs standalone without DB drivers or SDK credentials installed.

# Scripted student utterances (generic, no PII — Constitution III). ~10 turns.
_STUDENT_TURNS = [
    "Sure, I'm a final-year student studying computer science and I love building things.",
    "I think my greatest strength is that I stay calm under pressure and break problems down.",
    "Last semester our group project nearly fell apart and I had to step in and re-plan it.",
    "I want this role because it lets me work on real systems that people actually use.",
    "In five years I'd like to be leading a small team and mentoring junior engineers.",
    "Once a teammate and I disagreed on an approach, so we prototyped both and compared them.",
    "A weakness I'm working on is saying yes to too much, so I'm learning to prioritize.",
    "I led a hackathon team of four and we shipped a working demo in two days.",
    "When things get stressful I make a short list and tackle the highest-impact item first.",
    "Yes, what does success look like for someone in this role in the first six months?",
]

# Short, natural backchannel lead-ins for the --lead-clause strategy. The coach starts speaking
# one of these IMMEDIATELY (synthesized by TTS without waiting for the LLM), the way a human
# interviewer says "Right," while formulating a reply. Cycled by turn index for variety. This is
# a perceptual latency technique: it genuinely closes the response gap (the coach is speaking),
# and the substantive reply streams in behind it (measured separately as substantive_reply_ms).
_LEAD_INS = [
    "Got it.",
    "Right.",
    "Okay,",
    "Mm-hmm.",
    "I see.",
    "Sure.",
    "That's great.",
    "Understood.",
    "Makes sense.",
    "Thanks for sharing.",
]

_LLM_DONE = object()  # sentinel: background LLM drain finished


# --- Personalized (enriched-loop) fixtures (T028) -------------------------------------------------
# SC-001 must STILL pass on the ENRICHED loop, not just the generic G1 one. --personalized drives the
# same scripted turns through the grounded HR STAR+funnel persona + difficulty levers + the
# FunnelPlanner's advance-vs-probe walk, so the gate is re-measured on exactly what the product runs.
#
# The grounding payload below is SYNTHETIC and carries NO real PII (Constitution III) — it is generic,
# representative material, just enough to trigger the personalized prompt and the planner. It mirrors
# the SessionPlan the backend hands the worker (prep_handoff.SessionPlan) so the live prompt the
# provider sees is the real enriched one. Difficulty levers match bank/seed/difficulty_profiles.sql.
_PERSONA_RESUME_HIGHLIGHTS = [
    "Final-year Computer Science student focused on building reliable backend services.",
    "Key skills: Python, distributed systems, REST APIs, PostgreSQL, Docker, CI/CD.",
    "Software Engineering Intern at a fintech startup (Summer 2025) - built an event-driven "
    "payments reconciliation pipeline.",
    "Led a four-person hackathon team to ship a working demo in two days.",
]

_PERSONA_JOB_TITLE = "Software Engineer (Graduate)"
_PERSONA_JOB_REQUIREMENTS = [
    "Strong programming fundamentals in a modern language",
    "Experience building and debugging production services",
    "Ability to collaborate across a team and communicate trade-offs",
    "Familiar with relational databases and testing",
]

# Ordered representative plan: general competencies + one role-specific archetype. The FunnelPlanner
# walks this queue; the difficulty tier only sets the per-archetype follow-up budget (so Easy advances
# sooner than Difficult). Shape matches blueprint.PlannedQuestion.from_row.
_PERSONA_PLAN_ROWS = [
    {
        "id": "arch-motivation-fit",
        "competency": "motivation_fit",
        "question_type": "warmup",
        "prompt_template": "To start, tell me a little about yourself and what draws you to this role.",
        "follow_up_prompts": ["What specifically about this role appeals to you?"],
    },
    {
        "id": "arch-teamwork",
        "competency": "teamwork",
        "question_type": "behavioral",
        "prompt_template": "Tell me about a time you worked closely with others to get something done.",
        "follow_up_prompts": ["What was your specific role on that team?", "What action did you take?"],
    },
    {
        "id": "arch-problem-solving",
        "competency": "problem_solving",
        "question_type": "behavioral",
        "prompt_template": "Describe a difficult problem you faced and how you worked through it.",
        "follow_up_prompts": ["What made it difficult, specifically?", "What was the result?"],
    },
    {
        "id": "arch-communication",
        "competency": "communication",
        "question_type": "behavioral",
        "prompt_template": "Tell me about a time you had to explain something complex to a non-expert.",
        "follow_up_prompts": ["Who was the audience?", "How did you confirm they understood?"],
    },
    {
        "id": "arch-role-specific-swe",
        "competency": "role_specific",
        "question_type": "technical",
        "prompt_template": "Walk me through how you would debug a feature that works locally but "
        "fails in production.",
        "follow_up_prompts": ["What would you check first, and why?", "How do you confirm a fix?"],
    },
]

# Student answers for the PERSONALIZED loop, aligned to the synthetic persona resume above (the
# generic G1 path keeps _STUDENT_TURNS byte-for-byte — SC-001). In a real session the candidate's
# spoken answers are necessarily about their OWN background, so for a faithful grounding measurement
# (SC-002 / Check B) the scripted answers reference the same confirmed facts the coach was handed
# (hackathon team, fintech payments-reconciliation internship, distributed-systems/Python skills,
# debugging production). No PII — this is generic, representative material (Constitution III). The
# blind reviewer still independently and strictly judges each coach QUESTION; this only keeps the
# student's side of the dialogue consistent with the resume rather than referencing experiences the
# resume never mentions (which would mark a legitimate reference-back as ungrounded).
_PERSONA_STUDENT_TURNS = [
    "Sure. I'm a final-year computer science student and I've focused on building reliable backend "
    "services — Python, distributed systems, that kind of thing.",
    "What draws me to this role is that it's about building and debugging real production services, "
    "which is exactly what I enjoyed most in my internship.",
    "Last summer I interned at a fintech startup and built an event-driven payments reconciliation "
    "pipeline, so I got to own a real system end to end.",
    "The result was that reconciliation that used to be manual ran automatically every night, and I "
    "learned how much I like working on systems people actually depend on.",
    "One time that comes to mind is leading a four-person hackathon team — we had to ship a working "
    "demo in just two days.",
    "My role was basically the lead: I split the work, owned the backend, and kept us focused on a "
    "demoable slice.",
    "The main thing I did was cut scope hard and set up a simple CI pipeline early so we weren't "
    "integrating everything at the last minute.",
    "We shipped the demo on time and it actually worked in front of the judges, which felt great for "
    "two days of work.",
    "A genuinely hard problem was when the payments pipeline worked locally but kept failing in "
    "production — the reconciliation totals didn't match.",
    "I traced it down by adding structured logging and comparing the event ordering, and it turned "
    "out to be a race in how I consumed the event stream.",
]

# Behavioral levers per tier — mirror bank/seed/difficulty_profiles.sql (scoring_strictness omitted:
# F002 does not score). Selected by --difficulty so the enriched prompt is the real tiered persona.
_DIFFICULTY_PROFILES = {
    "easy": dict(probing_intensity=2, curveball_rate=0.00, warmth=5, hint_policy="offer", domain_depth=2),
    "moderate": dict(probing_intensity=3, curveball_rate=0.15, warmth=4, hint_policy="minimal", domain_depth=3),
    "difficult": dict(probing_intensity=5, curveball_rate=0.40, warmth=2, hint_policy="none", domain_depth=5),
}


def _student_turns(personalized: bool) -> list[str]:
    """Scripted student utterances. The personalized loop uses answers aligned to the synthetic
    persona resume (faithful grounding measurement); the generic G1 path is unchanged (SC-001)."""
    return _PERSONA_STUDENT_TURNS if personalized else _STUDENT_TURNS


def _build_session_plan(difficulty: str):
    """Assemble the minimized SessionPlan the enriched loop runs on (synthetic, no PII).

    Imported lazily so DRY mode without --personalized stays standalone. The dataclasses are pure
    (no DB / SDK), so this is safe to build in either mode.
    """
    from src.prep_handoff import SessionPlan
    from src.reply.interface import DifficultyProfile, JobScope

    levers = _DIFFICULTY_PROFILES.get(difficulty, _DIFFICULTY_PROFILES["moderate"])
    return SessionPlan(
        resume_highlights=list(_PERSONA_RESUME_HIGHLIGHTS),
        job_scope=JobScope(title=_PERSONA_JOB_TITLE, key_requirements=list(_PERSONA_JOB_REQUIREMENTS)),
        target_competencies=[r["competency"] for r in _PERSONA_PLAN_ROWS],
        difficulty_profile=DifficultyProfile(level=difficulty, **levers),
        plan_rows=list(_PERSONA_PLAN_ROWS),
        opening_archetype_id=_PERSONA_PLAN_ROWS[0]["id"],
    )


def _make_planner(plan):
    """Build the in-memory advance-vs-probe planner over the plan's queue (mirrors the live loop)."""
    from src.blueprint import BlueprintQueue, FunnelPlanner

    queue = BlueprintQueue.from_plan(plan.plan_rows, plan.opening_archetype_id)
    return FunnelPlanner(queue, plan.difficulty_profile)


def _enrich_ctx(ctx, plan, planner, student_text):
    """Widen one TurnContext exactly as VoiceLoop.on_student_turn does for a personalized session.

    Folds in the minimized grounding payload + the difficulty levers, then asks the planner (from the
    running transcript) which archetype/STAR element this turn targets. Returns the TurnPlan so the
    caller can record the turn's structural facts (archetype_id / is_followup / targeted_star_element).
    """
    ctx.resume_highlights = plan.resume_highlights or None
    ctx.job_scope = plan.job_scope
    ctx.target_competencies = plan.target_competencies or None
    ctx.difficulty_profile = plan.difficulty_profile
    turn_plan = planner.next_turn(student_text)
    ctx.current_archetype = turn_plan.intent
    return turn_plan


def _structural_facts(turn_plan) -> dict:
    """The per-turn structural facts (NO scores — Principle II) for transparency in the run JSON."""
    intent = turn_plan.intent if turn_plan is not None else None
    return {
        "archetype_id": intent.archetype_id if intent is not None else None,
        "competency": intent.competency if intent is not None else None,
        "is_followup": turn_plan.is_followup if turn_plan is not None else None,
        "targeted_star_element": turn_plan.targeted_star_element if turn_plan is not None else None,
    }


async def _run_dry(
    turns: int,
    provider: str,
    seed_base_ms: int,
    personalized: bool = False,
    difficulty: str = "moderate",
) -> dict:
    """Exercise the verdict pipeline with simulated, clearly-fake stage latencies.

    DRY mode does NOT touch the DB, SDKs, or media — it produces a run JSON in the exact shape
    the live loop emits so harness/aggregate.py can be validated end-to-end. The numbers are
    deterministic placeholders and MUST NOT be treated as a gate decision.
    """
    # When --personalized, walk the SAME FunnelPlanner the live loop uses so the DRY run records the
    # enriched-loop structural facts (archetype_id / is_followup / targeted_star_element). This proves
    # the planner integrates end-to-end without credentials; the latencies remain clearly-fake.
    plan = planner = None
    if personalized:
        plan = _build_session_plan(difficulty)
        planner = _make_planner(plan)

    student_turns = _student_turns(personalized)
    n = min(turns, len(student_turns)) if personalized else turns
    recorded: list[dict] = []
    for i in range(n):
        # Deterministic pseudo-latencies (no RNG; varies per turn + seed offset).
        stt = 180 + ((i + seed_base_ms) * 7) % 60
        ttft = 380 + ((i + seed_base_ms) * 13) % 120
        tts = 210 + ((i + seed_base_ms) * 5) % 50
        orch = 40 + ((i + seed_base_ms) * 3) % 25
        gap = stt + ttft + tts + orch
        turn: dict = {
            "turn_index": i,
            "response_gap_ms": gap,
            "stt_finalization_ms": stt,
            "reply_ttft_ms": ttft,
            "tts_first_audio_ms": tts,
            "orchestration_ms": orch,
        }
        if planner is not None:
            turn.update(_structural_facts(planner.next_turn(student_turns[i])))
        recorded.append(turn)
    label = "DRY-PERSONALIZED" if personalized else "DRY"
    out = {
        "mode": f"{label} (simulated — NOT a gate decision)",
        "reply_provider": provider,
        "network_path": "direct",
        "personalized": personalized,
        "turns": recorded,
    }
    if personalized:
        out["difficulty"] = difficulty
    return out


async def _run_live(
    turns: int,
    stt_finalization_ms: int,
    prewarm: bool = False,
    lead_clause: bool = False,
    personalized: bool = False,
    difficulty: str = "moderate",
) -> dict:
    """Drive the real reply generator + live Deepgram Aura TTS for a scripted session.

    For each scripted student turn this measures, against the live services:
      - reply_ttft_ms     : reply request -> first token from the configured ReplyGenerator
      - tts_first_audio_ms: first reply clause handed to Aura -> first synthesized audio frame
    and composes response_gap = stt_finalization (measured constant) + reply_ttft +
    tts_first_audio, per the sequential first-audio path in metrics-contract.md.

    Conversation history is carried turn-to-turn (the reply generator sees the running generic
    transcript), so this exercises the real provider under realistic, growing context.

    If prewarm=True, one full throwaway turn (reply + TTS) is run through the live services and
    discarded BEFORE measurement begins. This isolates warm steady-state latency by paying the
    one-time cold-start cost (model spin-up, TLS/socket setup, AgentCore session init) outside
    the measured window. The discarded turn is recorded in the result as `prewarm_turn` for
    transparency but is NOT part of the percentile distribution.

    If lead_clause=True, the coach speaks a short backchannel ("Right.", "Got it.") IMMEDIATELY
    via TTS the moment the student finishes, while the reply LLM generates concurrently behind
    it. This decouples first-audio from LLM TTFT: the gate response_gap is then stt + lead-in
    TTS first-audio (the LLM is OFF the critical path). To stay honest, the turn ALSO records
    `substantive_reply_ms` = stt + time to the first audio frame of the REAL reply content, so
    the trade (coach speaks fast, but the substantive answer still lands later) is fully visible.
    """
    import time

    from src.config import Config
    from src.chunking import SentenceChunker
    from src.reply.interface import OPENING_QUESTION, TurnContext, Utterance, build_provider
    from harness.tts_deepgram import DeepgramTTS, TTSConfig

    config = Config.load()
    if not config.deepgram_api_key:
        raise RuntimeError("DEEPGRAM_API_KEY not set (voice-worker/.env) — required for --live")

    reply = build_provider(config.reply_provider, config)

    # Personalized (enriched-loop) re-measure (T028 / SC-001). When set, every TurnContext is widened
    # with the synthetic grounding payload + difficulty levers and the FunnelPlanner's chosen archetype
    # — exactly as VoiceLoop.on_student_turn does — so the provider streams the REAL enriched prompt and
    # the gate is measured on the product loop, not the generic G1 one. No PII (Constitution III).
    session_plan = planner = None
    if personalized:
        session_plan = _build_session_plan(difficulty)
        planner = _make_planner(session_plan)

    # One TTS socket reused across the session. on_audio records two instants per turn: the very
    # first audio frame (gate-critical) and the first frame of SUBSTANTIVE content (lead-clause).
    marks: dict[str, float | None] = {"first": None, "substantive": None}
    phase: dict[str, bool] = {"substantive": False}

    async def on_audio(_frame: bytes) -> None:
        now = time.monotonic()
        if marks["first"] is None:
            marks["first"] = now
        if phase["substantive"] and marks["substantive"] is None:
            marks["substantive"] = now

    tts = DeepgramTTS(config.deepgram_api_key, TTSConfig(), on_audio)

    async def measure_turn(student_text: str, history: list[Utterance], turn_index: int) -> dict:
        """Run one turn end-to-end (reply -> chunk -> TTS) and return its latency components.

        Mutates `history` in place (appends the student turn, then the coach reply) so the next
        turn sees the growing transcript. The same code path is used for both the discarded
        pre-warm turn and the measured turns, so the warm-up exercises exactly what is measured.
        """
        history.append(Utterance(speaker="student", text=student_text))
        ctx = TurnContext(session_id="live-harness", student_text=student_text, history=list(history))

        # Enrich the context for the personalized loop (grounded persona + funnel + difficulty), and
        # capture the turn's structural facts so the run JSON records what the live loop would persist.
        turn_plan = None
        if personalized and planner is not None:
            turn_plan = _enrich_ctx(ctx, session_plan, planner, student_text)

        chunker = SentenceChunker()
        tts.reset()
        marks["first"] = None
        marks["substantive"] = None
        phase["substantive"] = False
        reply_parts: list[str] = []

        if lead_clause:
            result = await _measure_turn_lead(
                ctx, history, turn_index, chunker, reply_parts, marks, phase, reply, tts, time
            )
            if turn_plan is not None:
                result.update(_structural_facts(turn_plan))
            return result

        t_reply_requested = time.monotonic()
        t_first_token: float | None = None
        t_tts_requested: float | None = None

        phase["substantive"] = True  # no lead-in: all audio is substantive
        agen = reply.stream(ctx)
        try:
            async for token in agen:
                if t_first_token is None:
                    t_first_token = time.monotonic()
                reply_parts.append(token)
                for chunk in chunker.add(token):
                    if t_tts_requested is None:
                        t_tts_requested = time.monotonic()
                    await tts.speak_chunk(chunk)
                    if marks["first"] is not None:
                        break  # first audio captured; that is all the gap clock needs
                if marks["first"] is not None:
                    break
            if marks["first"] is None:
                tail = chunker.flush()
                if tail:
                    if t_tts_requested is None:
                        t_tts_requested = time.monotonic()
                    await tts.speak_chunk(tail)
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose:
                await aclose()

        reply_text = "".join(reply_parts).strip()
        history.append(Utterance(speaker="coach", text=reply_text))

        reply_ttft = int(round(((t_first_token or t_reply_requested) - t_reply_requested) * 1000))
        if marks["first"] is not None and t_tts_requested is not None:
            tts_first_audio = int(round((marks["first"] - t_tts_requested) * 1000))
        else:
            tts_first_audio = 0
        gap = stt_finalization_ms + reply_ttft + tts_first_audio
        # Orchestration is the glue/scheduling residual NOT captured by the three measured
        # components (metrics-contract.md). This composed harness DEFINES gap as exactly
        # stt + ttft + tts, so by construction that residual is 0 here — the live end-to-end
        # loop is where real orchestration overhead would surface.
        orch = max(0, gap - stt_finalization_ms - reply_ttft - tts_first_audio)
        result = {
            "turn_index": turn_index,
            "response_gap_ms": gap,
            "stt_finalization_ms": stt_finalization_ms,
            "reply_ttft_ms": reply_ttft,
            "tts_first_audio_ms": tts_first_audio,
            "orchestration_ms": orch,
            "reply_preview": reply_text[:60],
            "reply_text": reply_text,  # full coach text (additive — for offline B/D blind review)
        }
        if turn_plan is not None:
            result.update(_structural_facts(turn_plan))
        return result

    async def _measure_turn_lead(
        ctx, history, turn_index, chunker, reply_parts, marks, phase, reply, tts, time
    ) -> dict:
        """Lead-clause path: speak a backchannel immediately, generate the reply concurrently.

        The reply LLM is consumed in a background task (so its TTFT clock overlaps the lead-in
        TTS), the lead-in is synthesized first to close the gate gap, then the substantive reply
        streams in behind it. Both first-audio instants are captured for an honest comparison.
        """
        lead = _LEAD_INS[turn_index % len(_LEAD_INS)]
        reply_q: asyncio.Queue = asyncio.Queue()
        ttft: dict[str, float | None] = {"t": None}

        t_reply_requested = time.monotonic()  # == end-of-speech for both lead-in and the LLM

        async def _consume_llm() -> None:
            agen = reply.stream(ctx)
            try:
                async for token in agen:
                    if ttft["t"] is None:
                        ttft["t"] = time.monotonic()
                    reply_parts.append(token)
                    await reply_q.put(token)
            except Exception as exc:  # surface to the consumer
                await reply_q.put(exc)
            finally:
                await reply_q.put(_LLM_DONE)
                aclose = getattr(agen, "aclose", None)
                if aclose:
                    await aclose()

        llm_task = asyncio.create_task(_consume_llm())

        # 1) Coach starts speaking the backchannel immediately. This frame is the gate gap.
        t_tts_lead = time.monotonic()
        await tts.speak_chunk(lead)

        # Pre-warm the NEXT turn's socket in the background NOW, overlapping the substantive
        # streaming below. Aura sockets stop emitting audio after ~9-10 reuse cycles, so each turn
        # is given a fresh zero-reuse socket; warming it here keeps the TLS handshake off the next
        # turn's critical path. (rotate() swaps it in at end of turn.)
        warm_task = asyncio.create_task(tts.open_spare())

        # 2) Stream the substantive reply behind the lead-in until its first audio frame.
        phase["substantive"] = True
        t_tts_sub: float | None = None
        try:
            while marks["substantive"] is None:
                item = await reply_q.get()
                if item is _LLM_DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                for chunk in chunker.add(item):
                    if t_tts_sub is None:
                        t_tts_sub = time.monotonic()
                    await tts.speak_chunk(chunk)
                    if marks["substantive"] is not None:
                        break
            if marks["substantive"] is None:
                tail = chunker.flush()
                if tail:
                    if t_tts_sub is None:
                        t_tts_sub = time.monotonic()
                    await tts.speak_chunk(tail)
        finally:
            if not llm_task.done():
                llm_task.cancel()
                try:
                    await llm_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # End-of-turn socket handling. The lead path breaks out of the substantive clause
            # mid-stream once first-audio is captured, leaving dirty buffered state; worse, an Aura
            # socket stops emitting audio after ~9-10 reuse cycles (a cumulative server-side limit),
            # which deterministically stalls late turns when one socket is reused all session.
            # Retire this turn's socket and swap in the spare warmed in the background above
            # (open_spare) — the next turn runs on a fresh zero-reuse socket with no handshake on its
            # critical path. Ensure the warm completed first so the spare is ready to swap in.
            if not warm_task.done():
                try:
                    await warm_task
                except Exception:  # noqa: BLE001 - non-fatal: next turn connects lazily
                    pass
            await tts.rotate()

        reply_text = (lead + " " + "".join(reply_parts).strip()).strip()
        history.append(Utterance(speaker="coach", text=reply_text))

        reply_ttft = int(round(((ttft["t"] or t_reply_requested) - t_reply_requested) * 1000))
        lead_audio_ms = (
            int(round((marks["first"] - t_tts_lead) * 1000)) if marks["first"] is not None else 0
        )
        gap = stt_finalization_ms + lead_audio_ms  # LLM is OFF the critical path here
        if marks["substantive"] is not None:
            substantive_ms = stt_finalization_ms + int(
                round((marks["substantive"] - t_reply_requested) * 1000)
            )
        else:
            substantive_ms = 0
        return {
            "turn_index": turn_index,
            "response_gap_ms": gap,
            "stt_finalization_ms": stt_finalization_ms,
            "reply_ttft_ms": reply_ttft,
            "tts_first_audio_ms": lead_audio_ms,
            "orchestration_ms": 0,
            "substantive_reply_ms": substantive_ms,
            "lead_in": lead,
            "reply_preview": reply_text[:60],
            "reply_text": reply_text,  # full coach text (additive — for offline B/D blind review)
        }

    history: list[Utterance] = [Utterance(speaker="coach", text=OPENING_QUESTION)]
    recorded: list[dict] = []
    prewarm_turn: dict | None = None
    student_turns = _student_turns(personalized)
    n = min(turns, len(student_turns))

    if prewarm:
        # Discarded throwaway turn: pays cold-start cost outside the measured window. Uses a
        # generic warm-up utterance and is rolled back from history so it does not bias context.
        warm_history: list[Utterance] = list(history)
        prewarm_turn = await measure_turn(
            "Hi, thanks for having me today, I'm looking forward to this.", warm_history, -1
        )

    for i in range(n):
        recorded.append(await measure_turn(student_turns[i], history, i))

    await tts.finish()
    warm_tag = "-WARM" if prewarm else ""
    # The personalized tag marks that the gate was re-measured on the ENRICHED loop (grounded HR
    # persona + funnel + difficulty), not the generic G1 path — the whole point of T028 / SC-001.
    persona_tag = f"-PERSONALIZED[{difficulty}]" if personalized else ""
    if lead_clause:
        mode = (
            f"LIVE{warm_tag}{persona_tag}-LEAD (coach speaks an immediate backchannel; LLM off the "
            "critical path; response_gap = STT + lead-in TTS first-audio. substantive_reply_ms = STT "
            "+ first audio of the REAL reply, reported per turn for honesty; "
            f"STT finalization = {stt_finalization_ms}ms measured constant)"
        )
    elif prewarm:
        mode = (
            f"LIVE-WARM{persona_tag} (cold-start turn pre-warmed and discarded; reply+TTS measured "
            f"against live services; STT finalization = {stt_finalization_ms}ms measured constant)"
        )
    else:
        mode = (
            f"LIVE{persona_tag} (reply+TTS measured against live services; STT finalization = "
            f"{stt_finalization_ms}ms measured constant)"
        )
    out = {
        "mode": mode,
        "reply_provider": config.reply_provider,
        "bedrock_model_id": config.bedrock_model_id,
        "network_path": "direct",
        "prewarmed": prewarm,
        "lead_clause": lead_clause,
        "personalized": personalized,
        "prewarm_turn": prewarm_turn,
        "turns": recorded,
    }
    if personalized:
        out["difficulty"] = difficulty
    return out


async def _amain(args: argparse.Namespace) -> int:
    if args.live:
        run = await _run_live(
            args.turns,
            args.stt_finalization_ms,
            prewarm=args.prewarm,
            lead_clause=args.lead_clause,
            personalized=args.personalized,
            difficulty=args.difficulty,
        )
    else:
        run = await _run_dry(
            args.turns,
            args.provider,
            args.seed,
            personalized=args.personalized,
            difficulty=args.difficulty,
        )

    # --tag is a convenience for the SC-004 paired workflow (quickstart Check D): it names the run so a
    # later `grounding_eval --pair <easy_tag>,<difficult_tag>` can resolve it as runs/<tag>.json. When
    # set with a default --out, the run is written to runs/<tag>.json and stamped with the tag.
    out_path = args.out
    if args.tag:
        run["tag"] = args.tag
        if args.out == "runs/session.json":
            out_path = os.path.join("runs", f"{args.tag}.json")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(run, f, indent=2)
    print(f"wrote {out_path} ({len(run['turns'])} turns, mode={run.get('mode', 'live')})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive a scripted session for SC-001 measurement")
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument("--out", default="runs/session.json")
    parser.add_argument("--provider", default=os.environ.get("REPLY_PROVIDER", "agentcore"))
    parser.add_argument("--live", action="store_true", help="use real provider + Deepgram + media")
    parser.add_argument(
        "--prewarm",
        action="store_true",
        help="LIVE: run one throwaway turn (reply+TTS) before measuring to pay cold-start cost "
        "outside the measured window; isolates warm steady-state latency",
    )
    parser.add_argument(
        "--lead-clause",
        action="store_true",
        help="LIVE: coach speaks an immediate backchannel ('Right.') while the reply LLM "
        "generates concurrently; takes the LLM off the gate critical path. Also records "
        "substantive_reply_ms (time to first audio of the real reply) for honesty.",
    )
    parser.add_argument(
        "--personalized",
        action="store_true",
        help="Re-measure SC-001 on the ENRICHED loop (T028): widen each TurnContext with a synthetic "
        "grounded HR persona + STAR/funnel planner + difficulty levers — exactly what the product "
        "loop runs — so the gate verdict reflects the personalized prompt, not the generic G1 path. "
        "No PII (synthetic grounding only). Combine with --live --lead-clause for the real number.",
    )
    parser.add_argument(
        "--difficulty",
        default="moderate",
        choices=("easy", "moderate", "difficult"),
        help="--personalized difficulty tier (selects the difficulty_profile levers injected into "
        "the persona). Default moderate.",
    )
    parser.add_argument(
        "--tag",
        help="name this run for the SC-004 paired workflow (quickstart Check D); with a default "
        "--out the run is written to runs/<tag>.json so `grounding_eval --pair easy,difficult` "
        "can resolve it",
    )
    parser.add_argument("--seed", type=int, default=0, help="DRY-mode deterministic base offset")
    parser.add_argument(
        "--stt-finalization-ms",
        type=int,
        default=280,
        help="LIVE-mode STT finalization constant (end-of-speech -> final transcript); measured "
        "~267-286ms against Deepgram nova-2 with trailing silence. Overridable.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
