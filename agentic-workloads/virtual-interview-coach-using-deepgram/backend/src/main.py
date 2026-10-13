"""Backend app entry point.

Run:  python -m src.main   (or: uvicorn src.main:app --port 8000)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .api.consent import router as consent_router
from .api.guidance import router as guidance_router
from .api.job import router as job_router
from .api.resume import router as resume_router
from .api.sessions import router as sessions_router
from .observability import install_error_logging, router as observability_router

# App loggers ("backend") need a handler at INFO — uvicorn only configures its own loggers, so
# without this the request-error and client-event lines are dropped by the root WARNING default.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await db.close_pool()


app = FastAPI(title="InterviewCoach G1 App API", version="0.1.0", lifespan=lifespan)
install_error_logging(app)
app.include_router(sessions_router)
app.include_router(consent_router)
app.include_router(resume_router)
app.include_router(job_router)
app.include_router(guidance_router)
app.include_router(observability_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
