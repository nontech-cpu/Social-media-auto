"""FastAPI route smoke tests using ASGITransport."""

import httpx
import pytest

from app.core.security import TokenCipher
from app.main import app


@pytest.mark.asyncio
async def test_health_and_brand_routes_smoke(db_session_factory) -> None:
    from app.core.database import get_db

    # Verify non-Fernet strings (e.g. Render generateValue) safely derive a valid cipher
    derived_cipher = TokenCipher(key="render-generated-random-secret-string")
    assert derived_cipher.decrypt(derived_cipher.encrypt("token-ok")) == "token-ok"

    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            health_head = await client.head("/api/health")
            assert health_head.status_code == 200

            save_res = await client.put(
                "/api/onboarding/brand",
                json={
                    "brand_name": "ShapeBytes AI",
                    "niche": "AI SaaS Tools",
                    "posting_goal_per_week": 5,
                },
            )
            assert save_res.status_code == 200
            assert save_res.json()["brand"]["brand_name"] == "ShapeBytes AI"

            get_res = await client.get("/api/onboarding/brand")
            assert get_res.status_code == 200
            assert get_res.json()["brand"]["niche"] == "AI SaaS Tools"

            from datetime import datetime, timezone
            from app.models.entities import AIUsageLog, InstagramAccount, ScheduledPost

            async with db_session_factory() as db:
                acc = InstagramAccount(
                    ig_user_id="ig_act_1",
                    username="act_tester",
                    timezone="UTC",
                    encrypted_access_token=derived_cipher.encrypt("tok"),
                )
                db.add(acc)
                await db.flush()
                db.add(
                    ScheduledPost(
                        account_id=acc.id,
                        media_type="STORIES",
                        media_urls_json=["https://res.cloudinary.com/demo/image/upload/st.jpg"],
                        caption="Story CTA",
                        hashtags_json=[],
                        scheduled_for=datetime.now(timezone.utc),
                        published_at=datetime.now(timezone.utc),
                        published_permalink="https://instagram.com/stories/act_tester/1/",
                        status="PUBLISHED",
                    )
                )
                db.add(
                    AIUsageLog(
                        account_id=acc.id,
                        feature="copilot_chat",
                        model="gemini-3.5-flash-lite",
                        prompt_tokens=420,
                        output_tokens=110,
                        thoughts_tokens=0,
                        total_tokens=530,
                    )
                )
                await db.commit()
                acc_id = acc.id

            act_res = await client.get(f"/api/posts/{acc_id}/activity")
            assert act_res.status_code == 200
            events = act_res.json()["events"]
            assert len(events) == 1
            assert events[0]["status"] == "PUBLISHED"
            assert events[0]["media_type"] == "STORIES"

            usage_res = await client.get(f"/api/ai/{acc_id}/usage")
            assert usage_res.status_code == 200
            usage_json = usage_res.json()
            assert usage_json["request_count"] == 1
            assert usage_json["prompt_tokens"] == 420
            assert usage_json["output_tokens"] == 110
            assert usage_json["total_tokens"] == 530
            assert usage_json["by_feature"]["copilot_chat"]["requests"] == 1
            assert len(usage_json["recent_logs"]) == 1
    finally:
        app.dependency_overrides.clear()
