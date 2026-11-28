"""Async Google Business Profile API client (OAuth 2.0, Locations, Reviews, and Performance)."""

from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings

STAR_RATING_MAP = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "STAR_RATING_UNSPECIFIED": 0,
}

DAILY_PERFORMANCE_METRICS = (
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "WEBSITE_CLICKS",
    "CALL_CLICKS",
    "BUSINESS_DIRECTION_REQUESTS",
)


class GoogleBusinessAPIError(RuntimeError):
    """Structured error returned by Google OAuth or Google Business Profile APIs."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_reason: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_reason = error_reason


def _format_gbp_error(status_code: int, payload: dict[str, Any], raw_text: str) -> tuple[str, str]:
    err_obj = payload.get("error")
    reason = ""
    raw_msg = ""
    if isinstance(err_obj, dict):
        raw_msg = str(err_obj.get("message") or "")
        status_str = str(err_obj.get("status") or "")
        reason = status_str
        details = err_obj.get("details") or []
        for item in details:
            if isinstance(item, dict) and item.get("reason"):
                reason = str(item["reason"])
                break
    elif isinstance(err_obj, str):
        raw_msg = str(payload.get("error_description") or err_obj)
        reason = err_obj

    lower_msg = (raw_msg + " " + raw_text).lower()
    if (
        "insufficient authentication scopes" in lower_msg
        or "insufficient_scope" in lower_msg
        or "access_token_scope_insufficient" in lower_msg
    ):
        hint = (
            "Google signed you in, but the Google Business permission (business.manage) was NOT granted. "
            "Re-click Connect Google Business, TICK the checkbox 'See, edit, create, and delete your Google business listings' "
            "on Google's consent screen, and ensure https://www.googleapis.com/auth/business.manage is saved under "
            "Google Cloud -> Google Auth Platform -> Data Access. "
            f"Raw Google response: {raw_msg or raw_text[:200]}"
        )
        return hint, reason or "GBP_SCOPE_NOT_GRANTED"

    if status_code in (403, 429) and (
        "quota" in lower_msg
        or "rate_limit" in lower_msg
        or "0 per minute" in lower_msg
        or "has not been used in project" in lower_msg
        or "accessnotconfigured" in lower_msg
    ):
        hint = (
            "Google Business Profile API access is currently unapproved or has 0 QPM quota on this Google Cloud project. "
            "Enable My Business Account Management API, My Business Business Information API, and Business Profile Performance API "
            "in Google Cloud Console, and submit Google's GBP API Access Request form (https://developers.google.com/my-business/content/prereqs). "
            f"Raw Google response: {raw_msg or raw_text[:220]}"
        )
        return hint, reason or "GBP_ACCESS_OR_QUOTA_REQUIRED"

    return raw_msg or raw_text[:300] or f"Google API HTTP {status_code}", reason


class GoogleBusinessClient:
    """Handles Google OAuth 2.0, Location Discovery, Performance Metrics, and Review Replies."""

    OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
    ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
    INFO_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1"
    REVIEWS_BASE = "https://mybusiness.googleapis.com/v4"
    PERF_BASE = "https://businessprofileperformance.googleapis.com/v1"

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._external_client = http_client

    def build_oauth_url(self, state: str) -> str:
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise GoogleBusinessAPIError(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not configured in environment.",
                status_code=500,
                error_reason="GOOGLE_OAUTH_NOT_CONFIGURED",
            )
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_gbp_redirect_uri,
            "response_type": "code",
            "scope": self.settings.google_gbp_scopes,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{self.OAUTH_AUTH_URL}?{urlencode(params)}"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        access_token: str = "",
        params: list[tuple[str, str]] | dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        if self._external_client is not None:
            resp = await self._external_client.request(
                method,
                url,
                headers=headers or None,
                params=params,
                data=data,
                json=json_body,
            )
            return self._parse_response(resp)

        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.request(
                method,
                url,
                headers=headers or None,
                params=params,
                data=data,
                json=json_body,
            )
            return self._parse_response(resp)

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict[str, Any]:
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {}
        if resp.status_code >= 400:
            msg, reason = _format_gbp_error(resp.status_code, payload, resp.text)
            raise GoogleBusinessAPIError(msg, status_code=resp.status_code, error_reason=reason)
        return payload if isinstance(payload, dict) else {}

    async def exchange_code_for_tokens(self, code: str) -> dict[str, Any]:
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise GoogleBusinessAPIError(
                "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing.",
                status_code=500,
            )
        payload = await self._request(
            "POST",
            self.OAUTH_TOKEN_URL,
            data={
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.settings.google_gbp_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise GoogleBusinessAPIError("Google OAuth did not return an access token.", status_code=400)
        expires_in = int(payload.get("expires_in") or 3600)
        return {
            "access_token": access_token,
            "refresh_token": str(payload.get("refresh_token") or ""),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            "scope": str(payload.get("scope") or ""),
        }

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        if not refresh_token:
            raise GoogleBusinessAPIError(
                "No Google refresh token saved. Please reconnect Google Business Profile.",
                status_code=401,
            )
        payload = await self._request(
            "POST",
            self.OAUTH_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise GoogleBusinessAPIError("Failed to refresh Google OAuth token.", status_code=401)
        expires_in = int(payload.get("expires_in") or 3600)
        return {
            "access_token": access_token,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        }

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        try:
            return await self._request("GET", self.USERINFO_URL, access_token=access_token)
        except GoogleBusinessAPIError:
            return {}

    async def discover_locations_with_diagnostics(
        self, access_token: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Discover all verified Google Business accounts and locations managed by the signed-in user."""
        accounts_resp = await self._request("GET", self.ACCOUNTS_URL, access_token=access_token)
        accounts = accounts_resp.get("accounts") or []
        discovered: list[dict[str, Any]] = []
        accounts_without_locations: list[str] = []

        for acct in accounts:
            acct_name = str(acct.get("name") or "")
            if not acct_name:
                continue
            loc_resp = await self._request(
                "GET",
                f"{self.INFO_BASE}/{acct_name}/locations",
                access_token=access_token,
                params={
                    "readMask": "name,title,storeCode,websiteUri,phoneNumbers,categories,storefrontAddress,metadata",
                    "pageSize": 100,
                },
            )
            locations = loc_resp.get("locations") or []
            if not locations:
                accounts_without_locations.append(str(acct.get("accountName") or acct_name))
                continue

            for loc in locations:
                loc_name = str(loc.get("name") or "")
                loc_id = loc_name.rsplit("/", 1)[-1] if "/" in loc_name else loc_name
                categories = loc.get("categories") or {}
                primary_cat = (categories.get("primaryCategory") or {}).get("displayName") or ""
                phones = loc.get("phoneNumbers") or {}
                primary_phone = str(phones.get("primaryPhone") or "")
                addr = loc.get("storefrontAddress") or {}
                addr_parts = [
                    *(addr.get("addressLines") or []),
                    addr.get("locality") or "",
                    addr.get("administrativeArea") or "",
                    addr.get("postalCode") or "",
                ]
                address_summary = ", ".join(p.strip() for p in addr_parts if isinstance(p, str) and p.strip())
                discovered.append(
                    {
                        "gbp_account_name": acct_name,
                        "gbp_account_display": str(acct.get("accountName") or acct_name),
                        "location_name": loc_name,
                        "location_id": loc_id,
                        "title": str(loc.get("title") or loc_id),
                        "store_code": str(loc.get("storeCode") or ""),
                        "primary_category": str(primary_cat),
                        "website_uri": str(loc.get("websiteUri") or ""),
                        "phone_number": primary_phone,
                        "address_summary": address_summary,
                    }
                )

        diagnostics = {
            "accounts_found": len(accounts),
            "accounts_without_locations": accounts_without_locations,
            "locations_found": len(discovered),
        }
        return discovered, diagnostics

    @staticmethod
    def _review_parent(gbp_account_name: str, location_name: str) -> str:
        loc_clean = location_name.strip().lstrip("/")
        if loc_clean.startswith("accounts/"):
            return loc_clean
        acc_clean = gbp_account_name.strip().strip("/")
        if not acc_clean.startswith("accounts/"):
            acc_clean = f"accounts/{acc_clean}"
        if not loc_clean.startswith("locations/"):
            loc_clean = f"locations/{loc_clean}"
        return f"{acc_clean}/{loc_clean}"

    async def fetch_reviews(
        self,
        access_token: str,
        gbp_account_name: str,
        location_name: str,
        *,
        page_size: int = 50,
    ) -> dict[str, Any]:
        parent = self._review_parent(gbp_account_name, location_name)
        payload = await self._request(
            "GET",
            f"{self.REVIEWS_BASE}/{parent}/reviews",
            access_token=access_token,
            params={"pageSize": str(page_size), "orderBy": "updateTime desc"},
        )
        raw_reviews = payload.get("reviews") or []
        reviews: list[dict[str, Any]] = []
        for r in raw_reviews:
            reviewer = r.get("reviewer") or {}
            reply_obj = r.get("reviewReply") or {}
            star_raw = str(r.get("starRating") or "FIVE").upper()
            stars = STAR_RATING_MAP.get(star_raw, 5)
            rev_name = str(r.get("name") or "")
            rev_id = str(r.get("reviewId") or (rev_name.rsplit("/", 1)[-1] if "/" in rev_name else rev_name))
            reviews.append(
                {
                    "review_id": rev_id,
                    "review_resource_name": rev_name or f"{parent}/reviews/{rev_id}",
                    "reviewer_name": str(reviewer.get("displayName") or "Google Customer"),
                    "reviewer_photo_url": str(reviewer.get("profilePhotoUrl") or ""),
                    "star_rating": stars,
                    "comment": str(r.get("comment") or ""),
                    "review_created_at": r.get("createTime"),
                    "review_updated_at": r.get("updateTime"),
                    "reply_comment": str(reply_obj.get("comment") or ""),
                    "reply_updated_at": reply_obj.get("updateTime"),
                }
            )
        return {
            "average_rating": float(payload.get("averageRating") or 0.0),
            "total_review_count": int(payload.get("totalReviewCount") or len(reviews)),
            "reviews": reviews,
        }

    async def put_review_reply(
        self,
        access_token: str,
        review_resource_name: str,
        comment: str,
    ) -> dict[str, Any]:
        clean_name = review_resource_name.strip().lstrip("/")
        return await self._request(
            "PUT",
            f"{self.REVIEWS_BASE}/{clean_name}/reply",
            access_token=access_token,
            json_body={"comment": comment.strip()},
        )

    async def fetch_daily_performance_metrics(
        self,
        access_token: str,
        location_id: str,
        *,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        clean_loc_id = location_id.strip().rsplit("/", 1)[-1]
        query_params: list[tuple[str, str]] = [
            ("dailyMetrics", metric) for metric in DAILY_PERFORMANCE_METRICS
        ]
        query_params.extend(
            [
                ("dailyRange.start_date.year", str(start_date.year)),
                ("dailyRange.start_date.month", str(start_date.month)),
                ("dailyRange.start_date.day", str(start_date.day)),
                ("dailyRange.end_date.year", str(end_date.year)),
                ("dailyRange.end_date.month", str(end_date.month)),
                ("dailyRange.end_date.day", str(end_date.day)),
            ]
        )
        payload = await self._request(
            "GET",
            f"{self.PERF_BASE}/locations/{clean_loc_id}:fetchMultiDailyMetricsTimeSeries",
            access_token=access_token,
            params=query_params,
        )

        by_date: dict[str, dict[str, int]] = {}
        field_map = {
            "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "impressions_search_desktop",
            "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "impressions_search_mobile",
            "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "impressions_maps_desktop",
            "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "impressions_maps_mobile",
            "WEBSITE_CLICKS": "website_clicks",
            "CALL_CLICKS": "call_clicks",
            "BUSINESS_DIRECTION_REQUESTS": "direction_requests",
        }

        for multi in payload.get("multiDailyMetricTimeSeries") or []:
            for series in multi.get("dailyMetricTimeSeries") or []:
                metric_name = str(series.get("dailyMetric") or "")
                target_field = field_map.get(metric_name)
                if not target_field:
                    continue
                ts = (series.get("timeSeries") or {}).get("datedValues") or []
                for dv in ts:
                    d_obj = dv.get("date") or {}
                    y = int(d_obj.get("year") or 0)
                    m = int(d_obj.get("month") or 0)
                    d = int(d_obj.get("day") or 0)
                    if not (y and m and d):
                        continue
                    iso_d = f"{y:04d}-{m:02d}-{d:02d}"
                    bucket = by_date.setdefault(
                        iso_d,
                        {
                            "impressions_search_desktop": 0,
                            "impressions_search_mobile": 0,
                            "impressions_maps_desktop": 0,
                            "impressions_maps_mobile": 0,
                            "website_clicks": 0,
                            "call_clicks": 0,
                            "direction_requests": 0,
                        },
                    )
                    bucket[target_field] += int(dv.get("value") or 0)

        rows: list[dict[str, Any]] = []
        for iso_d in sorted(by_date.keys()):
            b = by_date[iso_d]
            total_impr = (
                b["impressions_search_desktop"]
                + b["impressions_search_mobile"]
                + b["impressions_maps_desktop"]
                + b["impressions_maps_mobile"]
            )
            total_act = b["website_clicks"] + b["call_clicks"] + b["direction_requests"]
            rows.append(
                {
                    "metric_date": iso_d,
                    **b,
                    "total_impressions": total_impr,
                    "total_actions": total_act,
                }
            )
        return rows
