"""Agent tools: semantic moment search, visual inspection, web lookup.

Each tool also pushes a UI event over the RTVI data channel, so the browser can
show what the agent is doing (and move the playhead) while it is still talking.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from talk2vid import agentcore, bedrock, media
from talk2vid.index_store import VideoIndex

TOOL_TIMEOUT_SECS = 12.0
# The query embedding is worth ~0.4s warm; past this budget the lexical ranking
# alone is a better answer than a longer silence.
EMBED_BUDGET_SECS = 1.2
# The AgentCore harness runs a full agent loop (search, read, reason).
WORLD_TIMEOUT_SECS = 30.0


@dataclass
class Session:
    """Per-connection state shared by the tools and the pipeline."""

    index: VideoIndex
    rtvi: Any = None  # RTVIProcessor, attached once the worker exists
    speak: Callable[[str], Awaitable[None]] | None = None  # say something immediately
    position: float = 0.0  # playhead reported by the browser
    playing: bool = False
    llm_spoke_this_turn: bool = False  # set by bot.TurnSpeechFlag
    started_at: float = field(default_factory=time.time)
    # Reusing one harness session keeps its environment and memory warm for the
    # whole conversation. AgentCore requires at least 33 characters.
    research_session_id: str = field(default_factory=lambda: f"talk2vid-{uuid.uuid4()}")

    async def emit(self, payload: dict) -> None:
        """Fire-and-forget UI event; never break the voice loop over this."""
        if not self.rtvi:
            return
        try:
            await self.rtvi.send_server_message(payload)
        except Exception as exc:
            logger.debug(f"ui event dropped: {exc}")

    def note_position(self, seconds: float, playing: bool | None = None) -> None:
        seconds = max(float(seconds), 0.0)
        if abs(seconds - self.position) > 3.0:  # log seeks, not every tick
            logger.info(f"playhead -> {media.hhmmss(seconds)}")
        self.position = seconds
        if playing is not None:
            self.playing = playing


def parse_timecode(value: str | float | None, fallback: float) -> float:
    """Accept 135, "135", "2:15", "2m15s", "about 2:15" or None."""
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if match := re.search(r"(\d+)\s*:\s*(\d{1,2})", text):
        return int(match.group(1)) * 60 + int(match.group(2))
    if match := re.search(r"(?:(\d+)\s*m(?:in)?)?\s*(\d+)?\s*s", text):
        minutes = int(match.group(1) or 0)
        seconds = int(match.group(2) or 0)
        if minutes or seconds:
            return minutes * 60 + seconds
    if match := re.search(r"\d+(?:\.\d+)?", text):
        return float(match.group(0))
    return fallback


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
async def find_moment(session: Session, query: str, seek: bool = False, top_k: int = 3) -> dict:
    """Hybrid semantic + lexical search over the video's segments."""
    t0 = time.time()
    await session.emit({"t": "tool", "name": "find_moment", "status": "running", "detail": query})

    vector = None
    try:
        vector = await asyncio.wait_for(
            asyncio.to_thread(bedrock.embed_text, query), timeout=EMBED_BUDGET_SECS
        )
    except asyncio.TimeoutError:
        logger.warning("query embedding over budget; lexical search only")
    except Exception as exc:  # fall back to lexical-only ranking
        logger.warning(f"query embedding unavailable ({exc}); lexical search only")

    hits = session.index.search(query, vector, top_k=top_k)
    logger.info(f"find_moment('{query}') -> {len(hits)} hits in {(time.time() - t0) * 1000:.0f}ms")

    if not hits:
        await session.emit({"t": "tool", "name": "find_moment", "status": "empty"})
        return {"found": False, "message": "Nothing in this video matches that."}

    await session.emit(
        {
            "t": "cue",
            "source": "find_moment",
            "query": query,
            "seek": bool(seek),
            "moments": [
                {
                    "start": h["start"],
                    "end": h["end"],
                    "timecode": h["timecode"],
                    "label": (h["speech"] or h["visual"] or "")[:120],
                }
                for h in hits
            ],
        }
    )
    return {
        "found": True,
        # Naming the exact moment the player jumped to keeps the spoken answer
        # and the on-screen playhead telling the same story.
        "player_moved_to": hits[0]["spoken_time"] if seek else None,
        "moments": [
            {
                "say_as": h["spoken_time"],
                "timecode": h["timecode"],
                "said": h["speech"][:400],
                "seen": h["visual"][:400],
            }
            for h in hits
        ],
    }


async def look_closer(session: Session, question: str, at_time: str | None = None) -> dict:
    """Pull real frames from the video and reason over the pixels."""
    when = parse_timecode(at_time, session.position)
    when = min(max(when, 0.0), max(session.index.duration - 0.2, 0.0))
    await session.emit(
        {
            "t": "tool",
            "name": "look_closer",
            "status": "running",
            "detail": f"{media.hhmmss(when)} — {question}",
        }
    )
    t0 = time.time()

    path: Path = session.index.source_path
    if not path.exists():
        return {"error": f"video file is not available at {path}"}

    offsets = [o for o in (-1.5, 0.0, 1.5) if 0 <= when + o <= session.index.duration]
    try:
        frames = await asyncio.gather(
            *(media.keyframe_jpeg_async(path, when + o) for o in offsets)
        )
    except Exception as exc:
        logger.warning(f"keyframe extraction failed at {when:.1f}s: {exc}")
        return {"error": "could not read frames at that point in the video"}

    idx = session.index.segment_at(when)
    hint = ""
    if idx is not None:
        seg = session.index.segments[idx]
        hint = f"At {media.hhmmss(when)} the speaker says: {seg['speech'][:300]}"

    try:
        observed = await asyncio.wait_for(
            asyncio.to_thread(bedrock.look_closer, list(frames), question, hint),
            timeout=TOOL_TIMEOUT_SECS,
        )
    except Exception as exc:
        logger.warning(f"look_closer failed: {exc}")
        return {"error": "the visual check did not come back in time"}

    logger.info(f"look_closer@{when:.1f}s in {(time.time() - t0) * 1000:.0f}ms")
    await session.emit(
        {
            "t": "cue",
            "source": "look_closer",
            "seek": False,
            "moments": [
                {
                    "start": when,
                    "end": min(when + 2, session.index.duration),
                    "timecode": media.hhmmss(when),
                    "label": observed[:120],
                }
            ],
        }
    )
    return {"at": media.hhmmss(when), "say_as": media.spoken_time(when), "observed": observed}


async def ask_the_world(session: Session, question: str, filler: str | None = None) -> dict:
    """Delegate to the AgentCore harness for knowledge outside the video.

    The harness runs its own agent loop against Amazon's web index, which takes
    around six seconds — long enough that the assistant says something first, so
    the pause reads as deliberate rather than broken.
    """
    if not agentcore.available():
        return {
            "unavailable": True,
            "message": (
                "The outside-knowledge agent is not configured here, so tell the user you can "
                "only answer from the video itself."
            ),
        }

    await session.emit(
        {"t": "tool", "name": "ask_the_world", "status": "running", "detail": question}
    )
    # Only fill the silence if the model did not already narrate this turn itself,
    # otherwise the user hears the same thing twice.
    if session.speak and not session.llm_spoke_this_turn:
        holding = filler or "Let me look that up."
        await session.speak(holding)
        # Spoken directly by TTS rather than by the LLM, so it would never appear in
        # the transcript — echo it to the UI to keep the two in sync.
        await session.emit({"t": "said", "text": holding})

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                agentcore.ask_world_sync, question, session_id=session.research_session_id
            ),
            timeout=WORLD_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        await session.emit({"t": "tool", "name": "ask_the_world", "status": "empty"})
        return {"error": "the research agent took too long; say you could not reach it"}
    except Exception as exc:
        logger.warning(f"ask_the_world failed: {exc}")
        return {"error": "the research agent could not be reached"}

    if error := result.get("error"):
        return {"error": error}

    await session.emit(
        {
            "t": "research",
            "question": question,
            "queries": result.get("queries", []),
            "tools_used": result.get("tools_used", []),
            "latency_ms": result.get("latency_ms"),
        }
    )
    return {
        "answer": result["answer"],
        "source": "AgentCore web search",
        "note": (
            "You already told the user you were looking this up. Give them the answer directly in "
            "one or two spoken sentences — do not repeat that you searched."
        ),
    }


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
def build_tools(session: Session):
    """Bind the tools to a session and return schemas for the LLM context."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.services.llm_service import FunctionCallParams

    async def _find_moment(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        result = await find_moment(
            session,
            query=str(args.get("query", "")),
            seek=bool(args.get("seek", False)),
        )
        await params.result_callback(result)

    async def _look_closer(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        result = await look_closer(
            session,
            question=str(args.get("question", "what is shown here?")),
            at_time=args.get("at_time"),
        )
        await params.result_callback(result)

    async def _ask_the_world(params: FunctionCallParams) -> None:
        args = params.arguments or {}
        result = await ask_the_world(
            session,
            question=str(args.get("question", "")),
            filler=args.get("say_first"),
        )
        await params.result_callback(result)

    schemas = [
        FunctionSchema(
            name="find_moment",
            description=(
                "Search this video for the moment that best matches a description, and optionally "
                "move the user's player there. Use for 'show me...', 'where do they...', "
                "'find the part about...' or when you need the exact timestamp of something."
            ),
            properties={
                "query": {
                    "type": "string",
                    "description": "What to look for, in plain language, e.g. 'the goal' or 'talking about encryption'.",
                },
                "seek": {
                    "type": "boolean",
                    "description": "True if the user wants the video to jump to that moment.",
                },
            },
            required=["query"],
            handler=_find_moment,
        ),
        FunctionSchema(
            name="look_closer",
            description=(
                "Look at the actual video frames at a point in time to answer a fine-grained visual "
                "question: small on-screen text, a number, a logo, a chart, a jersey, exactly what "
                "someone is doing. Defaults to where the user is currently watching."
            ),
            properties={
                "question": {
                    "type": "string",
                    "description": "The specific visual question to answer.",
                },
                "at_time": {
                    "type": "string",
                    "description": "Optional point in the video, as 'M:SS' or seconds. Omit to use the user's current playhead.",
                },
            },
            required=["question"],
            handler=_look_closer,
        ),
        FunctionSchema(
            name="ask_the_world",
            description=(
                "Ask a research agent with live web access about something outside this video: "
                "background on a company, person, product, technique or term, or current "
                "information the video could not contain. Takes a few seconds, so it also says "
                "your holding phrase out loud immediately."
            ),
            properties={
                "question": {
                    "type": "string",
                    "description": "The full question to research, in plain language.",
                },
                "say_first": {
                    "type": "string",
                    "description": (
                        "A short natural phrase to speak right away while the research runs, "
                        "e.g. 'Let me check that' or 'One second, looking it up'."
                    ),
                },
            },
            required=["question"],
            handler=_ask_the_world,
        ),
    ]
    return ToolsSchema(standard_tools=schemas)
