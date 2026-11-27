"""Unit tests covering every stage of the 7-stage zero-hardcode recommendation engine."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.recommendation_engine import (
    PostTelemetry,
    RecommendationConfig,
    _expand_online_followers,
    run_7stage_recommendation,
)


def _cell(heatmap: list[dict], dow: int, hour: int) -> dict:
    for item in heatmap:
        if item["day_of_week"] == dow and item["hour_of_day"] == hour:
            return item
    raise KeyError((dow, hour))


def test_stage1_circular_kernel_shares_signal_with_adjacent_hours() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    pub_19 = datetime(2026, 11, 20, 19, 55, tzinfo=ist).astimezone(timezone.utc)

    posts = [
        PostTelemetry(
            post_id="p1",
            published_at_utc=pub_19,
            content_format="REELS",
            reach=5000,
            impressions=6000,
            likes=300,
            comments=25,
            shares=40,
            saves=60,
        )
    ]
    res = run_7stage_recommendation(
        posts=posts,
        timezone_name="Asia/Kolkata",
        now_utc=now,
        config=RecommendationConfig(),
    )
    dow = pub_19.astimezone(ist).weekday()
    c_19 = _cell(res.heatmap, dow, 19)
    c_20 = _cell(res.heatmap, dow, 20)
    c_18 = _cell(res.heatmap, dow, 18)
    assert c_19["sample_size"] == 1
    assert c_18["n_eff"] > 0.0 and c_20["n_eff"] > 0.0
    assert c_19["n_eff"] > c_20["n_eff"]


def test_stage3_format_median_debiasing_prevents_format_bias() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    posts = []
    # 4 Reels at 10 AM: typical Reel gets 1000 eng/10000 reach (10%), slot at 10 AM only gets 8% (0.8x format median)
    for i in range(4):
        hour = 10 if i == 0 else 14
        likes = 400 if i == 0 else 1000
        posts.append(
            PostTelemetry(
                post_id=f"reel_{i}",
                published_at_utc=datetime(2026, 11, 10 + i, hour, 0, tzinfo=ist).astimezone(timezone.utc),
                content_format="REELS",
                reach=10000,
                impressions=12000,
                likes=likes,
                comments=20,
                shares=30,
                saves=30,
            )
        )
    # 4 Images at 20 PM vs 16 PM: typical Image gets 2% rate, 20 PM gets 4% (2.0x format median!)
    for i in range(4):
        hour = 20 if i < 2 else 16
        likes = 400 if i < 2 else 150
        posts.append(
            PostTelemetry(
                post_id=f"img_{i}",
                published_at_utc=datetime(2026, 11, 15 + i, hour, 0, tzinfo=ist).astimezone(timezone.utc),
                content_format="IMAGE",
                reach=10000,
                impressions=11000,
                likes=likes,
                comments=5,
                shares=5,
                saves=5,
            )
        )

    res = run_7stage_recommendation(
        posts=posts,
        timezone_name="Asia/Kolkata",
        now_utc=now,
        config=RecommendationConfig(),
    )
    # Image 20:00 (Sun/Mon, ~1.38x format lift) must outrank Reel 10:00 (Tue, ~0.43x format lift)
    sun_20 = _cell(res.heatmap, 6, 20)
    tue_10 = _cell(res.heatmap, 1, 10)
    assert sun_20["posterior_score"] > tue_10["posterior_score"]
    assert any(s.hour_of_day == 20 for s in res.weekly_schedule)


def test_stage4_mad_outlier_clipping_tames_single_viral_fluke() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    posts = []
    # Consistent strong slot: Monday 19:00 (4 posts at ~15% weighted score)
    for i in range(4):
        posts.append(
            PostTelemetry(
                post_id=f"steady_{i}",
                published_at_utc=datetime(2026, 11, 2 + 7 * i, 19, 0, tzinfo=ist).astimezone(timezone.utc),
                content_format="REELS",
                reach=10000,
                impressions=12000,
                likes=900,
                comments=60,
                shares=120,
                saves=120,
            )
        )
    # Baseline posts at 12:00 (~5% weighted score)
    for i in range(4):
        posts.append(
            PostTelemetry(
                post_id=f"base_{i}",
                published_at_utc=datetime(2026, 11, 3 + 7 * i, 12, 0, tzinfo=ist).astimezone(timezone.utc),
                content_format="REELS",
                reach=10000,
                impressions=11000,
                likes=300,
                comments=10,
                shares=20,
                saves=20,
            )
        )
    # 1 extreme outlier spike at Wednesday 03:00 (100x normal)
    posts.append(
        PostTelemetry(
            post_id="fluke",
            published_at_utc=datetime(2026, 11, 18, 3, 0, tzinfo=ist).astimezone(timezone.utc),
            content_format="REELS",
            reach=10000,
            impressions=15000,
            likes=9000,
            comments=2000,
            shares=5000,
            saves=5000,
        )
    )

    res = run_7stage_recommendation(
        posts=posts,
        timezone_name="Asia/Kolkata",
        now_utc=now,
        config=RecommendationConfig(),
    )
    mon_19 = _cell(res.heatmap, 0, 19)
    wed_03 = _cell(res.heatmap, 2, 3)
    # Bayesian smoothing + MAD cap keeps consistent Monday 19:00 competitive/proven vs single fluke
    assert mon_19["slot_type"] == "proven"
    assert wed_03["slot_type"] != "proven"
    assert mon_19["posterior_score"] > wed_03["posterior_score"]


def test_stage5_and_6_cold_start_uses_prepeak_online_prior_and_learning_tier() -> None:
    now = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)
    # Audience online peaks sharply at 20:00 -> pre-peak shift boosts 19:00!
    online_map = {str(h): (5000 if h == 20 else 100) for h in range(24)}
    online_grid = _expand_online_followers(online_map)

    res = run_7stage_recommendation(
        posts=[],
        timezone_name="UTC",
        target_posts_per_week=3,
        now_utc=now,
        follower_online_grid=online_grid,
        config=RecommendationConfig(),
    )
    assert res.confidence_tier == "learning"
    assert res.prior_source == "follower_online_prepeak"
    assert res.next_recommended_slot is not None
    assert res.next_recommended_slot.hour_of_day == 19
    assert res.next_recommended_slot.slot_type == "benchmark"


def test_stage7_greedy_nms_respects_last_published_cooldown_and_avoid_windows() -> None:
    now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    last_pub = now - timedelta(hours=2)  # published 2h ago -> must respect min_gap_hours!
    res = run_7stage_recommendation(
        posts=[],
        timezone_name="Asia/Kolkata",
        target_posts_per_week=4,
        now_utc=now,
        last_published_at_utc=last_pub,
        config=RecommendationConfig(platform_min_gap_hours=12),
    )
    assert res.min_gap_hours >= 12
    assert res.next_recommended_slot is not None
    gap_from_last = (res.next_recommended_slot.datetime_utc - last_pub).total_seconds() / 3600.0
    assert gap_from_last >= res.min_gap_hours
    assert len(res.weekly_schedule) == 4
    for i in range(len(res.weekly_schedule) - 1):
        delta_h = (
            res.weekly_schedule[i + 1].datetime_utc - res.weekly_schedule[i].datetime_utc
        ).total_seconds() / 3600.0
        assert delta_h >= res.min_gap_hours
    assert len(res.avoid_windows) == 5
