"""Analytics sync, dashboard overview, Best-Time heatmap, and AI Account Audit routes."""

from fastapi import APIRouter, Depends, HTTPException
from google.genai.errors import APIError as GeminiAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_token_cipher
from app.models.entities import BrandProfile, InstagramAccount
from app.services.ai_copilot import AICopilotService
from app.services.analytics_engine import (
    build_analytics_overview,
    sync_account_and_compute_slots,
)
from app.services.meta_client import MetaAPIError, MetaGraphClient
from app.services.recommendation_engine import (
    build_account_recommendations,
    serialize_recommendation_result,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


async def _get_account_or_404(db: AsyncSession, account_id: str) -> InstagramAccount:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")
    return account


@router.post("/{account_id}/sync")
async def sync_instagram_account(
    account_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    account = await _get_account_or_404(db, account_id)
    client = MetaGraphClient()
    cipher = get_token_cipher()
    try:
        stats = await sync_account_and_compute_slots(db, account, client, cipher)
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"synced": True, **stats}


@router.get("/{account_id}/overview")
async def get_analytics_overview(
    account_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    account = await _get_account_or_404(db, account_id)
    return await build_analytics_overview(db, account)


@router.get("/{account_id}/recommendations")
async def get_best_time_recommendations(
    account_id: str,
    content_format: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account = await _get_account_or_404(db, account_id)
    brand = await db.get(BrandProfile, account.brand_id) if account.brand_id else None
    rec = await build_account_recommendations(
        db, account, brand, content_format_filter=content_format
    )
    return serialize_recommendation_result(rec)


@router.post("/{account_id}/audit")
async def generate_account_audit(
    account_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    account = await _get_account_or_404(db, account_id)
    brand = await db.get(BrandProfile, account.brand_id) if account.brand_id else None
    settings = get_settings()
    try:
        audit = await AICopilotService(settings).generate_account_audit(db, account, brand)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc
    return {"audit": audit}
