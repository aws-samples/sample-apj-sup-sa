"""Persistence writers for the G1 slice (T008).

Turns are written INCREMENTALLY as they finalize (R7) so a mid-session drop never loses
prior turns — drop-resilience falls out for free (FR-012 / SC-007).

A connection pool is created lazily; callers use the module-level helpers. The data classes
mirror data-model.md exactly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class LatencyRecord:
    response_gap_ms: int
    stt_finalization_ms: int
    reply_ttft_ms: int
    tts_first_audio_ms: int
    orchestration_ms: int | None
    reply_provider: str


class Persistence:
    """Thin async data-access layer over the 3 G1 tables."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(
        cls, database_url: str, min_size: int = 1, max_size: int = 5, password_provider=None
    ) -> "Persistence":
        # password_provider (a callable) makes the pool rotation-proof: asyncpg evaluates it on every
        # new connection it opens, so a rotated RDS password is picked up without a restart. None ->
        # the password is taken from database_url as before (local dev / harness).
        kwargs = {"min_size": min_size, "max_size": max_size}
        if password_provider is not None:
            kwargs["password"] = password_provider
        pool = await asyncpg.create_pool(database_url, **kwargs)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    # --- voice_session ---

    async def create_session(self, session_id: str, user_sub: str, reply_provider: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO voice_session (session_id, user_sub, created_at, reply_provider)
            VALUES ($1, $2, $3, $4)
            """,
            session_id,
            user_sub,
            _utcnow(),
            reply_provider,
        )

    async def load_interview_plan(self, session_id: str) -> dict | None:
        """Read the assembled prep plan + the session's scope + the user's CONFIRMED resume facts
        for the session-start handoff (T018). Off the response_gap clock (runs before media start).

        Returns None for a generic G1 session (no blueprint) so the loop stays on the generic path.
        For a personalized session returns the raw read consumed by prep_handoff.build_session_plan:
          job_title, job_description, difficulty, resume_facts (CONFIRMED parsed facts),
          target_competencies, opening_archetype_id, difficulty_profile, and the ORDERED plan_rows
          (each archetype's competency/question_type/prompt_template/follow_up_prompts — NO scores).

        Raw PII (resume_facts, job_description) is read here only to be minimized by the caller into
        the live payload; it is never logged (FR-218 / Principle III). No scoring_guidance is read
        into the live path (Principle II)."""
        sess = await self._pool.fetchrow(
            """
            SELECT vs.job_title, vs.job_description, vs.difficulty,
                   vs.archetype_ids, vs.blueprint_id, vs.consent_store_materials,
                   vs.duration_minutes,
                   u.resume_parsed_facts
              FROM voice_session vs
              LEFT JOIN users u ON u.id = vs.user_id
             WHERE vs.session_id = $1
            """,
            uuid.UUID(session_id) if isinstance(session_id, str) else session_id,
        )
        if sess is None or sess["blueprint_id"] is None:
            return None  # generic G1 session — no personalized plan to hand off.

        bp = await self._pool.fetchrow(
            """
            SELECT target_competencies, ordered_archetype_ids, opening_archetype_id
              FROM interview_blueprint WHERE id = $1
            """,
            sess["blueprint_id"],
        )
        if bp is None:
            return None

        ordered = list(bp["ordered_archetype_ids"] or [])
        plan_rows: list[dict] = []
        if ordered:
            # Fetch the planned archetypes; reorder in Python to honor the blueprint's ranked order
            # (a SQL `= ANY` does not preserve array order). NO scoring_guidance (Principle II).
            rows = await self._pool.fetch(
                """
                SELECT id, competency, question_type, prompt_template, follow_up_prompts
                  FROM question_archetype WHERE id = ANY($1::uuid[])
                """,
                ordered,
            )
            by_id = {row["id"]: row for row in rows}
            for aid in ordered:
                row = by_id.get(aid)
                if row is None:
                    continue
                fu = row["follow_up_prompts"]
                if isinstance(fu, str):
                    import json as _json

                    fu = _json.loads(fu or "[]")
                plan_rows.append(
                    {
                        "id": str(row["id"]),
                        "competency": row["competency"],
                        "question_type": row["question_type"],
                        "prompt_template": row["prompt_template"],
                        "follow_up_prompts": list(fu or []),
                    }
                )

        profile = None
        if sess["difficulty"]:
            prow = await self._pool.fetchrow(
                """
                SELECT level, probing_intensity, curveball_rate, warmth, hint_policy, domain_depth
                  FROM difficulty_profile WHERE level = $1
                """,
                sess["difficulty"],
            )
            if prow is not None:
                profile = dict(prow)

        resume_facts = sess["resume_parsed_facts"]
        if isinstance(resume_facts, str):
            import json as _json

            resume_facts = _json.loads(resume_facts)

        return {
            "job_title": sess["job_title"],
            "job_description": sess["job_description"],
            "difficulty": sess["difficulty"],
            "resume_facts": resume_facts,
            "target_competencies": list(bp["target_competencies"] or []),
            "opening_archetype_id": str(bp["opening_archetype_id"]) if bp["opening_archetype_id"] else None,
            "difficulty_profile": profile,
            "plan_rows": plan_rows,
            "consent_store_materials": bool(sess["consent_store_materials"]),
            "duration_minutes": sess["duration_minutes"],
        }

    async def mark_started(self, session_id: str, network_path: str | None = None) -> None:
        await self._pool.execute(
            """
            UPDATE voice_session
               SET started_at = COALESCE(started_at, $2),
                   network_path = COALESCE($3, network_path)
             WHERE session_id = $1
            """,
            session_id,
            _utcnow(),
            network_path,
        )

    async def set_network_path(self, session_id: str, network_path: str) -> None:
        await self._pool.execute(
            "UPDATE voice_session SET network_path = $2 WHERE session_id = $1",
            session_id,
            network_path,
        )

    async def end_session(self, session_id: str, end_reason: str) -> None:
        """Idempotent: only sets the terminal state if not already ended."""
        await self._pool.execute(
            """
            UPDATE voice_session
               SET ended_at = COALESCE(ended_at, $2),
                   end_reason = COALESCE(end_reason, $3)
             WHERE session_id = $1
            """,
            session_id,
            _utcnow(),
            end_reason,
        )

    async def finalize_session(self, session_id: str, fallback_reason: str) -> str | None:
        """Close the session row (if still open) and return the AUTHORITATIVE end_reason.

        One atomic statement: writes `fallback_reason` only when no terminal state exists yet,
        and returns whatever the row ends up holding — the backend's student_ended, wrap-up's
        completed, or the fallback. The session-summary metric dimension uses this so it can
        never disagree with the DB (and a true network drop, where nobody else writes a terminal
        state, finally closes its row instead of staying open forever)."""
        return await self._pool.fetchval(
            """
            UPDATE voice_session
               SET ended_at = COALESCE(ended_at, $2),
                   end_reason = COALESCE(end_reason, $3)
             WHERE session_id = $1
            RETURNING end_reason
            """,
            session_id,
            _utcnow(),
            fallback_reason,
        )

    # --- conversation_turn (incremental) ---

    async def append_turn(
        self,
        session_id: str,
        turn_index: int,
        speaker: str,
        transcript: str,
        started_at: datetime,
        ended_at: datetime | None = None,
        interrupted: bool = False,
        archetype_id: str | None = None,
        is_followup: bool | None = None,
        targeted_star_element: str | None = None,
    ) -> str:
        """Write one finalized turn. The G2 structural facts (archetype_id, is_followup,
        targeted_star_element) are written on personalized COACH turns (T026 / FR-212a) and stay NULL
        for student turns and generic G1 turns (additive columns). NO scores (Principle II).

        archetype_id arrives as a string from the live planner and is coerced to UUID for the FK
        column; is_followup defaults to FALSE (the column's default) when not supplied.
        """
        turn_id = new_id()
        archetype_uuid = uuid.UUID(archetype_id) if isinstance(archetype_id, str) and archetype_id else None
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO conversation_turn
                        (turn_id, session_id, turn_index, speaker, transcript,
                         started_at, ended_at, interrupted,
                         archetype_id, is_followup, targeted_star_element)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    """,
                    turn_id,
                    session_id,
                    turn_index,
                    speaker,
                    transcript,
                    started_at,
                    ended_at,
                    interrupted,
                    archetype_uuid,
                    bool(is_followup) if is_followup is not None else False,
                    targeted_star_element,
                )
                await conn.execute(
                    "UPDATE voice_session SET turn_count = turn_count + 1 WHERE session_id = $1",
                    session_id,
                )
        return turn_id

    async def set_turn_audio_uri(self, turn_id: str, audio_uri: str) -> None:
        """Link a stored audio object to its turn (F006 / G6). Written by the worker's async upload
        task AFTER the turn, off the response_gap clock. NULL until/unless audio is stored (consent off,
        upload failed, or unconfigured bucket)."""
        await self._pool.execute(
            "UPDATE conversation_turn SET audio_uri = $2 WHERE turn_id = $1",
            turn_id,
            audio_uri,
        )

    # --- turn_latency ---

    async def record_latency(
        self, session_id: str, turn_id: str, rec: LatencyRecord, measured_at: datetime
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO turn_latency
                (latency_id, turn_id, session_id, response_gap_ms, stt_finalization_ms,
                 reply_ttft_ms, tts_first_audio_ms, orchestration_ms, reply_provider, measured_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
            new_id(),
            turn_id,
            session_id,
            rec.response_gap_ms,
            rec.stt_finalization_ms,
            rec.reply_ttft_ms,
            rec.tts_first_audio_ms,
            rec.orchestration_ms,
            rec.reply_provider,
            measured_at,
        )
