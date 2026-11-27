"""Background publisher & sync worker compatible with APScheduler and Render cron ticks."""

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import TokenCipher, get_token_cipher
from app.models.entities import CopilotMessage, InstagramAccount, ScheduledPost
from app.services.analytics_engine import sync_account_and_compute_slots
from app.services.meta_client import MetaAPIError, MetaGraphClient


def compose_final_caption(caption: str, hashtags: list[str]) -> str:
    """Append selected hashtags cleanly without duplicating tags already in caption."""
    base = (caption or "").strip()
    existing_lower = {
        token.strip().lower()
        for token in base.replace("\n", " ").split(" ")
        if token.strip().startswith("#")
    }
    additions: list[str] = []
    for raw in hashtags or []:
        tag = raw.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        if tag.lower() not in existing_lower:
            existing_lower.add(tag.lower())
            additions.append(tag)
    if not additions:
        return base
    if not base:
        return " ".join(additions)
    return f"{base}\n\n{' '.join(additions)}"


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, MetaAPIError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


async def publish_scheduled_post(
    db: AsyncSession,
    post: ScheduledPost,
    account: InstagramAccount,
    *,
    meta_client: MetaGraphClient,
    cipher: TokenCipher,
    settings: Settings | None = None,
) -> ScheduledPost:
    """Publish a single post to Instagram using Meta's 2-step container flow."""
    cfg = settings or get_settings()
    media_urls = [u.strip() for u in (post.media_urls_json or []) if u.strip()]
    if not media_urls:
        post.status = "FAILED"
        post.last_error = "No media URL attached to scheduled post."
        await db.commit()
        return post

    non_public = [
        u for u in media_urls if not u.startswith("https://") or "localhost" in u or "127.0.0.1" in u
    ]
    if non_public:
        post.status = "FAILED"
        post.last_error = (
            "Instagram Graph API requires public HTTPS media URLs. Configure Cloudinary or S3/R2 "
            "in .env (MEDIA_STORAGE_PROVIDER=cloudinary) before publishing live."
        )
        await db.commit()
        return post

    token = cipher.decrypt(account.encrypted_access_token)
    full_caption = compose_final_caption(post.caption, post.hashtags_json or [])

    try:
        container_id = await meta_client.create_media_container(
            account.ig_user_id,
            token,
            media_type=post.media_type,
            media_urls=media_urls,
            caption=full_caption,
            share_to_feed=post.share_to_feed,
            cover_url=post.cover_url or "",
        )
        post.ig_container_id = container_id
        await db.flush()

        await meta_client.poll_container_until_ready(container_id, token, timeout_seconds=180)
        published = await meta_client.publish_container(account.ig_user_id, container_id, token)

        post.ig_media_id = published["ig_media_id"]
        post.published_permalink = published.get("permalink", "")
        post.status = "PUBLISHED"
        post.published_at = datetime.now(timezone.utc)
        post.last_error = ""

        try:
            tz = ZoneInfo(account.timezone)
        except (ZoneInfoNotFoundError, KeyError):
            tz = ZoneInfo("UTC")
        local_disp = post.published_at.astimezone(tz).strftime("%a, %b %d at %I:%M %p")
        db.add(
            CopilotMessage(
                account_id=account.id,
                role="assistant",
                content=(
                    f"Auto-published your {post.media_type} to @{account.username} at {local_disp}!"
                    + (f"\nLink: {post.published_permalink}" if post.published_permalink else "")
                ),
                structured_payload_json={
                    "scheduled_post": {
                        "id": post.id,
                        "status": "PUBLISHED",
                        "scheduled_for": post.published_at.isoformat(),
                        "local_display": local_disp,
                        "published_permalink": post.published_permalink,
                        "last_error": "",
                    }
                },
            )
        )
        await db.commit()
        return post
    except Exception as exc:
        post.retry_count += 1
        post.last_error = str(exc)
        if _is_transient_error(exc) and post.retry_count <= cfg.max_publish_retries:
            backoff_minutes = 2**post.retry_count
            post.status = "QUEUED"
            post.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=backoff_minutes)
        else:
            post.status = "FAILED"
            db.add(
                CopilotMessage(
                    account_id=account.id,
                    role="assistant",
                    content=f"Scheduled {post.media_type} failed to publish: {post.last_error}",
                    structured_payload_json={
                        "scheduled_post": {
                            "id": post.id,
                            "status": "FAILED",
                            "scheduled_for": post.scheduled_for.isoformat(),
                            "local_display": post.scheduled_for.isoformat(),
                            "published_permalink": "",
                            "last_error": post.last_error,
                        }
                    },
                )
            )
        await db.commit()
        return post


async def recover_stale_publishing_locks(
    db: AsyncSession, settings: Settings
) -> int:
    """Recover posts stuck in PUBLISHING after a server crash or restart."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.stale_publish_lock_minutes)
    stuck_posts = (
        await db.execute(
            select(ScheduledPost).where(
                ScheduledPost.status == "PUBLISHING",
                ScheduledPost.updated_at <= cutoff,
            )
        )
    ).scalars().all()

    recovered = 0
    for post in stuck_posts:
        post.retry_count += 1
        if post.retry_count <= settings.max_publish_retries:
            backoff_minutes = 2**post.retry_count
            post.status = "QUEUED"
            post.scheduled_for = now + timedelta(minutes=backoff_minutes)
            post.last_error = "Recovered stale PUBLISHING lock after worker interruption."
        else:
            post.status = "FAILED"
            post.last_error = "Exceeded retry limit after stale PUBLISHING lock recovery."
            db.add(
                CopilotMessage(
                    account_id=post.account_id,
                    role="assistant",
                    content=f"Scheduled {post.media_type} failed after worker interruption: {post.last_error}",
                )
            )
        recovered += 1

    if recovered > 0:
        await db.commit()
    return recovered


async def process_due_posts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    meta_client: MetaGraphClient | None = None,
    cipher: TokenCipher | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Atomically claim and publish all QUEUED posts whose scheduled_for <= now."""
    cfg = settings or get_settings()
    client = meta_client or MetaGraphClient(cfg)
    tok_cipher = cipher or get_token_cipher()
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        recovered_stale = await recover_stale_publishing_locks(db, cfg)
        due_ids = (
            await db.execute(
                select(ScheduledPost.id)
                .where(
                    ScheduledPost.status == "QUEUED",
                    ScheduledPost.scheduled_for <= now,
                )
                .order_by(ScheduledPost.scheduled_for.asc())
                .limit(cfg.worker_batch_limit)
            )
        ).scalars().all()

    published_count = 0
    failed_count = 0

    for post_id in due_ids:
        async with session_factory() as db:
            # Atomic transition ensures two overlapping ticks never double-publish the same post
            claim_stmt = (
                update(ScheduledPost)
                .where(ScheduledPost.id == post_id, ScheduledPost.status == "QUEUED")
                .values(status="PUBLISHING", updated_at=datetime.now(timezone.utc))
            )
            res = await db.execute(claim_stmt)
            await db.commit()
            if res.rowcount == 0:
                continue

            post = await db.get(ScheduledPost, post_id)
            if post is None:
                continue
            account = await db.get(InstagramAccount, post.account_id)
            if account is None or not account.is_active:
                post.status = "FAILED"
                post.last_error = "Connected Instagram account is inactive or missing."
                await db.commit()
                failed_count += 1
                continue

            updated = await publish_scheduled_post(
                db,
                post,
                account,
                meta_client=client,
                cipher=tok_cipher,
                settings=cfg,
            )
            if updated.status == "PUBLISHED":
                published_count += 1
            elif updated.status == "FAILED":
                failed_count += 1

    return {
        "recovered_stale": recovered_stale,
        "checked_due": len(due_ids),
        "published": published_count,
        "failed": failed_count,
    }


async def refresh_expiring_account_token(
    db: AsyncSession,
    account: InstagramAccount,
    *,
    meta_client: MetaGraphClient,
    cipher: TokenCipher,
    settings: Settings,
) -> bool:
    """Proactively renew 60-day Meta tokens before they expire."""
    if account.token_expires_at is None:
        return False
    exp = (
        account.token_expires_at.replace(tzinfo=timezone.utc)
        if account.token_expires_at.tzinfo is None
        else account.token_expires_at
    )
    remaining = exp - datetime.now(timezone.utc)
    if remaining > timedelta(days=settings.token_refresh_threshold_days):
        return False

    try:
        current_tok = cipher.decrypt(account.encrypted_access_token)
        new_tok, new_exp = await meta_client.refresh_long_lived_token(current_tok)
        account.encrypted_access_token = cipher.encrypt(new_tok)
        account.token_expires_at = new_exp
        await db.commit()
        return True
    except Exception as exc:
        db.add(
            CopilotMessage(
                account_id=account.id,
                role="assistant",
                content=(
                    f"Action needed: Failed to auto-refresh Meta access token for @{account.username} ({exc}). "
                    "Please reconnect Instagram under Setup & Brand."
                ),
            )
        )
        await db.commit()
        return False


async def sync_all_active_accounts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    meta_client: MetaGraphClient | None = None,
    cipher: TokenCipher | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    client = meta_client or MetaGraphClient(cfg)
    tok_cipher = cipher or get_token_cipher()
    async with session_factory() as db:
        accounts = (
            await db.execute(
                select(InstagramAccount).where(InstagramAccount.is_active.is_(True))
            )
        ).scalars().all()

    synced = 0
    refreshed_tokens = 0
    errors: list[str] = []
    for acc in accounts:
        async with session_factory() as db:
            fresh_acc = await db.get(InstagramAccount, acc.id)
            if fresh_acc is None:
                continue
            try:
                if await refresh_expiring_account_token(
                    db, fresh_acc, meta_client=client, cipher=tok_cipher, settings=cfg
                ):
                    refreshed_tokens += 1
                await sync_account_and_compute_slots(db, fresh_acc, client, tok_cipher)
                synced += 1
            except Exception as exc:
                errors.append(f"@{fresh_acc.username}: {exc}")
    return {"synced_accounts": synced, "refreshed_tokens": refreshed_tokens, "errors": errors}
