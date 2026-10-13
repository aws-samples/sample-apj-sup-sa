"""FastAPI app: serves the UI, the local video files, and WebRTC signalling.

Binds to localhost by default. Nothing here is exposed publicly: the browser and
the bot are WebRTC peers on the same machine, and AWS is reached outbound only.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection

from talk2vid import agentcore, bedrock, bot, config, index_store, media, rooms

INDEXES: dict[str, index_store.VideoIndex] = {}
CONNECTIONS: dict[str, SmallWebRTCConnection] = {}
ICE_SERVERS = [IceServer(urls="stun:stun.l.google.com:19302")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    refresh_indexes()
    if INDEXES:
        for vid, idx in INDEXES.items():
            logger.info(
                f"loaded '{vid}' · {idx.title} · {media.hhmmss(idx.duration)} · "
                f"{len(idx.segments)} segments · embeddings={idx.vectors is not None}"
            )
    else:
        logger.warning("no indexes found — run: uv run talk2vid-ingest videos/<file>.mp4")
    logger.info(f"media transport: {config.TRANSPORT}")
    if config.TRANSPORT == "daily":
        await rooms.broker.start()
    # Warm the Bedrock TLS connections now so the first question is not the one
    # that pays for them (~700ms difference on a cold call).
    asyncio.create_task(asyncio.to_thread(bedrock.warmup))
    yield
    for conn in list(CONNECTIONS.values()):
        try:
            await conn.disconnect()
        except Exception:
            pass
    await rooms.broker.close()


class NoCacheStatic(StaticFiles):
    """Serve the UI without caching.

    A stale cached app.js is a miserable thing to debug live, and the files are
    a few KB served from localhost, so caching buys nothing here.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


app = FastAPI(title="Talk to your Video", lifespan=lifespan)
if config.CLIENT_DIR.exists():
    app.mount("/static", NoCacheStatic(directory=config.CLIENT_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse(
        (config.CLIENT_DIR / "index.html").read_text(),
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "videos": len(INDEXES),
        "region": config.AWS_REGION,
        "llm": config.LLM_MODEL_ID,
        "world_knowledge": agentcore.available(),
        # The client picks its media path from this.
        "transport": config.TRANSPORT,
        # …and its hard cap on one spoken turn, so the limit has one home.
        "max_listen_secs": config.MAX_LISTEN_SECS,
    }


_INDEX_FINGERPRINT: tuple = ()


def refresh_indexes() -> bool:
    """Reload indexes when the files on disk change, so a re-ingest shows up
    without restarting the server mid-demo."""
    global _INDEX_FINGERPRINT
    fingerprint = tuple(
        sorted((p.name, p.stat().st_mtime_ns) for p in config.INDEX_DIR.glob("*.json"))
    )
    if fingerprint == _INDEX_FINGERPRINT:
        return False
    _INDEX_FINGERPRINT = fingerprint
    fresh = index_store.load_all()
    INDEXES.clear()
    INDEXES.update(fresh)
    logger.info(f"indexes reloaded ({len(INDEXES)} video(s))")
    return True


@app.post("/api/reload")
async def reload_indexes() -> dict:
    changed = refresh_indexes()
    return {"reloaded": changed, "videos": sorted(INDEXES)}


@app.get("/api/videos")
async def list_videos() -> JSONResponse:
    refresh_indexes()
    payload = []
    for vid, idx in INDEXES.items():
        meta = idx.public_meta()
        meta["playable"] = idx.source_path.exists()
        payload.append(meta)
    payload.sort(key=lambda m: m["title"])
    return JSONResponse(payload)


@app.get("/api/videos/{video_id}/timeline")
async def timeline(video_id: str) -> JSONResponse:
    idx = INDEXES.get(video_id)
    if not idx:
        raise HTTPException(404, "unknown video")
    return JSONResponse(
        [
            {
                "start": s["start"],
                "end": s["end"],
                "timecode": media.hhmmss(s["start"]),
                "speech": s["speech"],
                "visual": s["visual"],
            }
            for s in idx.segments
        ]
    )


@app.get("/media/{video_id}")
async def media_file(video_id: str, request: Request):
    idx = INDEXES.get(video_id)
    if not idx or not idx.source_path.exists():
        raise HTTPException(404, "video file not available")
    # FileResponse handles Range requests, which the <video> element needs to seek.
    return FileResponse(idx.source_path, media_type="video/mp4")


@app.get("/api/thumb/{video_id}")
async def thumbnail(video_id: str, t: float = Query(default=-1.0)):
    idx = INDEXES.get(video_id)
    if not idx or not idx.source_path.exists():
        raise HTTPException(404, "video file not available")
    stamp = t if t >= 0 else min(max(idx.duration * 0.12, 1.0), max(idx.duration - 0.5, 0.5))
    cache = config.CACHE_DIR / f"thumb-{video_id}-{stamp:.1f}.jpg"
    if not cache.exists():
        try:
            data = await media.keyframe_jpeg_async(idx.source_path, stamp, max_width=640)
        except Exception as exc:
            raise HTTPException(500, f"thumbnail failed: {exc}") from exc
        cache.write_bytes(data)
    return Response(
        cache.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"}
    )


@app.post("/api/connect")
async def connect(background_tasks: BackgroundTasks, video_id: str = Query(...)) -> JSONResponse:
    """Daily path: hand the browser a private room the bot is already joining."""
    if config.TRANSPORT != "daily":
        raise HTTPException(400, "server is running in webrtc mode; use /api/offer")
    idx = INDEXES.get(video_id)
    if not idx:
        raise HTTPException(404, f"unknown video '{video_id}'")

    room = await rooms.broker.create(video_id)
    background_tasks.add_task(_run_daily_session, room, idx)
    return JSONResponse(
        {"transport": "daily", "room_url": room["room_url"], "token": room["user_token"]}
    )


async def _run_daily_session(room: dict, idx: index_store.VideoIndex) -> None:
    try:
        await bot.run_bot_daily(room["room_url"], room["bot_token"], idx)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(f"daily bot session failed: {exc}")
    finally:
        await rooms.broker.destroy(room["room_url"])


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks, video_id: str = Query(...)):
    """WebRTC signalling. One bot session per peer connection."""
    idx = INDEXES.get(video_id)
    if not idx:
        raise HTTPException(404, f"unknown video '{video_id}'")

    pc_id = request.get("pc_id")
    if pc_id and pc_id in CONNECTIONS:  # renegotiation of an existing session
        connection = CONNECTIONS[pc_id]
        await connection.renegotiate(
            sdp=request["sdp"], type=request["type"], restart_pc=request.get("restart_pc", False)
        )
        return connection.get_answer()

    connection = SmallWebRTCConnection(ice_servers=ICE_SERVERS)
    await connection.initialize(sdp=request["sdp"], type=request["type"])

    @connection.event_handler("closed")
    async def on_closed(conn: SmallWebRTCConnection):
        CONNECTIONS.pop(conn.pc_id, None)
        logger.info(f"peer closed ({conn.pc_id})")

    background_tasks.add_task(_run_session, connection, idx)
    answer = connection.get_answer()
    CONNECTIONS[answer["pc_id"]] = connection
    logger.info(f"offer accepted · video={video_id} · pc={answer['pc_id']}")
    return answer


async def _run_session(connection: SmallWebRTCConnection, idx: index_store.VideoIndex) -> None:
    try:
        await bot.run_bot(connection, idx)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(f"bot session failed: {exc}")


def main() -> None:
    import uvicorn

    logger.info(f"Talk2Vid on http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
