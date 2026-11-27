"""Integration tests with respx mocking Meta Graph API publishing, OAuth, and hashtag quota."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.models.entities import HashtagCache, InstagramAccount, ScheduledPost
from app.services.hashtag_engine import META_HASHTAG_WEEKLY_QUOTA, HashtagEngine
from app.services.meta_client import MetaGraphClient
from app.workers.publisher import process_due_posts


@pytest.mark.asyncio
async def test_meta_2step_reel_publish_and_atomic_claim(
    db_session_factory, test_settings, cipher
) -> None:
    async with db_session_factory() as db:
        account = InstagramAccount(
            ig_user_id="178414000001",
            username="brand_official",
            timezone="UTC",
            encrypted_access_token=cipher.encrypt("long_lived_token_123"),
        )
        db.add(account)
        await db.flush()

        post = ScheduledPost(
            account_id=account.id,
            media_type="REELS",
            media_urls_json=["https://res.cloudinary.com/demo/video/upload/sample.mp4"],
            caption="3 AI tools that save 5 hours/week",
            hashtags_json=["#aitools", "#reels"],
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
            status="QUEUED",
        )
        db.add(post)
        await db.commit()
        post_id = post.id

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://graph.facebook.com/v21.0/178414000001/media").mock(
            return_value=httpx.Response(200, json={"id": "container_777"})
        )
        mock.get("https://graph.facebook.com/v21.0/container_777").mock(
            side_effect=[
                httpx.Response(200, json={"status_code": "IN_PROGRESS"}),
                httpx.Response(200, json={"status_code": "FINISHED"}),
            ]
        )
        mock.post("https://graph.facebook.com/v21.0/178414000001/media_publish").mock(
            return_value=httpx.Response(200, json={"id": "published_media_999"})
        )
        mock.get("https://graph.facebook.com/v21.0/published_media_999").mock(
            return_value=httpx.Response(
                200, json={"permalink": "https://www.instagram.com/reel/XYZ999/"}
            )
        )

        meta_client = MetaGraphClient(test_settings)
        # Speed up polling in tests
        orig_poll = meta_client.poll_container_until_ready

        async def fast_poll(cid: str, tok: str, timeout_seconds: int = 180):
            return await orig_poll(cid, tok, timeout_seconds=10, poll_interval_seconds=0.01)

        meta_client.poll_container_until_ready = fast_poll  # type: ignore[method-assign]

        result = await process_due_posts(
            db_session_factory,
            meta_client=meta_client,
            cipher=cipher,
            settings=test_settings,
        )
        assert result == {"recovered_stale": 0, "checked_due": 1, "published": 1, "failed": 0}

    async with db_session_factory() as db:
        saved = await db.get(ScheduledPost, post_id)
        assert saved is not None
        assert saved.status == "PUBLISHED"
        assert saved.ig_container_id == "container_777"
        assert saved.ig_media_id == "published_media_999"
        assert saved.published_permalink == "https://www.instagram.com/reel/XYZ999/"


@pytest.mark.asyncio
async def test_localhost_media_url_fails_cleanly_before_meta_call(
    db_session_factory, test_settings, cipher
) -> None:
    async with db_session_factory() as db:
        account = InstagramAccount(
            ig_user_id="178414000002",
            username="local_tester",
            timezone="UTC",
            encrypted_access_token=cipher.encrypt("tok"),
        )
        db.add(account)
        await db.flush()
        post = ScheduledPost(
            account_id=account.id,
            media_type="IMAGE",
            media_urls_json=["http://localhost:8000/uploads/pic.jpg"],
            caption="Test",
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
            status="QUEUED",
        )
        db.add(post)
        await db.commit()
        post_id = post.id

    result = await process_due_posts(
        db_session_factory,
        meta_client=MetaGraphClient(test_settings),
        cipher=cipher,
        settings=test_settings,
    )
    assert result["failed"] == 1
    async with db_session_factory() as db:
        saved = await db.get(ScheduledPost, post_id)
        assert saved is not None
        assert saved.status == "FAILED"
        assert "public HTTPS" in saved.last_error


@pytest.mark.asyncio
async def test_hashtag_engine_respects_30_weekly_quota(
    db_session, test_settings, cipher
) -> None:
    account = InstagramAccount(
        ig_user_id="178414000003",
        username="tag_tester",
        timezone="UTC",
        encrypted_access_token=cipher.encrypt("tok"),
    )
    db_session.add(account)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    # Fill 29 out of 30 weekly quota slots
    for idx in range(META_HASHTAG_WEEKLY_QUOTA - 1):
        db_session.add(
            HashtagCache(
                account_id=account.id,
                hashtag_name=f"existingtag{idx}",
                ig_hashtag_id=f"ht_{idx}",
                top_media_avg_engagement=50.0,
                last_queried_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=6),
            )
        )
    await db_session.flush()

    with respx.mock() as mock:
        mock.get("https://graph.facebook.com/v21.0/ig_hashtag_search").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "ht_new_30"}]})
        )
        mock.get("https://graph.facebook.com/v21.0/ht_new_30/top_media").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "m1", "like_count": 200, "comments_count": 20, "caption": "Nice"}
                    ]
                },
            )
        )
        engine = HashtagEngine(
            settings=test_settings,
            meta_client=MetaGraphClient(test_settings),
            cipher=cipher,
        )
        # Request 3 new hashtags — only 1 slot remains in the 30/week quota!
        res = await engine.validate_hashtags_via_meta(
            db_session,
            account,
            ["#existingtag0", "#newslot30", "#blocked31"],
            max_live_lookups=4,
        )
        assert "#existingtag0" in res and res["#existingtag0"]["source"] == "meta_cache"
        assert "#newslot30" in res and res["#newslot30"]["source"] == "meta_live"
        assert "#blocked31" not in res
