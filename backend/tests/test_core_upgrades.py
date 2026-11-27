"""Unit and integration tests for hashtag rankability, lock reaper, token refresh, media specs, and Content DNA."""

from datetime import datetime, timedelta, timezone
import struct

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.entities import (
    CopilotMessage,
    HashtagCache,
    InstagramAccount,
    MediaItem,
    ScheduledPost,
)
from app.services.analytics_engine import (
    _compute_period_trend,
    _weighted_engagement_rate,
    compute_content_dna,
)
from app.services.hashtag_engine import (
    HashtagCandidate,
    HashtagEngine,
    classify_rankability,
)
from app.services.media_storage import (
    _extract_image_dimensions,
    validate_instagram_media_specs,
)
from app.services.meta_client import MetaGraphClient
from app.workers.publisher import (
    recover_stale_publishing_locks,
    refresh_expiring_account_token,
)


def _make_png_bytes(width: int, height: int) -> bytes:
    """Build minimal valid PNG header bytes with IHDR dimensions."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_len = struct.pack(">I", 13)
    ihdr_type = b"IHDR"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return sig + ihdr_len + ihdr_type + ihdr_data + b"\x00\x00\x00\x00"


def _make_jpeg_bytes(width: int, height: int) -> bytes:
    """Build minimal JPEG header bytes containing a SOF0 segment."""
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0\x00\x06JFIF"
    sof0_payload = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00" * 3
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    eoi = b"\xff\xd9"
    return soi + app0 + sof0 + eoi


def test_extract_image_dimensions_and_instagram_spec_validation(test_settings) -> None:
    png_w, png_h = _extract_image_dimensions(_make_png_bytes(1080, 1350))
    assert (png_w, png_h) == (1080, 1350)

    jpg_w, jpg_h = _extract_image_dimensions(_make_jpeg_bytes(1080, 1080))
    assert (jpg_w, jpg_h) == (1080, 1080)

    # Ideal 4:5 portrait image triggers zero warnings
    assert (
        validate_instagram_media_specs(
            media_type="IMAGE",
            size_bytes=500_000,
            width=1080,
            height=1350,
            duration_seconds=None,
            settings=test_settings,
        )
        == []
    )

    # Ultra-wide 3:1 image triggers aspect ratio warning
    wide_warnings = validate_instagram_media_specs(
        media_type="IMAGE",
        size_bytes=500_000,
        width=1800,
        height=600,
        duration_seconds=None,
        settings=test_settings,
    )
    assert len(wide_warnings) == 1
    assert "aspect ratio" in wide_warnings[0].lower()

    # Oversized image raises ValueError before enqueueing
    with pytest.raises(ValueError, match="Image size"):
        validate_instagram_media_specs(
            media_type="IMAGE",
            size_bytes=test_settings.media_max_image_bytes + 1024,
            width=1080,
            height=1080,
            duration_seconds=None,
            settings=test_settings,
        )

    # Reel shorter than 3s raises ValueError
    with pytest.raises(ValueError, match="Reel duration"):
        validate_instagram_media_specs(
            media_type="REELS",
            size_bytes=1_000_000,
            width=1080,
            height=1920,
            duration_seconds=1.5,
            settings=test_settings,
        )

    # Reel longer than 15m raises ValueError
    with pytest.raises(ValueError, match="Reel duration"):
        validate_instagram_media_specs(
            media_type="REELS",
            size_bytes=10_000_000,
            width=1080,
            height=1920,
            duration_seconds=950.0,
            settings=test_settings,
        )


def test_classify_rankability_bands() -> None:
    ratio_win, label_win = classify_rankability(100.0, 200.0)
    assert ratio_win == 0.5
    assert label_win == "winnable"

    ratio_stretch, label_stretch = classify_rankability(20.0, 100.0)
    assert ratio_stretch == 0.2
    assert label_stretch == "stretch"

    ratio_sat, label_sat = classify_rankability(10.0, 1000.0)
    assert ratio_sat == 0.01
    assert label_sat == "saturated"

    assert classify_rankability(50.0, None) == (None, "unverified")


@pytest.mark.asyncio
async def test_hashtag_engine_bayesian_lift_and_rankability(
    db_session, test_settings, cipher
) -> None:
    account = InstagramAccount(
        ig_user_id="178414009999",
        username="creator_pro",
        timezone="UTC",
        encrypted_access_token=cipher.encrypt("token_abc"),
    )
    db_session.add(account)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    posts = [
        MediaItem(
            account_id=account.id,
            ig_media_id="m1",
            media_type="REELS",
            caption="Video #winnable",
            hashtags_json=["#winnable"],
            published_at=now - timedelta(days=3),
            reach=2000,
            likes=180,
            comments=20,
            saves=30,
            shares=15,
            engagement_rate=12.0,
        ),
        MediaItem(
            account_id=account.id,
            ig_media_id="m2",
            media_type="REELS",
            caption="Post #average",
            hashtags_json=["#average"],
            published_at=now - timedelta(days=6),
            reach=600,
            likes=40,
            comments=5,
            saves=5,
            shares=2,
            engagement_rate=8.0,
        ),
    ]
    db_session.add_all(posts)

    # Pre-seed HashtagCache so enrich_and_rank_candidates uses cached Meta p25 benchmarks without live HTTP
    db_session.add_all(
        [
            HashtagCache(
                account_id=account.id,
                hashtag_name="winnable",
                ig_hashtag_id="h1",
                top_media_avg_engagement=150.0,
                top_media_sample_count=10,
                last_queried_at=now - timedelta(hours=2),
                expires_at=now + timedelta(days=3),
            ),
            HashtagCache(
                account_id=account.id,
                hashtag_name="saturated",
                ig_hashtag_id="h2",
                top_media_avg_engagement=50000.0,
                top_media_sample_count=10,
                last_queried_at=now - timedelta(hours=2),
                expires_at=now + timedelta(days=3),
            ),
        ]
    )
    await db_session.commit()

    engine = HashtagEngine(settings=test_settings, meta_client=MetaGraphClient(test_settings), cipher=cipher)
    ranked = await engine.enrich_and_rank_candidates(
        db_session,
        account,
        [
            HashtagCandidate(hashtag="#saturated", tier="trending", why_it_boosts_reach="High volume"),
            HashtagCandidate(hashtag="#winnable", tier="niche", why_it_boosts_reach="Strong niche fit"),
        ],
        desired_count=10,
    )

    assert len(ranked["hashtags"]) == 2
    assert ranked["hashtags"][0]["hashtag"] == "#winnable"
    assert ranked["hashtags"][0]["rankability"] == "winnable"
    assert ranked["hashtags"][0]["account_avg_reach"] is not None
    assert ranked["hashtags"][0]["historical_lift"] is not None
    assert ranked["hashtags"][1]["hashtag"] == "#saturated"
    assert ranked["hashtags"][1]["rankability"] == "saturated"


@pytest.mark.asyncio
async def test_recover_stale_publishing_locks_requeues_and_fails(
    db_session_factory, test_settings, cipher
) -> None:
    stale_time = datetime.now(timezone.utc) - timedelta(
        minutes=test_settings.stale_publish_lock_minutes + 5
    )
    async with db_session_factory() as db:
        account = InstagramAccount(
            ig_user_id="178414008888",
            username="reaper_acct",
            timezone="UTC",
            encrypted_access_token=cipher.encrypt("tok"),
        )
        db.add(account)
        await db.flush()

        recoverable = ScheduledPost(
            account_id=account.id,
            media_type="IMAGE",
            media_urls_json=["https://res.cloudinary.com/demo/image/upload/sample.jpg"],
            caption="Stuck once",
            hashtags_json=["#test"],
            scheduled_for=stale_time,
            updated_at=stale_time,
            status="PUBLISHING",
            retry_count=0,
        )
        exhausted = ScheduledPost(
            account_id=account.id,
            media_type="IMAGE",
            media_urls_json=["https://res.cloudinary.com/demo/image/upload/sample2.jpg"],
            caption="Stuck forever",
            hashtags_json=["#test"],
            scheduled_for=stale_time,
            updated_at=stale_time,
            status="PUBLISHING",
            retry_count=test_settings.max_publish_retries,
        )
        db.add_all([recoverable, exhausted])
        await db.commit()
        recoverable_id = recoverable.id
        exhausted_id = exhausted.id
        account_id = account.id

    async with db_session_factory() as db:
        reaped = await recover_stale_publishing_locks(db, test_settings)
        assert reaped == 2

    async with db_session_factory() as db:
        rec = await db.get(ScheduledPost, recoverable_id)
        exh = await db.get(ScheduledPost, exhausted_id)
        msgs = (
            await db.execute(
                select(CopilotMessage).where(CopilotMessage.account_id == account_id)
            )
        ).scalars().all()
        assert rec is not None and rec.status == "QUEUED"
        assert rec.retry_count == 1
        assert exh is not None and exh.status == "FAILED"
        assert len(msgs) == 1
        assert "failed" in msgs[0].content.lower() or "stale" in msgs[0].content.lower()


@pytest.mark.asyncio
async def test_refresh_expiring_account_token_updates_encrypted_token(
    db_session, test_settings, cipher
) -> None:
    account = InstagramAccount(
        ig_user_id="178414007777",
        username="token_acct",
        timezone="UTC",
        encrypted_access_token=cipher.encrypt("old_token_val"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    db_session.add(account)
    await db_session.commit()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://graph.facebook.com/v21.0/oauth/access_token").mock(
            return_value=httpx.Response(
                200, json={"access_token": "fresh_60d_token", "expires_in": 5184000}
            )
        )
        refreshed = await refresh_expiring_account_token(
            db_session,
            account,
            meta_client=MetaGraphClient(test_settings),
            cipher=cipher,
            settings=test_settings,
        )
        assert refreshed is True
        assert cipher.decrypt(account.encrypted_access_token) == "fresh_60d_token"
        assert account.token_expires_at is not None


def test_compute_content_dna_segments_caption_hooks_and_hashtags() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        MediaItem(
            account_id="a1",
            ig_media_id="1",
            media_type="REELS",
            caption=(
                "Why do 90% of creators quit before seeing compounding growth?\n"
                "Here is the full 3-step retention breakdown that keeps viewers watching."
            ),
            hashtags_json=["#a", "#b", "#c", "#d", "#e", "#f", "#g"],
            published_at=now,
            reach=3000,
            likes=200,
            comments=30,
            saves=90,
            shares=60,
            engagement_rate=12.6,
        ),
        MediaItem(
            account_id="a1",
            ig_media_id="2",
            media_type="IMAGE",
            caption="Short update.",
            hashtags_json=["#x", "#y"],
            published_at=now,
            reach=400,
            likes=20,
            comments=2,
            saves=3,
            shares=1,
            engagement_rate=6.5,
        ),
    ]
    dna = compute_content_dna(rows)
    assert dna["has_data"] is True
    assert dna["best_caption_length_bucket"] == "medium_80_220"
    assert dna["best_hashtag_count_bucket"] == "6_to_12"
    assert dna["question_hook_avg_reach"] == 3000.0
    assert dna["statement_hook_avg_reach"] == 400.0


def test_weighted_er_and_period_trend_prevent_small_reach_distortion() -> None:
    now = datetime.now(timezone.utc)
    # 1 tiny-reach post (reach=10, 5 likes => 50% ER) + 3 large posts (reach=10000, 300 likes => 3% ER)
    rows = [
        MediaItem(
            account_id="a1",
            ig_media_id="tiny",
            media_type="IMAGE",
            published_at=now - timedelta(days=1),
            reach=10,
            likes=5,
            comments=0,
            saves=0,
            shares=0,
            engagement_rate=50.0,
        ),
        MediaItem(
            account_id="a1",
            ig_media_id="big1",
            media_type="REELS",
            published_at=now - timedelta(days=2),
            reach=20000,
            likes=500,
            comments=40,
            saves=60,
            shares=40,
            engagement_rate=3.2,
        ),
        MediaItem(
            account_id="a1",
            ig_media_id="big2",
            media_type="REELS",
            published_at=now - timedelta(days=10),
            reach=5000,
            likes=100,
            comments=5,
            saves=10,
            shares=5,
            engagement_rate=2.4,
        ),
        MediaItem(
            account_id="a1",
            ig_media_id="big3",
            media_type="IMAGE",
            published_at=now - timedelta(days=15),
            reach=5000,
            likes=100,
            comments=5,
            saves=5,
            shares=5,
            engagement_rate=2.3,
        ),
    ]
    # Exposure-weighted ER should stay near 2.9%, NOT unweighted mean ~14.5%
    weighted_er = _weighted_engagement_rate(rows, followers_fallback=100)
    assert 2.5 <= weighted_er <= 3.5
    trend = _compute_period_trend(rows, followers_fallback=100)
    assert trend["reach_trend_pct"] is not None and trend["reach_trend_pct"] > 0


@pytest.mark.asyncio
async def test_fetch_media_insights_fallback_preserves_shares(test_settings) -> None:
    client = MetaGraphClient(test_settings)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://graph.facebook.com/v21.0/media_123/insights")
        route.side_effect = [
            httpx.Response(400, json={"error": {"message": "views unsupported"}}),
            httpx.Response(400, json={"error": {"message": "total_interactions unsupported"}}),
            httpx.Response(
                200,
                json={
                    "data": [
                        {"name": "reach", "values": [{"value": 1200}]},
                        {"name": "saved", "values": [{"value": 18}]},
                        {"name": "shares", "values": [{"value": 11}]},
                    ]
                },
            ),
        ]
        metrics = await client.fetch_media_insights("media_123", "FEED", "tok")
        assert metrics == {"reach": 1200, "saved": 18, "shares": 11}

