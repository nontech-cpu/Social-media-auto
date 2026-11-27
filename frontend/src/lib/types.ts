export interface BrandProfile {
  id: string;
  website_url: string;
  brand_name: string;
  niche: string;
  description: string;
  tone_of_voice: string;
  target_audience: string;
  language: string;
  logo_url: string;
  colors: string[];
  content_pillars: string[];
  posting_goal_per_week: number;
}

export interface InstagramAccount {
  id: string;
  brand_id: string | null;
  ig_user_id: string;
  fb_page_name: string;
  username: string;
  name: string;
  biography: string;
  website: string;
  profile_picture_url: string;
  followers_count: number;
  media_count: number;
  timezone: string;
  last_synced_at: string | null;
  token_expires_at: string | null;
}

export interface BestTimeWindow {
  day_of_week: number;
  day_name: string;
  hour_of_day: number;
  label: string;
  combined_score: number;
  reach_score: number;
  engagement_score: number;
  audience_online_score: number;
  sample_size: number;
  confidence: number;
  slot_type?: "proven" | "benchmark" | "exploration";
  lift_vs_baseline?: number;
}

export interface HeatmapCell {
  day_of_week: number;
  day_name: string;
  hour_of_day: number;
  combined_score: number;
  reach_score: number;
  audience_online_score: number;
  sample_size: number;
  confidence: number;
  slot_type?: "proven" | "benchmark" | "exploration";
  lift_vs_baseline?: number;
}

export interface RecommendedSlotDetail {
  datetime_utc: string;
  datetime_local: string;
  local_display: string;
  day_of_week: number;
  day_name: string;
  hour_of_day: number;
  time_label: string;
  score: number;
  display_score_100: number;
  slot_type: "proven" | "benchmark" | "exploration";
  sample_count: number;
  n_eff: number;
  lift_vs_baseline: number;
  reason: string;
}

export interface RecommendationPayload {
  confidence_tier: "learning" | "calibrating" | "high_confidence";
  posts_analyzed_count: number;
  lookback_days: number;
  min_gap_hours: number;
  timezone: string;
  prior_source: string;
  next_recommended_slot: RecommendedSlotDetail | null;
  weekly_schedule: RecommendedSlotDetail[];
  avoid_windows: Array<{
    day_of_week: number;
    day_name: string;
    hour_of_day: number;
    label: string;
    lift_vs_baseline: number;
    sample_size: number;
    reason: string;
  }>;
}

export interface MediaInsightItem {
  id: string;
  ig_media_id: string;
  media_type: string;
  permalink: string;
  thumbnail_url: string;
  caption: string;
  published_at: string;
  reach: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  plays: number;
  engagement_rate: number;
  format_multiplier?: number;
}

export interface AccountAudit {
  summary: string;
  winning_format: string;
  best_posting_strategy: string;
  top_strengths: string[];
  missed_opportunities: string[];
  next_7_days_action_plan: string[];
}

export interface AnalyticsOverview {
  account: {
    id: string;
    username: string;
    name: string;
    profile_picture_url: string;
    followers_count: number;
    media_count: number;
    timezone: string;
    last_synced_at: string | null;
  };
  kpis: {
    analyzed_posts: number;
    total_reach: number;
    avg_reach_per_post: number;
    avg_engagement_rate: number;
    viral_rate_pct?: number;
    reach_trend_pct?: number | null;
    er_trend_pct?: number | null;
    total_likes: number;
    total_comments: number;
    total_shares: number;
    total_saves: number;
  };
  has_historical_data: boolean;
  format_breakdown: Array<{
    format: string;
    count: number;
    avg_reach: number;
    avg_engagement_rate: number;
    avg_saves: number;
    avg_shares: number;
  }>;
  hashtag_leaderboard: Array<{
    hashtag: string;
    uses: number;
    avg_reach: number;
    avg_engagement_rate: number;
  }>;
  timeline: Array<{
    date: string;
    reach: number;
    engagement_rate: number;
    likes: number;
    saves: number;
    shares: number;
    media_type: string;
  }>;
  top_posts: MediaInsightItem[];
  bottom_posts?: Array<{
    id?: string;
    ig_media_id: string;
    media_type: string;
    permalink?: string;
    thumbnail_url?: string;
    reach: number;
    likes?: number;
    saves?: number;
    shares?: number;
    engagement_rate: number;
    format_multiplier?: number;
    caption: string;
  }>;
  top_windows: BestTimeWindow[];
  heatmap: HeatmapCell[];
  recommendations?: RecommendationPayload | null;
  content_dna?: {
    has_data?: boolean;
    best_caption_length_bucket?: string | null;
    best_hashtag_count_bucket?: string | null;
    question_hook_avg_reach?: number | null;
    statement_hook_avg_reach?: number | null;
    caption_length_performance?: Record<
      string,
      { count: number; avg_reach: number; avg_save_share_rate: number }
    >;
    hashtag_count_performance?: Record<string, { count: number; avg_reach: number }>;
  } | null;
  discovery_metrics?: {
    discovery_multiplier: number;
    save_rate_pct: number;
    share_rate_pct: number;
    posts_last_30d: number;
    actual_posts_per_week: number;
    goal_posts_per_week: number;
    days_since_last_post: number | null;
  } | null;
  profile_funnel?: {
    reach_day: number;
    profile_views_day: number;
    profile_visit_rate_pct: number;
  } | null;
  story_analytics?: {
    stories_tracked: number;
    avg_story_reach: number;
    total_story_replies: number;
    avg_completion_rate_pct: number;
    recent_stories: Array<{
      ig_media_id: string;
      media_type: string;
      thumbnail_url: string;
      permalink: string;
      published_at: string;
      reach: number;
      views: number;
      replies: number;
      exits: number;
      completion_rate: number;
    }>;
  } | null;
  ai_audit: AccountAudit | null;
}

export interface HashtagItem {
  hashtag: string;
  tier: string;
  why_it_boosts_reach: string;
  account_avg_reach: number | null;
  historical_lift?: number | null;
  meta_top_media_engagement: number | null;
  meta_validated: boolean;
  rankability?: string;
  rankability_fit_ratio?: number | null;
}

export interface GeneratedPostPackage {
  recommended_caption: string;
  variants: Array<{
    angle: string;
    hook: string;
    caption_body: string;
    call_to_action: string;
  }>;
  reach_prediction_note: string;
  hashtags: HashtagItem[];
  hashtag_strategy_note: string;
  meta_weekly_quota_used: number;
  meta_weekly_quota_limit: number;
  recommended_slot: {
    scheduled_for: string;
    local_display: string;
    combined_score: number;
    confidence: number;
    rationale: string;
  } | null;
}

export interface ScheduledPost {
  id: string;
  account_id: string;
  media_type: "IMAGE" | "REELS" | "CAROUSEL" | "STORIES";
  media_urls: string[];
  cover_url: string;
  caption: string;
  hashtags: string[];
  share_to_feed: boolean;
  scheduled_for: string;
  timezone: string;
  status: "DRAFT" | "QUEUED" | "PUBLISHING" | "PUBLISHED" | "FAILED";
  ai_rationale: string;
  predicted_score: number;
  ig_container_id: string;
  ig_media_id: string;
  published_permalink: string;
  published_at: string | null;
  retry_count: number;
  last_error: string;
}

export interface PublishActivityEvent {
  id: string;
  account_id: string;
  username: string;
  media_type: "IMAGE" | "REELS" | "CAROUSEL" | "STORIES";
  status: "PUBLISHED" | "FAILED";
  caption_preview: string;
  published_permalink: string;
  last_error: string;
  scheduled_for: string;
  published_at: string | null;
  updated_at: string;
}

export interface CopilotMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  structured_payload?: {
    draft_post?: {
      caption: string;
      media_type: "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
      media_urls?: string[];
    };
    media_urls?: string[];
    media_type?: "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
    hashtags?: string[];
    recommended_slots?: Array<{
      scheduled_for: string;
      local_display: string;
      combined_score: number;
      rationale: string;
    }>;
    scheduled_post?: {
      id: string;
      status: string;
      scheduled_for: string;
      local_display: string;
      published_permalink?: string;
      last_error?: string;
    };
  } | null;
  created_at: string;
}
