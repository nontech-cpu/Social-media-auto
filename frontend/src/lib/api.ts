import type {
  AIUsageSummary,
  AnalyticsOverview,
  BrandProfile,
  CopilotMessage,
  GeneratedPostPackage,
  InstagramAccount,
  PublishActivityEvent,
  ScheduledPost,
} from "./types";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const payload = await res.json();
      detail = payload.detail || payload.message || detail;
    } catch {
      // ignore json parse failure
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  baseUrl: API_BASE,

  health: () =>
    apiRequest<{
      status: string;
      environment: string;
      media_storage_provider: string;
      meta_configured: boolean;
      gemini_configured: boolean;
    }>("/api/health"),

  // Onboarding & Brand Kit
  getBrand: () => apiRequest<{ brand: BrandProfile | null }>("/api/onboarding/brand"),
  fetchBrandFromWebsite: (website_url: string, language: string) =>
    apiRequest<{ brand: BrandProfile }>("/api/onboarding/fetch-brand", {
      method: "POST",
      body: JSON.stringify({ website_url, language }),
    }),
  saveBrand: (brand: Partial<BrandProfile> & { brand_name: string }) =>
    apiRequest<{ brand: BrandProfile }>("/api/onboarding/brand", {
      method: "PUT",
      body: JSON.stringify(brand),
    }),

  // Instagram OAuth & Accounts
  getInstagramOAuthUrl: (brandId = "", timezoneName?: string) => {
    const tz =
      timezoneName ||
      (typeof Intl !== "undefined"
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : "UTC") ||
      "UTC";
    return apiRequest<{ oauth_url: string; state: string }>(
      `/api/auth/instagram/url?brand_id=${encodeURIComponent(brandId)}&timezone_name=${encodeURIComponent(tz)}`
    );
  },
  listAccounts: () => apiRequest<{ accounts: InstagramAccount[] }>("/api/auth/accounts"),
  updateTimezone: (accountId: string, timezone: string) =>
    apiRequest<{ id: string; timezone: string }>(`/api/auth/accounts/${accountId}/timezone`, {
      method: "PATCH",
      body: JSON.stringify({ timezone }),
    }),
  disconnectAccount: (accountId: string) =>
    apiRequest<{ deleted: boolean }>(`/api/auth/accounts/${accountId}`, { method: "DELETE" }),

  // Analytics & Audit
  syncAccount: (accountId: string) =>
    apiRequest<{ synced: boolean; synced_media_count: number; computed_slots: number }>(
      `/api/analytics/${accountId}/sync`,
      { method: "POST" }
    ),
  getOverview: (accountId: string) =>
    apiRequest<AnalyticsOverview>(`/api/analytics/${accountId}/overview`),
  runAudit: (accountId: string) =>
    apiRequest<{ audit: AnalyticsOverview["ai_audit"] }>(`/api/analytics/${accountId}/audit`, {
      method: "POST",
    }),

  // Media Upload
  uploadMedia: async (
    file: File,
    targetFormat?: "IMAGE" | "REELS" | "CAROUSEL" | "STORIES",
    accountId?: string
  ) => {
    const form = new FormData();
    form.append("file", file);
    const params = new URLSearchParams();
    if (targetFormat) params.set("target_format", targetFormat);
    if (accountId) params.set("account_id", accountId);
    const qs = params.toString();
    const query = qs ? `?${qs}` : "";
    return apiRequest<{
      asset_id?: string;
      url: string;
      media_type: "IMAGE" | "REELS";
      filename: string;
      size_bytes: number;
      provider: string;
      is_public_https: boolean;
      width?: number | null;
      height?: number | null;
      aspect_ratio?: number | null;
      duration_seconds?: number | null;
      warnings?: string[];
    }>(`/api/media/upload${query}`, { method: "POST", body: form });
  },

  // AI Copilot & Batch Planner
  generatePostPackage: (
    accountId: string,
    payload: {
      topic: string;
      media_type: "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
      tone_override?: string;
      hashtags_count?: number;
    }
  ) =>
    apiRequest<GeneratedPostPackage>(`/api/ai/${accountId}/generate-post`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  planBatch: (
    accountId: string,
    payload: {
      items: Array<{
        topic: string;
        media_type: "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
        media_urls: string[];
      }>;
      min_gap_hours?: number;
      generate_copy?: boolean;
    }
  ) =>
    apiRequest<{
      account_id: string;
      timezone: string;
      planned_items: Array<{
        index: number;
        topic: string;
        media_type: "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
        media_urls: string[];
        recommended_caption: string;
        hashtags: Array<{ hashtag: string; tier: string; why_it_boosts_reach: string }>;
        reach_prediction_note: string;
        scheduled_for: string;
        local_display: string;
        combined_score: number;
        confidence: number;
        rationale: string;
      }>;
    }>(`/api/ai/${accountId}/plan-batch`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getChatHistory: (accountId: string) =>
    apiRequest<{ messages: CopilotMessage[] }>(`/api/ai/${accountId}/chat`),
  getAiUsage: (accountId: string) =>
    apiRequest<AIUsageSummary>(`/api/ai/${accountId}/usage`),
  sendChatMessage: (
    accountId: string,
    message: string,
    media?: {
      media_urls?: string[];
      media_type?: "" | "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
    }
  ) =>
    apiRequest<CopilotMessage>(`/api/ai/${accountId}/chat`, {
      method: "POST",
      body: JSON.stringify({
        message,
        media_urls: media?.media_urls || [],
        media_type: media?.media_type || "",
      }),
    }),

  // Posts & Queue
  listPosts: (accountId: string) =>
    apiRequest<{ posts: ScheduledPost[] }>(`/api/posts/${accountId}`),
  getPublishActivity: (accountId: string) =>
    apiRequest<{ events: PublishActivityEvent[] }>(`/api/posts/${accountId}/activity`),
  schedulePosts: (
    accountId: string,
    posts: Array<{
      media_type: "IMAGE" | "REELS" | "CAROUSEL" | "STORIES";
      media_urls: string[];
      cover_url?: string;
      caption: string;
      hashtags: string[];
      share_to_feed?: boolean;
      scheduled_for?: string | null;
      status?: "DRAFT" | "QUEUED";
      ai_rationale?: string;
      predicted_score?: number;
    }>
  ) =>
    apiRequest<{ posts: ScheduledPost[] }>(`/api/posts/${accountId}/schedule`, {
      method: "POST",
      body: JSON.stringify({ posts, auto_space_missing_times: true }),
    }),
  publishNow: (
    accountId: string,
    post: {
      media_type: "IMAGE" | "REELS" | "CAROUSEL" | "STORIES";
      media_urls: string[];
      cover_url?: string;
      caption: string;
      hashtags: string[];
      share_to_feed?: boolean;
      ai_rationale?: string;
    }
  ) =>
    apiRequest<{ post: ScheduledPost }>(`/api/posts/${accountId}/publish-now`, {
      method: "POST",
      body: JSON.stringify(post),
    }),
  updatePost: (
    postId: string,
    patch: Partial<{
      caption: string;
      hashtags: string[];
      media_urls: string[];
      scheduled_for: string;
      status: "DRAFT" | "QUEUED";
    }>
  ) =>
    apiRequest<{ post: ScheduledPost }>(`/api/posts/item/${postId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deletePost: (postId: string) =>
    apiRequest<{ deleted: boolean }>(`/api/posts/item/${postId}`, { method: "DELETE" }),
};
