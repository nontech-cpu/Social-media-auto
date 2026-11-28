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
from app.models.entities import (
    BrandProfile,
    CopilotMessage,
    InstagramAccount,
    MediaAsset,
    ScheduledPost,
)
from app.services.analytics_engine import build_analytics_overview
from app.services.brand_scraper import ScrapedBrand
from app.services.gemini_helper import (
    generate_agent_turn_with_tools,
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
    reply: str = Field(
        description=(
            "Proportional, crisp answer matching ONLY what the user asked (no fluff/filler, not too terse), "
            "STRICTLY in the exact language of the Latest User Message"
        )
    )
    requested_action: str = Field(
        description="chat | draft | schedule | publish_now — use 'schedule' ONLY when user explicitly commands scheduling, 'publish_now' ONLY when user explicitly commands immediate posting"
    )
    requested_scheduled_for_iso: str = Field(
        description="ISO-8601 datetime with timezone offset if user specified a custom date/time (e.g. 2026-11-26T15:36:00+05:30), 'best' if bot should pick optimal slot, or empty string"
    )
    use_previous_draft: bool = Field(
        description="True if user refers to the previously drafted/attached post, caption, hashtags, or media"
    )
    selected_media_refs: list[str] = Field(
        default_factory=list,
        description="Asset refs like ['M1'] from available_media when user picks/schedules specific uploaded media (e.g. the .mp4 video)",
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


def _infer_media_ext_and_kind(
    url: str, filename: str = "", media_type: str = ""
) -> tuple[str, str]:
    """Infer file extension and media kind ('video' | 'image') cleanly without regex."""
    candidate = (filename or url.split("?")[0]).strip().lower()
    ext = ""
    if "." in candidate:
        raw_ext = "." + candidate.rsplit(".", 1)[-1]
        if len(raw_ext) <= 8:
            ext = raw_ext
    video_exts = (".mp4", ".mov", ".m4v", ".webm")
    is_video = ext in video_exts or "/video/" in url.lower() or media_type == "REELS"
    if not ext:
        ext = ".mp4" if is_video else ".jpg"
    return ext, ("video" if is_video else "image")


_HINGLISH_MARKERS = frozenset(
    {
        "kya",
        "kyu",
        "kyun",
        "nahi",
        "nhi",
        "hai",
        "aur",
        "bhi",
        "karo",
        "krdo",
        "karna",
        "krna",
        "kro",
        "btao",
        "batao",
        "merko",
        "mereko",
        "mujhko",
        "apna",
        "apne",
        "aapke",
        "abhi",
        "kab",
        "kaise",
        "konsa",
        "konsi",
        "bhai",
        "bhy",
        "yaar",
        "chahiye",
        "chaiye",
        "hogya",
        "mein",
        "toh",
        "woh",
        "yeh",
        "iska",
        "uska",
        "thoda",
        "zyada",
        "jayda",
        "achha",
        "theek",
        "baje",
        "kal",
        "aaj",
        "subah",
        "shaam",
        "kitne",
        "kitna",
        "daal",
        "daalni",
        "dalni",
        "lagado",
        "karde",
        "samjhao",
        "likho",
    }
)


def _detect_message_language_rule(text: str) -> str:
    """Detect script/Hinglish cues without regex so Copilot mirrors the latest message language."""
    for ch in text:
        if "\u0900" <= ch <= "\u097f":
            return "Hindi (Devanagari script — reply strictly in Hindi)"
    tokens = "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
    if any(tok in _HINGLISH_MARKERS for tok in tokens):
        return "Hinglish (user wrote Romanized Hindi — reply naturally in Hinglish)"
    return (
        "EXACT same language as Latest User Message (if English -> 100% pure English with ZERO Hinglish words; "
        "if German -> 100% German; if French/Spanish/other -> that exact language). "
        "NEVER mirror Hinglish or another language from earlier chat history turns."
    )


def _needs_structured_action_fallback(text: str, attached_urls: list[str]) -> bool:
    if attached_urls:
        return True
    lower = text.lower()
    action_cues = (
        "schedule",
        "publish",
        "queue",
        "post now",
        "draft",
        "caption",
        "hashtag",
        "story",
        "stories",
        "best time",
        "daal",
        "lagao",
        "kar do",
        "kr do",
        "krdo",
        "post kar",
        "post kr",
    )
    return any(cue in lower for cue in action_cues)


_COPILOT_TOOLS: list[types.Tool] = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_recent_posts",
                description=(
                    "Fetch recent published, failed, and queued/scheduled Instagram posts PLUS all recently uploaded media files "
                    "(with refs M1/M2, filename, .mp4/.jpg extension, and status). Call when user asks about posts or uploaded media."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"limit": types.Schema(type=types.Type.INTEGER)},
                ),
            ),
            types.FunctionDeclaration(
                name="get_analytics_summary",
                description=(
                    "Fetch reach KPIs, engagement rate, discovery multiplier, save/share rates, format breakdown, "
                    "Content DNA, top/bottom posts, profile funnel, and story analytics. "
                    "Call this ONLY when user asks about performance, analytics, insights, or growth metrics."
                ),
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="get_best_time_slots",
                description=(
                    "Fetch ranked optimal posting time windows and windows to avoid. "
                    "Call this when user asks when to post or asks you to pick the best time."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "count": types.Schema(type=types.Type.INTEGER),
                        "media_type": types.Schema(type=types.Type.STRING),
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="get_account_audit_and_hashtags",
                description=(
                    "Fetch AI account audit summary, next 7-day plan, and top historical hashtags. "
                    "Call this when user asks for strategy/audit advice or hashtag recommendations."
                ),
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="complete_response",
                description=(
                    "Submit your final structured Copilot response (use this to draft captions, schedule posts, "
                    "publish immediately, recommend slots, or finish a structured answer)."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "reply": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "Balanced, proportional reply (exact facts asked, zero filler/fluff, not bloated or missing details) "
                                "STRICTLY in the exact language of Latest User Message."
                            ),
                        ),
                        "requested_action": types.Schema(
                            type=types.Type.STRING,
                            description="chat | draft | schedule | publish_now",
                        ),
                        "requested_scheduled_for_iso": types.Schema(type=types.Type.STRING),
                        "use_previous_draft": types.Schema(type=types.Type.BOOLEAN),
                        "selected_media_refs": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description="Refs like ['M1'] from available_media when selecting specific uploaded photo(s)/video(s).",
                        ),
                        "proposed_caption": types.Schema(type=types.Type.STRING),
                        "proposed_hashtags": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                        ),
                        "proposed_media_type": types.Schema(
                            type=types.Type.STRING,
                            description="REELS | IMAGE | CAROUSEL | STORIES",
                        ),
                        "should_recommend_best_slot": types.Schema(type=types.Type.BOOLEAN),
                        "batch_topics": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                        ),
                    },
                    required=["reply", "requested_action"],
                ),
            ),
        ]
    )
]


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
        # Keep only the previous 4 short turns (excluding just-flushed current msg) to limit tokens & prevent language drift
        recent_history = [
            {"role": m.role, "text": m.content[:150]}
            for m in reversed(history_rows[1:5])
        ]

        try:
            tz = ZoneInfo(account.timezone)
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("UTC")
        now_local = datetime.now(timezone.utc).astimezone(tz)
        current_local_str = now_local.strftime("%a, %b %d at %I:%M %p")
        current_local_iso = now_local.isoformat(timespec="seconds")

        # Persist any current-turn attached URLs into MediaAsset if not already linked to this account
        if attached_urls:
            existing_assets = (
                await db.execute(
                    select(MediaAsset).where(MediaAsset.url.in_(attached_urls))
                )
            ).scalars().all()
            by_url = {a.url: a for a in existing_assets}
            for u in attached_urls:
                inferred_type = media_type or (
                    "CAROUSEL" if len(attached_urls) > 1 else "REELS"
                )
                ext, _ = _infer_media_ext_and_kind(u, "", inferred_type)
                existing_row = by_url.get(u)
                if existing_row is not None:
                    if existing_row.account_id is None:
                        existing_row.account_id = account.id
                else:
                    db.add(
                        MediaAsset(
                            account_id=account.id,
                            url=u,
                            media_type="REELS" if ext in (".mp4", ".mov", ".m4v", ".webm") else (media_type or "IMAGE"),
                            file_ext=ext,
                        )
                    )
            await db.flush()

        # Recover draft continuity & full persistent multi-turn media registry (not clipped at 10 messages!)
        media_ctx = await self.collect_available_media_assets(
            db,
            account=account,
            tz=tz,
            attached_urls=attached_urls,
            attached_media_type=media_type,
        )
        available_media = media_ctx["available_media"]
        ref_to_asset = {m["ref"]: m for m in available_media}
        prior_media_urls = media_ctx["prior_media_urls"]
        prior_media_type = media_ctx["prior_media_type"]
        prior_caption = media_ctx["prior_caption"]
        prior_hashtags = media_ctx["prior_hashtags"]

        slot_media_hint = media_type or prior_media_type or ("CAROUSEL" if len(attached_urls) > 1 else None)

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

        # Level 3 lazy per-turn caches: DB analytics / posts / slots load ONLY when Gemini calls a tool or needs slots
        turn_cache: dict[str, Any] = {
            "overview": None,
            "slots": None,
            "recent_posts": None,
            "tool_results": {},
        }

        async def _ensure_overview() -> dict[str, Any]:
            if turn_cache["overview"] is None:
                turn_cache["overview"] = await build_analytics_overview(db, account)
            return turn_cache["overview"]

        async def _ensure_slots(req_count: int = 3, req_media_type: str | None = None) -> list[Any]:
            hint = req_media_type or slot_media_hint
            if turn_cache["slots"] is None or len(turn_cache["slots"]) < req_count:
                turn_cache["slots"] = await recommend_slots_for_batch(
                    db, account, brand, count=max(3, req_count), media_type=hint
                )
            return turn_cache["slots"]

        async def _ensure_recent_posts(limit: int = 8) -> list[ScheduledPost]:
            if turn_cache["recent_posts"] is None:
                turn_cache["recent_posts"] = (
                    await db.execute(
                        select(ScheduledPost)
                        .where(ScheduledPost.account_id == account.id)
                        .order_by(ScheduledPost.updated_at.desc())
                        .limit(max(8, min(limit, 20)))
                    )
                ).scalars().all()
            return turn_cache["recent_posts"]

        async def _tool_executor(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
            result = await self.execute_copilot_tool(
                tool_name=tool_name,
                args=args,
                account=account,
                brand=brand,
                tz=tz,
                ensure_recent_posts=_ensure_recent_posts,
                ensure_overview=_ensure_overview,
                ensure_slots=_ensure_slots,
                available_media=available_media,
            )
            turn_cache["tool_results"][tool_name] = result
            return result

        # Level 2: Static cache-friendly prefix in system_instruction (never invalidated by timestamps/history)
        static_profile = {
            "username": account.username,
            "name": account.name,
            "followers": account.followers_count,
            "timezone": account.timezone,
            "brand_name": brand.brand_name if brand else account.name,
            "niche": brand.niche if brand else "",
            "tone": brand.tone_of_voice if brand else "",
            "caption_language": brand.language if brand else "English",
            "goal_posts_per_week": brand.posting_goal_per_week if brand else 4,
        }
        static_system_instruction = (
            "You are SocialMediaAI's Instagram Growth & Scheduling Copilot. You CAN schedule and publish posts directly.\n"
            f"Account & Brand Profile (static): {json.dumps(static_profile)}\n\n"
            "CRITICAL LANGUAGE & RESPONSE LENGTH RULES (GOLDILOCKS PRECISION):\n"
            "1. `reply` language MUST match ONLY the Latest User Message's language (English -> pure English with ZERO Hinglish; "
            "German -> German; French -> French; Hindi -> Hindi; Hinglish -> Hinglish). NEVER switch language because of older chat turns.\n"
            "2. RESPONSE SIZING: Give ONLY as much detail as the user asked for — neither bloated nor missing asked details:\n"
            "   - Simple status / yes-no / timing / confirmation question: 1-2 direct sentences with exact status/time/link. ZERO greetings, ZERO unsolicited advice, ZERO 'let me know if you need anything else'.\n"
            "   - Multi-item status / media inventory / analytics question: 2-4 crisp bullets or short lines with exact numbers/formats/file types.\n"
            "   - Caption / Story / strategy / audit request: Complete ready-to-use output without padding.\n"
            "3. Explain insights in plain everyday creator words — NEVER use developer/math jargon like 'Bayesian', 'UCB', 'MAD outlier', 'n_eff', or 'dynamic_niche_prior'.\n"
            "4. `proposed_caption` MUST be in `caption_language` unless user asks for another caption language.\n\n"
            "MEDIA MEMORY, TOOL & ACTION RULES:\n"
            "- NEVER guess post statuses, metrics, or slots — call `get_recent_posts`, `get_analytics_summary`, `get_best_time_slots`, or `get_account_audit_and_hashtags` when data is needed.\n"
            "- `available_media` lists uploaded photos/videos persistently across turns (`M1`, `M2`, ... with `.mp4`/`.jpg` & status). When user asks about previously sent files (e.g. '2 media', '.mp4 video'), check `available_media` or call `get_recent_posts`.\n"
            "- ONLY set `requested_action='schedule'` or `'publish_now'` when user explicitly commands scheduling/posting. If user is asking about/clarifying uploaded media, set `requested_action='chat'`.\n"
            "- When user selects/schedules specific uploaded file(s) (e.g. 'post the mp4 video'), set `selected_media_refs=['M1']` matching exact ref(s) from `available_media`.\n"
            "- For `schedule`, convert clock times using `current_local_iso` timezone (if today's clock time passed, use tomorrow), or `'best'` if user asks for optimal time.\n"
            "- For Instagram Stories (`proposed_media_type='STORIES'`), adapt 100% creatively to any requested style/vibe/poll/CTA and put overlay/CTA ideas in `proposed_caption`."
        )

        # Level 1: Minimal dynamic turn payload (~300-500 tokens vs ~8k full dump)
        turn_payload: dict[str, Any] = {
            "recent_chat": recent_history,
        }
        if available_media:
            turn_payload["available_media"] = [
                {
                    "ref": m["ref"],
                    "type": m["media_type"],
                    "ext": m["ext"],
                    "kind": m["kind"],
                    "status": m["status"],
                }
                for m in available_media[:6]
            ]
        if attached_urls:
            turn_payload["attached_media"] = {
                "media_type": media_type or ("CAROUSEL" if len(attached_urls) > 1 else "REELS"),
                "count": len(attached_urls),
                "visual_images_provided": len(media_parts),
            }
        if prior_media_urls or prior_caption:
            turn_payload["previous_draft_in_chat"] = {
                "has_media": bool(prior_media_urls),
                "media_type": prior_media_type,
                "caption_preview": prior_caption[:220],
                "hashtags": prior_hashtags[:8],
            }
        strict_lang_rule = _detect_message_language_rule(user_message)
        turn_payload["current_local_time"] = current_local_str
        turn_payload["current_local_iso"] = current_local_iso

        user_prompt_text = (
            f"Turn Context JSON: {json.dumps(turn_payload)}\n"
            f"Latest User Message: {user_message}\n"
            f"Mandatory Reply Language Lock: {strict_lang_rule}"
        )

        finish_args, plain_reply, chat_usage = await generate_agent_turn_with_tools(
            self.settings,
            system_instruction=static_system_instruction,
            user_text=user_prompt_text,
            tools=_COPILOT_TOOLS,
            tool_executor=_tool_executor,
            temperature=0.35,
            media_parts=media_parts or None,
        )

        action: CopilotChatAction
        if finish_args is not None:
            action = CopilotChatAction(
                reply=str(finish_args.get("reply") or plain_reply or ""),
                requested_action=str(finish_args.get("requested_action") or "chat"),
                requested_scheduled_for_iso=str(finish_args.get("requested_scheduled_for_iso") or ""),
                use_previous_draft=bool(finish_args.get("use_previous_draft", False)),
                selected_media_refs=[
                    str(r).strip().upper()
                    for r in (finish_args.get("selected_media_refs") or [])
                    if str(r).strip()
                ],
                proposed_caption=str(finish_args.get("proposed_caption") or ""),
                proposed_hashtags=[str(t) for t in (finish_args.get("proposed_hashtags") or [])],
                proposed_media_type=str(
                    finish_args.get("proposed_media_type")
                    or media_type
                    or prior_media_type
                    or "REELS"
                ),
                should_recommend_best_slot=bool(
                    finish_args.get("should_recommend_best_slot", False)
                ),
                batch_topics=[str(t) for t in (finish_args.get("batch_topics") or [])],
            )
        elif plain_reply and not _needs_structured_action_fallback(user_message, attached_urls):
            action = CopilotChatAction(
                reply=plain_reply,
                requested_action="chat",
                requested_scheduled_for_iso="",
                use_previous_draft=False,
                selected_media_refs=[],
                proposed_caption="",
                proposed_hashtags=[],
                proposed_media_type=media_type or prior_media_type or "REELS",
                should_recommend_best_slot=False,
                batch_topics=[],
            )
        else:
            fallback_prompt = (
                f"Turn Context JSON: {json.dumps(turn_payload)}\n"
                f"Fetched Tool Data: {json.dumps(turn_cache['tool_results'])}\n"
                f"Latest User Message: {user_message}\n"
                f"Mandatory Reply Language Lock: {strict_lang_rule}"
            )
            action, fb_usage = await generate_structured_with_usage(
                self.settings,
                prompt=fallback_prompt,
                system_instruction=static_system_instruction,
                response_schema=CopilotChatAction,
                temperature=0.35,
                media_parts=media_parts or None,
            )
            chat_usage["prompt_tokens"] += fb_usage["prompt_tokens"]
            chat_usage["output_tokens"] += fb_usage["output_tokens"]
            chat_usage["thoughts_tokens"] += fb_usage["thoughts_tokens"]
            chat_usage["cached_tokens"] += fb_usage.get("cached_tokens", 0)
            chat_usage["total_tokens"] += fb_usage["total_tokens"]
            chat_usage["model"] = fb_usage["model"]

        record_ai_usage(db, account_id=account.id, feature="copilot_chat", usage=chat_usage)

        selected_assets = [
            ref_to_asset[r.strip().upper()]
            for r in action.selected_media_refs
            if r.strip().upper() in ref_to_asset
        ]
        selected_urls = [a["url"] for a in selected_assets]
        unscheduled_fallback = [
            m["url"] for m in available_media if m["status"] == "chat_unscheduled"
        ]

        structured_payload: dict[str, Any] = {}
        effective_media_urls = (
            attached_urls
            or selected_urls
            or (
                prior_media_urls or unscheduled_fallback[:1]
                if (action.use_previous_draft or action.requested_action in ("schedule", "publish_now"))
                else []
            )
        )
        selected_media_type_hint = (
            "STORIES"
            if action.proposed_media_type == "STORIES"
            else (
                "CAROUSEL"
                if len(selected_assets) > 1
                else (selected_assets[0]["media_type"] if len(selected_assets) == 1 else "")
            )
        )
        effective_media_type = (
            media_type
            or selected_media_type_hint
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
                slots = (await _ensure_slots(slot_count, slot_media_hint))[:slot_count]
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
                if available_media:
                    summary = ", ".join(
                        f"{m['ref']} ({m['media_type']} {m['ext']}, {m['status']})"
                        for m in available_media[:4]
                    )
                    reply_text = (
                        f"Please specify which media to schedule/publish ({summary}), "
                        "or attach a new photo/video."
                    )
                else:
                    reply_text = (
                        "No photo or Reel is attached yet — please attach or paste (Ctrl+V) "
                        "your media first, then tell me when to schedule or publish it."
                    )
            else:
                target_dt = datetime.now(timezone.utc)
                rationale_text = "Scheduled via AI Copilot chat."
                predicted_slot_score = 0.0
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
                            slots = await _ensure_slots(1, slot_media_hint)
                            target_dt = slots[0].scheduled_for_utc
                            rationale_text = slots[0].rationale
                            predicted_slot_score = slots[0].combined_score
                    else:
                        slots = await _ensure_slots(1, slot_media_hint)
                        target_dt = slots[0].scheduled_for_utc
                        rationale_text = slots[0].rationale
                        predicted_slot_score = slots[0].combined_score

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
                    predicted_score=predicted_slot_score,
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

    async def collect_available_media_assets(
        self,
        db: AsyncSession,
        *,
        account: InstagramAccount,
        tz: ZoneInfo,
        attached_urls: list[str],
        attached_media_type: str = "",
    ) -> dict[str, Any]:
        """Build deduplicated multi-source media memory (MediaAsset + up to 60 media-bearing msgs + ScheduledPosts)."""
        asset_rows = (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.account_id == account.id)
                .order_by(MediaAsset.created_at.desc())
                .limit(25)
            )
        ).scalars().all()
        msg_rows = (
            await db.execute(
                select(CopilotMessage)
                .where(
                    CopilotMessage.account_id == account.id,
                    CopilotMessage.structured_payload_json.isnot(None),
                )
                .order_by(CopilotMessage.created_at.desc())
                .limit(60)
            )
        ).scalars().all()
        post_rows = (
            await db.execute(
                select(ScheduledPost)
                .where(ScheduledPost.account_id == account.id)
                .order_by(ScheduledPost.updated_at.desc())
                .limit(15)
            )
        ).scalars().all()

        url_to_post_status: dict[str, dict[str, Any]] = {}
        for p in post_rows:
            sched_utc = (
                p.scheduled_for.replace(tzinfo=timezone.utc)
                if p.scheduled_for.tzinfo is None
                else p.scheduled_for
            )
            sched_disp = sched_utc.astimezone(tz).strftime("%a, %b %d at %I:%M %p")
            for u in p.media_urls_json or []:
                if u not in url_to_post_status:
                    url_to_post_status[u] = {
                        "status": p.status,
                        "scheduled_for_local": sched_disp,
                        "published_permalink": p.published_permalink,
                        "media_type": p.media_type,
                    }

        ordered_entries: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        def _add_entry(
            url: str,
            m_type: str = "",
            filename: str = "",
            status_override: str = "",
            created_at: datetime | None = None,
        ) -> None:
            clean_url = (url or "").strip()
            if not clean_url or clean_url in seen_urls:
                return
            seen_urls.add(clean_url)
            post_meta = url_to_post_status.get(clean_url)
            eff_type = m_type or (post_meta["media_type"] if post_meta else "")
            ext, kind = _infer_media_ext_and_kind(clean_url, filename, eff_type)
            if not eff_type:
                eff_type = "REELS" if kind == "video" else "IMAGE"
            status_val = status_override or (
                post_meta["status"] if post_meta else "chat_unscheduled"
            )
            disp_time = None
            if created_at is not None:
                c_utc = (
                    created_at.replace(tzinfo=timezone.utc)
                    if created_at.tzinfo is None
                    else created_at
                )
                disp_time = c_utc.astimezone(tz).strftime("%a, %b %d at %I:%M %p")
            ordered_entries.append(
                {
                    "ref": f"M{len(ordered_entries) + 1}",
                    "url": clean_url,
                    "media_type": eff_type,
                    "ext": ext,
                    "kind": kind,
                    "filename": filename,
                    "status": status_val,
                    "uploaded_at_local": disp_time,
                    "scheduled_for_local": post_meta["scheduled_for_local"] if post_meta else None,
                    "published_permalink": post_meta["published_permalink"] if post_meta else "",
                }
            )

        for u in attached_urls:
            _add_entry(u, m_type=attached_media_type, status_override="just_attached")

        for a in asset_rows:
            _add_entry(
                a.url,
                m_type=a.media_type,
                filename=a.filename,
                created_at=a.created_at,
            )

        prior_media_urls: list[str] = []
        prior_media_type = ""
        prior_caption = ""
        prior_hashtags: list[str] = []
        for m in msg_rows:
            payload = m.structured_payload_json or {}
            draft = payload.get("draft_post") or {}
            msg_urls = draft.get("media_urls") or payload.get("media_urls") or []
            msg_type = str(draft.get("media_type") or payload.get("media_type") or "")
            if msg_urls and not prior_media_urls:
                prior_media_urls = list(msg_urls)
                prior_media_type = msg_type
            for u in msg_urls:
                _add_entry(u, m_type=msg_type, created_at=m.created_at)
            if not prior_caption and draft.get("caption"):
                prior_caption = str(draft["caption"])
            if not prior_hashtags and payload.get("hashtags"):
                prior_hashtags = list(payload["hashtags"])

        for p in post_rows:
            for u in p.media_urls_json or []:
                _add_entry(u, m_type=p.media_type, created_at=p.created_at)

        return {
            "available_media": ordered_entries[:8],
            "prior_media_urls": prior_media_urls,
            "prior_media_type": prior_media_type,
            "prior_caption": prior_caption,
            "prior_hashtags": prior_hashtags,
        }

    async def execute_copilot_tool(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        account: InstagramAccount,
        brand: BrandProfile | None,
        tz: ZoneInfo,
        ensure_recent_posts: Any,
        ensure_overview: Any,
        ensure_slots: Any,
        available_media: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Dispatch Level 3 on-demand Copilot data tools cleanly without loading unused context."""
        if tool_name == "get_recent_posts":
            limit = int(args.get("limit") or 8)
            rows = await ensure_recent_posts(limit)
            queued = [p for p in rows if p.status in ("QUEUED", "PUBLISHING")]
            formatted = []
            for p in rows[:limit]:
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
                formatted.append(
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
            uploaded_media = [
                {
                    "ref": m["ref"],
                    "media_type": m["media_type"],
                    "ext": m["ext"],
                    "kind": m["kind"],
                    "filename": m.get("filename") or "",
                    "status": m["status"],
                    "scheduled_for_local": m.get("scheduled_for_local"),
                    "published_permalink": m.get("published_permalink") or "",
                }
                for m in (available_media or [])
            ]
            return {
                "posts": formatted,
                "uploaded_media": uploaded_media,
                "existing_queue": summarize_existing_queue(queued, account.timezone),
            }

        if tool_name == "get_analytics_summary":
            ov = await ensure_overview()
            return {
                "kpis": ov["kpis"],
                "account_insights": ov.get("account_insights"),
                "discovery_metrics": ov.get("discovery_metrics"),
                "profile_funnel": ov.get("profile_funnel"),
                "story_analytics": {
                    k: v
                    for k, v in (ov.get("story_analytics") or {}).items()
                    if k != "recent_stories"
                },
                "format_breakdown": ov["format_breakdown"],
                "content_dna": ov.get("content_dna"),
                "top_posts": [
                    {
                        "media_type": p["media_type"],
                        "reach": p["reach"],
                        "engagement_rate": p["engagement_rate"],
                        "format_multiplier": p.get("format_multiplier"),
                        "caption_preview": p["caption"][:100],
                    }
                    for p in ov.get("top_posts", [])[:3]
                ],
                "bottom_posts": [
                    {
                        "media_type": p["media_type"],
                        "reach": p["reach"],
                        "format_multiplier": p.get("format_multiplier"),
                        "caption_preview": p["caption"][:80],
                    }
                    for p in ov.get("bottom_posts", [])[:2]
                ],
            }

        if tool_name == "get_best_time_slots":
            count = max(1, min(int(args.get("count") or 3), 7))
            req_media = str(args.get("media_type") or "").strip() or None
            slots = await ensure_slots(count, req_media)
            ov = await ensure_overview()
            rec_info = ov.get("recommendations") or {}
            return {
                "confidence_tier": rec_info.get("confidence_tier", "learning"),
                "posts_analyzed_count": rec_info.get("posts_analyzed_count", 0),
                "min_gap_hours": rec_info.get("min_gap_hours", 12),
                "recommended_slots": [
                    {
                        "local_display": s.local_display,
                        "combined_score": s.combined_score,
                        "slot_type": s.slot_type,
                        "lift_vs_baseline": s.lift_vs_baseline,
                        "rationale": s.rationale,
                    }
                    for s in slots[:count]
                ],
                "avoid_windows": rec_info.get("avoid_windows", [])[:4],
            }

        if tool_name == "get_account_audit_and_hashtags":
            ov = await ensure_overview()
            audit = ov.get("ai_audit") or {}
            return {
                "audit_summary": audit.get("summary", ""),
                "winning_format": audit.get("winning_format", ""),
                "best_posting_strategy": audit.get("best_posting_strategy", ""),
                "next_7_days_action_plan": (audit.get("next_7_days_action_plan") or [])[:4],
                "top_hashtags": [
                    {"hashtag": h["hashtag"], "avg_reach": h.get("account_avg_reach")}
                    for h in ov.get("hashtag_leaderboard", [])[:8]
                ],
            }

        return {"error": f"Unknown tool: {tool_name}"}
