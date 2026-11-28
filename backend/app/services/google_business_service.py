"""Google Business Profile analytics formulas, growth computation, sync, and humble AI replies."""

from datetime import datetime, timedelta, timezone
import json
from statistics import median
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import TokenCipher, get_token_cipher
from app.models.entities import (
    BrandProfile,
    GoogleBusinessDailyMetric,
    GoogleBusinessProfile,
    GoogleBusinessReview,
)
from app.services.gemini_helper import generate_structured_with_usage, record_ai_usage
from app.services.google_business_client import GoogleBusinessAPIError, GoogleBusinessClient


class ReviewReplyOutput(BaseModel):
    reply_text: str = Field(
        description="Polite, warm, humble Google review reply matching the customer's language and Brand Kit tone"
    )
    tone_note: str = Field(
        description="Short summary of the approach taken (e.g., Humble apology + offline follow-up)"
    )


def _parse_iso_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw.astimezone(timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _pct_growth(current_val: int | float, previous_val: int | float) -> float | None:
    """Compute exact period-over-period growth %; return None when prior baseline is zero."""
    if previous_val <= 0:
        return None
    return round(((current_val - previous_val) / previous_val) * 100.0, 2)


async def ensure_valid_gbp_token(
    db: AsyncSession,
    profile: GoogleBusinessProfile,
    *,
    client: GoogleBusinessClient,
    cipher: TokenCipher,
) -> str:
    """Decrypt active Google OAuth token and auto-refresh via refresh_token when near expiry."""
    now = datetime.now(timezone.utc)
    exp = (
        profile.token_expires_at.replace(tzinfo=timezone.utc)
        if profile.token_expires_at and profile.token_expires_at.tzinfo is None
        else profile.token_expires_at
    )
    if profile.encrypted_access_token and exp and exp > (now + timedelta(minutes=5)):
        return cipher.decrypt(profile.encrypted_access_token)

    refresh_tok = cipher.decrypt(profile.encrypted_refresh_token) if profile.encrypted_refresh_token else ""
    if not refresh_tok:
        if profile.encrypted_access_token:
            return cipher.decrypt(profile.encrypted_access_token)
        raise GoogleBusinessAPIError("Google Business OAuth token missing. Please reconnect.", status_code=401)

    refreshed = await client.refresh_access_token(refresh_tok)
    profile.encrypted_access_token = cipher.encrypt(refreshed["access_token"])
    profile.token_expires_at = refreshed["expires_at"]
    await db.commit()
    return refreshed["access_token"]


async def generate_polite_review_reply(
    db: AsyncSession,
    *,
    settings: Settings,
    profile: GoogleBusinessProfile,
    review: GoogleBusinessReview,
    brand: BrandProfile | None = None,
    custom_instruction: str = "",
) -> dict[str, Any]:
    """Craft a warm, humble, non-defensive reply grounded in the customer's exact review and Brand Kit."""
    static_profile = {
        "business_name": profile.title or (brand.brand_name if brand else "Our Business"),
        "category": profile.primary_category,
        "brand_tone": brand.tone_of_voice if brand else "Warm, humble, grateful",
        "brand_niche": brand.niche if brand else "",
        "website": profile.website_uri,
        "phone": profile.phone_number,
    }
    system_instruction = (
        "You are an empathetic, humble Business Owner replying to a Google Business Profile customer review.\n"
        f"Business Profile: {json.dumps(static_profile)}\n\n"
        "RULES:\n"
        "1. Be genuinely humble, polite, appreciative, and human — NEVER robotic, defensive, or argumentative.\n"
        "2. Match the exact language of the customer's review (English -> English; Hindi -> Hindi; Hinglish -> polite English/Hinglish; German -> German; if no text comment, use warm English).\n"
        "3. For 4-5 star reviews: thank them by first name (if available), reference what they appreciated, and warmly welcome them back.\n"
        "4. For 1-3 star reviews: sincerely apologize for falling short, thank them humbly for bringing it up, avoid excuses, and invite them to reach out directly via phone/website so you can make it right.\n"
        "5. Keep reply concise (2-4 natural sentences) with zero placeholder brackets like [Your Name]."
    )
    prompt_payload = {
        "reviewer_name": review.reviewer_name,
        "star_rating": review.star_rating,
        "review_comment": review.comment or "(Star rating only — no written comment)",
        "owner_custom_instruction": custom_instruction.strip() if custom_instruction else None,
    }
    parsed, usage = await generate_structured_with_usage(
        settings,
        prompt=f"Review Details: {json.dumps(prompt_payload)}",
        system_instruction=system_instruction,
        response_schema=ReviewReplyOutput,
        temperature=0.45,
    )
    record_ai_usage(db, account_id=None, feature="gbp_review_reply", usage=usage)
    return {
        "reply_text": parsed.reply_text.strip(),
        "tone_note": parsed.tone_note.strip(),
        "token_usage": usage,
    }


async def sync_gbp_profile(
    db: AsyncSession,
    profile: GoogleBusinessProfile,
    client: GoogleBusinessClient,
    cipher: TokenCipher,
    *,
    settings: Settings | None = None,
    auto_reply_eligible: bool = True,
) -> dict[str, Any]:
    """Sync 60d daily performance metrics & latest reviews, and optionally auto-reply to positive reviews."""
    cfg = settings or get_settings()
    token = await ensure_valid_gbp_token(db, profile, client=client, cipher=cipher)

    end_d = datetime.now(timezone.utc).date()
    start_d = end_d - timedelta(days=59)

    synced_days = 0
    synced_reviews = 0
    auto_replied = 0
    drafts_prepared = 0
    sync_warnings: list[str] = []

    try:
        daily_rows = await client.fetch_daily_performance_metrics(
            token, profile.location_id, start_date=start_d, end_date=end_d
        )
        for row in daily_rows:
            existing = (
                await db.execute(
                    select(GoogleBusinessDailyMetric).where(
                        GoogleBusinessDailyMetric.profile_id == profile.id,
                        GoogleBusinessDailyMetric.metric_date == row["metric_date"],
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = GoogleBusinessDailyMetric(
                    profile_id=profile.id, metric_date=row["metric_date"]
                )
                db.add(existing)
            existing.impressions_search_desktop = row["impressions_search_desktop"]
            existing.impressions_search_mobile = row["impressions_search_mobile"]
            existing.impressions_maps_desktop = row["impressions_maps_desktop"]
            existing.impressions_maps_mobile = row["impressions_maps_mobile"]
            existing.website_clicks = row["website_clicks"]
            existing.call_clicks = row["call_clicks"]
            existing.direction_requests = row["direction_requests"]
            existing.total_impressions = row["total_impressions"]
            existing.total_actions = row["total_actions"]
            existing.synced_at = datetime.now(timezone.utc)
            synced_days += 1
    except GoogleBusinessAPIError as exc:
        sync_warnings.append(f"Performance metrics: {exc}")

    try:
        rev_data = await client.fetch_reviews(
            token, profile.gbp_account_name, profile.location_name
        )
        if rev_data["average_rating"] > 0:
            profile.average_rating = rev_data["average_rating"]
        profile.total_review_count = rev_data["total_review_count"]

        brand = await db.get(BrandProfile, profile.brand_id) if profile.brand_id else None
        for r in rev_data["reviews"]:
            existing_rev = (
                await db.execute(
                    select(GoogleBusinessReview).where(
                        GoogleBusinessReview.profile_id == profile.id,
                        GoogleBusinessReview.review_id == r["review_id"],
                    )
                )
            ).scalar_one_or_none()
            if existing_rev is None:
                existing_rev = GoogleBusinessReview(
                    profile_id=profile.id,
                    review_id=r["review_id"],
                )
                db.add(existing_rev)
            existing_rev.review_resource_name = r["review_resource_name"]
            existing_rev.reviewer_name = r["reviewer_name"]
            existing_rev.reviewer_photo_url = r["reviewer_photo_url"]
            existing_rev.star_rating = r["star_rating"]
            existing_rev.comment = r["comment"]
            existing_rev.review_created_at = _parse_iso_dt(r["review_created_at"])
            existing_rev.review_updated_at = _parse_iso_dt(r["review_updated_at"])
            existing_rev.synced_at = datetime.now(timezone.utc)

            if r["reply_comment"]:
                existing_rev.reply_comment = r["reply_comment"]
                existing_rev.reply_updated_at = _parse_iso_dt(r["reply_updated_at"])
                if existing_rev.reply_status != "AUTO_REPLIED":
                    existing_rev.reply_status = "REPLIED"
            elif existing_rev.reply_status not in ("AUTO_REPLIED", "REPLIED"):
                if auto_reply_eligible and profile.auto_reply_enabled and cfg.gemini_api_key:
                    try:
                        gen = await generate_polite_review_reply(
                            db,
                            settings=cfg,
                            profile=profile,
                            review=existing_rev,
                            brand=brand,
                        )
                        existing_rev.ai_suggested_reply = gen["reply_text"]
                        if existing_rev.star_rating >= profile.auto_reply_min_stars:
                            await client.put_review_reply(
                                token, existing_rev.review_resource_name, gen["reply_text"]
                            )
                            existing_rev.reply_comment = gen["reply_text"]
                            existing_rev.reply_updated_at = datetime.now(timezone.utc)
                            existing_rev.reply_status = "AUTO_REPLIED"
                            auto_replied += 1
                        else:
                            existing_rev.reply_status = "DRAFT_READY"
                            drafts_prepared += 1
                    except Exception as exc:
                        sync_warnings.append(f"Auto-reply skipped ({r['review_id']}): {exc}")
                elif existing_rev.ai_suggested_reply:
                    existing_rev.reply_status = "DRAFT_READY"
                else:
                    existing_rev.reply_status = "UNREPLIED"
            synced_reviews += 1
    except GoogleBusinessAPIError as exc:
        sync_warnings.append(f"Reviews: {exc}")

    profile.last_synced_at = datetime.now(timezone.utc)
    profile.last_sync_error = " | ".join(sync_warnings) if sync_warnings else ""
    await db.commit()

    return {
        "synced_days": synced_days,
        "synced_reviews": synced_reviews,
        "auto_replied": auto_replied,
        "drafts_prepared": drafts_prepared,
        "warnings": sync_warnings,
    }


def compute_gbp_analytics_metrics(
    profile: GoogleBusinessProfile,
    daily_rows: list[GoogleBusinessDailyMetric],
    reviews: list[GoogleBusinessReview],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Compute real growth rates, action conversion rate, SLA metrics, and exact transparent formulas."""
    now = now_utc or datetime.now(timezone.utc)
    end_d = now.date()
    curr_start = (end_d - timedelta(days=29)).isoformat()
    prev_start = (end_d - timedelta(days=59)).isoformat()

    curr_rows = [r for r in daily_rows if r.metric_date >= curr_start]
    prev_rows = [r for r in daily_rows if prev_start <= r.metric_date < curr_start]

    def _sum_rows(rows: list[GoogleBusinessDailyMetric]) -> dict[str, int]:
        search_desktop = sum(r.impressions_search_desktop for r in rows)
        search_mobile = sum(r.impressions_search_mobile for r in rows)
        maps_desktop = sum(r.impressions_maps_desktop for r in rows)
        maps_mobile = sum(r.impressions_maps_mobile for r in rows)
        website = sum(r.website_clicks for r in rows)
        calls = sum(r.call_clicks for r in rows)
        directions = sum(r.direction_requests for r in rows)
        impr = search_desktop + search_mobile + maps_desktop + maps_mobile
        actions = website + calls + directions
        return {
            "impressions": impr,
            "search_impressions": search_desktop + search_mobile,
            "maps_impressions": maps_desktop + maps_mobile,
            "website_clicks": website,
            "call_clicks": calls,
            "direction_requests": directions,
            "actions": actions,
        }

    curr = _sum_rows(curr_rows)
    prev = _sum_rows(prev_rows)

    impr_growth = _pct_growth(curr["impressions"], prev["impressions"])
    actions_growth = _pct_growth(curr["actions"], prev["actions"])
    calls_growth = _pct_growth(curr["call_clicks"], prev["call_clicks"])
    website_growth = _pct_growth(curr["website_clicks"], prev["website_clicks"])
    directions_growth = _pct_growth(curr["direction_requests"], prev["direction_requests"])

    conversion_rate_pct = (
        round((curr["actions"] / curr["impressions"]) * 100.0, 2)
        if curr["impressions"] > 0
        else 0.0
    )
    search_share_pct = (
        round((curr["search_impressions"] / curr["impressions"]) * 100.0, 1)
        if curr["impressions"] > 0
        else 0.0
    )
    maps_share_pct = round(100.0 - search_share_pct, 1) if curr["impressions"] > 0 else 0.0

    # Reputation & SLA math from real reviews
    rev_count = len(reviews)
    computed_avg_rating = (
        round(sum(r.star_rating for r in reviews) / rev_count, 2)
        if rev_count > 0
        else round(profile.average_rating, 2)
    )
    replied_reviews = [r for r in reviews if r.reply_comment.strip()]
    unreplied_count = rev_count - len(replied_reviews)
    response_rate_pct = (
        round((len(replied_reviews) / rev_count) * 100.0, 1) if rev_count > 0 else 0.0
    )
    positive_count = sum(1 for r in reviews if r.star_rating >= 4)
    positive_share_pct = round((positive_count / rev_count) * 100.0, 1) if rev_count > 0 else 0.0
    critical_unreplied_count = sum(
        1 for r in reviews if r.star_rating <= 3 and not r.reply_comment.strip()
    )

    reply_hours: list[float] = []
    for r in replied_reviews:
        if r.review_created_at and r.reply_updated_at:
            c_dt = (
                r.review_created_at.replace(tzinfo=timezone.utc)
                if r.review_created_at.tzinfo is None
                else r.review_created_at
            )
            rep_dt = (
                r.reply_updated_at.replace(tzinfo=timezone.utc)
                if r.reply_updated_at.tzinfo is None
                else r.reply_updated_at
            )
            diff_h = (rep_dt - c_dt).total_seconds() / 3600.0
            if diff_h >= 0:
                reply_hours.append(diff_h)
    median_reply_hours = round(float(median(reply_hours)), 1) if reply_hours else None

    recent_cutoff = now - timedelta(days=30)
    prior_cutoff = now - timedelta(days=90)
    recent_stars: list[int] = []
    prior_stars: list[int] = []
    for r in reviews:
        dt = r.review_created_at or r.review_updated_at
        if dt is None:
            continue
        aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        if aware >= recent_cutoff:
            recent_stars.append(r.star_rating)
        elif aware >= prior_cutoff:
            prior_stars.append(r.star_rating)

    rating_delta_30d = (
        round((sum(recent_stars) / len(recent_stars)) - (sum(prior_stars) / len(prior_stars)), 2)
        if (recent_stars and prior_stars)
        else None
    )

    # Dynamic Local Health Score (0-100): Completeness 25% + Rating 30% + Reply SLA 25% + Action Rate 20%
    completeness_fields = [
        bool(profile.title),
        bool(profile.primary_category),
        bool(profile.website_uri),
        bool(profile.phone_number),
        bool(profile.address_summary),
    ]
    completeness_pct = (sum(1 for f in completeness_fields if f) / len(completeness_fields)) * 100.0
    rating_pct = (computed_avg_rating / 5.0) * 100.0 if computed_avg_rating > 0 else 0.0
    # 10% action conversion rate is strong benchmark in local search (~10% -> full 100 subscore)
    action_bench_pct = min(100.0, (conversion_rate_pct / 10.0) * 100.0)
    local_health_score = round(
        0.25 * completeness_pct
        + 0.30 * rating_pct
        + 0.25 * response_rate_pct
        + 0.20 * action_bench_pct,
        1,
    )

    formulas = {
        "impressions_growth": {
            "label": "30-Day Profile Views Growth %",
            "formula": "((Current 30d Views - Previous 30d Views) / Previous 30d Views) * 100",
            "inputs": {
                "current_30d_views": curr["impressions"],
                "previous_30d_views": prev["impressions"],
            },
            "result": impr_growth,
        },
        "actions_growth": {
            "label": "30-Day Customer Actions Growth %",
            "formula": "((Current 30d [Calls + Website + Directions] - Previous 30d Actions) / Previous 30d Actions) * 100",
            "inputs": {
                "current_30d_actions": curr["actions"],
                "previous_30d_actions": prev["actions"],
            },
            "result": actions_growth,
        },
        "action_conversion_rate": {
            "label": "Customer Action Conversion Rate %",
            "formula": "((Call Clicks + Website Clicks + Direction Requests) / Total Profile Impressions) * 100",
            "inputs": {
                "total_actions_30d": curr["actions"],
                "total_impressions_30d": curr["impressions"],
            },
            "result": conversion_rate_pct,
        },
        "response_rate": {
            "label": "Review Response Rate % (SLA)",
            "formula": "(Replied Reviews / Total Synced Reviews) * 100",
            "inputs": {
                "replied_reviews": len(replied_reviews),
                "total_reviews": rev_count,
            },
            "result": response_rate_pct,
        },
        "local_health_score": {
            "label": "Local Profile Health Score (0-100)",
            "formula": "0.25*Completeness% + 0.30*(AvgRating/5*100) + 0.25*ReplyRate% + 0.20*min(100, ActionRate%/10*100)",
            "inputs": {
                "completeness_pct": round(completeness_pct, 1),
                "rating_pct": round(rating_pct, 1),
                "reply_rate_pct": response_rate_pct,
                "action_subscore_pct": round(action_bench_pct, 1),
            },
            "result": local_health_score,
        },
    }

    return {
        "kpis": {
            "impressions_30d": curr["impressions"],
            "impressions_prev_30d": prev["impressions"],
            "impressions_growth_pct": impr_growth,
            "search_impressions_30d": curr["search_impressions"],
            "maps_impressions_30d": curr["maps_impressions"],
            "search_share_pct": search_share_pct,
            "maps_share_pct": maps_share_pct,
            "actions_30d": curr["actions"],
            "actions_prev_30d": prev["actions"],
            "actions_growth_pct": actions_growth,
            "website_clicks_30d": curr["website_clicks"],
            "website_clicks_growth_pct": website_growth,
            "call_clicks_30d": curr["call_clicks"],
            "call_clicks_growth_pct": calls_growth,
            "direction_requests_30d": curr["direction_requests"],
            "direction_requests_growth_pct": directions_growth,
            "action_conversion_rate_pct": conversion_rate_pct,
            "average_rating": computed_avg_rating,
            "total_review_count": max(profile.total_review_count, rev_count),
            "synced_review_count": rev_count,
            "replied_review_count": len(replied_reviews),
            "unreplied_review_count": unreplied_count,
            "critical_unreplied_count": critical_unreplied_count,
            "response_rate_pct": response_rate_pct,
            "positive_share_pct": positive_share_pct,
            "median_reply_hours": median_reply_hours,
            "rating_delta_30d": rating_delta_30d,
            "local_health_score": local_health_score,
        },
        "formulas": formulas,
        "daily_trend": [
            {
                "date": r.metric_date,
                "impressions": r.total_impressions,
                "actions": r.total_actions,
                "website_clicks": r.website_clicks,
                "call_clicks": r.call_clicks,
                "direction_requests": r.direction_requests,
            }
            for r in sorted(curr_rows, key=lambda x: x.metric_date)
        ],
    }


async def build_gbp_overview(db: AsyncSession, profile: GoogleBusinessProfile) -> dict[str, Any]:
    """Assemble full Google Business Profile dashboard payload from live DB snapshots."""
    daily_rows = (
        await db.execute(
            select(GoogleBusinessDailyMetric)
            .where(GoogleBusinessDailyMetric.profile_id == profile.id)
            .order_by(GoogleBusinessDailyMetric.metric_date.asc())
        )
    ).scalars().all()
    reviews = (
        await db.execute(
            select(GoogleBusinessReview)
            .where(GoogleBusinessReview.profile_id == profile.id)
            .order_by(
                GoogleBusinessReview.review_updated_at.desc(),
                GoogleBusinessReview.synced_at.desc(),
            )
            .limit(50)
        )
    ).scalars().all()

    metrics_bundle = compute_gbp_analytics_metrics(profile, daily_rows, reviews)
    return {
        "profile": {
            "id": profile.id,
            "brand_id": profile.brand_id,
            "google_email": profile.google_email,
            "gbp_account_name": profile.gbp_account_name,
            "location_name": profile.location_name,
            "location_id": profile.location_id,
            "title": profile.title,
            "store_code": profile.store_code,
            "primary_category": profile.primary_category,
            "website_uri": profile.website_uri,
            "phone_number": profile.phone_number,
            "address_summary": profile.address_summary,
            "timezone": profile.timezone,
            "auto_reply_enabled": profile.auto_reply_enabled,
            "auto_reply_min_stars": profile.auto_reply_min_stars,
            "last_synced_at": profile.last_synced_at.isoformat() if profile.last_synced_at else None,
            "last_sync_error": profile.last_sync_error,
        },
        **metrics_bundle,
        "reviews": [
            {
                "id": r.id,
                "review_id": r.review_id,
                "reviewer_name": r.reviewer_name,
                "reviewer_photo_url": r.reviewer_photo_url,
                "star_rating": r.star_rating,
                "comment": r.comment,
                "review_created_at": r.review_created_at.isoformat() if r.review_created_at else None,
                "review_updated_at": r.review_updated_at.isoformat() if r.review_updated_at else None,
                "reply_comment": r.reply_comment,
                "reply_updated_at": r.reply_updated_at.isoformat() if r.reply_updated_at else None,
                "ai_suggested_reply": r.ai_suggested_reply,
                "reply_status": r.reply_status,
            }
            for r in reviews
        ],
    }


async def sync_all_active_gbp_profiles(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    gbp_client: GoogleBusinessClient | None = None,
    cipher: TokenCipher | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Periodic worker task to refresh Google Business metrics, reviews, and auto-replies."""
    cfg = settings or get_settings()
    client = gbp_client or GoogleBusinessClient(cfg)
    tok_cipher = cipher or get_token_cipher()
    async with session_factory() as db:
        profiles = (
            await db.execute(
                select(GoogleBusinessProfile).where(GoogleBusinessProfile.is_active.is_(True))
            )
        ).scalars().all()

    synced = 0
    auto_replied = 0
    errors: list[str] = []
    for p in profiles:
        async with session_factory() as db:
            fresh = await db.get(GoogleBusinessProfile, p.id)
            if fresh is None:
                continue
            try:
                res = await sync_gbp_profile(
                    db, fresh, client, tok_cipher, settings=cfg, auto_reply_eligible=True
                )
                synced += 1
                auto_replied += int(res.get("auto_replied", 0))
            except Exception as exc:
                errors.append(f"{fresh.title}: {exc}")
    return {"synced_profiles": synced, "auto_replied": auto_replied, "errors": errors}
