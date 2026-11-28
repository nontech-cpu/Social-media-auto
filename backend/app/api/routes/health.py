"""Healthcheck and external cron worker tick routes (ideal for Render + UptimeRobot)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.workers.publisher import process_due_posts

router = APIRouter(prefix="/api", tags=["health"])


@router.api_route("/health", methods=["GET", "HEAD"])
async def healthcheck() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.app_env,
        "media_storage_provider": settings.media_storage_provider,
        "meta_configured": bool(settings.meta_app_id and settings.meta_app_secret),
        "google_business_configured": bool(
            settings.google_client_id and settings.google_client_secret
        ),
        "gemini_configured": bool(settings.gemini_api_key),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.api_route("/worker/tick", methods=["GET", "POST"])
async def trigger_worker_tick(
    x_cron_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> dict:
    """Trigger immediate processing of due queued posts (for UptimeRobot / cron-job.org)."""
    settings = get_settings()
    provided = x_cron_secret or secret or ""
    if settings.app_env == "production" and provided != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret.")

    result = await process_due_posts(get_session_factory(), settings=settings)
    return {"ok": True, **result, "timestamp": datetime.now(timezone.utc).isoformat()}
