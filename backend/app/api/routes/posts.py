"""Media upload, post scheduling, live immediate publishing, and calendar drag-drop routes."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_token_cipher
from app.models.entities import BrandProfile, InstagramAccount, ScheduledPost
from app.services.media_storage import MediaStorageService
from app.services.meta_client import MetaGraphClient
from app.services.queue_planner import recommend_slots_for_batch
from app.workers.publisher import publish_scheduled_post

router = APIRouter(prefix="/api", tags=["posts"])


class CreateScheduledPostInput(BaseModel):
    media_type: Literal["IMAGE", "REELS", "CAROUSEL"] = "IMAGE"
    media_urls: list[str] = Field(min_length=1, max_length=10)
    cover_url: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    share_to_feed: bool = True
    scheduled_for: datetime | None = None
    status: Literal["DRAFT", "QUEUED"] = "QUEUED"
    ai_rationale: str = ""
    predicted_score: float = 0.0


class ScheduleBatchRequest(BaseModel):
    posts: list[CreateScheduledPostInput] = Field(min_length=1, max_length=20)
    auto_space_missing_times: bool = True


class UpdateScheduledPostRequest(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    media_urls: list[str] | None = None
    cover_url: str | None = None
    share_to_feed: bool | None = None
    scheduled_for: datetime | None = None
    status: Literal["DRAFT", "QUEUED"] | None = None


def _as_utc_iso(dt: datetime) -> str:
    aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return aware.isoformat()


def _serialize_post(post: ScheduledPost) -> dict:
    return {
        "id": post.id,
        "account_id": post.account_id,
        "media_type": post.media_type,
        "media_urls": post.media_urls_json or [],
        "cover_url": post.cover_url,
        "caption": post.caption,
        "hashtags": post.hashtags_json or [],
        "share_to_feed": post.share_to_feed,
        "scheduled_for": _as_utc_iso(post.scheduled_for),
        "timezone": post.timezone,
        "status": post.status,
        "ai_rationale": post.ai_rationale,
        "predicted_score": post.predicted_score,
        "ig_container_id": post.ig_container_id,
        "ig_media_id": post.ig_media_id,
        "published_permalink": post.published_permalink,
        "published_at": _as_utc_iso(post.published_at) if post.published_at else None,
        "retry_count": post.retry_count,
        "last_error": post.last_error,
    }


@router.post("/media/upload")
async def upload_media_file(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    service = MediaStorageService(get_settings())
    try:
        uploaded = await service.upload(
            data,
            filename=file.filename or "upload.jpg",
            content_type=file.content_type or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "url": uploaded.url,
        "media_type": uploaded.media_type,
        "filename": uploaded.filename,
        "size_bytes": uploaded.size_bytes,
        "provider": uploaded.provider,
        "is_public_https": uploaded.is_public_https,
        "width": uploaded.width,
        "height": uploaded.height,
        "aspect_ratio": uploaded.aspect_ratio,
        "duration_seconds": uploaded.duration_seconds,
        "warnings": uploaded.warnings,
    }


@router.get("/posts/{account_id}")
async def list_scheduled_posts(
    account_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")

    rows = (
        await db.execute(
            select(ScheduledPost)
            .where(ScheduledPost.account_id == account_id)
            .order_by(ScheduledPost.scheduled_for.asc())
        )
    ).scalars().all()
    return {"posts": [_serialize_post(p) for p in rows]}


@router.post("/posts/{account_id}/schedule")
async def schedule_posts(
    account_id: str,
    body: ScheduleBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")
    brand = await db.get(BrandProfile, account.brand_id) if account.brand_id else None

    missing_indices = [idx for idx, item in enumerate(body.posts) if item.scheduled_for is None]
    recommended_slots = []
    if missing_indices and body.auto_space_missing_times:
        recommended_slots = await recommend_slots_for_batch(
            db, account, brand, count=len(missing_indices)
        )

    slot_cursor = 0
    created: list[ScheduledPost] = []
    for item in body.posts:
        sched_dt = item.scheduled_for
        rationale = item.ai_rationale
        score = item.predicted_score

        if sched_dt is None:
            if slot_cursor < len(recommended_slots):
                rec = recommended_slots[slot_cursor]
                slot_cursor += 1
                sched_dt = rec.scheduled_for_utc
                rationale = rationale or rec.rationale
                score = score or rec.combined_score
            else:
                sched_dt = datetime.now(timezone.utc)
        elif sched_dt.tzinfo is None:
            sched_dt = sched_dt.replace(tzinfo=timezone.utc)

        post = ScheduledPost(
            account_id=account.id,
            media_type=item.media_type,
            media_urls_json=item.media_urls,
            cover_url=item.cover_url,
            caption=item.caption,
            hashtags_json=item.hashtags,
            share_to_feed=item.share_to_feed,
            scheduled_for=sched_dt.astimezone(timezone.utc),
            timezone=account.timezone,
            status=item.status,
            ai_rationale=rationale,
            predicted_score=score,
        )
        db.add(post)
        created.append(post)

    await db.commit()
    for p in created:
        await db.refresh(p)
    return {"posts": [_serialize_post(p) for p in created]}


@router.post("/posts/{account_id}/publish-now")
async def publish_post_immediately(
    account_id: str,
    body: CreateScheduledPostInput,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")

    post = ScheduledPost(
        account_id=account.id,
        media_type=body.media_type,
        media_urls_json=body.media_urls,
        cover_url=body.cover_url,
        caption=body.caption,
        hashtags_json=body.hashtags,
        share_to_feed=body.share_to_feed,
        scheduled_for=datetime.now(timezone.utc),
        timezone=account.timezone,
        status="PUBLISHING",
        ai_rationale=body.ai_rationale or "Published immediately by user.",
        predicted_score=body.predicted_score,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    updated = await publish_scheduled_post(
        db,
        post,
        account,
        meta_client=MetaGraphClient(),
        cipher=get_token_cipher(),
        settings=get_settings(),
    )
    return {"post": _serialize_post(updated)}


@router.patch("/posts/item/{post_id}")
async def update_scheduled_post(
    post_id: str,
    body: UpdateScheduledPostRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    post = await db.get(ScheduledPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Scheduled post not found.")
    if post.status == "PUBLISHED":
        raise HTTPException(status_code=400, detail="Published posts cannot be modified.")

    if body.caption is not None:
        post.caption = body.caption
    if body.hashtags is not None:
        post.hashtags_json = body.hashtags
    if body.media_urls is not None:
        post.media_urls_json = body.media_urls
    if body.cover_url is not None:
        post.cover_url = body.cover_url
    if body.share_to_feed is not None:
        post.share_to_feed = body.share_to_feed
    if body.scheduled_for is not None:
        dt = body.scheduled_for
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        post.scheduled_for = dt.astimezone(timezone.utc)
    if body.status is not None:
        post.status = body.status

    await db.commit()
    await db.refresh(post)
    return {"post": _serialize_post(post)}


@router.delete("/posts/item/{post_id}")
async def delete_scheduled_post(
    post_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    post = await db.get(ScheduledPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Scheduled post not found.")
    await db.delete(post)
    await db.commit()
    return {"deleted": True, "id": post_id}
