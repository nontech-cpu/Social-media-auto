"""Reach-focused Hashtag Intelligence Engine with Rankability Fit & Bayesian Lift."""

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenCipher
from app.models.entities import BrandProfile, HashtagCache, InstagramAccount, MediaItem
from app.services.gemini_helper import generate_structured_content
from app.services.meta_client import MetaAPIError, MetaGraphClient

META_HASHTAG_WEEKLY_QUOTA = 30


class HashtagCandidate(BaseModel):
    hashtag: str = Field(description="Lowercase hashtag starting with # and no spaces")
    tier: str = Field(description="trending | niche | branded")
    why_it_boosts_reach: str = Field(description="Concrete reason this tag improves discovery")


class HashtagDiscoveryResult(BaseModel):
    hashtags: list[HashtagCandidate]
    strategy_note: str


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int((len(sorted_vals) - 1) * q)))
    return float(sorted_vals[idx])


def classify_rankability(
    account_median_eng: float, hashtag_top_p25_eng: float | None
) -> tuple[float | None, str]:
    """Compare account's median engagement against a hashtag's 25th-percentile Top Posts bar."""
    if hashtag_top_p25_eng is None or hashtag_top_p25_eng <= 0:
        return None, "unverified"
    fit_ratio = round(min(2.0, max(account_median_eng, 1.0) / hashtag_top_p25_eng), 2)
    if fit_ratio >= 0.35:
        return fit_ratio, "winnable"
    if fit_ratio >= 0.10:
        return fit_ratio, "stretch"
    return fit_ratio, "saturated"


class HashtagEngine:
    """Combines Bayesian historical hashtag lift, Meta rankability fit, and Gemini trends."""

    def __init__(
        self,
        settings: Settings | None = None,
        meta_client: MetaGraphClient | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.meta_client = meta_client or MetaGraphClient(self.settings)
        self.cipher = cipher or TokenCipher()

    async def get_weekly_quota_used(
        self, db: AsyncSession, account: InstagramAccount
    ) -> int:
        """Count distinct hashtags queried against Meta API within the trailing 7 days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.settings.hashtag_cache_ttl_days)
        rows = (
            await db.execute(
                select(HashtagCache).where(
                    HashtagCache.account_id == account.id,
                    HashtagCache.last_queried_at >= cutoff,
                    HashtagCache.ig_hashtag_id != "",
                )
            )
        ).scalars().all()
        return len(rows)

    async def compute_historical_hashtag_stats(
        self, db: AsyncSession, account: InstagramAccount
    ) -> dict[str, Any]:
        """Compute Bayesian hashtag lift & account median engagement from synced MediaItem rows."""
        media_rows = (
            await db.execute(
                select(MediaItem)
                .where(MediaItem.account_id == account.id)
                .order_by(MediaItem.published_at.desc())
                .limit(100)
            )
        ).scalars().all()

        reaches = [float(m.reach) for m in media_rows if m.reach > 0]
        eng_scores = [
            float(m.likes + m.comments * 2 + m.shares * 3 + m.saves * 3)
            for m in media_rows
        ]
        account_median_reach = median(reaches) if reaches else 0.0
        account_median_eng = median(eng_scores) if eng_scores else float(max(account.followers_count * 0.03, 1.0))

        by_tag: dict[str, list[float]] = {}
        for item in media_rows:
            for tag in item.hashtags_json or []:
                by_tag.setdefault(tag.lower(), []).append(float(item.reach))

        m_prior = self.settings.hashtag_bayesian_m
        tag_stats: dict[str, dict[str, Any]] = {}
        for tag, vals in by_tag.items():
            cnt = len(vals)
            raw_avg = sum(vals) / cnt
            bayes_avg = (
                (sum(vals) + m_prior * account_median_reach) / (cnt + m_prior)
                if account_median_reach > 0
                else raw_avg
            )
            lift = (
                round(bayes_avg / account_median_reach, 2)
                if account_median_reach > 0
                else 1.0
            )
            tag_stats[tag] = {
                "hashtag": tag,
                "uses": cnt,
                "avg_reach": round(raw_avg, 1),
                "bayesian_reach": round(bayes_avg, 1),
                "lift_vs_median": lift,
            }

        top_historical = sorted(
            tag_stats.values(),
            key=lambda x: (x["lift_vs_median"], x["bayesian_reach"]),
            reverse=True,
        )[:12]

        return {
            "account_median_reach": account_median_reach,
            "account_median_eng": account_median_eng,
            "by_tag": tag_stats,
            "top_historical": top_historical,
        }

    async def validate_hashtags_via_meta(
        self,
        db: AsyncSession,
        account: InstagramAccount,
        candidate_tags: list[str],
        max_live_lookups: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Validate candidate hashtags with Meta ig_hashtag_search while obeying the 30/7d cap."""
        now = datetime.now(timezone.utc)
        results: dict[str, dict[str, Any]] = {}
        if not account.encrypted_access_token:
            return results

        lookup_cap = (
            max_live_lookups
            if max_live_lookups is not None
            else self.settings.hashtag_max_live_lookups
        )
        weekly_cap = self.settings.hashtag_weekly_quota
        ttl_days = self.settings.hashtag_cache_ttl_days

        token = self.cipher.decrypt(account.encrypted_access_token)
        existing_rows = (
            await db.execute(select(HashtagCache).where(HashtagCache.account_id == account.id))
        ).scalars().all()
        cache_by_name = {row.hashtag_name: row for row in existing_rows}

        quota_used = await self.get_weekly_quota_used(db, account)
        live_calls = 0

        for raw_tag in candidate_tags:
            clean = raw_tag.lstrip("#").strip().lower()
            if not clean:
                continue
            cached = cache_by_name.get(clean)
            if cached and cached.ig_hashtag_id:
                exp = (
                    cached.expires_at.replace(tzinfo=timezone.utc)
                    if cached.expires_at.tzinfo is None
                    else cached.expires_at
                )
                if exp > now:
                    results[f"#{clean}"] = {
                        "ig_hashtag_id": cached.ig_hashtag_id,
                        "top_media_avg_engagement": cached.top_media_avg_engagement,
                        "source": "meta_cache",
                    }
                    continue

            if quota_used >= weekly_cap or live_calls >= lookup_cap:
                continue

            try:
                hashtag_id = await self.meta_client.search_hashtag_id(
                    account.ig_user_id, clean, token
                )
                top_media = await self.meta_client.fetch_hashtag_top_media(
                    hashtag_id, account.ig_user_id, token
                )
                engagements = sorted(
                    float(int(m.get("like_count", 0) or 0) + int(m.get("comments_count", 0) or 0) * 2)
                    for m in top_media
                )
                # Use 25th-percentile of Top Posts to measure the actual minimum bar to rank
                p25_eng = round(_percentile(engagements, 0.25), 2) if engagements else 0.0
                sample_captions = [
                    str(m.get("caption", ""))[:180] for m in top_media[:3] if m.get("caption")
                ]

                if cached is None:
                    cached = HashtagCache(account_id=account.id, hashtag_name=clean)
                    db.add(cached)
                cached.ig_hashtag_id = hashtag_id
                cached.top_media_avg_engagement = p25_eng
                cached.top_media_sample_count = len(top_media)
                cached.sample_top_captions_json = sample_captions
                cached.last_queried_at = now
                cached.expires_at = now + timedelta(days=ttl_days)

                quota_used += 1
                live_calls += 1
                results[f"#{clean}"] = {
                    "ig_hashtag_id": hashtag_id,
                    "top_media_avg_engagement": p25_eng,
                    "source": "meta_live",
                }
            except MetaAPIError:
                continue

        await db.flush()
        return results

    async def enrich_and_rank_candidates(
        self,
        db: AsyncSession,
        account: InstagramAccount,
        candidates: list[HashtagCandidate],
        *,
        desired_count: int = 15,
        hist_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Enrich candidate hashtags with Bayesian history lift & Meta rankability fit."""
        stats = hist_stats or await self.compute_historical_hashtag_stats(db, account)
        meta_metrics = await self.validate_hashtags_via_meta(
            db, account, [c.hashtag for c in candidates]
        )

        enriched: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            tag = item.hashtag.strip().lower().replace(" ", "")
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag
            if len(tag) < 3 or tag in seen:
                continue
            seen.add(tag)

            hist_info = stats["by_tag"].get(tag)
            meta_info = meta_metrics.get(tag, {})
            top_bar = meta_info.get("top_media_avg_engagement")
            fit_ratio, rankability = classify_rankability(stats["account_median_eng"], top_bar)

            # Combined rank score favors winnable Meta tags + historical lift
            rank_score = 1.0
            if hist_info:
                rank_score += (hist_info["lift_vs_median"] - 1.0) * 1.5
            if rankability == "winnable":
                rank_score += 0.8
            elif rankability == "stretch":
                rank_score += 0.25
            elif rankability == "saturated":
                rank_score -= 0.4

            enriched.append(
                {
                    "hashtag": tag,
                    "tier": item.tier,
                    "why_it_boosts_reach": item.why_it_boosts_reach,
                    "account_avg_reach": hist_info["avg_reach"] if hist_info else None,
                    "historical_lift": hist_info["lift_vs_median"] if hist_info else None,
                    "meta_top_media_engagement": top_bar,
                    "meta_validated": bool(meta_info),
                    "rankability": rankability,
                    "rankability_fit_ratio": fit_ratio,
                    "_rank_score": rank_score,
                }
            )

        enriched.sort(key=lambda x: x["_rank_score"], reverse=True)
        for row in enriched:
            row.pop("_rank_score", None)

        quota_used = await self.get_weekly_quota_used(db, account)
        return {
            "hashtags": enriched[:desired_count],
            "meta_weekly_quota_used": quota_used,
            "meta_weekly_quota_limit": self.settings.hashtag_weekly_quota,
        }

    async def recommend_hashtags(
        self,
        db: AsyncSession,
        account: InstagramAccount,
        brand: BrandProfile | None,
        *,
        topic: str,
        media_type: str = "REELS",
        desired_count: int = 15,
    ) -> dict[str, Any]:
        """Generate high-reach trending + niche hashtags grounded in account history & rankability."""
        hist_stats = await self.compute_historical_hashtag_stats(db, account)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        brand_context = (
            f"Brand: {brand.brand_name}, Niche: {brand.niche}, Audience: {brand.target_audience}, "
            f"Language: {brand.language}"
            if brand
            else f"Account: @{account.username}, Bio: {account.biography}"
        )

        prompt = (
            f"Today is {today_str}. Recommend {desired_count} high-performing Instagram {media_type} "
            "hashtags tailored to this account's size and designed to maximize organic reach "
            "(avoid banned, spammy, or mega-saturated generic tags like #love #instagood #fyp).\n"
            f"Account followers: {account.followers_count}, median engagement per post: {hist_stats['account_median_eng']:.0f}.\n"
            f"Topic/Concept: {topic}\n"
            f"{brand_context}\n"
            f"Account's historically highest-lift hashtags: {hist_stats['top_historical']}\n\n"
            "Return a balanced mix of trending discovery tags, winnable mid-competition niche tags, and branded/community intent tags. "
            "Every hashtag must start with # and contain no spaces."
        )

        parsed = await generate_structured_content(
            self.settings,
            prompt=prompt,
            response_schema=HashtagDiscoveryResult,
            temperature=0.4,
            use_search_grounding=True,
        )

        ranked = await self.enrich_and_rank_candidates(
            db, account, parsed.hashtags, desired_count=desired_count, hist_stats=hist_stats
        )
        return {
            **ranked,
            "strategy_note": parsed.strategy_note,
        }
