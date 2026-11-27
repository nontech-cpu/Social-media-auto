"""FastAPI application entrypoint with CORS, upload serving, and background scheduler."""

from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import ai, analytics, auth, health, onboarding, posts
from app.core.config import get_settings
from app.core.database import dispose_db, get_session_factory, init_db
from app.workers.publisher import process_due_posts, sync_all_active_accounts

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    scheduler: AsyncIOScheduler | None = None
    if settings.enable_internal_scheduler:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            process_due_posts,
            "interval",
            seconds=settings.worker_poll_interval_seconds,
            args=[get_session_factory()],
            id="publish_due_posts",
            replace_existing=True,
        )
        scheduler.add_job(
            sync_all_active_accounts,
            "interval",
            hours=settings.insights_sync_interval_hours,
            args=[get_session_factory()],
            id="sync_active_accounts",
            replace_existing=True,
        )
        scheduler.start()

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await dispose_db()


app = FastAPI(
    title="Social Media AI — Instagram Planner & Auto-Publisher",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Return structured JSON on unexpected errors so CORS headers remain intact."""
    return JSONResponse(
        status_code=502,
        content={"detail": f"Upstream service error: {exc}"},
    )

upload_path = Path(settings.local_upload_dir)
upload_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(analytics.router)
app.include_router(ai.router)
app.include_router(posts.router)
