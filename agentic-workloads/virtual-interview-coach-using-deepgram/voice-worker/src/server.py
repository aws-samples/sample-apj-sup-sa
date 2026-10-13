"""Worker signaling server — the HTTP entry point for the media plane (Feature 007: Pipecat).

The SPA (frontend/src/lib/webrtcClient.ts) POSTs its WebRTC offer to `{media_endpoint}/offer`
with `Authorization: Bearer <voice_token>`. This server:

  1. Verifies the voice_token (HS256, signed by the backend with the SAME VOICE_TOKEN_SECRET,
     scope "media-join", bound to a session id) — NFR-5: no long-lived secret reaches the SPA.
  2. Initializes a Pipecat SmallWebRTCConnection from the offer SDP (direct browser<->worker media,
     FR-013) and returns the answer SDP — the SAME JSON {sdp, type} contract the SPA already speaks.
  3. Builds an InterviewPipeline for the session (Deepgram STT/TTS + Bedrock via our reply adapter +
     Silero VAD + the custom processors) and runs it; the session-start handoff (minimized plan) is
     attached BEFORE the opening question, which is spoken only after the connection is ready.
  4. Routes data-channel control messages (push-to-talk / mode) into the pipeline's turn gate.

Run:  python -m src.server      (host/port from SERVER_HOST/SERVER_PORT, default 0.0.0.0:8080)

GET /health is an unauthenticated liveness probe (for the NLB/ECS health check).
"""

from __future__ import annotations

import asyncio
import logging

import jwt
from aiohttp import web

from .config import Config
from .logging_setup import setup_logging
from .metrics import MetricsSink
from .persistence import Persistence
from .pipecat_pipeline import InterviewPipeline

log = logging.getLogger("voice_worker")


class _Session:
    """One live media session: the Pipecat pipeline + its run task, torn down together."""

    def __init__(self, session_id: str, pipeline: InterviewPipeline, run_task: asyncio.Task) -> None:
        self.session_id = session_id
        self.pipeline = pipeline
        self.run_task = run_task

    async def close(self) -> None:
        try:
            await self.pipeline.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.run_task is not None and not self.run_task.done():
            self.run_task.cancel()
            try:
                await self.run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


class SignalingServer:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._persistence: Persistence | None = None
        self._cw = self._build_cw_client()
        self._metrics = MetricsSink(
            None, namespace=config.cw_metric_namespace, cw_client=self._cw
        )
        self._sessions: dict[str, _Session] = {}

    def _build_cw_client(self):
        """A CloudWatch client for the custom metrics (turn latency + session summaries).

        Was NEVER wired before (MetricsSink always got cw_client=None), so the per-turn metrics
        existed in code but no datapoint ever reached CloudWatch — the namespace was empty in
        production. None (and a log line) when boto3/credentials are unavailable (local harness)."""
        try:
            import boto3

            return boto3.client("cloudwatch", region_name=self._config.aws_region)
        except Exception as exc:  # noqa: BLE001 - metrics are optional; the loop runs without them
            log.warning("cloudwatch client unavailable (%s); metrics not emitted", type(exc).__name__)
            return None

    async def startup(self, _app: web.Application) -> None:
        if self._config.database_url:
            try:
                provider = None
                if self._config.db_secret_arn:
                    from .db_secret import make_password_provider

                    provider = make_password_provider(
                        self._config.db_secret_arn, self._config.aws_region
                    )
                self._persistence = await Persistence.connect(
                    self._config.database_url, password_provider=provider
                )
                self._metrics = MetricsSink(
                    self._persistence,
                    namespace=self._config.cw_metric_namespace,
                    cw_client=self._cw,
                )
                log.info("persistence connected")
            except Exception as exc:  # noqa: BLE001 - the loop still runs without durable storage
                log.warning("persistence unavailable (%s); running without DB writes", exc)

    async def cleanup(self, _app: web.Application) -> None:
        for sess in list(self._sessions.values()):
            await sess.close()
        self._sessions.clear()
        if self._persistence is not None:
            await self._persistence.close()

    # --- token ---------------------------------------------------------------------------

    def _verify_token(self, request: web.Request) -> dict:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise web.HTTPUnauthorized(reason="missing bearer token")
        token = auth[len("Bearer ") :].strip()
        secret = self._config.voice_token_secret
        if not secret:
            # No real secret -> refuse rather than verify against a public, guessable fallback, which
            # would make media-join forgeable (code-review finding #3). The deployed stack always
            # injects VOICE_TOKEN_SECRET; an unset value here is a misconfiguration, not a valid state.
            log.error("VOICE_TOKEN_SECRET is not set — refusing media join (cannot verify token)")
            raise web.HTTPUnauthorized(reason="server token secret not configured")
        try:
            claims = jwt.decode(token, secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise web.HTTPUnauthorized(reason=f"invalid token: {exc}")
        if claims.get("scope") != "media-join":
            raise web.HTTPForbidden(reason="token scope is not media-join")
        if not claims.get("sid"):
            raise web.HTTPBadRequest(reason="token missing session id")
        return claims

    # --- handlers ------------------------------------------------------------------------

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "sessions": len(self._sessions)})

    async def offer(self, request: web.Request) -> web.Response:
        claims = self._verify_token(request)
        session_id = claims["sid"]
        user_sub = claims.get("sub", "unknown")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise web.HTTPBadRequest(reason="body must be JSON {sdp, type}")
        offer_sdp = body.get("sdp")
        offer_type = body.get("type", "offer")
        if not offer_sdp:
            raise web.HTTPBadRequest(reason="missing sdp")

        # Idempotency: if a session already exists for this id, tear it down and re-accept.
        old = self._sessions.pop(session_id, None)
        if old is not None:
            await old.close()

        # Build the WebRTC connection from the offer and produce the answer SDP. Direct media
        # (browser<->worker UDP); STUN srflx lets the browser reach the Fargate task's public IP.
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

        connection = SmallWebRTCConnection(ice_servers=list(self._config.ice_stun_urls))
        await connection.initialize(sdp=offer_sdp, type=offer_type)

        # Session-start handoff (T018): if the backend assembled a personalized plan during prep, read
        # it + the CONFIRMED resume facts and minimize them into the live grounding payload BEFORE the
        # pipeline opens. Raw PII stays in RDS/S3; only derived highlights reach the live context and
        # nothing raw is logged (FR-204/218). Absent plan -> generic loop.
        session_plan = await self._load_session_plan(session_id)

        pipeline = InterviewPipeline(
            config=self._config,
            connection=connection,
            session_id=session_id,
            metrics=self._metrics,
            persistence=self._persistence,
            session_plan=session_plan,
        )

        # Persist the session row + mark started (network path resolves once ICE connects).
        if self._persistence is not None:
            try:
                await self._persistence.create_session(
                    session_id, user_sub, self._config.reply_provider
                )
                await self._persistence.mark_started(session_id, None)
            except Exception as exc:  # noqa: BLE001 - session may already exist
                log.warning("session row not created (%s)", exc)

        # Route data-channel control messages (push-to-talk / mode) into the pipeline's turn gate.
        @connection.event_handler("app-message")
        async def _on_app_message(_conn, message):  # noqa: ANN001
            if isinstance(message, dict):
                await pipeline.on_control(message)

        # Run the pipeline in the background; the opening question is spoken on the 'connected' event.
        run_task = asyncio.create_task(self._run_pipeline(session_id, pipeline))
        self._sessions[session_id] = _Session(session_id, pipeline, run_task)

        answer = connection.get_answer()
        if not answer:
            raise web.HTTPInternalServerError(reason="failed to produce SDP answer")
        log.info("session %s accepted (pipecat pipeline)", session_id)
        return web.json_response({"sdp": answer["sdp"], "type": answer["type"]})

    async def _load_session_plan(self, session_id: str):
        if self._persistence is None:
            return None
        try:
            raw_plan = await self._persistence.load_interview_plan(session_id)
            if raw_plan is None:
                return None
            from .prep_handoff import build_session_plan

            plan = build_session_plan(raw_plan)
            log.info(
                "session %s personalized: %d planned questions, %d resume highlights",
                session_id,
                len(plan.plan_rows),
                len(plan.resume_highlights),
            )
            return plan
        except Exception as exc:  # noqa: BLE001 - degrade to the generic loop, never block media
            log.warning(
                "session %s plan handoff failed (%s); running generic loop",
                session_id,
                type(exc).__name__,
            )
            return None

    async def _run_pipeline(self, session_id: str, pipeline: InterviewPipeline) -> None:
        try:
            await pipeline.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("session %s pipeline ended with error: %s", session_id, type(exc).__name__)
            pipeline.end_reason = "error"
        finally:
            # Runs on every exit path (normal end, error, cancel-on-teardown).
            self._sessions.pop(session_id, None)
            await self._emit_session_summary(session_id, pipeline)

    async def _emit_session_summary(self, session_id: str, pipeline: InterviewPipeline) -> None:
        """Emit the once-per-session product-quality summary (idempotent; counts only, no PII).

        The end_reason dimension is the AUTHORITATIVE DB value: finalize_session closes the row
        if nothing else did (a true drop) and returns whatever terminal state won — the backend's
        student_ended, wrap-up's completed, or the worker fallback — so the metric can never
        disagree with RDS. Without a DB, the worker-local fallback is used as before.

        Shielded: this is awaited from the run task's `finally`, where a cancel-on-teardown
        (the "dropped" path) would otherwise cancel the emission itself — losing the summary for
        exactly the sessions the evidence loop most needs to count. The CancelledError from a
        second cancel during this await is deliberately swallowed (the shield keeps the emission
        itself alive; teardown must finish regardless)."""
        if pipeline.summary_emitted:
            return
        pipeline.summary_emitted = True
        end_reason = pipeline.end_reason
        try:
            await asyncio.shield(self._finalize_and_record(session_id, pipeline, end_reason))
        except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001 - never affect teardown
            log.warning("session %s summary emission failed (%s)", session_id, type(exc).__name__)

    async def _finalize_and_record(
        self, session_id: str, pipeline: InterviewPipeline, fallback_reason: str
    ) -> None:
        end_reason = fallback_reason
        if self._persistence is not None:
            try:
                authoritative = await self._persistence.finalize_session(
                    session_id, fallback_reason
                )
                if authoritative:
                    end_reason = authoritative
            except Exception as exc:  # noqa: BLE001 - DB trouble must not lose the metric
                log.warning(
                    "session %s finalize failed (%s); using worker-local end_reason",
                    session_id,
                    type(exc).__name__,
                )
        await self._metrics.record_session(
            pipeline.stats, end_reason, self._config.reply_provider
        )


def build_app(config: Config | None = None) -> web.Application:
    config = config or Config.load()
    server = SignalingServer(config)
    app = web.Application()
    app["server"] = server
    app.router.add_get("/health", server.health)
    app.router.add_post("/offer", server.offer)
    app.on_startup.append(server.startup)
    app.on_cleanup.append(server.cleanup)
    return app


def main() -> None:
    config = Config.load()
    setup_logging(config.log_file)
    app = build_app(config)
    log.info("voice-worker signaling server on %s:%d", config.server_host, config.server_port)
    web.run_app(app, host=config.server_host, port=config.server_port, print=None)


if __name__ == "__main__":
    main()
