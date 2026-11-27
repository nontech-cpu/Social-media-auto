"""Tests for dynamic Best-Time heatmap scoring and smart multi-post spacing."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.entities import (
    AudienceSnapshot,
    BrandProfile,
    InstagramAccount,
    MediaItem,
    ScheduledPost,
)
from app.services.analytics_engine import recompute_best_time_slots
from app.services.queue_planner import recommend_slots_for_batch


@pytest.mark.asyncio
async def test_best_time_heatmap_ranks_real_peak_window(db_session, cipher) -> None:
    account = InstagramAccount(
        ig_user_id="ig_1",
        username="creator_test",
        followers_count=5000,
        timezone="Asia/Kolkata",
        encrypted_access_token=cipher.encrypt("tok"),
    )
    db_session.add(account)
    await db_session.flush()

    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    # Seed 4 high-reach posts on Thursday 19:00 IST and 4 low-reach posts on Monday 03:00 IST
    thursday_19 = (now_ist - timedelta(days=7)).replace(hour=19, minute=10, second=0, microsecond=0)
    while thursday_19.weekday() != 3:
        thursday_19 -= timedelta(days=1)

    monday_03 = (now_ist - timedelta(days=6)).replace(hour=3, minute=15, second=0, microsecond=0)
    while monday_03.weekday() != 0:
        monday_03 -= timedelta(days=1)

    for i in range(4):
        db_session.add(
            MediaItem(
                account_id=account.id,
                ig_media_id=f"high_{i}",
                media_type="REELS",
                media_product_type="REELS",
                published_at=(thursday_19 - timedelta(days=7 * i)).astimezone(timezone.utc),
                reach=25000,
                likes=1800,
                comments=140,
                shares=300,
                saves=450,
                engagement_rate=10.76,
            )
        )
        db_session.add(
            MediaItem(
                account_id=account.id,
                ig_media_id=f"low_{i}",
                media_type="IMAGE",
                media_product_type="FEED",
                published_at=(monday_03 - timedelta(days=7 * i)).astimezone(timezone.utc),
                reach=1200,
                likes=40,
                comments=2,
                shares=1,
                saves=3,
                engagement_rate=3.83,
            )
        )

    db_session.add(
        AudienceSnapshot(
            account_id=account.id,
            online_followers_hourly_json={"3": 120, "19": 2800, "20": 2400},
            follower_count=5000,
        )
    )
    await db_session.flush()

    slots = await recompute_best_time_slots(db_session, account)
    assert len(slots) == 168

    slot_lookup = {(s.day_of_week, s.hour_of_day): s for s in slots}
    peak_slot = slot_lookup[(3, 19)]
    off_slot = slot_lookup[(0, 3)]
    assert peak_slot.combined_score > off_slot.combined_score
    assert peak_slot.sample_size == 4
    assert peak_slot.confidence > 0.7


@pytest.mark.asyncio
async def test_best_time_zero_history_reports_zero_confidence(db_session, cipher) -> None:
    account = InstagramAccount(
        ig_user_id="ig_empty",
        username="new_account",
        followers_count=50,
        timezone="UTC",
        encrypted_access_token=cipher.encrypt("tok"),
    )
    db_session.add(account)
    await db_session.flush()

    slots = await recompute_best_time_slots(db_session, account)
    assert len(slots) == 168
    assert all(s.confidence == 0.0 and s.combined_score == 0.0 for s in slots)


@pytest.mark.asyncio
async def test_smart_batch_planner_enforces_spacing_and_avoids_conflicts(
    db_session, cipher
) -> None:
    brand = BrandProfile(brand_name="TestBrand", posting_goal_per_week=4)
    db_session.add(brand)
    await db_session.flush()

    account = InstagramAccount(
        brand_id=brand.id,
        ig_user_id="ig_queue",
        username="queue_tester",
        followers_count=4000,
        timezone="Asia/Kolkata",
        encrypted_access_token=cipher.encrypt("tok"),
    )
    db_session.add(account)
    await db_session.flush()

    await recompute_best_time_slots(db_session, account)

    existing_time = datetime.now(timezone.utc) + timedelta(hours=4)
    db_session.add(
        ScheduledPost(
            account_id=account.id,
            media_type="REELS",
            media_urls_json=["https://cdn.example.com/v0.mp4"],
            caption="Existing post",
            scheduled_for=existing_time,
            status="QUEUED",
        )
    )
    await db_session.commit()

    recs = await recommend_slots_for_batch(
        db_session, account, brand, count=3, min_gap_hours=24
    )
    assert len(recs) == 3

    for rec in recs:
        assert abs((rec.scheduled_for_utc - existing_time).total_seconds()) >= 24 * 3600

    for i in range(len(recs) - 1):
        diff_hours = (recs[i + 1].scheduled_for_utc - recs[i].scheduled_for_utc).total_seconds() / 3600.0
        assert diff_hours >= 24.0
