"""Async Meta Graph API client for Facebook Login for Business & Instagram Graph API."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings


class MetaAPIError(RuntimeError):
    """Structured error returned by Meta Graph API."""

    def __init__(self, message: str, status_code: int = 400, error_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class MetaGraphClient:
    """Handles OAuth, Account Sync, Insights, Hashtag Search, and 2-Step Media Publishing."""

    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._external_client = http_client

    @property
    def graph_base(self) -> str:
        return f"https://graph.facebook.com/{self.settings.meta_graph_version}"

    def build_oauth_url(self, state: str) -> str:
        if not self.settings.meta_app_id:
            raise MetaAPIError("META_APP_ID is not configured in environment.", status_code=500)
        params: dict[str, str] = {
            "client_id": self.settings.meta_app_id,
            "redirect_uri": self.settings.meta_redirect_uri,
            "state": state,
            "response_type": "code",
            "auth_type": "rerequest",
        }
        if self.settings.meta_login_config_id.strip():
            params["config_id"] = self.settings.meta_login_config_id.strip()
        else:
            params["scope"] = ",".join(self.settings.meta_scope_list)
        return f"https://www.facebook.com/{self.settings.meta_graph_version}/dialog/oauth?{urlencode(params)}"

    async def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{self.graph_base}/{path_or_url.lstrip('/')}"
        )
        if self._external_client is not None:
            response = await self._external_client.request(method, url, params=params, data=data)
            return self._parse_response(response)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, params=params, data=data)
            return self._parse_response(response)

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": response.text or "Invalid JSON from Meta Graph API"}}

        if response.status_code >= 400 or "error" in payload:
            err = payload.get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or f"Meta API HTTP {response.status_code}"
            raise MetaAPIError(msg, status_code=response.status_code, error_code=err.get("code"))
        return payload

    async def exchange_code_for_long_lived_token(self, code: str) -> tuple[str, datetime]:
        """Exchange OAuth code -> short-lived token -> 60-day long-lived user token."""
        if not self.settings.meta_app_id or not self.settings.meta_app_secret:
            raise MetaAPIError("META_APP_ID and META_APP_SECRET must be set.", status_code=500)

        short_resp = await self._request(
            "GET",
            "/oauth/access_token",
            params={
                "client_id": self.settings.meta_app_id,
                "redirect_uri": self.settings.meta_redirect_uri,
                "client_secret": self.settings.meta_app_secret,
                "code": code,
            },
        )
        short_token = short_resp["access_token"]

        long_resp = await self._request(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.settings.meta_app_id,
                "client_secret": self.settings.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )
        expires_in = int(long_resp.get("expires_in", 60 * 24 * 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return long_resp["access_token"], expires_at

    async def refresh_long_lived_token(self, existing_token: str) -> tuple[str, datetime]:
        """Renew an active 60-day long-lived Meta token before expiry."""
        if not self.settings.meta_app_id or not self.settings.meta_app_secret:
            raise MetaAPIError("META_APP_ID and META_APP_SECRET must be set.", status_code=500)
        resp = await self._request(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.settings.meta_app_id,
                "client_secret": self.settings.meta_app_secret,
                "fb_exchange_token": existing_token,
            },
        )
        expires_in = int(resp.get("expires_in", 60 * 24 * 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return resp["access_token"], expires_at

    async def discover_instagram_accounts(self, access_token: str) -> list[dict[str, Any]]:
        """Return Facebook Pages with linked Instagram Business/Creator accounts."""
        data, _ = await self.discover_instagram_accounts_with_diagnostics(access_token)
        return data

    async def discover_instagram_accounts_with_diagnostics(
        self, access_token: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return linked IG accounts across /me/accounts, /me/assigned_pages, and /me/businesses."""
        page_fields = (
            "id,name,access_token,"
            "instagram_business_account{"
            "id,username,name,biography,website,profile_picture_url,"
            "followers_count,follows_count,media_count},"
            "connected_instagram_account{id,username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count}"
        )

        raw_pages: list[dict[str, Any]] = []
        seen_page_ids: set[str] = set()

        def _collect_pages(pages_list: list[dict[str, Any]]) -> None:
            for p in pages_list:
                pid = str(p.get("id") or "")
                if pid and pid not in seen_page_ids:
                    seen_page_ids.add(pid)
                    raw_pages.append(p)

        data = await self._request(
            "GET",
            "/me/accounts",
            params={"fields": page_fields, "access_token": access_token},
        )
        _collect_pages(data.get("data", []))

        if not raw_pages:
            try:
                assigned = await self._request(
                    "GET",
                    "/me/assigned_pages",
                    params={"fields": page_fields, "access_token": access_token},
                )
                _collect_pages(assigned.get("data", []))
            except MetaAPIError:
                pass

        if not raw_pages:
            try:
                biz_resp = await self._request(
                    "GET",
                    "/me/businesses",
                    params={
                        "fields": f"id,name,owned_pages{{{page_fields}}},client_pages{{{page_fields}}}",
                        "access_token": access_token,
                    },
                )
                for biz in biz_resp.get("data", []):
                    _collect_pages((biz.get("owned_pages") or {}).get("data", []))
                    _collect_pages((biz.get("client_pages") or {}).get("data", []))
            except MetaAPIError:
                pass

        accounts: list[dict[str, Any]] = []
        pages_without_ig: list[str] = []
        for page in raw_pages:
            ig = page.get("instagram_business_account") or page.get("connected_instagram_account")
            if ig and ig.get("id"):
                accounts.append(
                    {
                        "fb_page_id": page["id"],
                        "fb_page_name": page.get("name", ""),
                        "ig": ig,
                    }
                )
            else:
                pages_without_ig.append(str(page.get("name") or page.get("id")))

        granted_scopes: list[str] = []
        if not accounts:
            try:
                perms = await self._request(
                    "GET",
                    "/me/permissions",
                    params={"access_token": access_token},
                )
                granted_scopes = [
                    str(row.get("permission"))
                    for row in perms.get("data", [])
                    if row.get("status") == "granted"
                ]
            except MetaAPIError:
                pass

        diagnostics = {
            "pages_found": len(raw_pages),
            "pages_without_ig": pages_without_ig,
            "granted_scopes": granted_scopes,
        }
        return accounts, diagnostics

    async def fetch_account_profile(self, ig_user_id: str, access_token: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/{ig_user_id}",
            params={
                "fields": (
                    "id,username,name,biography,website,profile_picture_url,"
                    "followers_count,follows_count,media_count"
                ),
                "access_token": access_token,
            },
        )

    async def fetch_account_media(
        self, ig_user_id: str, access_token: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch recent media posts/Reels using pagination up to `limit`."""
        items: list[dict[str, Any]] = []
        next_url: str | None = None
        fields = (
            "id,caption,media_type,media_product_type,media_url,thumbnail_url,"
            "permalink,timestamp,like_count,comments_count"
        )
        while len(items) < limit:
            page_limit = min(25, limit - len(items))
            if next_url is None:
                resp = await self._request(
                    "GET",
                    f"/{ig_user_id}/media",
                    params={
                        "fields": fields,
                        "limit": page_limit,
                        "access_token": access_token,
                    },
                )
            else:
                resp = await self._request("GET", next_url)
            batch = resp.get("data", [])
            items.extend(batch)
            next_url = resp.get("paging", {}).get("next")
            if not next_url or not batch:
                break
        return items[:limit]

    async def fetch_media_insights(
        self, ig_media_id: str, media_product_type: str, access_token: str
    ) -> dict[str, int]:
        """Fetch media-level insights safely across Reels, Carousel, and Feed items."""
        if media_product_type == "REELS":
            metric_candidates = [
                "reach,saved,shares,likes,comments,views",
                "reach,saved,shares,total_interactions,views",
                "reach,saved,shares,plays",
                "reach,saved,shares",
                "reach,saved",
            ]
        else:
            metric_candidates = [
                "reach,saved,shares,views",
                "reach,saved,shares,total_interactions",
                "reach,saved,shares",
                "reach,saved,impressions",
                "reach,saved",
            ]

        for metrics in metric_candidates:
            try:
                resp = await self._request(
                    "GET",
                    f"/{ig_media_id}/insights",
                    params={"metric": metrics, "access_token": access_token},
                )
                parsed: dict[str, int] = {}
                for row in resp.get("data", []):
                    name = row.get("name")
                    values = row.get("values") or []
                    if name and values:
                        parsed[name] = int(values[0].get("value", 0) or 0)
                return parsed
            except MetaAPIError:
                continue
        return {}

    async def fetch_account_insights(
        self, ig_user_id: str, access_token: str
    ) -> dict[str, Any]:
        """Fetch daily account reach, profile_views, and online_followers distribution."""
        result: dict[str, Any] = {
            "reach_day": 0,
            "profile_views_day": 0,
            "online_followers_hourly": {},
        }
        try:
            resp = await self._request(
                "GET",
                f"/{ig_user_id}/insights",
                params={
                    "metric": "reach,profile_views",
                    "period": "day",
                    "access_token": access_token,
                },
            )
            for row in resp.get("data", []):
                values = row.get("values") or []
                latest = int(values[-1].get("value", 0) or 0) if values else 0
                if row.get("name") == "reach":
                    result["reach_day"] = latest
                elif row.get("name") == "profile_views":
                    result["profile_views_day"] = latest
        except MetaAPIError:
            pass

        try:
            online_resp = await self._request(
                "GET",
                f"/{ig_user_id}/insights",
                params={
                    "metric": "online_followers",
                    "period": "lifetime",
                    "access_token": access_token,
                },
            )
            data_rows = online_resp.get("data", [])
            if data_rows and data_rows[0].get("values"):
                raw_map = data_rows[0]["values"][-1].get("value") or {}
                if isinstance(raw_map, dict):
                    result["online_followers_hourly"] = {
                        str(k): int(v) for k, v in raw_map.items()
                    }
        except MetaAPIError:
            # Meta requires >= 100 followers for online_followers; safely omit otherwise
            pass

        return result

    async def fetch_recently_searched_hashtags(
        self, ig_user_id: str, access_token: str
    ) -> list[dict[str, Any]]:
        resp = await self._request(
            "GET",
            f"/{ig_user_id}/recently_searched_hashtags",
            params={"access_token": access_token},
        )
        return resp.get("data", [])

    async def search_hashtag_id(
        self, ig_user_id: str, hashtag_name: str, access_token: str
    ) -> str:
        clean = hashtag_name.lstrip("#").strip().lower()
        resp = await self._request(
            "GET",
            "/ig_hashtag_search",
            params={"user_id": ig_user_id, "q": clean, "access_token": access_token},
        )
        items = resp.get("data", [])
        if not items:
            raise MetaAPIError(f"No Meta hashtag ID returned for #{clean}", status_code=404)
        return str(items[0]["id"])

    async def fetch_hashtag_top_media(
        self, hashtag_id: str, ig_user_id: str, access_token: str
    ) -> list[dict[str, Any]]:
        resp = await self._request(
            "GET",
            f"/{hashtag_id}/top_media",
            params={
                "user_id": ig_user_id,
                "fields": "id,caption,media_type,like_count,comments_count,permalink",
                "access_token": access_token,
            },
        )
        return resp.get("data", [])

    async def create_media_container(
        self,
        ig_user_id: str,
        access_token: str,
        *,
        media_type: str,
        media_urls: list[str],
        caption: str,
        share_to_feed: bool = True,
        cover_url: str = "",
    ) -> str:
        """Create an Instagram media container for IMAGE, REELS, or CAROUSEL."""
        if not media_urls:
            raise MetaAPIError("At least one public media URL is required.", status_code=400)

        if media_type == "CAROUSEL":
            if len(media_urls) < 2:
                raise MetaAPIError("Carousel posts require at least 2 media URLs.", status_code=400)
            child_ids: list[str] = []
            for url in media_urls[:10]:
                is_video = url.lower().split("?")[0].endswith((".mp4", ".mov"))
                child_payload: dict[str, Any] = {
                    "is_carousel_item": "true",
                    "access_token": access_token,
                }
                if is_video:
                    child_payload["media_type"] = "VIDEO"
                    child_payload["video_url"] = url
                else:
                    child_payload["image_url"] = url
                child_resp = await self._request("POST", f"/{ig_user_id}/media", data=child_payload)
                child_id = str(child_resp["id"])
                await self.poll_container_until_ready(child_id, access_token, timeout_seconds=120)
                child_ids.append(child_id)

            parent_resp = await self._request(
                "POST",
                f"/{ig_user_id}/media",
                data={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": caption,
                    "access_token": access_token,
                },
            )
            return str(parent_resp["id"])

        if media_type == "REELS":
            payload: dict[str, Any] = {
                "media_type": "REELS",
                "video_url": media_urls[0],
                "caption": caption,
                "share_to_feed": "true" if share_to_feed else "false",
                "access_token": access_token,
            }
            if cover_url:
                payload["cover_url"] = cover_url
            resp = await self._request("POST", f"/{ig_user_id}/media", data=payload)
            return str(resp["id"])

        resp = await self._request(
            "POST",
            f"/{ig_user_id}/media",
            data={
                "image_url": media_urls[0],
                "caption": caption,
                "access_token": access_token,
            },
        )
        return str(resp["id"])

    async def get_container_status(self, container_id: str, access_token: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/{container_id}",
            params={"fields": "status_code,status", "access_token": access_token},
        )

    async def poll_container_until_ready(
        self,
        container_id: str,
        access_token: str,
        timeout_seconds: int = 180,
        poll_interval_seconds: float = 3.0,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        delay = poll_interval_seconds
        while loop.time() < deadline:
            status_data = await self.get_container_status(container_id, access_token)
            code = str(status_data.get("status_code", "")).upper()
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise MetaAPIError(
                    f"Instagram container {container_id} failed: {status_data.get('status', code)}",
                    status_code=502,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.3, 10.0)
        raise MetaAPIError(
            f"Timed out waiting for Instagram media container {container_id}.",
            status_code=504,
        )

    async def publish_container(
        self, ig_user_id: str, container_id: str, access_token: str
    ) -> dict[str, str]:
        pub_resp = await self._request(
            "POST",
            f"/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
        )
        media_id = str(pub_resp["id"])
        permalink = ""
        try:
            info = await self._request(
                "GET",
                f"/{media_id}",
                params={"fields": "permalink", "access_token": access_token},
            )
            permalink = str(info.get("permalink", ""))
        except MetaAPIError:
            pass
        return {"ig_media_id": media_id, "permalink": permalink}
