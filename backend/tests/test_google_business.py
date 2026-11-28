"""Tests for Google Business Profile OAuth, API client, growth formulas, auto-reply, and routes."""

from datetime import date, datetime, timedelta, timezone
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.security import get_token_cipher
from app.main import app
from app.models.entities import GoogleBusinessDailyMetric, GoogleBusinessProfile, GoogleBusinessReview
from app.services.google_business_client import GoogleBusinessAPIError, GoogleBusinessClient
from app.services.google_business_service import (
    ReviewReplyOutput,
    build_gbp_overview,
    sync_gbp_profile,
)


@pytest.mark.asyncio
async def test_gbp_client_discovery_metrics_reviews_and_quota_diagnostic(test_settings) -> None:
    test_settings.google_client_id = "test-google-client-id"
    test_settings.google_client_secret = "test-google-client-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "ya29.access", "refresh_token": "1//refresh", "expires_in": 3600},
            )
        if "mybusinessaccountmanagement.googleapis.com/v1/accounts" in url:
            return httpx.Response(
                200,
                json={"accounts": [{"name": "accounts/111222", "accountName": "ShapeBytes Corp"}]},
            )
        if "mybusinessbusinessinformation.googleapis.com/v1/accounts/111222/locations" in url:
            return httpx.Response(
                200,
                json={
                    "locations": [
                        {
                            "name": "locations/998877",
                            "title": "ShapeBytes Cafe",
                            "storeCode": "SB-01",
                            "websiteUri": "https://shapebytes.example",
                            "phoneNumbers": {"primaryPhone": "+919999999999"},
                            "categories": {"primaryCategory": {"displayName": "Software Company"}},
                            "storefrontAddress": {
                                "addressLines": ["100 MG Road"],
                                "locality": "Bengaluru",
                                "administrativeArea": "KA",
                                "postalCode": "560001",
                            },
                        }
                    ]
                },
            )
        if "fetchMultiDailyMetricsTimeSeries" in url:
            return httpx.Response(
                200,
                json={
                    "multiDailyMetricTimeSeries": [
                        {
                            "dailyMetricTimeSeries": [
                                {
                                    "dailyMetric": "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
                                    "timeSeries": {
                                        "datedValues": [
                                            {"date": {"year": 2026, "month": 11, "day": 20}, "value": "120"}
                                        ]
                                    },
                                },
                                {
                                    "dailyMetric": "CALL_CLICKS",
                                    "timeSeries": {
                                        "datedValues": [
                                            {"date": {"year": 2026, "month": 11, "day": 20}, "value": "9"}
                                        ]
                                    },
                                },
                            ]
                        }
                    ]
                },
            )
        if "/reviews" in url and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "averageRating": 4.5,
                    "totalReviewCount": 2,
                    "reviews": [
                        {
                            "name": "accounts/111222/locations/998877/reviews/r1",
                            "reviewId": "r1",
                            "reviewer": {"displayName": "Aarav Mehta"},
                            "starRating": "FIVE",
                            "comment": "Super fast service and very polite team!",
                            "createTime": "2026-11-25T10:00:00Z",
                            "updateTime": "2026-11-25T10:00:00Z",
                        }
                    ],
                },
            )
        if url.endswith("/reviews/r1/reply") and request.method == "PUT":
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"comment": body["comment"]})
        return httpx.Response(404, json={"error": {"message": "Not found"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GoogleBusinessClient(test_settings, http_client=http)
        oauth_url = client.build_oauth_url("signed-state")
        assert "business.manage" in oauth_url
        assert "access_type=offline" in oauth_url

        toks = await client.exchange_code_for_tokens("auth-code")
        assert toks["access_token"] == "ya29.access"
        assert toks["refresh_token"] == "1//refresh"

        locs, diag = await client.discover_locations_with_diagnostics(toks["access_token"])
        assert diag["locations_found"] == 1
        assert locs[0]["location_id"] == "998877"
        assert locs[0]["primary_category"] == "Software Company"

        daily = await client.fetch_daily_performance_metrics(
            toks["access_token"],
            "998877",
            start_date=date(2026, 11, 19),
            end_date=date(2026, 11, 21),
        )
        assert len(daily) == 1
        assert daily[0]["total_impressions"] == 120
        assert daily[0]["call_clicks"] == 9

        revs = await client.fetch_reviews(toks["access_token"], "accounts/111222", "locations/998877")
        assert revs["reviews"][0]["star_rating"] == 5
        assert revs["reviews"][0]["reviewer_name"] == "Aarav Mehta"

        rep = await client.put_review_reply(
            toks["access_token"], revs["reviews"][0]["review_resource_name"], "Thank you warmly!"
        )
        assert rep["comment"] == "Thank you warmly!"

    # Verify 0 QPM / unapproved API diagnostic error message
    def quota_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"code": 429, "message": "Quota exceeded for quota metric 'Requests' (0 per minute)", "status": "RESOURCE_EXHAUSTED"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(quota_handler)) as quota_http:
        q_client = GoogleBusinessClient(test_settings, http_client=quota_http)
        with pytest.raises(GoogleBusinessAPIError) as exc_info:
            await q_client.discover_locations_with_diagnostics("tok")
        assert "0 QPM quota" in str(exc_info.value)

    def scope_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": 403, "message": "Request had insufficient authentication scopes.", "status": "PERMISSION_DENIED"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(scope_handler)) as scope_http:
        s_client = GoogleBusinessClient(test_settings, http_client=scope_http)
        with pytest.raises(GoogleBusinessAPIError) as scope_exc:
            await s_client.discover_locations_with_diagnostics("tok")
        assert "business.manage" in str(scope_exc.value)
        assert "0 QPM quota" not in str(scope_exc.value)


@pytest.mark.asyncio
async def test_gbp_analytics_formulas_and_smart_auto_reply(
    monkeypatch, db_session, test_settings, cipher
) -> None:
    profile = GoogleBusinessProfile(
        google_email="owner@shapebytes.example",
        gbp_account_name="accounts/101",
        location_name="locations/202",
        location_id="202",
        title="ShapeBytes Studio",
        primary_category="Marketing Agency",
        website_uri="https://shapebytes.example",
        phone_number="+919876543210",
        address_summary="Bengaluru",
        auto_reply_enabled=True,
        auto_reply_min_stars=4,
        encrypted_access_token=cipher.encrypt("valid-tok"),
        encrypted_refresh_token=cipher.encrypt("valid-refresh"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(profile)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    # Seed 30 prior days (days 30..59 ago: 100 impr/day, 5 actions/day) & 30 recent days (0..29 ago: 150 impr/day, 15 actions/day)
    for offset in range(60):
        d_str = (now.date() - timedelta(days=offset)).isoformat()
        is_recent = offset < 30
        db_session.add(
            GoogleBusinessDailyMetric(
                profile_id=profile.id,
                metric_date=d_str,
                impressions_search_mobile=90 if is_recent else 60,
                impressions_maps_mobile=60 if is_recent else 40,
                website_clicks=6 if is_recent else 2,
                call_clicks=5 if is_recent else 2,
                direction_requests=4 if is_recent else 1,
                total_impressions=150 if is_recent else 100,
                total_actions=15 if is_recent else 5,
            )
        )
    db_session.add_all(
        [
            GoogleBusinessReview(
                profile_id=profile.id,
                review_id="rev_old",
                review_resource_name="accounts/101/locations/202/reviews/rev_old",
                reviewer_name="Priya S",
                star_rating=4,
                comment="Helpful team",
                review_created_at=now - timedelta(days=45),
                review_updated_at=now - timedelta(days=45),
                reply_comment="Thank you so much, Priya!",
                reply_updated_at=now - timedelta(days=45) + timedelta(hours=4),
                reply_status="REPLIED",
            ),
            GoogleBusinessReview(
                profile_id=profile.id,
                review_id="rev_new_5",
                review_resource_name="accounts/101/locations/202/reviews/rev_new_5",
                reviewer_name="Rohan K",
                star_rating=5,
                comment="Best studio in town!",
                review_created_at=now - timedelta(days=2),
                review_updated_at=now - timedelta(days=2),
                reply_status="UNREPLIED",
            ),
        ]
    )
    await db_session.commit()

    ov = await build_gbp_overview(db_session, profile)
    kpis = ov["kpis"]
    assert kpis["impressions_30d"] == 4500
    assert kpis["impressions_prev_30d"] == 3000
    assert kpis["impressions_growth_pct"] == 50.0
    assert kpis["actions_30d"] == 450
    assert kpis["actions_prev_30d"] == 150
    assert kpis["actions_growth_pct"] == 200.0
    assert kpis["action_conversion_rate_pct"] == 10.0
    assert kpis["response_rate_pct"] == 50.0
    assert kpis["median_reply_hours"] == 4.0
    assert "impressions_growth" in ov["formulas"]
    assert "action_conversion_rate" in ov["formulas"]

    # Now test sync_gbp_profile smart auto-reply:
    # 5-star review auto-replies live; 2-star review stays DRAFT_READY for approval
    posted_replies: list[tuple[str, str]] = []

    def sync_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "fetchMultiDailyMetricsTimeSeries" in url:
            return httpx.Response(200, json={"multiDailyMetricTimeSeries": []})
        if "/reviews" in url and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "averageRating": 3.7,
                    "totalReviewCount": 3,
                    "reviews": [
                        {
                            "name": "accounts/101/locations/202/reviews/rev_new_5",
                            "reviewId": "rev_new_5",
                            "reviewer": {"displayName": "Rohan K"},
                            "starRating": "FIVE",
                            "comment": "Best studio in town!",
                            "createTime": "2026-11-26T10:00:00Z",
                            "updateTime": "2026-11-26T10:00:00Z",
                        },
                        {
                            "name": "accounts/101/locations/202/reviews/rev_crit_2",
                            "reviewId": "rev_crit_2",
                            "reviewer": {"displayName": "Meera N"},
                            "starRating": "TWO",
                            "comment": "Had to wait 20 minutes at reception.",
                            "createTime": "2026-11-27T10:00:00Z",
                            "updateTime": "2026-11-27T10:00:00Z",
                        },
                    ],
                },
            )
        if "/reply" in url and request.method == "PUT":
            body = json.loads(request.content.decode("utf-8"))
            posted_replies.append((url, body["comment"]))
            return httpx.Response(200, json={"comment": body["comment"]})
        return httpx.Response(200, json={})

    async def fake_generate_structured(settings, *, prompt, response_schema, **kwargs):
        return (
            ReviewReplyOutput(
                reply_text="Thank you so much! We truly appreciate your feedback and hope to welcome you again.",
                tone_note="Humble & warm",
            ),
            {"model": "gemini-3.5-flash-lite", "prompt_tokens": 120, "output_tokens": 25, "thoughts_tokens": 0, "cached_tokens": 0, "total_tokens": 145},
        )

    from app.services import google_business_service

    monkeypatch.setattr(
        google_business_service, "generate_structured_with_usage", fake_generate_structured
    )
    test_settings.gemini_api_key = "test-gem"

    async with httpx.AsyncClient(transport=httpx.MockTransport(sync_handler)) as sync_http:
        gbp_client = GoogleBusinessClient(test_settings, http_client=sync_http)
        res = await sync_gbp_profile(
            db_session, profile, gbp_client, cipher, settings=test_settings, auto_reply_eligible=True
        )
    assert res["auto_replied"] == 1
    assert res["drafts_prepared"] == 1
    assert len(posted_replies) == 1
    assert "rev_new_5" in posted_replies[0][0]

    crit_row = (
        await db_session.execute(
            select(GoogleBusinessReview).where(GoogleBusinessReview.review_id == "rev_crit_2")
        )
    ).scalar_one()
    assert crit_row.reply_status == "DRAFT_READY"
    assert crit_row.reply_comment == ""
    assert "truly appreciate" in crit_row.ai_suggested_reply


@pytest.mark.asyncio
async def test_gbp_routes_smoke(db_session_factory) -> None:
    from app.core.database import get_db

    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        cipher = get_token_cipher()
        async with db_session_factory() as db:
            prof = GoogleBusinessProfile(
                google_email="biz@example.com",
                gbp_account_name="accounts/5",
                location_name="locations/9",
                location_id="9",
                title="Smoke Bakery",
                encrypted_access_token=cipher.encrypt("tok"),
            )
            db.add(prof)
            await db.commit()
            prof_id = prof.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            h = await client.get("/api/health")
            assert h.status_code == 200
            assert "google_business_configured" in h.json()

            lst = await client.get("/api/google-business/profiles")
            assert lst.status_code == 200
            assert lst.json()["profiles"][0]["title"] == "Smoke Bakery"

            patch_res = await client.patch(
                f"/api/google-business/{prof_id}/settings",
                json={"auto_reply_enabled": True, "auto_reply_min_stars": 3},
            )
            assert patch_res.status_code == 200
            assert patch_res.json()["auto_reply_enabled"] is True
            assert patch_res.json()["auto_reply_min_stars"] == 3

            ov_res = await client.get(f"/api/google-business/{prof_id}/overview")
            assert ov_res.status_code == 200
            assert ov_res.json()["profile"]["id"] == prof_id
    finally:
        app.dependency_overrides.clear()
