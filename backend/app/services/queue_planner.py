"""Smart Multi-Post Queue & Optimal Spacing Planner powered by the 7-stage engine."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import BrandProfile, InstagramAccount, ScheduledPost
from app.services.recommendation_engine import build_account_recommendations


@dataclass
class SlotRecommendation:
    scheduled_for_utc: datetime
    local_display: str
    combined_score: float
    confidence: float
    rationale: str
    slot_type: str = "exploration"
    lift_vs_baseline: float = 0.0


def _safe_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


async def recommend_slots_for_batch(
    db: AsyncSession,
    account: InstagramAccount,
    brand: BrandProfile | None,
    *,
    count: int = 1,
    earliest_start: datetime | None = None,
    min_gap_hours: int | None = None,
    horizon_days: int = 21,
    media_type: str | None = None,
) -> list[SlotRecommendation]:
    """Select the N highest-reach conflict-free future slots via the 7-stage NMS engine."""
    rec = await build_account_recommendations(
        db,
        account,
        brand,
        content_format_filter=media_type,
        schedule_count=count,
        earliest_start=earliest_start,
        min_gap_hours_override=min_gap_hours,
    )
    return [
        SlotRecommendation(
            scheduled_for_utc=s.datetime_utc,
            local_display=s.local_display,
            combined_score=s.display_score_100,
            confidence=min(1.0, s.n_eff / 4.0),
            rationale=s.reason,
            slot_type=s.slot_type,
            lift_vs_baseline=s.lift_vs_baseline,
        )
        for s in rec.weekly_schedule
    ]


def summarize_existing_queue(
    posts: list[ScheduledPost], timezone_name: str
) -> list[dict[str, Any]]:
    tz = _safe_tz(timezone_name)
    items = []
    for p in posts:
        ts = p.scheduled_for
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local_ts = ts.astimezone(tz)
        items.append(
            {
                "id": p.id,
                "media_type": p.media_type,
                "status": p.status,
                "scheduled_for": ts.isoformat(),
                "local_display": local_ts.strftime("%a, %b %d %I:%M %p"),
                "caption_preview": p.caption[:100],
            }
        )
    return items
