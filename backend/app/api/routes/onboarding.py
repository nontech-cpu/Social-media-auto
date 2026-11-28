"""Ocoya-style Brand Kit onboarding routes (website fetch + AI enrichment)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import BrandProfile
from app.services.ai_copilot import AICopilotService
from app.services.brand_scraper import scrape_brand_from_website

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class FetchBrandRequest(BaseModel):
    website_url: str
    language: str = "English"


class SuggestBrandRequest(BaseModel):
    brand_name: str = Field(min_length=1, max_length=200)
    niche_hint: str = ""
    language: str = "English"


class BrandUpdateRequest(BaseModel):
    id: str | None = None
    website_url: str = ""
    brand_name: str = Field(min_length=1, max_length=200)
    niche: str = ""
    description: str = ""
    tone_of_voice: str = "Authentic, engaging, value-driven"
    target_audience: str = ""
    language: str = "English"
    logo_url: str = ""
    colors: list[str] = Field(default_factory=list)
    content_pillars: list[str] = Field(default_factory=list)
    posting_goal_per_week: int = Field(default=4, ge=1, le=14)


def _serialize_brand(b: BrandProfile) -> dict:
    return {
        "id": b.id,
        "website_url": b.website_url,
        "brand_name": b.brand_name,
        "niche": b.niche,
        "description": b.description,
        "tone_of_voice": b.tone_of_voice,
        "target_audience": b.target_audience,
        "language": b.language,
        "logo_url": b.logo_url,
        "colors": b.colors_json or [],
        "content_pillars": b.content_pillars_json or [],
        "posting_goal_per_week": b.posting_goal_per_week,
    }


@router.post("/fetch-brand")
async def fetch_brand_from_website(
    body: FetchBrandRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        scraped = await scrape_brand_from_website(body.website_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not fetch website: {exc}") from exc

    settings = get_settings()
    niche = ""
    tone = "Authentic, engaging, value-driven"
    audience = ""
    pillars: list[str] = []
    goal = 4
    brand_name = scraped.brand_name
    description = scraped.description

    if settings.gemini_api_key:
        try:
            enriched = await AICopilotService(settings).enrich_brand_from_signals(
                scraped=scraped, language=body.language
            )
            brand_name = enriched.brand_name or brand_name
            description = enriched.description or description
            niche = enriched.niche
            tone = enriched.tone_of_voice
            audience = enriched.target_audience
            pillars = enriched.content_pillars
            goal = enriched.posting_goal_per_week
        except Exception:
            pass

    brand = BrandProfile(
        website_url=scraped.website_url,
        brand_name=brand_name,
        niche=niche,
        description=description,
        tone_of_voice=tone,
        target_audience=audience,
        language=body.language,
        logo_url=scraped.logo_url,
        colors_json=scraped.colors,
        content_pillars_json=pillars,
        posting_goal_per_week=goal,
    )
    db.add(brand)
    await db.commit()
    await db.refresh(brand)
    return {"brand": _serialize_brand(brand)}


@router.post("/suggest-brand")
async def suggest_brand_from_name(body: SuggestBrandRequest) -> dict:
    """Use Gemini to generate Brand Kit suggestions tailored to the user's entered Brand Name."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=400,
            detail="GEMINI_API_KEY is required for AI brand auto-fill.",
        )
    try:
        enriched = await AICopilotService(settings).enrich_brand_from_signals(
            brand_name=body.brand_name.strip(),
            niche_hint=body.niche_hint.strip(),
            language=body.language,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI brand suggestion failed: {exc}") from exc

    return {
        "suggestion": {
            "brand_name": enriched.brand_name or body.brand_name.strip(),
            "niche": enriched.niche,
            "description": enriched.description,
            "tone_of_voice": enriched.tone_of_voice,
            "target_audience": enriched.target_audience,
            "content_pillars": enriched.content_pillars,
            "posting_goal_per_week": enriched.posting_goal_per_week,
        }
    }


@router.get("/brand")
async def get_latest_brand(db: AsyncSession = Depends(get_db)) -> dict:
    brand = (
        await db.execute(select(BrandProfile).order_by(BrandProfile.updated_at.desc()).limit(1))
    ).scalar_one_or_none()
    return {"brand": _serialize_brand(brand) if brand else None}


@router.put("/brand")
async def save_brand(
    body: BrandUpdateRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    brand = await db.get(BrandProfile, body.id) if body.id else None
    if brand is None:
        brand = BrandProfile()
        db.add(brand)

    brand.website_url = body.website_url
    brand.brand_name = body.brand_name
    brand.niche = body.niche
    brand.description = body.description
    brand.tone_of_voice = body.tone_of_voice
    brand.target_audience = body.target_audience
    brand.language = body.language
    brand.logo_url = body.logo_url
    brand.colors_json = body.colors
    brand.content_pillars_json = body.content_pillars
    brand.posting_goal_per_week = body.posting_goal_per_week

    await db.commit()
    await db.refresh(brand)
    return {"brand": _serialize_brand(brand)}
