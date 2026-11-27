"""AI Copilot routes for captions, trending hashtags, smart batch spacing, and chat."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from google.genai.errors import APIError as GeminiAPIError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import BrandProfile, CopilotMessage, InstagramAccount
from app.services.ai_copilot import AICopilotService
from app.services.hashtag_engine import HashtagEngine
from app.services.queue_planner import recommend_slots_for_batch

router = APIRouter(prefix="/api/ai", tags=["ai"])


class GeneratePostRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=1500)
    media_type: Literal["REELS", "IMAGE", "CAROUSEL", "STORIES"] = "REELS"
    tone_override: str = ""
    hashtags_count: int = Field(default=15, ge=0, le=30)


class BatchPlanItemInput(BaseModel):
    topic: str = Field(min_length=2, max_length=1500)
    media_type: Literal["REELS", "IMAGE", "CAROUSEL", "STORIES"] = "REELS"
    media_urls: list[str] = Field(default_factory=list)


class PlanBatchRequest(BaseModel):
    items: list[BatchPlanItemInput] = Field(min_length=1, max_length=20)
    earliest_start: datetime | None = None
    min_gap_hours: int | None = Field(default=None, ge=6, le=168)
    generate_copy: bool = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    media_urls: list[str] = Field(default_factory=list, max_length=10)
    media_type: Literal["", "REELS", "IMAGE", "CAROUSEL", "STORIES"] = ""


class HashtagLookupRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=1000)
    media_type: Literal["REELS", "IMAGE", "CAROUSEL"] = "REELS"
    count: int = Field(default=15, ge=3, le=30)


async def _load_account_and_brand(
    db: AsyncSession, account_id: str
) -> tuple[InstagramAccount, BrandProfile | None]:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")
    brand = await db.get(BrandProfile, account.brand_id) if account.brand_id else None
    return account, brand


@router.post("/{account_id}/generate-post")
async def generate_post_package(
    account_id: str,
    body: GeneratePostRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account, brand = await _load_account_and_brand(db, account_id)
    try:
        return await AICopilotService(get_settings()).generate_post_package(
            db,
            account,
            brand,
            topic=body.topic,
            media_type=body.media_type,
            tone_override=body.tone_override,
            include_hashtags_count=body.hashtags_count,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI generation error: {exc}") from exc


@router.post("/{account_id}/hashtags")
async def recommend_trending_hashtags(
    account_id: str,
    body: HashtagLookupRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account, brand = await _load_account_and_brand(db, account_id)
    try:
        return await HashtagEngine(get_settings()).recommend_hashtags(
            db,
            account,
            brand,
            topic=body.topic,
            media_type=body.media_type,
            desired_count=body.count,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI generation error: {exc}") from exc


@router.post("/{account_id}/plan-batch")
async def plan_smart_batch_queue(
    account_id: str,
    body: PlanBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account, brand = await _load_account_and_brand(db, account_id)
    slots = await recommend_slots_for_batch(
        db,
        account,
        brand,
        count=len(body.items),
        earliest_start=body.earliest_start,
        min_gap_hours=body.min_gap_hours,
    )

    copilot = AICopilotService(get_settings())
    planned_items = []
    for idx, item in enumerate(body.items):
        slot = slots[idx]
        caption = ""
        hashtags: list[dict] = []
        reach_note = ""
        if body.generate_copy:
            try:
                pkg = await copilot.generate_post_package(
                    db,
                    account,
                    brand,
                    topic=item.topic,
                    media_type=item.media_type,
                    include_hashtags_count=12,
                )
                caption = pkg["recommended_caption"]
                hashtags = pkg["hashtags"]
                reach_note = pkg["reach_prediction_note"]
            except RuntimeError:
                pass

        planned_items.append(
            {
                "index": idx,
                "topic": item.topic,
                "media_type": item.media_type,
                "media_urls": item.media_urls,
                "recommended_caption": caption,
                "hashtags": hashtags,
                "reach_prediction_note": reach_note,
                "scheduled_for": slot.scheduled_for_utc.isoformat(),
                "local_display": slot.local_display,
                "combined_score": slot.combined_score,
                "confidence": slot.confidence,
                "rationale": slot.rationale,
            }
        )

    return {"account_id": account.id, "timezone": account.timezone, "planned_items": planned_items}


@router.get("/{account_id}/chat")
async def get_copilot_history(
    account_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    account, _ = await _load_account_and_brand(db, account_id)
    rows = (
        await db.execute(
            select(CopilotMessage)
            .where(CopilotMessage.account_id == account.id)
            .order_by(CopilotMessage.created_at.asc())
            .limit(50)
        )
    ).scalars().all()
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "structured_payload": m.structured_payload_json,
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ]
    }


@router.post("/{account_id}/chat")
async def send_copilot_message(
    account_id: str,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account, brand = await _load_account_and_brand(db, account_id)
    try:
        return await AICopilotService(get_settings()).chat_with_copilot(
            db,
            account,
            brand,
            body.message,
            media_urls=body.media_urls,
            media_type=body.media_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI generation error: {exc}") from exc
