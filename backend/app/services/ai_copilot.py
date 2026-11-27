"""Google Gemini AI Copilot for Brand Analysis, Account Audit, Captions, and Smart Planning."""

from datetime import datetime, timezone
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import get_token_cipher
from app.models.entities import BrandProfile, CopilotMessage, InstagramAccount, ScheduledPost
from app.services.analytics_engine import build_analytics_overview
from app.services.brand_scraper import ScrapedBrand
from app.services.gemini_helper import (
    generate_structured_content,
    generate_structured_with_usage,
    record_ai_usage,
)
from app.services.hashtag_engine import HashtagCandidate, HashtagEngine
from app.services.meta_client import MetaGraphClient
from app.services.queue_planner import recommend_slots_for_batch, summarize_existing_queue
from app.workers.publisher import publish_scheduled_post


class BrandEnrichmentOutput(BaseModel):
    brand_name: str
    niche: str
    description: str
    tone_of_voice: str
    target_audience: str
    content_pillars: list[str]
    posting_goal_per_week: int = Field(ge=1, le=14)


class AccountAuditOutput(BaseModel):
    summary: str
    winning_format: str
    best_posting_strategy: str
    top_strengths: list[str]
    missed_opportunities: list[str]
    next_7_days_action_plan: list[str]


class CaptionVariant(BaseModel):
    angle: str = Field(description="e.g., Viral Hook, Storytelling, Value Carousel")
    hook: str
    caption_body: str
    call_to_action: str


class GeneratedPostPackage(BaseModel):
    variants: list[CaptionVariant]
    recommended_caption: str
    reach_prediction_note: str
    hashtags: list[HashtagCandidate]
    hashtag_strategy_note: str


class CopilotChatAction(BaseModel):
    reply: str = Field(description="Helpful, concise answer in the user's language (English/Hinglish)")
    requested_action: str = Field(
        description="chat | draft | schedule | publish_now — use 'schedule' ONLY when user confirms scheduling, 'publish_now' ONLY when user asks to post immediately"
    )
    requested_scheduled_for_iso: str = Field(
        description="ISO-8601 datetime with timezone offset if user specified a custom date/time (e.g. 2026-11-26T15:36:00+05:30), 'best' if bot should pick optimal slot, or empty string"
    )
    use_previous_draft: bool = Field(
        description="True if user refers to the previously drafted/attached post, caption, hashtags, or media"
    )
    proposed_caption: str = Field(
        description="Ready-to-publish caption if drafting/updating a post, otherwise empty string"
    )
    proposed_hashtags: list[str] = Field(
        description="8-12 reach-boosting lowercase #hashtags relevant to the post/topic, or empty list if purely conversational"
    )
    proposed_media_type: str = Field(description="REELS | IMAGE | CAROUSEL | STORIES")
    should_recommend_best_slot: bool = Field(
        description="True if user wants posting time recommendation or scheduling, else false"
    )
    batch_topics: list[str] = Field(
        description="List of post topics if user wants multiple posts planned/queued, else empty list"
    )


class AICopilotService:
    """Orchestrates Gemini intelligence grounded in real Instagram analytics & queue state."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.hashtag_engine = HashtagEngine(self.settings)

    async def enrich_brand_from_signals(
        self,
        *,
        scraped: ScrapedBrand | None = None,
        account: InstagramAccount | None = None,
        language: str = "English",
    ) -> BrandEnrichmentOutput:
        context = {
            "scraped_website": (
                {
                    "url": scraped.website_url,
                    "name": scraped.brand_name,
                    "description": scraped.description,
                    "snippet": scraped.raw_text_snippet,
                }
                if scraped
                else None
            ),
            "instagram_profile": (
                {
                    "username": account.username,
                    "name": account.name,
                    "biography": account.biography,
                    "followers": account.followers_count,
                }
                if account
                else None
            ),
            "preferred_language": language,
        }
        prompt = (
            "Analyze the brand signals below and build a sharp Instagram Brand Kit. "
            "Recommend a realistic `posting_goal_per_week` (between 3 and 7 for most brands).\n"
            f"Signals: {json.dumps(context)}"
        )
        return await generate_structured_content(
            self.settings,
            prompt=prompt,
            response_schema=BrandEnrichmentOutput,
            temperature=0.3,
        )

    async def generate_account_audit(
        self, db: AsyncSession, account: InstagramAccount, brand: BrandProfile | None
    ) -> dict[str, Any]:
        overview = await build_analytics_overview(db, account)

        rec_info = overview.get("recommendations") or {}
        audit_input = {
            "account": overview["account"],
            "kpis": overview["kpis"],
            "account_insights": overview.get("account_insights"),
            "format_breakdown": overview["format_breakdown"],
            "content_dna": overview.get("content_dna"),
            "recommendations_summary": {
                "confidence_tier": rec_info.get("confidence_tier"),
                "posts_analyzed_count": rec_info.get("posts_analyzed_count"),
                "prior_source": rec_info.get("prior_source"),
                "min_gap_hours": rec_info.get("min_gap_hours"),
                "next_recommended_slot": rec_info.get("next_recommended_slot"),
                "weekly_schedule": (rec_info.get("weekly_schedule") or [])[:5],
                "avoid_windows": (rec_info.get("avoid_windows") or [])[:5],
            },
            "top_windows": overview["top_windows"][:5],
            "top_hashtags": overview["hashtag_leaderboard"][:10],
            "top_captions": [p["caption"][:180] for p in overview["top_posts"][:5]],
            "bottom_posts": overview.get("bottom_posts", []),
            "queue_stats": overview.get("queue_stats"),
            "brand": {
                "brand_name": brand.brand_name if brand else account.name,
                "niche": brand.niche if brand else "",
                "tone_of_voice": brand.tone_of_voice if brand else "",
                "target_audience": brand.target_audience if brand else "",
                "content_pillars": brand.content_pillars_json if brand else [],
                "posting_goal_per_week": brand.posting_goal_per_week if brand else 4,
                "language": brand.language if brand else "English",
            },
        }
        prompt = (
            "You are a senior Instagram growth strategist. Analyze ONLY the real account metrics below "
            "(never invent numbers not present in the input). Inspect `account.biography`, `account.website`, "
            "and `brand` fields accurately before claiming any profile or niche info is missing. "
            "Ground timing advice strictly in `recommendations_summary` (including `avoid_windows` and spacing), "
            "compare `top_posts` vs `bottom_posts` and `content_dna`, and give a concrete 7-day plan.\n"
            f"Metrics JSON: {json.dumps(audit_input)}"
        )
        parsed, usage = await generate_structured_with_usage(
            self.settings,
            prompt=prompt,
            response_schema=AccountAuditOutput,
            temperature=0.3,
        )
        audit = parsed.model_dump()
        audit["token_usage"] = usage
        account.ai_audit_json = audit
        record_ai_usage(db, account_id=account.id, feature="account_audit", usage=usage)
        await db.commit()
        return audit

    async def generate_post_package(
        self,
        db: AsyncSession,
        account: InstagramAccount,
        brand: BrandProfile | None,
        *,
        topic: str,
        media_type: str = "REELS",
        tone_override: str = "",
        include_hashtags_count: int = 15,
    ) -> dict[str, Any]:
        overview = await build_analytics_overview(db, account)
        slot_recs = await recommend_slots_for_batch(db, account, brand, count=1)
        best_slot = slot_recs[0] if slot_recs else None

        top_caption_examples = [p["caption"][:200] for p in overview["top_posts"][:3] if p["caption"]]
        top_historical_tags = overview["hashtag_leaderboard"][:10]
        content_dna = overview.get("content_dna") or {}
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if media_type == "STORIES":
            prompt = (
                f"Today is {today_str}. Craft 3 completely distinct, customizable Instagram Story creative concepts "
                "(on-screen hook text + visual layout direction + interactive sticker/poll/CTA idea) tailored strictly to the user's style and request below.\n"
                "Do NOT repeat one fixed template — match whatever mood, aesthetic, or custom style the user describes.\n"
                f"User Request / Story Style & Topic: {topic}\n"
                f"Brand Name: {brand.brand_name if brand else account.name}\n"
                f"Niche: {brand.niche if brand else account.biography}\n"
                f"Tone / Style Override: {tone_override or (brand.tone_of_voice if brand else 'Authentic & engaging')}\n"
                f"Language: {brand.language if brand else 'English'}\n"
                f"Account Content DNA: {json.dumps(content_dna)}\n\n"
                "Rules:\n"
                "- Put the ready-to-use on-screen Story text + sticker/CTA suggestion inside `caption_body` and `recommended_caption` (note: Meta Stories API publishes the media image/video itself, so keep text punchy for screen overlay).\n"
                f"- Include up to {min(include_hashtags_count, 5)} light niche hashtags if useful (or 0-3 subtle tags)."
            )
        else:
            prompt = (
                f"Today is {today_str}. Write 3 high-retention Instagram {media_type} caption options AND "
                f"recommend {include_hashtags_count} reach-maximizing hashtags (trending + niche + branded; "
                "avoid banned/spam tags like #love #instagood #fyp).\n"
                f"Topic/Media Description: {topic}\n"
                f"Brand Name: {brand.brand_name if brand else account.name}\n"
                f"Niche: {brand.niche if brand else account.biography}\n"
                f"Tone: {tone_override or (brand.tone_of_voice if brand else 'Authentic & engaging')}\n"
                f"Language: {brand.language if brand else 'English'}\n"
                f"Examples of this account's past top-reach captions: {top_caption_examples}\n"
                f"Account's historically top-reach hashtags: {top_historical_tags}\n"
                f"Account Content DNA (historical length/hook/hashtag lift): {json.dumps(content_dna)}\n\n"
                "Rules:\n"
                "- First line must be a scroll-stopping hook (no generic 'In today's post').\n"
                "- Include value-packed body + clear save/share/comment CTA.\n"
                "- Do NOT append hashtags inside `caption_body` or `recommended_caption` (hashtags go in `hashtags`)."
            )
        pkg, usage = await generate_structured_with_usage(
            self.settings,
            prompt=prompt,
            response_schema=GeneratedPostPackage,
            temperature=0.55,
        )
        record_ai_usage(db, account_id=account.id, feature="generate_post", usage=usage)

        ranked = await self.hashtag_engine.enrich_and_rank_candidates(
            db, account, pkg.hashtags, desired_count=include_hashtags_count
        )
        await db.commit()

        return {
            "recommended_caption": pkg.recommended_caption,
            "variants": [v.model_dump() for v in pkg.variants],
            "reach_prediction_note": pkg.reach_prediction_note,
            "hashtags": ranked["hashtags"],
            "hashtag_strategy_note": pkg.hashtag_strategy_note,
            "meta_weekly_quota_used": ranked["meta_weekly_quota_used"],
            "meta_weekly_quota_limit": ranked["meta_weekly_quota_limit"],
            "token_usage": usage,
            "recommended_slot": (
                {
                    "scheduled_for": best_slot.scheduled_for_utc.isoformat(),
                    "local_display": best_slot.local_display,
                    "combined_score": best_slot.combined_score,
                    "confidence": best_slot.confidence,
                    "rationale": best_slot.rationale,
                }
                if best_slot
                else None
            ),
        }

    async def chat_with_copilot(
        self,
        db: AsyncSession,
        account: InstagramAccount,
        brand: BrandProfile | None,
        user_message: str,
        *,
        media_urls: list[str] | None = None,
        media_type: str = "",
    ) -> dict[str, Any]:
        attached_urls = [u.strip() for u in (media_urls or []) if u.strip()]
        attached_note = (
            f" [Attached media: {media_type or 'MEDIA'} ({len(attached_urls)} file)]"
            if attached_urls
            else ""
        )
        db.add(
            CopilotMessage(
                account_id=account.id,
                role="user",
                content=user_message + attached_note,
                structured_payload_json=(
                    {"media_urls": attached_urls, "media_type": media_type}
                    if attached_urls
                    else None
                ),
            )
        )
        await db.flush()

        history_rows = (
            await db.execute(
                select(CopilotMessage)
                .where(CopilotMessage.account_id == account.id)
                .order_by(CopilotMessage.created_at.desc())
                .limit(10)
            )
        ).scalars().all()
        recent_history = [
            {"role": m.role, "content": m.content} for m in reversed(history_rows)
        ]

        # Recover most recent attached media & draft from conversation history for multi-turn continuity
        prior_media_urls: list[str] = []
        prior_media_type = ""
        prior_caption = ""
        prior_hashtags: list[str] = []
        for m in history_rows:
            payload = m.structured_payload_json or {}
            draft = payload.get("draft_post") or {}
            if not prior_media_urls:
                urls = draft.get("media_urls") or payload.get("media_urls") or []
                if urls:
                    prior_media_urls = list(urls)
                    prior_media_type = str(draft.get("media_type") or payload.get("media_type") or "")
            if not prior_caption and draft.get("caption"):
                prior_caption = str(draft["caption"])
            if not prior_hashtags and payload.get("hashtags"):
                prior_hashtags = list(payload["hashtags"])
            if prior_media_urls and prior_caption and prior_hashtags:
                break

        overview = await build_analytics_overview(db, account)
        recent_posts = (
            await db.execute(
                select(ScheduledPost)
                .where(ScheduledPost.account_id == account.id)
                .order_by(ScheduledPost.updated_at.desc())
                .limit(10)
            )
        ).scalars().all()
        queued_posts = [p for p in recent_posts if p.status in ("QUEUED", "PUBLISHING")]

        try:
            tz = ZoneInfo(account.timezone)
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("UTC")
        now_local = datetime.now(timezone.utc).astimezone(tz)
        current_local_str = now_local.strftime("%a, %b %d at %I:%M %p")
        current_local_iso = now_local.isoformat(timespec="seconds")

        recent_posts_status = []
        for p in recent_posts:
            sched_utc = (
                p.scheduled_for.replace(tzinfo=timezone.utc)
                if p.scheduled_for.tzinfo is None
                else p.scheduled_for
            )
            pub_disp = None
            if p.published_at is not None:
                pub_utc = (
                    p.published_at.replace(tzinfo=timezone.utc)
                    if p.published_at.tzinfo is None
                    else p.published_at
                )
                pub_disp = pub_utc.astimezone(tz).strftime("%a, %b %d at %I:%M %p")
            recent_posts_status.append(
                {
                    "id": p.id,
                    "media_type": p.media_type,
                    "status": p.status,
                    "scheduled_for_local": sched_utc.astimezone(tz).strftime("%a, %b %d at %I:%M %p"),
                    "published_at_local": pub_disp,
                    "published_permalink": p.published_permalink,
                    "last_error": p.last_error,
                    "caption_preview": p.caption[:100],
                }
            )
        slot_media_hint = media_type or prior_media_type or ("CAROUSEL" if len(attached_urls) > 1 else None)
        precomputed_slots = await recommend_slots_for_batch(
            db, account, brand, count=3, media_type=slot_media_hint
        )

        # Fetch up to 3 attached images so Gemini Vision can inspect the actual visual content
        media_parts: list[types.Part] = []
        image_urls = [
            u
            for u in attached_urls[:3]
            if not u.lower().split("?")[0].endswith((".mp4", ".mov"))
        ]
        if image_urls:
            async with httpx.AsyncClient(timeout=8.0) as http:
                for img_url in image_urls:
                    try:
                        img_resp = await http.get(img_url)
                        if img_resp.status_code == 200 and len(img_resp.content) <= 4_500_000:
                            ctype = (img_resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
                            if ctype.startswith("image/"):
                                media_parts.append(
                                    types.Part.from_bytes(data=img_resp.content, mime_type=ctype)
                                )
                    except Exception:
                        continue

        rec_info = overview.get("recommendations") or {}
        context = {
            "current_local_time": current_local_str,
            "current_local_iso": current_local_iso,
            "timezone": account.timezone,
            "account": overview["account"],
            "account_insights": overview.get("account_insights"),
            "kpis": overview["kpis"],
            "has_historical_data": overview["has_historical_data"],
            "confidence_tier": rec_info.get("confidence_tier", "learning"),
            "posts_analyzed_count": rec_info.get("posts_analyzed_count", 0),
            "lookback_days": rec_info.get("lookback_days", 90),
            "prior_source": rec_info.get("prior_source", "uniform"),
            "min_gap_hours": rec_info.get("min_gap_hours", 12),
            "format_breakdown": overview["format_breakdown"],
            "content_dna": overview.get("content_dna"),
            "top_posts": [
                {
                    "media_type": p["media_type"],
                    "reach": p["reach"],
                    "likes": p["likes"],
                    "comments": p["comments"],
                    "saves": p["saves"],
                    "shares": p["shares"],
                    "engagement_rate": p["engagement_rate"],
                    "published_at": p["published_at"],
                    "caption_preview": p["caption"][:120],
                }
                for p in overview.get("top_posts", [])[:5]
            ],
            "bottom_posts": overview.get("bottom_posts", []),
            "top_windows": overview["top_windows"][:5],
            "weekly_schedule": rec_info.get("weekly_schedule", [])[:7],
            "avoid_windows": rec_info.get("avoid_windows", [])[:5],
            "next_recommended_slots": [
                {
                    "local_display": s.local_display,
                    "combined_score": s.combined_score,
                    "slot_type": s.slot_type,
                    "lift_vs_baseline": s.lift_vs_baseline,
                    "rationale": s.rationale,
                }
                for s in precomputed_slots
            ],
            "top_hashtags": overview["hashtag_leaderboard"][:8],
            "queue_stats": overview.get("queue_stats"),
            "ai_audit": overview.get("ai_audit"),
            "existing_queue": summarize_existing_queue(queued_posts, account.timezone),
            "recent_posts_status": recent_posts_status,
            "attached_media": {
                "has_media": bool(attached_urls),
                "media_type": media_type or ("CAROUSEL" if len(attached_urls) > 1 else "REELS"),
                "count": len(attached_urls),
                "visual_images_provided": len(media_parts),
            },
            "previous_draft_in_chat": {
                "has_media": bool(prior_media_urls),
                "media_type": prior_media_type,
                "caption": prior_caption,
                "hashtags": prior_hashtags,
            },
            "brand": {
                "name": brand.brand_name if brand else account.name,
                "niche": brand.niche if brand else "",
                "tone": brand.tone_of_voice if brand else "",
                "language": brand.language if brand else "English",
                "posting_goal_per_week": brand.posting_goal_per_week if brand else 4,
            },
            "recent_chat": recent_history,
        }

        prompt = (
            "You are SocialMediaAI's built-in Instagram Growth & Scheduling Agent. You CAN schedule and publish posts directly when the user asks!\n"
            "STRICT LANGUAGE & TONE RULES:\n"
            "1. `reply` MUST match the exact language/style of the user's Latest User Message "
            "(if user writes English -> reply in English; if Hinglish -> reply in natural Hinglish; if Hindi -> Hindi).\n"
            "2. Explain analytics, best times, avoid hours, format performance, Content DNA, and audit findings in simple everyday creator-friendly words — NEVER use technical math/developer jargon like 'Bayesian', 'UCB', 'MAD outlier', 'n_eff', or 'dynamic_niche_prior'.\n"
            "3. `proposed_caption` MUST be written in `brand.language` (English by default unless user explicitly asks for another language).\n\n"
            "ACTION & SCHEDULING RULES (CRITICAL — NEVER CLAIM SOMETHING IS SCHEDULED UNLESS `requested_action` IS SET):\n"
            "- If the user confirms scheduling (e.g. 'post at 3:36pm today', '5pm ko post kar do', 'schedule it', 'queue kar do'), set `requested_action='schedule'` "
            "and set `requested_scheduled_for_iso` to the exact ISO-8601 timestamp using `current_local_iso` timezone (e.g. '2026-11-27T17:00:00+05:30'), or 'best' if they ask you to pick the best time. If a clock time like '5pm' already passed relative to `current_local_time` and no explicit past date was requested, schedule it for the NEXT upcoming occurrence (tomorrow at that time).\n"
            "- If the user asks to post immediately right now (e.g. 'post now', 'abhi post kar do', 'post it now'), set `requested_action='publish_now'`.\n"
            "- If the user refers to the earlier attached photo/caption/hashtags (e.g. 'same caption and hashtag', 'schedule this', 'have u posted?'), set `use_previous_draft=true`.\n"
            "- Otherwise use `requested_action='draft'` (when creating/updating a draft) or `'chat'` (for pure questions — check `recent_posts_status`, `kpis`, `format_breakdown`, `top_posts`, `bottom_posts`, `weekly_schedule`, `avoid_windows`, `content_dna`, and `ai_audit` to answer accurately with exact numbers, and return empty `proposed_caption`/`proposed_hashtags` when `requested_action='chat'`).\n\n"
            "DATA, STORY & TIMING RULES:\n"
            "- If images are attached in this turn, analyze what is actually visible in the image(s) to craft a specific, relevant caption or Story overlay idea.\n"
            "- If user asks to create/schedule/post an Instagram Story (in ANY custom style, vibe, poll, minimalist, bold, BTS, meme, product teaser, etc.), set `proposed_media_type='STORIES'` and adapt 100% creatively to their exact requested style (never force a single rigid Story template; suggest on-screen text/sticker ideas in `proposed_caption` and keep `proposed_hashtags` optional/minimal).\n"
            "- Recommend posting timing dynamically based on the content/format AND the account's real historical performance (`format_breakdown`, `top_windows`, `has_historical_data`) and `existing_queue`.\n"
            f"Context JSON: {json.dumps(context)}\n"
            f"Latest User Message: {user_message}"
        )
        action, chat_usage = await generate_structured_with_usage(
            self.settings,
            prompt=prompt,
            response_schema=CopilotChatAction,
            temperature=0.4,
            media_parts=media_parts or None,
        )
        record_ai_usage(db, account_id=account.id, feature="copilot_chat", usage=chat_usage)

        structured_payload: dict[str, Any] = {}
        effective_media_urls = attached_urls or (
            prior_media_urls
            if (action.use_previous_draft or action.requested_action in ("schedule", "publish_now"))
            else []
        )
        effective_media_type = (
            media_type
            or (prior_media_type if action.use_previous_draft and prior_media_type else "")
            or action.proposed_media_type
            or "REELS"
        )
        effective_caption = (
            prior_caption
            if (action.use_previous_draft and prior_caption and not action.proposed_caption)
            else (action.proposed_caption or prior_caption)
        )

        cleaned_tags: list[str] = []
        seen_tags: set[str] = set()
        raw_tag_source = (
            prior_hashtags
            if (action.use_previous_draft and prior_hashtags)
            else action.proposed_hashtags
        )
        for raw_tag in raw_tag_source:
            t = raw_tag.strip().lower().replace(" ", "")
            if not t:
                continue
            if not t.startswith("#"):
                t = "#" + t
            if t not in seen_tags:
                seen_tags.add(t)
                cleaned_tags.append(t)

        if action.requested_action == "chat" and not attached_urls:
            structured_payload = {}
        else:
            if cleaned_tags:
                if not (action.use_previous_draft and prior_hashtags):
                    ranked_chat = await self.hashtag_engine.enrich_and_rank_candidates(
                        db,
                        account,
                        [
                            HashtagCandidate(
                                hashtag=t,
                                tier="niche",
                                why_it_boosts_reach="Copilot-selected reach hashtag.",
                            )
                            for t in cleaned_tags
                        ],
                        desired_count=15,
                    )
                    structured_payload["hashtags"] = [
                        h["hashtag"] for h in ranked_chat["hashtags"]
                    ]
                else:
                    structured_payload["hashtags"] = cleaned_tags[:15]

            if action.should_recommend_best_slot or effective_caption or effective_media_urls:
                slot_count = max(1, len(action.batch_topics))
                slots = (
                    precomputed_slots[:slot_count]
                    if slot_count <= len(precomputed_slots)
                    else await recommend_slots_for_batch(
                        db, account, brand, count=slot_count, media_type=slot_media_hint
                    )
                )
                structured_payload["recommended_slots"] = [
                    {
                        "scheduled_for": s.scheduled_for_utc.isoformat(),
                        "local_display": s.local_display,
                        "combined_score": s.combined_score,
                        "rationale": s.rationale,
                    }
                    for s in slots
                ]
            if effective_caption or effective_media_urls:
                structured_payload["draft_post"] = {
                    "caption": effective_caption,
                    "media_type": effective_media_type,
                    "media_urls": effective_media_urls,
                }

        reply_text = action.reply
        if action.requested_action in ("schedule", "publish_now"):
            if not effective_media_urls:
                reply_text = (
                    "Note: No photo or Reel is attached yet — please attach or paste (Ctrl+V) "
                    "your media first, and then tell me when to schedule or publish it."
                )
            else:
                target_dt = datetime.now(timezone.utc)
                rationale_text = "Scheduled via AI Copilot chat."
                if action.requested_action == "schedule":
                    iso_val = action.requested_scheduled_for_iso.strip()
                    if iso_val and iso_val.lower() != "best":
                        try:
                            parsed_dt = datetime.fromisoformat(iso_val)
                            if parsed_dt.tzinfo is None:
                                parsed_dt = parsed_dt.replace(tzinfo=tz)
                            target_dt = parsed_dt.astimezone(timezone.utc)
                            rationale_text = f"Scheduled at user-requested time ({parsed_dt.astimezone(tz).strftime('%a, %b %d at %I:%M %p')})."
                        except ValueError:
                            target_dt = precomputed_slots[0].scheduled_for_utc
                            rationale_text = precomputed_slots[0].rationale
                    else:
                        target_dt = precomputed_slots[0].scheduled_for_utc
                        rationale_text = precomputed_slots[0].rationale

                post_row = ScheduledPost(
                    account_id=account.id,
                    media_type=effective_media_type,
                    media_urls_json=effective_media_urls,
                    caption=effective_caption,
                    hashtags_json=cleaned_tags[:15],
                    share_to_feed=True,
                    scheduled_for=target_dt,
                    timezone=account.timezone,
                    status="PUBLISHING" if action.requested_action == "publish_now" else "QUEUED",
                    ai_rationale=rationale_text,
                    predicted_score=precomputed_slots[0].combined_score if precomputed_slots else 0.0,
                )
                db.add(post_row)
                await db.commit()
                await db.refresh(post_row)

                sched_utc = (
                    post_row.scheduled_for.replace(tzinfo=timezone.utc)
                    if post_row.scheduled_for.tzinfo is None
                    else post_row.scheduled_for
                )
                local_disp = sched_utc.astimezone(tz).strftime("%a, %b %d at %I:%M %p")

                if action.requested_action == "publish_now":
                    post_row = await publish_scheduled_post(
                        db,
                        post_row,
                        account,
                        meta_client=MetaGraphClient(self.settings),
                        cipher=get_token_cipher(),
                        settings=self.settings,
                        write_copilot_notification=False,
                    )
                    if post_row.status == "PUBLISHED":
                        link_note = f"\nLink: {post_row.published_permalink}" if post_row.published_permalink else ""
                        reply_text = f"{action.reply.rstrip()}{link_note}" if action.reply.strip() else f"Published live on Instagram!{link_note}"
                    else:
                        reply_text = f"Publish failed: {post_row.last_error}"
                else:
                    reply_text = (
                        action.reply
                        if action.reply.strip()
                        else f"Scheduled in your queue for {local_disp} ({account.timezone})."
                    )

                structured_payload["scheduled_post"] = {
                    "id": post_row.id,
                    "status": post_row.status,
                    "scheduled_for": sched_utc.isoformat(),
                    "local_display": local_disp,
                    "published_permalink": post_row.published_permalink,
                    "last_error": post_row.last_error,
                }

        structured_payload["token_usage"] = chat_usage

        assistant_msg = CopilotMessage(
            account_id=account.id,
            role="assistant",
            content=reply_text,
            structured_payload_json=structured_payload or None,
        )
        db.add(assistant_msg)
        await db.commit()

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": reply_text,
            "structured_payload": structured_payload or None,
            "created_at": assistant_msg.created_at.isoformat(),
        }
