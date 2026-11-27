"""Real-time Instagram analytics aggregation and dynamic 7x24 Best-Time-to-Post engine."""

import asyncio
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenCipher
from app.models.entities import (
    AudienceSnapshot,
    BestTimeSlot,
    BrandProfile,
    InstagramAccount,
    MediaItem,
    ScheduledPost,
    StoryItem,
)
from app.services.brand_scraper import extract_hashtags_from_text
from app.services.meta_client import MetaGraphClient
from app.services.recommendation_engine import (
    _normalize_format,
    build_account_recommendations,
    serialize_recommendation_result,
)

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _safe_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


def _parse_meta_timestamp(raw_ts: str) -> datetime:
    if not raw_ts:
        return datetime.now(timezone.utc)
    normalized = raw_ts.strip()
    if normalized.endswith("+0000"):
        normalized = normalized[:-5] + "+00:00"
    elif normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_content_dna(media_rows: list[MediaItem]) -> dict[str, Any]:
    """Mine quantitative correlations between caption length, hooks, hashtag count & reach."""
    if not media_rows:
        return {"has_data": False}

    length_buckets: dict[str, list[tuple[int, float]]] = defaultdict(list)
    hashtag_buckets: dict[str, list[int]] = defaultdict(list)
    question_hook_reaches: list[int] = []
    statement_hook_reaches: list[int] = []

    for m in media_rows:
        cap = (m.caption or "").strip()
        first_line = cap.splitlines()[0] if cap else ""
        save_share_rate = (
            ((m.saves + m.shares) / m.reach) * 100.0 if m.reach > 0 else m.engagement_rate
        )
        if len(cap) < 80:
            length_buckets["short_under_80"].append((m.reach, save_share_rate))
        elif len(cap) <= 220:
            length_buckets["medium_80_220"].append((m.reach, save_share_rate))
        else:
            length_buckets["story_over_220"].append((m.reach, save_share_rate))

        tag_cnt = len(m.hashtags_json or [])
        if tag_cnt <= 5:
            hashtag_buckets["0_to_5"].append(m.reach)
        elif tag_cnt <= 12:
            hashtag_buckets["6_to_12"].append(m.reach)
        else:
            hashtag_buckets["13_plus"].append(m.reach)

        if "?" in first_line:
            question_hook_reaches.append(m.reach)
        elif first_line:
            statement_hook_reaches.append(m.reach)

    caption_summary = {
        k: {
            "count": len(v),
            "avg_reach": round(sum(x[0] for x in v) / len(v), 1),
            "avg_save_share_rate": round(sum(x[1] for x in v) / len(v), 2),
        }
        for k, v in length_buckets.items()
    }
    best_caption_bucket = (
        max(caption_summary.items(), key=lambda kv: kv[1]["avg_reach"])[0]
        if caption_summary
        else None
    )
    hashtag_count_summary = {
        k: {"count": len(v), "avg_reach": round(sum(v) / len(v), 1)}
        for k, v in hashtag_buckets.items()
    }
    best_hashtag_bucket = (
        max(hashtag_count_summary.items(), key=lambda kv: kv[1]["avg_reach"])[0]
        if hashtag_count_summary
        else None
    )

    q_avg = (
        round(sum(question_hook_reaches) / len(question_hook_reaches), 1)
        if question_hook_reaches
        else None
    )
    s_avg = (
        round(sum(statement_hook_reaches) / len(statement_hook_reaches), 1)
        if statement_hook_reaches
        else None
    )

    return {
        "has_data": True,
        "caption_length_performance": caption_summary,
        "best_caption_length_bucket": best_caption_bucket,
        "hashtag_count_performance": hashtag_count_summary,
        "best_hashtag_count_bucket": best_hashtag_bucket,
        "question_hook_avg_reach": q_avg,
        "statement_hook_avg_reach": s_avg,
    }


def compute_engagement_rate(
    *,
    likes: int,
    comments: int,
    shares: int,
    saves: int,
    reach: int,
    followers_count: int,
) -> float:
    interactions = max(0, likes + comments + shares + saves)
    denominator = reach if reach > 0 else max(1, followers_count)
    return round((interactions / denominator) * 100.0, 3)


def _weighted_engagement_rate(items: list[MediaItem], followers_fallback: int = 1) -> float:
    """Exposure-weighted engagement rate so tiny-reach posts never distort account averages."""
    if not items:
        return 0.0
    total_interactions = sum(
        max(0, m.likes + m.comments + m.shares + m.saves) for m in items
    )
    total_reach = sum(max(0, m.reach) for m in items)
    denominator = total_reach if total_reach > 0 else max(1, followers_fallback) * len(items)
    return round((total_interactions / denominator) * 100.0, 2)


def _compute_period_trend(media_rows: list[MediaItem], followers_fallback: int = 1) -> dict[str, float | None]:
    """Compare newer half of synced posts against older half for real velocity deltas."""
    if len(media_rows) < 4:
        return {"reach_trend_pct": None, "er_trend_pct": None}
    # media_rows are sorted newest -> oldest
    mid = len(media_rows) // 2
    recent_slice = media_rows[:mid]
    prior_slice = media_rows[mid:]

    recent_avg_reach = sum(m.reach for m in recent_slice) / len(recent_slice)
    prior_avg_reach = sum(m.reach for m in prior_slice) / len(prior_slice)
    reach_delta = (
        round(((recent_avg_reach - prior_avg_reach) / prior_avg_reach) * 100.0, 1)
        if prior_avg_reach > 0
        else None
    )

    recent_er = _weighted_engagement_rate(recent_slice, followers_fallback)
    prior_er = _weighted_engagement_rate(prior_slice, followers_fallback)
    er_delta = round(recent_er - prior_er, 2) if prior_er > 0 else None
    return {"reach_trend_pct": reach_delta, "er_trend_pct": er_delta}


async def sync_account_and_compute_slots(
    db: AsyncSession,
    account: InstagramAccount,
    meta_client: MetaGraphClient,
    cipher: TokenCipher,
    media_limit: int = 100,
) -> dict[str, Any]:
    """Sync profile, recent posts/Reels, insights, and recompute dynamic Best-Time heatmap."""
    access_token = cipher.decrypt(account.encrypted_access_token)
    profile = await meta_client.fetch_account_profile(account.ig_user_id, access_token)

    account.username = profile.get("username", account.username)
    account.name = profile.get("name", account.name)
    account.biography = profile.get("biography", account.biography)
    account.website = profile.get("website", account.website)
    account.profile_picture_url = profile.get("profile_picture_url", account.profile_picture_url)
    account.followers_count = int(profile.get("followers_count", account.followers_count) or 0)
    account.follows_count = int(profile.get("follows_count", account.follows_count) or 0)
    account.media_count = int(profile.get("media_count", account.media_count) or 0)

    raw_media = await meta_client.fetch_account_media(
        account.ig_user_id, access_token, limit=media_limit
    )

    existing_rows = (
        await db.execute(select(MediaItem).where(MediaItem.account_id == account.id))
    ).scalars().all()
    existing_by_ig_id = {row.ig_media_id: row for row in existing_rows}

    sem = asyncio.Semaphore(8)

    async def _fetch_item_insights(item_id: str, prod_type: str) -> dict[str, int]:
        async with sem:
            return await meta_client.fetch_media_insights(item_id, prod_type, access_token)

    insights_list = await asyncio.gather(
        *[
            _fetch_item_insights(str(item["id"]), str(item.get("media_product_type") or "FEED"))
            for item in raw_media
        ]
    )

    synced_count = 0
    for item, insights in zip(raw_media, insights_list):
        ig_media_id = str(item["id"])
        product_type = str(item.get("media_product_type") or "FEED")
        media_type = str(item.get("media_type") or "IMAGE")
        if product_type == "REELS":
            media_type = "REELS"

        likes = int(insights.get("likes", item.get("like_count", 0)) or 0)
        comments = int(insights.get("comments", item.get("comments_count", 0)) or 0)
        shares = int(insights.get("shares", 0) or 0)
        saves = int(insights.get("saved", 0) or 0)
        reach = int(insights.get("reach", 0) or 0)
        views = int(insights.get("views", insights.get("impressions", 0)) or 0)
        plays = int(insights.get("plays", views) or 0)
        caption = str(item.get("caption") or "")
        hashtags = extract_hashtags_from_text(caption)
        published_at = _parse_meta_timestamp(str(item.get("timestamp") or ""))
        eng_rate = compute_engagement_rate(
            likes=likes,
            comments=comments,
            shares=shares,
            saves=saves,
            reach=reach,
            followers_count=account.followers_count,
        )

        record = existing_by_ig_id.get(ig_media_id)
        if record is None:
            record = MediaItem(account_id=account.id, ig_media_id=ig_media_id, published_at=published_at)
            db.add(record)

        record.media_type = media_type
        record.media_product_type = product_type
        record.permalink = str(item.get("permalink") or "")
        record.media_url = str(item.get("media_url") or "")
        record.thumbnail_url = str(item.get("thumbnail_url") or item.get("media_url") or "")
        record.caption = caption
        record.hashtags_json = hashtags
        record.published_at = published_at
        record.reach = reach
        record.views = views
        record.likes = likes
        record.comments = comments
        record.shares = shares
        record.saves = saves
        record.plays = plays
        record.engagement_rate = eng_rate
        record.synced_at = datetime.now(timezone.utc)
        synced_count += 1

    account_insights = await meta_client.fetch_account_insights(account.ig_user_id, access_token)
    snapshot = AudienceSnapshot(
        account_id=account.id,
        online_followers_hourly_json=account_insights.get("online_followers_hourly", {}),
        reach_day=int(account_insights.get("reach_day", 0) or 0),
        profile_views_day=int(account_insights.get("profile_views_day", 0) or 0),
        follower_count=account.followers_count,
    )
    db.add(snapshot)

    # Capture active 24h Instagram Stories + insights before expiry
    synced_stories_count = 0
    raw_stories = await meta_client.fetch_active_stories(account.ig_user_id, access_token)
    if raw_stories:
        existing_stories = (
            await db.execute(select(StoryItem).where(StoryItem.account_id == account.id))
        ).scalars().all()
        stories_by_ig_id = {row.ig_media_id: row for row in existing_stories}

        async def _fetch_story_ins(sid: str) -> dict[str, int]:
            async with sem:
                return await meta_client.fetch_story_insights(sid, access_token)

        story_insights_list = await asyncio.gather(
            *[_fetch_story_ins(str(s["id"])) for s in raw_stories]
        )
        for st_item, st_ins in zip(raw_stories, story_insights_list):
            sid = str(st_item["id"])
            st_pub = _parse_meta_timestamp(str(st_item.get("timestamp") or ""))
            st_reach = int(st_ins.get("reach", 0) or 0)
            st_views = int(st_ins.get("views", st_ins.get("impressions", 0)) or 0)
            st_replies = int(st_ins.get("replies", 0) or 0)
            st_exits = int(st_ins.get("exits", 0) or 0)
            st_fwd = int(st_ins.get("taps_forward", 0) or 0)
            st_back = int(st_ins.get("taps_back", 0) or 0)
            comp_rate = (
                round(max(0.0, (1.0 - (st_exits / st_reach)) * 100.0), 1)
                if st_reach > 0
                else 0.0
            )
            st_rec = stories_by_ig_id.get(sid)
            if st_rec is None:
                st_rec = StoryItem(account_id=account.id, ig_media_id=sid, published_at=st_pub)
                db.add(st_rec)
            st_rec.media_type = str(st_item.get("media_type") or "IMAGE")
            st_rec.media_url = str(st_item.get("media_url") or "")
            st_rec.thumbnail_url = str(st_item.get("thumbnail_url") or st_item.get("media_url") or "")
            st_rec.permalink = str(st_item.get("permalink") or "")
            st_rec.published_at = st_pub
            st_rec.reach = st_reach
            st_rec.views = st_views
            st_rec.replies = st_replies
            st_rec.exits = st_exits
            st_rec.taps_forward = st_fwd
            st_rec.taps_back = st_back
            st_rec.completion_rate = comp_rate
            st_rec.synced_at = datetime.now(timezone.utc)
            synced_stories_count += 1

    account.last_synced_at = datetime.now(timezone.utc)
    await db.flush()

    slots = await recompute_best_time_slots(db, account)
    await db.commit()
    return {
        "synced_media_count": synced_count,
        "synced_stories_count": synced_stories_count,
        "computed_slots": len(slots),
        "followers_count": account.followers_count,
    }


async def recompute_best_time_slots(
    db: AsyncSession, account: InstagramAccount
) -> list[BestTimeSlot]:
    """Compute a 7x24 dynamic reach & engagement heatmap using the 7-stage engine."""
    now = datetime.now(timezone.utc)
    brand = await db.get(BrandProfile, account.brand_id) if account.brand_id else None
    rec = await build_account_recommendations(
        db, account, brand, allow_dynamic_niche_prior=False
    )

    await db.execute(delete(BestTimeSlot).where(BestTimeSlot.account_id == account.id))

    created_slots: list[BestTimeSlot] = []
    is_cold_uniform = rec.posts_analyzed_count == 0 and rec.prior_source == "uniform"
    for cell in rec.heatmap:
        score_100 = 0.0 if is_cold_uniform else float(cell["combined_score"])
        slot = BestTimeSlot(
            account_id=account.id,
            day_of_week=int(cell["day_of_week"]),
            hour_of_day=int(cell["hour_of_day"]),
            reach_score=float(cell["reach_score"]),
            engagement_score=round(float(cell["posterior_score"]) * 100.0, 2),
            audience_online_score=float(cell["audience_online_score"]),
            combined_score=score_100,
            sample_size=int(cell["sample_size"]),
            confidence=0.0 if is_cold_uniform else float(cell["confidence"]),
            computed_at=now,
        )
        db.add(slot)
        created_slots.append(slot)

    return created_slots


async def build_analytics_overview(
    db: AsyncSession, account: InstagramAccount
) -> dict[str, Any]:
    """Assemble real dashboard analytics, top posts, format breakdown, and hashtag lift."""
    media_rows = (
        await db.execute(
            select(MediaItem)
            .where(MediaItem.account_id == account.id)
            .order_by(MediaItem.published_at.desc())
        )
    ).scalars().all()

    slots = (
        await db.execute(
            select(BestTimeSlot)
            .where(BestTimeSlot.account_id == account.id)
            .order_by(BestTimeSlot.combined_score.desc())
        )
    ).scalars().all()

    total_posts = len(media_rows)
    total_reach = sum(m.reach for m in media_rows)
    total_likes = sum(m.likes for m in media_rows)
    total_comments = sum(m.comments for m in media_rows)
    total_shares = sum(m.shares for m in media_rows)
    total_saves = sum(m.saves for m in media_rows)
    avg_eng = _weighted_engagement_rate(media_rows, account.followers_count)
    viral_rate_pct = (
        round(((total_saves + total_shares) / total_reach) * 100.0, 2)
        if total_reach > 0
        else 0.0
    )
    trend = _compute_period_trend(media_rows, account.followers_count)

    format_groups: dict[str, list[MediaItem]] = defaultdict(list)
    hashtag_perf: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for item in media_rows:
        format_groups[item.media_type].append(item)
        item_interactions = max(0, item.likes + item.comments + item.shares + item.saves)
        for tag in item.hashtags_json or []:
            hashtag_perf[tag].append((item.reach, item_interactions))

    format_breakdown = []
    for fmt, items in format_groups.items():
        cnt = len(items)
        fmt_reach = sum(max(0, i.reach) for i in items)
        fmt_viral = sum(max(0, i.saves + i.shares) for i in items)
        format_breakdown.append(
            {
                "format": fmt,
                "count": cnt,
                "avg_reach": round(sum(i.reach for i in items) / cnt, 1),
                "avg_engagement_rate": _weighted_engagement_rate(items, account.followers_count),
                "viral_rate_pct": round((fmt_viral / fmt_reach) * 100.0, 2) if fmt_reach > 0 else 0.0,
                "avg_saves": round(sum(i.saves for i in items) / cnt, 1),
                "avg_shares": round(sum(i.shares for i in items) / cnt, 1),
            }
        )

    hashtag_leaderboard = []
    for tag, samples in hashtag_perf.items():
        cnt = len(samples)
        tag_reach = sum(max(0, s[0]) for s in samples)
        tag_inter = sum(s[1] for s in samples)
        tag_er = (
            round((tag_inter / tag_reach) * 100.0, 2)
            if tag_reach > 0
            else round((tag_inter / (max(1, account.followers_count) * cnt)) * 100.0, 2)
        )
        hashtag_leaderboard.append(
            {
                "hashtag": tag,
                "uses": cnt,
                "avg_reach": round(tag_reach / cnt, 1),
                "avg_engagement_rate": tag_er,
            }
        )
    hashtag_leaderboard.sort(key=lambda x: (x["avg_reach"], x["avg_engagement_rate"]), reverse=True)

    all_reaches = [max(0, m.reach) for m in media_rows]
    global_median_reach = float(median(all_reaches)) if all_reaches else 0.0
    format_median_reach: dict[str, float] = {}
    by_norm_fmt: dict[str, list[int]] = defaultdict(list)
    for m in media_rows:
        by_norm_fmt[_normalize_format(m.media_type)].append(max(0, m.reach))
    for n_fmt, r_vals in by_norm_fmt.items():
        format_median_reach[n_fmt] = float(median(r_vals)) if len(r_vals) >= 2 else global_median_reach

    def _format_multiplier(item: MediaItem) -> float:
        base = format_median_reach.get(_normalize_format(item.media_type), global_median_reach)
        if base <= 0:
            return 1.0 if item.reach > 0 else 0.0
        return round(item.reach / base, 2)

    top_posts = sorted(
        media_rows,
        key=lambda m: (m.reach, m.saves + m.shares, m.likes),
        reverse=True,
    )[:8]
    bottom_posts = (
        [
            {
                "id": m.id,
                "ig_media_id": m.ig_media_id,
                "media_type": m.media_type,
                "permalink": m.permalink,
                "thumbnail_url": m.thumbnail_url,
                "reach": m.reach,
                "likes": m.likes,
                "saves": m.saves,
                "shares": m.shares,
                "engagement_rate": m.engagement_rate,
                "format_multiplier": _format_multiplier(m),
                "caption": m.caption[:140],
            }
            for m in sorted(media_rows, key=lambda m: (m.reach, m.saves + m.shares))[:3]
        ]
        if total_posts >= 4
        else []
    )

    tz = _safe_zoneinfo(account.timezone)
    timeline = [
        {
            "date": (
                m.published_at.replace(tzinfo=timezone.utc)
                if m.published_at.tzinfo is None
                else m.published_at
            )
            .astimezone(tz)
            .strftime("%Y-%m-%d"),
            "reach": m.reach,
            "engagement_rate": m.engagement_rate,
            "likes": m.likes,
            "saves": m.saves,
            "shares": m.shares,
            "media_type": m.media_type,
        }
        for m in reversed(media_rows[:30])
    ]

    brand = await db.get(BrandProfile, account.brand_id) if account.brand_id else None
    rec = await build_account_recommendations(db, account, brand)
    rec_serialized = serialize_recommendation_result(rec)

    top_windows = [
        {
            "day_of_week": s["day_of_week"],
            "day_name": s["day_name"],
            "hour_of_day": s["hour_of_day"],
            "label": f"{s['day_name']} {s['hour_of_day']:02d}:00",
            "combined_score": s["combined_score"],
            "reach_score": s["reach_score"],
            "engagement_score": s["posterior_score"],
            "audience_online_score": s["audience_online_score"],
            "sample_size": s["sample_size"],
            "confidence": s["confidence"],
            "slot_type": s["slot_type"],
            "lift_vs_baseline": s["lift_vs_baseline"],
        }
        for s in sorted(
            rec_serialized["heatmap"],
            key=lambda x: (x["combined_score"], x["posterior_score"]),
            reverse=True,
        )[:10]
    ]

    heatmap = rec_serialized["heatmap"]

    latest_snap = (
        await db.execute(
            select(AudienceSnapshot)
            .where(AudienceSnapshot.account_id == account.id)
            .order_by(AudienceSnapshot.snapshot_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    sched_rows = (
        await db.execute(
            select(ScheduledPost.status).where(ScheduledPost.account_id == account.id)
        )
    ).scalars().all()
    queue_stats = {
        "queued": sum(1 for st in sched_rows if st in ("QUEUED", "PUBLISHING")),
        "published": sum(1 for st in sched_rows if st == "PUBLISHED"),
        "failed": sum(1 for st in sched_rows if st == "FAILED"),
    }

    story_rows = (
        await db.execute(
            select(StoryItem)
            .where(StoryItem.account_id == account.id)
            .order_by(StoryItem.published_at.desc())
        )
    ).scalars().all()
    story_analytics = {
        "stories_tracked": len(story_rows),
        "avg_story_reach": (
            round(sum(s.reach for s in story_rows) / len(story_rows), 1)
            if story_rows
            else 0.0
        ),
        "total_story_replies": sum(s.replies for s in story_rows),
        "avg_completion_rate_pct": (
            round(sum(s.completion_rate for s in story_rows) / len(story_rows), 1)
            if story_rows
            else 0.0
        ),
        "recent_stories": [
            {
                "ig_media_id": s.ig_media_id,
                "media_type": s.media_type,
                "thumbnail_url": s.thumbnail_url,
                "permalink": s.permalink,
                "published_at": s.published_at.isoformat(),
                "reach": s.reach,
                "views": s.views,
                "replies": s.replies,
                "exits": s.exits,
                "completion_rate": s.completion_rate,
            }
            for s in story_rows[:6]
        ],
    }

    now_utc = datetime.now(timezone.utc)
    cutoff_30d = now_utc - timedelta(days=30)
    posts_last_30d = sum(
        1
        for m in media_rows
        if (
            m.published_at.replace(tzinfo=timezone.utc)
            if m.published_at.tzinfo is None
            else m.published_at
        )
        >= cutoff_30d
    )
    days_since_last = None
    if media_rows:
        newest_dt = (
            media_rows[0].published_at.replace(tzinfo=timezone.utc)
            if media_rows[0].published_at.tzinfo is None
            else media_rows[0].published_at
        )
        days_since_last = max(0, (now_utc - newest_dt).days)

    goal_per_week = brand.posting_goal_per_week if brand and brand.posting_goal_per_week > 0 else 4
    discovery_metrics = {
        "discovery_multiplier": (
            round((total_reach / total_posts) / max(account.followers_count, 1), 1)
            if total_posts
            else 0.0
        ),
        "save_rate_pct": round((total_saves / total_reach) * 100.0, 2) if total_reach > 0 else 0.0,
        "share_rate_pct": round((total_shares / total_reach) * 100.0, 2) if total_reach > 0 else 0.0,
        "posts_last_30d": posts_last_30d,
        "actual_posts_per_week": round(posts_last_30d / (30.0 / 7.0), 1),
        "goal_posts_per_week": goal_per_week,
        "days_since_last_post": days_since_last,
    }

    profile_funnel = {
        "reach_day": latest_snap.reach_day if latest_snap else 0,
        "profile_views_day": latest_snap.profile_views_day if latest_snap else 0,
        "profile_visit_rate_pct": (
            round((latest_snap.profile_views_day / latest_snap.reach_day) * 100.0, 2)
            if latest_snap and latest_snap.reach_day > 0
            else 0.0
        ),
    }

    return {
        "account": {
            "id": account.id,
            "username": account.username,
            "name": account.name,
            "biography": account.biography,
            "website": account.website,
            "profile_picture_url": account.profile_picture_url,
            "followers_count": account.followers_count,
            "follows_count": account.follows_count,
            "media_count": account.media_count,
            "timezone": account.timezone,
            "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else None,
        },
        "account_insights": (
            {
                "reach_day": latest_snap.reach_day,
                "profile_views_day": latest_snap.profile_views_day,
                "has_online_followers": bool(latest_snap.online_followers_hourly_json),
            }
            if latest_snap
            else None
        ),
        "kpis": {
            "analyzed_posts": total_posts,
            "total_reach": total_reach,
            "avg_reach_per_post": round(total_reach / total_posts, 1) if total_posts else 0.0,
            "avg_engagement_rate": avg_eng,
            "viral_rate_pct": viral_rate_pct,
            "reach_trend_pct": trend["reach_trend_pct"],
            "er_trend_pct": trend["er_trend_pct"],
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_shares": total_shares,
            "total_saves": total_saves,
        },
        "has_historical_data": total_posts > 0,
        "content_dna": compute_content_dna(media_rows),
        "discovery_metrics": discovery_metrics,
        "profile_funnel": profile_funnel,
        "story_analytics": story_analytics,
        "format_breakdown": format_breakdown,
        "hashtag_leaderboard": hashtag_leaderboard[:15],
        "timeline": timeline,
        "top_posts": [
            {
                "id": m.id,
                "ig_media_id": m.ig_media_id,
                "media_type": m.media_type,
                "permalink": m.permalink,
                "thumbnail_url": m.thumbnail_url,
                "caption": m.caption,
                "published_at": m.published_at.isoformat(),
                "reach": m.reach,
                "likes": m.likes,
                "comments": m.comments,
                "shares": m.shares,
                "saves": m.saves,
                "plays": m.plays,
                "engagement_rate": m.engagement_rate,
                "format_multiplier": _format_multiplier(m),
            }
            for m in top_posts
        ],
        "bottom_posts": bottom_posts,
        "queue_stats": queue_stats,
        "top_windows": top_windows,
        "heatmap": heatmap,
        "recommendations": rec_serialized,
        "ai_audit": account.ai_audit_json,
    }
