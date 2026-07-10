"""AnalyticsAgentCoreProcessor — bridges Pipecat and the deployed Strands agent.

Differences from the stock AWSAgentCoreProcessor:
1. Injects gateway_token into every payload (via token_fn callable).
2. Properly buffers the SSE stream across HTTP chunk boundaries before splitting
   on newlines, preventing the partial-line warnings and dropped text.
3. Reads text from the Strands event path:
       data: {"event": {"contentBlockDelta": {"delta": {"text": "..."}}}}
   rather than the reference-agent shape {"response": "..."}.
"""

import asyncio
import codecs
import json
import os
import secrets
from collections.abc import Callable
from urllib.parse import quote

import aiobotocore.session
import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)

# Short instant fillers spoken the moment the agent call starts, so the user
# hears acknowledgement immediately instead of waiting for the full turn. Index
# rotates per call (no Date/random needed for determinism in replay).
_FILLERS = [
    "Sure, let me pull that up.",
    "Got it — give me a moment.",
    "On it, fetching that now.",
    "Let me look into that for you.",
    "One moment while I get the numbers.",
]
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.aws.agent_core import default_context_to_payload_transformer
from pipecat.services.aws.utils import resolve_credentials


def split_presenter_output(full: str) -> tuple[str, str]:
    """Split an agent response into (spoken_track, displayed_track).

    Presenter contract (see the `voice` branch of Step 5 in
    app/agentcore_strands/unicorn_rental_analytics.sop.md): the agent
    emits exactly one leading ``<speak>...</speak>`` block, then the full
    displayed answer. We SPEAK only the inner text of that block, and DISPLAY
    everything (the whole response with the markers stripped) so the transcript
    keeps the full answer.

    Fallbacks (agent non-compliance) keep the bot from ever reading a markdown
    table aloud:
      * no ``<speak>`` block  -> speak the first sentence/line of plain prose,
        display the whole response.
    """
    import re

    m = re.search(r"<speak>(.*?)</speak>", full, re.S | re.I)
    if m:
        spoken = m.group(1).strip()
        # Displayed track = ONLY what follows </speak> (the formal, digit-form
        # answer + tables). We deliberately DROP the agent's pre-<speak> tool
        # narration AND do not echo the spoken sentence, so the spoken version
        # never appears as text. Strip any stray markers for safety.
        displayed = full[m.end():]
        displayed = re.sub(r"</?speak>", "", displayed, flags=re.I).strip()
        # Trivial answers may have nothing after </speak>; fall back to the
        # spoken line so the chat isn't left blank.
        if not displayed:
            displayed = spoken
        return spoken, displayed
    # Malformed: an OPENING <speak> with no matching close (the agent got cut off
    # or emitted a stray tag). Treat everything after the first <speak> as the
    # spoken track and display the prose with all speak markers removed, so the
    # literal "<speak>" can never leak into the chat (bug: <speak> shown as text).
    open_m = re.search(r"<speak>", full, re.I)
    if open_m:
        spoken = re.sub(r"</?speak>", "", full[open_m.end():], flags=re.I).strip()
        displayed = re.sub(r"</?speak>", "", full, flags=re.I).strip()
        return spoken, displayed
    # Fallback: no <speak> markers at all (agent ignored the SOP). Display the
    # WHOLE response (with any stray speak markers stripped, belt-and-suspenders),
    # and speak the first non-empty, non-table, non-heading line so we never read
    # a markdown table aloud.
    displayed = re.sub(r"</?speak>", "", full, flags=re.I).strip()
    for line in full.splitlines():
        s = line.strip()
        if not s:
            continue
        if s[0] in "|#>`-*" or s.startswith("```"):
            continue
        # first sentence of that line
        spoken = re.split(r"(?<=[.!?])\s", re.sub(r"</?speak>", "", s, flags=re.I), maxsplit=1)[0]
        return spoken, displayed
    return "", displayed


def _extract_chart_tags(displayed: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract chart tags from the displayed track and strip them out.

    The agent presigns charts in its stream loop, so the normal tag carries a ready
    URL (self-closing or paired):
      <chart caption="Bookings by breed" url="https://..." />
    We also still accept the raw-key form as a fallback (e.g. if presign failed
    upstream): <chart caption="..." s3key="charts/abc.png" />.
    Returns (displayed_without_chart_tags, [(caption, url, s3key), ...]); url or
    s3key may be "" — _emit_chart prefers url and falls back to presigning s3key.
    Attribute order is not assumed; caption is optional.
    """
    import re

    charts: list[tuple[str, str, str]] = []
    for m in re.finditer(r"<chart\b([^>]*?)/?>(?:\s*</chart>)?", displayed, re.S | re.I):
        attrs = m.group(1)
        url_m = re.search(r'\burl\s*=\s*"([^"]+)"', attrs, re.I)
        key_m = re.search(r'\bs3key\s*=\s*"([^"]+)"', attrs, re.I)
        cap_m = re.search(r'\bcaption\s*=\s*"([^"]*)"', attrs, re.I)
        if url_m or key_m:
            charts.append((
                cap_m.group(1).strip() if cap_m else "",
                url_m.group(1).strip() if url_m else "",
                key_m.group(1).strip() if key_m else "",
            ))
    cleaned = re.sub(r"<chart\b[^>]*?/?>(?:\s*</chart>)?", "", displayed, flags=re.S | re.I).strip()
    return cleaned, charts


def _extract_ack(buf: str) -> tuple[str | None, str]:
    """If `buf` contains a COMPLETE <ack>...</ack> block, return (ack_text, buf_without_ack).

    Used to detect the model's leading spoken acknowledgement AS IT STREAMS, so we can
    speak it immediately (during the silent tool-call gap) instead of waiting for the
    whole turn. Returns (None, buf) until a full closing </ack> has arrived. The ack is
    a short plain sentence (no markup), so a simple non-greedy match is safe.
    """
    import re
    m = re.search(r"<ack>(.*?)</ack>", buf, re.S | re.I)
    if not m:
        return None, buf
    ack = re.sub(r"</?ack>", "", m.group(1), flags=re.I).strip()
    cleaned = (buf[:m.start()] + buf[m.end():])
    return (ack or None), cleaned


def _strip_ack(text: str) -> str:
    """Remove any <ack>...</ack> block (and stray ack tags) from text — final-cleanup
    backstop so the acknowledgement never leaks into the spoken headline or the display."""
    import re
    text = re.sub(r"<ack>.*?</ack>", "", text, flags=re.S | re.I)
    return re.sub(r"</?ack>", "", text, flags=re.I)


def _extract_text(line: str) -> str | None:
    """Extract spoken text from a single Strands SSE data line (without 'data: ' prefix).

    Looks for: {"event": {"contentBlockDelta": {"delta": {"text": "<chunk>"}}}}
    Also handles the agent's error-fallback shape {"type": "text", "content": "..."}
    so agent-side failures are SPOKEN rather than silently dropped (which would make
    the bot go mute mid-conversation).
    Returns None for all other event types (tool calls, metadata, lifecycle markers).
    """
    try:
        d = json.loads(line)
        delta = d.get("event", {}).get("contentBlockDelta", {}).get("delta", {})
        text = delta.get("text")
        if text:
            return text
        # Agent error fallback: {"type": "text", "content": "..."}
        if d.get("type") == "text" and d.get("content"):
            return d["content"]
        return None
    except Exception:
        return None


class AnalyticsAgentCoreProcessor(FrameProcessor):
    """Pipecat processor that invokes the deployed Strands analytics agent.

    Accepts an LLMContextFrame, calls AgentCore with the last user message +
    a fresh gateway_token, buffers the SSE response stream across HTTP chunk
    boundaries, and emits LLMTextFrames for each spoken-text chunk.
    """

    def __init__(
        self,
        agent_arn: str,
        token_fn: Callable[[], str],
        aws_region: str | None = None,
        session_id: str | None = None,
        user_token: str | None = None,
        qualifier: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._agent_arn = agent_arn
        self._token_fn = token_fn
        # The SPEAKING user's own Cognito access token, forwarded from the browser
        # via the JWT-gated start proxy (session body.gateway_token). This is THE
        # identity used for AgentCore RBAC/RLS — each user gets their own, and it is
        # the Bearer credential the runtime's JWT authorizer validates. There is no
        # demo identity in hosted mode; token_fn only does anything in laptop dev
        # with ALLOW_DEMO_FALLBACK=true (otherwise it raises).
        self._user_token = user_token
        self._aws_region = aws_region or os.getenv("AWS_REGION", "us-west-2")
        # Runtime endpoint qualifier for the HTTPS invoke URL (the SigV4 SDK defaulted
        # this; raw HTTPS must pass it). Our endpoint is 'agentic_analytics_endpoint'.
        self._qualifier = qualifier or os.getenv("AWS_AGENT_QUALIFIER", "DEFAULT")
        # aiobotocore session retained ONLY for the chart-presign backstop (an S3 call
        # made with the task role, not the user JWT). The agent normally presigns.
        self._aws_session = aiobotocore.session.get_session()
        self._aws_params = resolve_credentials(region=self._aws_region).to_boto_kwargs()
        # Stable runtimeSessionId per Pipecat connection: keeps AgentCore Memory threading
        # (MemoryHookProvider keys on context.session_id) and microVM warm-start affinity.
        # AWS requires 33–256 chars; secrets.token_hex(20) → 40 ASCII-safe chars.
        self._session_id = session_id or f"voice-{secrets.token_hex(20)}"
        self._filler_idx = 0
        # Spoken filler is OFF by default. It plays bot audio during the agent's
        # (silent) thinking gap; on a transport WITHOUT acoustic echo cancellation
        # (e.g. Krisp unavailable) that audio echoes back into the mic, trips VAD,
        # and fires a FALSE barge-in that cancels the in-flight turn — discarding an
        # answer the agent already generated and wedging the conversation. The UI's
        # "thinking" indicator already covers latency feedback. Only enable this
        # where AEC is guaranteed (set VOICE_SPOKEN_FILLER=true).
        self._spoken_filler = os.getenv("VOICE_SPOKEN_FILLER", "false").lower() in ("true", "1", "yes")
        # Dynamic ack (Option A): speak the model's own leading <ack> as it streams,
        # instead of a static _FILLERS phrase. Defaults ON whenever spoken filler is
        # enabled (i.e. on AEC-capable transports). Set VOICE_DYNAMIC_ACK=false to fall
        # back to the static filler. Has no effect unless _spoken_filler is also on.
        self._dynamic_ack = os.getenv("VOICE_DYNAMIC_ACK", "true").lower() in ("true", "1", "yes")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        payload_str = default_context_to_payload_transformer(frame.context)
        if not payload_str:
            return

        payload = json.loads(payload_str)
        # Use the speaking user's forwarded token (real identity → correct
        # RBAC/RLS). No demo fallback in hosted mode: if it's absent, _token_fn()
        # raises unless ALLOW_DEMO_FALLBACK=true (laptop dev). Speak a clear error
        # rather than crash the turn.
        try:
            gateway_token = self._user_token or self._token_fn()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"no per-user gateway_token: {e}")
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame("Sorry, I couldn't verify your sign-in, so I can't access your data. Please log in and try again."))
            await self.push_frame(LLMFullResponseEndFrame())
            return
        # gateway_token is sent as the Authorization: Bearer header (below), NOT in the
        # payload — the runtime's JWT authorizer validates it and the agent reads it
        # from the request headers.
        # Voice mode: the agent (one unified SOP) splits its reply into a spoken
        # <speak> headline + the full displayed answer. No sop_s3_key — the agent
        # selects the voice branch of its single SOP from this mode field.
        payload["mode"] = "voice"

        # Acknowledgement during the (silent) agent think gap. TWO mutually-exclusive
        # strategies, both gated on AEC being present (without echo cancellation the
        # bot's own audio echoes into the mic and trips a false barge-in):
        #   • Dynamic ack (Option A, PREFERRED): the MODEL emits a leading
        #     <ack>...</ack>, which the stream loop below speaks the moment it arrives
        #     — contextual, no static words. Enabled by VOICE_DYNAMIC_ACK (default on
        #     where _spoken_filler is on). Nothing to push here; the loop handles it.
        #   • Static filler (legacy fallback): a canned phrase from _FILLERS, spoken
        #     immediately. Only if explicitly forced AND dynamic ack is off, so the two
        #     never double-speak.
        if self._spoken_filler and not self._dynamic_ack:
            filler = _FILLERS[self._filler_idx % len(_FILLERS)]
            self._filler_idx += 1
            await self.push_frame(TTSSpeakFrame(filler))

        logger.info(f"[turn] invoking agent (session={self._session_id[:24]}..., mode=voice)")

        # Accumulate the full agent text, then split. The agent turn is short
        # enough that buffering before TTS costs little, and it lets us speak
        # ONLY the <speak> block (never the agent's pre-<speak> tool narration or
        # the post-</speak> markdown table).
        chunks: list[str] = []
        # Streaming acknowledgement (Option A): the model emits a leading
        # <ack>...</ack> BEFORE calling tools. We watch the growing text and, the
        # moment a complete <ack> has streamed in, speak it immediately — so the user
        # hears a contextual acknowledgement during the (silent) data-fetch gap,
        # instead of a static Pipecat-side filler. Gated to AEC-capable transports
        # (the bot would otherwise hear its own ack as a barge-in); PCC+Daily runs
        # Krisp, so it's enabled there. _ack_text accumulates only the spoken text
        # seen so far (charts/tool noise are not in the spoken stream).
        ack_spoken = False
        ack_seen = ""

        # JWT-native invoke: the runtime authorizer validates the user's Cognito access
        # token (gateway_token), so we call InvokeAgentRuntime over plain HTTPS with
        # `Authorization: Bearer` — NOT the SigV4 SDK. AWS docs: OAuth-integrated agents
        # must use a direct HTTPS request. The SSE line-parsing below is unchanged.
        invoke_url = (
            f"https://bedrock-agentcore.{self._aws_region}.amazonaws.com/runtimes/"
            f"{quote(self._agent_arn, safe='')}/invocations?qualifier={quote(self._qualifier, safe='')}"
        )
        headers = {
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": self._session_id,
        }
        decoder = codecs.getincrementaldecoder("utf-8")()
        buf = ""
        total_bytes = 0
        done = False
        # Safety cap only (see note): charts go via S3 + a tiny <chart> tag, and the
        # agent yields only text-delta events, so responses stay small. Backstop only.
        MAX_BYTES = 16_000_000

        # total= bounds the WHOLE turn so a stalled agent can never hang the pipeline
        # indefinitely (the read either completes, errors, or trips this deadline and
        # we close the response cleanly). sock_read guards against a silently stalled
        # socket mid-stream.
        timeout = aiohttp.ClientTimeout(total=180, sock_read=120)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(invoke_url, headers=headers,
                                      data=json.dumps(payload).encode()) as response:
                    if response.status != 200:
                        body = await response.text()
                        logger.error(f"AgentCore invoke HTTP {response.status}: {body[:300]}")
                        await self.push_frame(LLMFullResponseStartFrame())
                        msg = ("Sorry, your session looks expired — please sign in again."
                               if response.status in (401, 403)
                               else "Sorry, I hit an error reaching the analytics agent. Please try again.")
                        await self.push_frame(LLMTextFrame(msg))
                        await self.push_frame(LLMFullResponseEndFrame())
                        return
                    is_sse = "text/event-stream" in (response.headers.get("Content-Type", ""))

                    async for chunk in response.content.iter_any():
                        total_bytes += len(chunk)
                        buf += decoder.decode(chunk)
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            if is_sse:
                                if not line.startswith("data: "):
                                    continue
                                text = _extract_text(line[6:])
                            else:
                                text = _extract_text(line)
                            if text:
                                chunks.append(text)
                                # Speak the leading <ack> the instant it's complete,
                                # before the rest of the turn (the slow tool calls)
                                # arrives. Only on AEC-capable transports.
                                if self._spoken_filler and self._dynamic_ack and not ack_spoken:
                                    ack_seen += text
                                    ack, _ = _extract_ack(ack_seen)
                                    if ack:
                                        ack_spoken = True
                                        await self.push_frame(LLMFullResponseStartFrame())
                                        await self.push_frame(LLMTextFrame(ack))
                                        await self.push_frame(LLMFullResponseEndFrame())
                        if total_bytes > MAX_BYTES:
                            logger.warning(f"AgentCore stream exceeded {MAX_BYTES} bytes; truncating read")
                            done = True
                            break
        except asyncio.CancelledError:
            # A (possibly false) barge-in cancelled this turn mid-flight. Log it so the
            # cause is visible, then re-raise: Pipecat MUST see the cancellation to tear
            # down the in-flight turn and accept the next one. Swallowing it would leave
            # the pipeline wedged (the original failure mode).
            logger.info(f"[turn] cancelled mid-stream after {total_bytes}B (likely barge-in)")
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"[turn] agent stream failed after {total_bytes}B: {type(e).__name__}: {e}")
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame("Sorry, that took too long to answer. Please try again."))
            await self.push_frame(LLMFullResponseEndFrame())
            return

        if not done:
            buf += decoder.decode(b"", final=True)
        if buf:
            line = buf.strip()
            if is_sse and line.startswith("data: "):
                line = line[6:]
            text = _extract_text(line)
            if text:
                chunks.append(text)

        full = "".join(chunks)
        if not full:
            logger.warning(f"[turn] agent returned no text ({total_bytes}B read)")
            return
        # Drop the leading <ack> from the final text: if it was spoken mid-stream it
        # must not be re-spoken in the headline; and it must never appear in the
        # displayed transcript. Backstop strip covers stray/unspoken acks too.
        full = _strip_ack(full).lstrip()
        logger.info(f"[turn] agent answer received ({len(full)} chars, {total_bytes}B)")

        # Voice mode always returns the presenter split (spoken <speak> headline +
        # displayed detail). split_presenter_output falls back gracefully if the
        # agent ever omits the <speak> markers.
        spoken, displayed = split_presenter_output(full)

        # Spoken track -> TTS (LLMTextFrame flows downstream to the TTS service).
        if spoken:
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(spoken))
            await self.push_frame(LLMFullResponseEndFrame())

        # Pull any chart tags out of the displayed track. The agent presigns charts
        # in its own stream loop, so the tag normally carries a ready URL:
        #   <chart caption="..." url="https://..." />
        # We just forward it (with an s3key backstop). Binary image data never crosses
        # the LLM stream (no base64) — that's what previously bloated it to 100MB+.
        charts: list[tuple[str, str, str]] = []
        if displayed:
            displayed, charts = _extract_chart_tags(displayed)

        # Emit in natural reading order: the narration/answer TEXT first, THEN the
        # chart image beneath it. (Previously the chart RTVI message was emitted
        # before the display text, so the image appeared above its own explanation.)
        if displayed:
            await self._emit_display(displayed)
        for caption, url, s3key in charts:
            await self._emit_chart(caption, url, s3key)

    async def _emit_chart(self, caption: str, url: str, s3key: str = ""):
        """Send a chart to the client as an RTVI 'chart' message.

        The agent presigns charts in its stream loop, so `url` is normally already a
        viewable presigned GET URL — we just forward it. As a backstop (e.g. if the
        agent emitted only an s3key), we presign here. No base64 ever crosses the LLM
        stream or the RTVI channel either way.
        """
        if not url and s3key:
            url = await self._presign_chart(s3key) or ""
        if not url:
            return
        try:
            from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame  # type: ignore

            await self.push_frame(
                RTVIServerMessageFrame(
                    data={"type": "chart", "mime": "image/png", "url": url, "caption": caption}
                )
            )
        except Exception as e:  # pragma: no cover
            logger.debug(f"chart not emitted as RTVI frame ({e}); skipping")

    async def _presign_chart(self, s3key: str) -> str | None:
        """Backstop presign for a chart PNG (normally the agent already presigned)."""
        bucket = os.getenv("CHART_BUCKET") or os.getenv("SOP_S3_BUCKET")
        if not bucket:
            logger.warning("CHART_BUCKET/SOP_S3_BUCKET unset; cannot presign chart")
            return None
        # Guard against the agent emitting an absolute s3:// URI or a stray bucket prefix.
        key = s3key.replace(f"s3://{bucket}/", "").lstrip("/")
        try:
            async with self._aws_session.create_client(  # pyright: ignore
                "s3", **self._aws_params  # pyright: ignore
            ) as s3:
                return await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key},
                    ExpiresIn=3600,
                )
        except Exception as e:
            logger.warning(f"failed to presign chart s3://{bucket}/{key}: {e}")
            return None

    async def _emit_display(self, markdown: str):
        """Send the displayed track to the client without speaking it.

        Uses an RTVI server message when an RTVI processor is reachable; otherwise
        logs and drops (the spoken track already carried the answer aloud). This
        keeps the bot working in the bare Pipecat Playground and in the dashboard.
        """
        try:
            from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame  # type: ignore

            await self.push_frame(
                RTVIServerMessageFrame(data={"type": "display-text", "markdown": markdown})
            )
        except Exception as e:  # pragma: no cover - depends on pipecat build
            logger.debug(f"display-text not emitted as RTVI frame ({e}); skipping")
