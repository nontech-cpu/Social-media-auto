"""SQLAlchemy ORM models for Brand Kits, Instagram Accounts, Insights, Hashtags, and Queue."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class BrandProfile(Base):
    __tablename__ = "brand_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    website_url: Mapped[str] = mapped_column(String(500), default="")
    brand_name: Mapped[str] = mapped_column(String(200), default="My Brand")
    niche: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tone_of_voice: Mapped[str] = mapped_column(String(200), default="Authentic, engaging, value-driven")
    target_audience: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(50), default="English")
    logo_url: Mapped[str] = mapped_column(String(1000), default="")
    colors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_pillars_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    posting_goal_per_week: Mapped[int] = mapped_column(Integer, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    accounts: Mapped[list["InstagramAccount"]] = relationship(
        back_populates="brand", cascade="all, delete-orphan"
    )


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("brand_profiles.id", ondelete="SET NULL"), nullable=True
    )
    ig_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fb_page_id: Mapped[str] = mapped_column(String(64), default="")
    fb_page_name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(150), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    biography: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(500), default="")
    profile_picture_url: Mapped[str] = mapped_column(String(1000), default="")
    followers_count: Mapped[int] = mapped_column(Integer, default=0)
    follows_count: Mapped[int] = mapped_column(Integer, default=0)
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_audit_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    brand: Mapped[BrandProfile | None] = relationship(back_populates="accounts")
    media_items: Mapped[list["MediaItem"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    audience_snapshots: Mapped[list["AudienceSnapshot"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    best_time_slots: Mapped[list["BestTimeSlot"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint("account_id", "ig_media_id", name="uq_account_ig_media"),
        Index("ix_media_account_published", "account_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    ig_media_id: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(32), default="IMAGE")
    media_product_type: Mapped[str] = mapped_column(String(32), default="FEED")
    permalink: Mapped[str] = mapped_column(String(1000), default="")
    media_url: Mapped[str] = mapped_column(String(1500), default="")
    thumbnail_url: Mapped[str] = mapped_column(String(1500), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    plays: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0.0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[InstagramAccount] = relationship(back_populates="media_items")


class AudienceSnapshot(Base):
    __tablename__ = "audience_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    online_followers_hourly_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    reach_day: Mapped[int] = mapped_column(Integer, default=0)
    profile_views_day: Mapped[int] = mapped_column(Integer, default=0)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)

    account: Mapped[InstagramAccount] = relationship(back_populates="audience_snapshots")


class BestTimeSlot(Base):
    __tablename__ = "best_time_slots"
    __table_args__ = (
        UniqueConstraint("account_id", "day_of_week", "hour_of_day", name="uq_account_day_hour"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=Monday .. 6=Sunday
    hour_of_day: Mapped[int] = mapped_column(Integer)  # 0..23
    reach_score: Mapped[float] = mapped_column(Float, default=0.0)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)
    audience_online_score: Mapped[float] = mapped_column(Float, default=0.0)
    combined_score: Mapped[float] = mapped_column(Float, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[InstagramAccount] = relationship(back_populates="best_time_slots")


class HashtagCache(Base):
    __tablename__ = "hashtag_cache"
    __table_args__ = (
        UniqueConstraint("account_id", "hashtag_name", name="uq_account_hashtag_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    hashtag_name: Mapped[str] = mapped_column(String(120), index=True)
    ig_hashtag_id: Mapped[str] = mapped_column(String(64), default="")
    top_media_avg_engagement: Mapped[float] = mapped_column(Float, default=0.0)
    top_media_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    sample_top_captions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_queried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = (Index("ix_scheduled_status_for", "status", "scheduled_for"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    media_type: Mapped[str] = mapped_column(
        String(24), default="IMAGE"
    )  # IMAGE | REELS | CAROUSEL | STORIES
    media_urls_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    cover_url: Mapped[str] = mapped_column(String(1500), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    share_to_feed: Mapped[bool] = mapped_column(default=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[str] = mapped_column(
        String(24), default="QUEUED", index=True
    )  # DRAFT | QUEUED | PUBLISHING | PUBLISHED | FAILED
    ai_rationale: Mapped[str] = mapped_column(Text, default="")
    predicted_score: Mapped[float] = mapped_column(Float, default=0.0)
    ig_container_id: Mapped[str] = mapped_column(String(64), default="")
    ig_media_id: Mapped[str] = mapped_column(String(64), default="")
    published_permalink: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    account: Mapped[InstagramAccount] = relationship(back_populates="scheduled_posts")


class CopilotMessage(Base):
    __tablename__ = "copilot_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    structured_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NichePriorCache(Base):
    __tablename__ = "niche_prior_cache"
    __table_args__ = (UniqueConstraint("niche_key", "timezone", name="uq_niche_timezone_prior"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    niche_key: Mapped[str] = mapped_column(String(200), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    prior_grid_json: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoryItem(Base):
    __tablename__ = "story_items"
    __table_args__ = (
        UniqueConstraint("account_id", "ig_media_id", name="uq_account_ig_story"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), index=True
    )
    ig_media_id: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(24), default="IMAGE")
    media_url: Mapped[str] = mapped_column(String(1500), default="")
    thumbnail_url: Mapped[str] = mapped_column(String(1500), default="")
    permalink: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    replies: Mapped[int] = mapped_column(Integer, default=0)
    exits: Mapped[int] = mapped_column(Integer, default=0)
    taps_forward: Mapped[int] = mapped_column(Integer, default=0)
    taps_back: Mapped[int] = mapped_column(Integer, default=0)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    feature: Mapped[str] = mapped_column(String(48), index=True)
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thoughts_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("instagram_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(String(1500), index=True)
    media_type: Mapped[str] = mapped_column(String(24), default="IMAGE")
    filename: Mapped[str] = mapped_column(String(260), default="")
    file_ext: Mapped[str] = mapped_column(String(16), default="")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
