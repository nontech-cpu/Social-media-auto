"""Google Business Profile OAuth 2.0, sync, analytics overview, and AI review-reply routes."""

from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google.genai.errors import APIError as GeminiAPIError
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_signed_state, get_token_cipher, verify_signed_state
from app.models.entities import BrandProfile, GoogleBusinessProfile, GoogleBusinessReview
from app.services.ai_copilot import AICopilotService
from app.services.google_business_client import GoogleBusinessAPIError, GoogleBusinessClient
from app.services.google_business_service import (
    build_gbp_overview,
    ensure_valid_gbp_token,
    generate_polite_review_reply,
    sync_gbp_profile,
)

router = APIRouter(tags=["google-business"])


class GBPSettingsPatchRequest(BaseModel):
    auto_reply_enabled: bool | None = None
    auto_reply_min_stars: int | None = Field(default=None, ge=1, le=5)
    timezone: str | None = None


class DraftReviewReplyRequest(BaseModel):
    custom_instruction: str = ""


class PublishReviewReplyRequest(BaseModel):
    reply_text: str = Field(min_length=2, max_length=4096)


class GBPChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2500)


async def _get_profile_or_404(db: AsyncSession, profile_id: str) -> GoogleBusinessProfile:
    profile = await db.get(GoogleBusinessProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Google Business Profile not found.")
    return profile


@router.get("/api/auth/google-business/url")
async def get_google_business_oauth_url(
    brand_id: str = Query(default=""),
    timezone_name: str = Query(default="UTC"),
) -> dict:
    settings = get_settings()
    client = GoogleBusinessClient(settings)
    state = create_signed_state({"brand_id": brand_id, "timezone": timezone_name})
    try:
        oauth_url = client.build_oauth_url(state)
    except GoogleBusinessAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"oauth_url": oauth_url, "state": state}


@router.get("/api/auth/google-business/callback")
async def google_business_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    format: str = Query(default="redirect"),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    frontend_base = settings.frontend_url.rstrip("/")

    if error:
        if format == "json":
            raise HTTPException(status_code=400, detail=error_description or error)
        return RedirectResponse(
            f"{frontend_base}/onboarding?{urlencode({'gbp_error': error_description or error})}"
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing Google OAuth code or state parameter.")

    try:
        state_data = verify_signed_state(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = GoogleBusinessClient(settings)
    cipher = get_token_cipher()

    try:
        tokens = await client.exchange_code_for_tokens(code)
        userinfo = await client.fetch_userinfo(tokens["access_token"])
        discovered, diag = await client.discover_locations_with_diagnostics(tokens["access_token"])
    except GoogleBusinessAPIError as exc:
        if format == "json":
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return RedirectResponse(f"{frontend_base}/onboarding?{urlencode({'gbp_error': str(exc)})}")

    if not discovered:
        if diag["accounts_found"] == 0:
            msg = (
                "No Google Business Profile accounts were found for this Google ID. "
                "Make sure you signed in with the Gmail that owns or manages your Google Business Profile."
            )
        else:
            msg = (
                f"Found {diag['accounts_found']} Google Business account(s) "
                f"({', '.join(diag['accounts_without_locations'])}), but 0 locations inside them. "
                "Add or verify your business location in Google Business Profile and retry."
            )
        if format == "json":
            raise HTTPException(status_code=404, detail=msg)
        return RedirectResponse(f"{frontend_base}/onboarding?{urlencode({'gbp_error': msg})}")

    brand_id = state_data.get("brand_id") or None
    if brand_id and (await db.get(BrandProfile, brand_id)) is None:
        brand_id = None
    tz_name = state_data.get("timezone") or "UTC"
    google_email = str(userinfo.get("email") or "")

    connected_profiles: list[GoogleBusinessProfile] = []
    for item in discovered:
        loc_name = item["location_name"]
        existing = (
            await db.execute(
                select(GoogleBusinessProfile).where(
                    GoogleBusinessProfile.location_name == loc_name
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = GoogleBusinessProfile(
                location_name=loc_name,
                location_id=item["location_id"],
                timezone=tz_name,
                auto_reply_min_stars=settings.gbp_auto_reply_min_stars,
            )
            db.add(existing)

        existing.brand_id = brand_id or existing.brand_id
        existing.google_email = google_email or existing.google_email
        existing.gbp_account_name = item["gbp_account_name"]
        existing.location_id = item["location_id"]
        existing.title = item["title"]
        existing.store_code = item["store_code"]
        existing.primary_category = item["primary_category"]
        existing.website_uri = item["website_uri"]
        existing.phone_number = item["phone_number"]
        existing.address_summary = item["address_summary"]
        existing.encrypted_access_token = cipher.encrypt(tokens["access_token"])
        if tokens["refresh_token"]:
            existing.encrypted_refresh_token = cipher.encrypt(tokens["refresh_token"])
        existing.token_expires_at = tokens["expires_at"]
        existing.is_active = True
        connected_profiles.append(existing)

    await db.commit()
    for prof in connected_profiles:
        await db.refresh(prof)
        try:
            await sync_gbp_profile(
                db, prof, client, cipher, settings=settings, auto_reply_eligible=False
            )
        except Exception:
            pass

    primary = connected_profiles[0]
    if format == "json":
        return {
            "connected": [
                {"id": p.id, "title": p.title, "location_name": p.location_name}
                for p in connected_profiles
            ]
        }
    return RedirectResponse(
        f"{frontend_base}/google-business?connected=1&profile_id={primary.id}"
    )


@router.get("/api/google-business/profiles")
async def list_google_business_profiles(db: AsyncSession = Depends(get_db)) -> dict:
    rows = (
        await db.execute(
            select(GoogleBusinessProfile)
            .where(GoogleBusinessProfile.is_active.is_(True))
            .order_by(GoogleBusinessProfile.created_at.desc())
        )
    ).scalars().all()
    return {
        "profiles": [
            {
                "id": p.id,
                "brand_id": p.brand_id,
                "google_email": p.google_email,
                "gbp_account_name": p.gbp_account_name,
                "location_name": p.location_name,
                "location_id": p.location_id,
                "title": p.title,
                "primary_category": p.primary_category,
                "website_uri": p.website_uri,
                "phone_number": p.phone_number,
                "address_summary": p.address_summary,
                "timezone": p.timezone,
                "average_rating": p.average_rating,
                "total_review_count": p.total_review_count,
                "auto_reply_enabled": p.auto_reply_enabled,
                "auto_reply_min_stars": p.auto_reply_min_stars,
                "last_synced_at": p.last_synced_at.isoformat() if p.last_synced_at else None,
                "last_sync_error": p.last_sync_error,
            }
            for p in rows
        ]
    }


@router.patch("/api/google-business/{profile_id}/settings")
async def update_gbp_settings(
    profile_id: str,
    body: GBPSettingsPatchRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await _get_profile_or_404(db, profile_id)
    if body.auto_reply_enabled is not None:
        profile.auto_reply_enabled = body.auto_reply_enabled
    if body.auto_reply_min_stars is not None:
        profile.auto_reply_min_stars = body.auto_reply_min_stars
    if body.timezone is not None and body.timezone.strip():
        profile.timezone = body.timezone.strip()
    await db.commit()
    return {
        "id": profile.id,
        "auto_reply_enabled": profile.auto_reply_enabled,
        "auto_reply_min_stars": profile.auto_reply_min_stars,
        "timezone": profile.timezone,
    }


@router.delete("/api/google-business/{profile_id}")
async def disconnect_google_business_profile(
    profile_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    await _get_profile_or_404(db, profile_id)
    await db.execute(
        delete(GoogleBusinessProfile).where(GoogleBusinessProfile.id == profile_id)
    )
    await db.commit()
    return {"deleted": True, "id": profile_id}


@router.post("/api/google-business/{profile_id}/sync")
async def sync_google_business_profile(
    profile_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    profile = await _get_profile_or_404(db, profile_id)
    settings = get_settings()
    client = GoogleBusinessClient(settings)
    cipher = get_token_cipher()
    try:
        stats = await sync_gbp_profile(
            db, profile, client, cipher, settings=settings, auto_reply_eligible=True
        )
    except GoogleBusinessAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"synced": True, **stats}


@router.get("/api/google-business/{profile_id}/overview")
async def get_google_business_overview(
    profile_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    profile = await _get_profile_or_404(db, profile_id)
    return await build_gbp_overview(db, profile)


@router.post("/api/google-business/{profile_id}/reviews/{review_id}/draft-reply")
async def draft_gbp_review_reply(
    profile_id: str,
    review_id: str,
    body: DraftReviewReplyRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await _get_profile_or_404(db, profile_id)
    review = (
        await db.execute(
            select(GoogleBusinessReview).where(
                GoogleBusinessReview.profile_id == profile.id,
                GoogleBusinessReview.id == review_id,
            )
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    brand = await db.get(BrandProfile, profile.brand_id) if profile.brand_id else None
    settings = get_settings()
    try:
        draft = await generate_polite_review_reply(
            db,
            settings=settings,
            profile=profile,
            review=review,
            brand=brand,
            custom_instruction=body.custom_instruction,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc

    review.ai_suggested_reply = draft["reply_text"]
    if review.reply_status == "UNREPLIED":
        review.reply_status = "DRAFT_READY"
    await db.commit()
    return draft


@router.post("/api/google-business/{profile_id}/reviews/{review_id}/reply")
async def publish_gbp_review_reply(
    profile_id: str,
    review_id: str,
    body: PublishReviewReplyRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await _get_profile_or_404(db, profile_id)
    review = (
        await db.execute(
            select(GoogleBusinessReview).where(
                GoogleBusinessReview.profile_id == profile.id,
                GoogleBusinessReview.id == review_id,
            )
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    settings = get_settings()
    client = GoogleBusinessClient(settings)
    cipher = get_token_cipher()
    try:
        token = await ensure_valid_gbp_token(db, profile, client=client, cipher=cipher)
        await client.put_review_reply(
            token, review.review_resource_name, body.reply_text.strip()
        )
    except GoogleBusinessAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    review.reply_comment = body.reply_text.strip()
    review.reply_updated_at = datetime.now(timezone.utc)
    review.reply_status = "REPLIED"
    await db.commit()
    return {
        "id": review.id,
        "reply_comment": review.reply_comment,
        "reply_status": review.reply_status,
        "reply_updated_at": review.reply_updated_at.isoformat(),
    }


@router.post("/api/google-business/{profile_id}/chat")
async def chat_google_business_copilot(
    profile_id: str,
    body: GBPChatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await _get_profile_or_404(db, profile_id)
    brand = await db.get(BrandProfile, profile.brand_id) if profile.brand_id else None
    settings = get_settings()
    try:
        return await AICopilotService(settings).chat_with_gbp_copilot(
            db, profile, brand, body.message
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc

