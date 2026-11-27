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
    finally:
        app.dependency_overrides.clear()
