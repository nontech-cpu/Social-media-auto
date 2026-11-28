"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Bot,
  Calculator,
  CheckCircle2,
  Eye,
  Globe,
  Loader2,
  MapPin,
  MessageSquareHeart,
  MousePointerClick,
  Navigation,
  PhoneCall,
  RefreshCw,
  Send,
  Sparkles,
  Star,
  Store,
  TrendingUp,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { pickInitialAccountId, saveSelectedAccountId } from "@/lib/selectedAccount";
import type {
  GoogleBusinessOverview,
  GoogleBusinessProfileSummary,
  GoogleBusinessReviewItem,
  InstagramAccount,
} from "@/lib/types";
import { Navbar } from "@/components/Navbar";

function formatGrowthBadge(pct: number | null | undefined): {
  label: string;
  tone: string;
} {
  if (pct === null || pct === undefined) {
    return { label: "Baseline building", tone: "text-slate-400 border-white/10 bg-white/5" };
  }
  if (pct > 0) {
    return {
      label: `+${pct.toFixed(1)}% vs prior 30d`,
      tone: "text-emerald-300 border-emerald-500/30 bg-emerald-500/10",
    };
  }
  if (pct < 0) {
    return {
      label: `${pct.toFixed(1)}% vs prior 30d`,
      tone: "text-rose-300 border-rose-500/30 bg-rose-500/10",
    };
  }
  return { label: "0.0% vs prior 30d", tone: "text-slate-300 border-white/15 bg-white/5" };
}

function GoogleBusinessWorkspace() {
  const searchParams = useSearchParams();
  const [igAccounts, setIgAccounts] = useState<InstagramAccount[]>([]);
  const [selectedIgId, setSelectedIgId] = useState("");
  const [profiles, setProfiles] = useState<GoogleBusinessProfileSummary[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [overview, setOverview] = useState<GoogleBusinessOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [showFormulas, setShowFormulas] = useState(true);
  const [reviewFilter, setReviewFilter] = useState<"ALL" | "UNREPLIED" | "REPLIED">("ALL");
  const [draftInputs, setDraftInputs] = useState<Record<string, string>>({});
  const [toneInputs, setToneInputs] = useState<Record<string, string>>({});
  const [busyReviewId, setBusyReviewId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [statusNote, setStatusNote] = useState("");

  // GBP AI Copilot Drawer state
  const [copilotOpen, setCopilotOpen] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [chatMessages, setChatMessages] = useState<
    Array<{
      role: "user" | "assistant";
      text: string;
      tokenUsage?: {
        model: string;
        prompt_tokens: number;
        output_tokens: number;
        total_tokens: number;
        tool_calls?: string[];
      };
    }>
  >([]);

  const loadOverview = useCallback(async (profileId: string) => {
    setLoading(true);
    setError("");
    try {
      const data = await api.getGoogleBusinessOverview(profileId);
      setOverview(data);
      setDraftInputs((prev) => {
        const next = { ...prev };
        for (const r of data.reviews) {
          if (next[r.id] === undefined) {
            next[r.id] = r.reply_comment || r.ai_suggested_reply || "";
          }
        }
        return next;
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load Google Business Profile overview."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const queryProfId = searchParams.get("profile_id") || "";
    const justConnected = searchParams.get("connected") === "1";
    if (justConnected) {
      setStatusNote("Google Business Profile connected and synced.");
    }

    Promise.all([
      api.listAccounts().catch(() => ({ accounts: [] })),
      api.listGoogleBusinessProfiles().catch(() => ({ profiles: [] })),
    ])
      .then(([igRes, gbpRes]) => {
        setIgAccounts(igRes.accounts || []);
        if ((igRes.accounts || []).length > 0) {
          setSelectedIgId(pickInitialAccountId(igRes.accounts));
        }
        const list = gbpRes.profiles || [];
        setProfiles(list);
        if (list.length > 0) {
          const picked =
            (queryProfId && list.find((p) => p.id === queryProfId)?.id) || list[0].id;
          setSelectedProfileId(picked);
          loadOverview(picked);
        } else {
          setLoading(false);
        }
      })
      .catch(() => setLoading(false));
  }, [loadOverview, searchParams]);

  async function handleConnectGoogleBusiness() {
    setError("");
    setConnecting(true);
    try {
      const { oauth_url } = await api.getGoogleBusinessOAuthUrl("");
      window.location.href = oauth_url;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to initialize Google Business login."
      );
      setConnecting(false);
    }
  }

  async function handleSync() {
    if (!selectedProfileId) return;
    setSyncing(true);
    setError("");
    setStatusNote("");
    try {
      const res = await api.syncGoogleBusinessProfile(selectedProfileId);
      await loadOverview(selectedProfileId);
      setStatusNote(
        `Synced ${res.synced_days} days & ${res.synced_reviews} reviews` +
          (res.auto_replied > 0 ? ` • Auto-replied ${res.auto_replied}` : "") +
          (res.drafts_prepared > 0 ? ` • ${res.drafts_prepared} draft(s) ready` : "")
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  async function handleSettingsChange(
    patch: Partial<{ auto_reply_enabled: boolean; auto_reply_min_stars: number }>
  ) {
    if (!selectedProfileId || !overview) return;
    setSavingSettings(true);
    setError("");
    try {
      const updated = await api.updateGoogleBusinessSettings(selectedProfileId, patch);
      setOverview((prev) =>
        prev
          ? {
              ...prev,
              profile: {
                ...prev.profile,
                auto_reply_enabled: updated.auto_reply_enabled,
                auto_reply_min_stars: updated.auto_reply_min_stars,
              },
            }
          : prev
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save auto-reply settings.");
    } finally {
      setSavingSettings(false);
    }
  }

  async function handleDraftReply(review: GoogleBusinessReviewItem) {
    if (!selectedProfileId) return;
    setBusyReviewId(review.id);
    setError("");
    try {
      const res = await api.draftGoogleBusinessReviewReply(
        selectedProfileId,
        review.id,
        toneInputs[review.id] || ""
      );
      setDraftInputs((prev) => ({ ...prev, [review.id]: res.reply_text }));
      setOverview((prev) =>
        prev
          ? {
              ...prev,
              reviews: prev.reviews.map((r) =>
                r.id === review.id
                  ? {
                      ...r,
                      ai_suggested_reply: res.reply_text,
                      reply_status: r.reply_comment ? r.reply_status : "DRAFT_READY",
                    }
                  : r
              ),
            }
          : prev
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI draft generation failed.");
    } finally {
      setBusyReviewId(null);
    }
  }

  async function handlePublishReply(review: GoogleBusinessReviewItem) {
    if (!selectedProfileId) return;
    const text = (draftInputs[review.id] || "").trim();
    if (!text) {
      setError("Enter or generate a reply before publishing to Google.");
      return;
    }
    setBusyReviewId(review.id);
    setError("");
    try {
      const res = await api.publishGoogleBusinessReviewReply(selectedProfileId, review.id, text);
      setOverview((prev) =>
        prev
          ? {
              ...prev,
              reviews: prev.reviews.map((r) =>
                r.id === review.id
                  ? {
                      ...r,
                      reply_comment: res.reply_comment,
                      reply_status: "REPLIED",
                      reply_updated_at: res.reply_updated_at,
                    }
                  : r
              ),
            }
          : prev
      );
      setStatusNote(`Published reply to ${review.reviewer_name}'s review live on Google.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to post reply on Google.");
    } finally {
      setBusyReviewId(null);
    }
  }

  async function handleSendCopilot(overrideMsg?: string) {
    if (!selectedProfileId || chatSending) return;
    const text = (overrideMsg ?? chatInput).trim();
    if (!text) return;
    if (!overrideMsg) setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", text }]);
    setChatSending(true);
    try {
      const res = await api.chatGoogleBusinessCopilot(selectedProfileId, text);
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", text: res.reply, tokenUsage: res.token_usage },
      ]);
      await loadOverview(selectedProfileId);
    } catch (err) {
      setChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: err instanceof Error ? err.message : "Google Business Copilot failed.",
        },
      ]);
    } finally {
      setChatSending(false);
    }
  }

  const filteredReviews = (overview?.reviews || []).filter((r) => {
    if (reviewFilter === "UNREPLIED") return !r.reply_comment;
    if (reviewFilter === "REPLIED") return Boolean(r.reply_comment);
    return true;
  });

  if (!loading && profiles.length === 0) {
    return (
      <div className="min-h-screen">
        <Navbar
          accounts={igAccounts}
          selectedAccountId={selectedIgId}
          onSelectAccount={(id) => {
            setSelectedIgId(id);
            saveSelectedAccountId(id);
          }}
        />
        <main className="mx-auto max-w-3xl px-4 py-20 text-center">
          <div className="glass-panel rounded-3xl p-10">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-tr from-sky-500 via-blue-500 to-emerald-400 text-white shadow-lg">
              <Store className="h-7 w-7" />
            </div>
            <h1 className="mt-4 text-2xl font-bold text-white">
              Connect your Google Business Profile
            </h1>
            <p className="mt-2 text-sm text-slate-400">
              Sign in with the Google account managing your business to sync Search &amp; Maps impressions, growth formulas, customer actions, and polite AI review replies.
            </p>
            {error && (
              <div className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
                {error}
              </div>
            )}
            <button
              type="button"
              onClick={handleConnectGoogleBusiness}
              disabled={connecting}
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-sky-400 px-6 py-3 text-sm font-semibold text-slate-950 transition hover:bg-sky-300 disabled:opacity-50"
            >
              {connecting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Store className="h-4 w-4" />
              )}
              Connect Google Business
            </button>
          </div>
        </main>
      </div>
    );
  }

  const kpis = overview?.kpis;
  const profile = overview?.profile;
  const imprBadge = formatGrowthBadge(kpis?.impressions_growth_pct);
  const actionsBadge = formatGrowthBadge(kpis?.actions_growth_pct);

  return (
    <div className="min-h-screen">
      <Navbar
        accounts={igAccounts}
        selectedAccountId={selectedIgId}
        onSelectAccount={(id) => {
          setSelectedIgId(id);
          saveSelectedAccountId(id);
        }}
      />

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6">
        {/* Header Bar */}
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-2xl font-bold text-white">
                {profile?.title || "Google Business Profile"}
              </h1>
              {profile?.primary_category && (
                <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-medium text-sky-200">
                  {profile.primary_category}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {profile?.google_email ? `${profile.google_email} • ` : ""}
              {profile?.address_summary || "Google Business Location"}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {profiles.length > 1 && (
              <select
                value={selectedProfileId}
                onChange={(e) => {
                  setSelectedProfileId(e.target.value);
                  loadOverview(e.target.value);
                }}
                className="rounded-xl border border-white/15 bg-black/40 px-3 py-2 text-xs text-white outline-none"
              >
                {profiles.map((p) => (
                  <option key={p.id} value={p.id} className="bg-slate-900">
                    {p.title}
                  </option>
                ))}
              </select>
            )}

            <button
              type="button"
              onClick={handleSync}
              disabled={syncing || loading}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/5 px-3.5 py-2 text-xs font-medium text-slate-200 hover:bg-white/10 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
              Sync Google Data
            </button>

            <button
              type="button"
              onClick={handleConnectGoogleBusiness}
              disabled={connecting}
              className="inline-flex items-center gap-1.5 rounded-xl border border-sky-500/30 bg-sky-500/10 px-3.5 py-2 text-xs font-medium text-sky-200 hover:bg-sky-500/20 disabled:opacity-50"
            >
              <Store className="h-3.5 w-3.5" />
              Connect Location
            </button>

            <button
              type="button"
              onClick={() => setCopilotOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-emerald-400 to-sky-400 px-4 py-2 text-xs font-semibold text-slate-950 shadow-lg hover:opacity-95"
            >
              <Bot className="h-3.5 w-3.5" />
              Ask Business Copilot
            </button>
          </div>
        </div>

        {statusNote && (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-xs text-emerald-200">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            <span>{statusNote}</span>
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3.5 text-xs text-rose-200">
            {error}
          </div>
        )}

        {profile?.last_sync_error && (
          <div className="flex items-start gap-2.5 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            <div>
              <p className="font-semibold">Google Business API Sync Notice</p>
              <p className="mt-1 opacity-90">{profile.last_sync_error}</p>
            </div>
          </div>
        )}

        {loading && !overview ? (
          <div className="glass-panel flex items-center justify-center rounded-3xl p-16 text-sm text-slate-300">
            <Loader2 className="mr-3 h-5 w-5 animate-spin text-sky-400" />
            Loading Google Business intelligence...
          </div>
        ) : (
          overview &&
          kpis && (
            <>
              {/* Top 4 KPI Cards */}
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="glass-panel rounded-2xl p-5">
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>Profile Views (30d)</span>
                    <Eye className="h-4 w-4 text-sky-400" />
                  </div>
                  <p className="mt-2 text-2xl font-bold text-white">
                    {kpis.impressions_30d.toLocaleString()}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${imprBadge.tone}`}>
                      {imprBadge.label}
                    </span>
                    <span className="text-[11px] text-slate-400">
                      Search {kpis.search_share_pct}% • Maps {kpis.maps_share_pct}%
                    </span>
                  </div>
                </div>

                <div className="glass-panel rounded-2xl p-5">
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>Customer Actions (30d)</span>
                    <MousePointerClick className="h-4 w-4 text-emerald-400" />
                  </div>
                  <p className="mt-2 text-2xl font-bold text-white">
                    {kpis.actions_30d.toLocaleString()}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${actionsBadge.tone}`}>
                      {actionsBadge.label}
                    </span>
                    <span className="text-[11px] text-emerald-300">
                      {kpis.action_conversion_rate_pct}% Conv. Rate
                    </span>
                  </div>
                </div>

                <div className="glass-panel rounded-2xl p-5">
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>Average Star Rating</span>
                    <Star className="h-4 w-4 text-amber-400" />
                  </div>
                  <p className="mt-2 text-2xl font-bold text-white">
                    {kpis.average_rating > 0 ? `${kpis.average_rating.toFixed(1)} ★` : "—"}
                  </p>
                  <p className="mt-2 text-[11px] text-slate-400">
                    {kpis.total_review_count} total reviews • {kpis.positive_share_pct}% 4★–5★
                    {kpis.rating_delta_30d !== null &&
                      ` • ${kpis.rating_delta_30d >= 0 ? "+" : ""}${kpis.rating_delta_30d} 30d delta`}
                  </p>
                </div>

                <div className="glass-panel rounded-2xl p-5">
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <span>Reply SLA &amp; Health Score</span>
                    <TrendingUp className="h-4 w-4 text-indigo-400" />
                  </div>
                  <p className="mt-2 text-2xl font-bold text-white">
                    {kpis.response_rate_pct}% Replied
                  </p>
                  <p className="mt-2 text-[11px] text-slate-400">
                    Health {kpis.local_health_score}/100
                    {kpis.median_reply_hours !== null && ` • ${kpis.median_reply_hours}h median SLA`}
                  </p>
                </div>
              </div>

              {/* Action Channels + Auto-Reply Controls */}
              <div className="grid gap-4 lg:grid-cols-3">
                <div className="glass-panel rounded-2xl p-5 lg:col-span-2">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-white">
                      Customer Intent Actions Breakdown (Last 30 Days)
                    </h2>
                    <button
                      type="button"
                      onClick={() => setShowFormulas((v) => !v)}
                      className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-white/10"
                    >
                      <Calculator className="h-3 w-3 text-sky-400" />
                      {showFormulas ? "Hide Formulas" : "Show Formulas"}
                    </button>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <div className="rounded-xl border border-white/10 bg-black/30 p-3.5">
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <PhoneCall className="h-3.5 w-3.5 text-emerald-400" />
                        Call Clicks
                      </div>
                      <p className="mt-1 text-xl font-bold text-white">
                        {kpis.call_clicks_30d.toLocaleString()}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        {formatGrowthBadge(kpis.call_clicks_growth_pct).label}
                      </p>
                    </div>

                    <div className="rounded-xl border border-white/10 bg-black/30 p-3.5">
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <Globe className="h-3.5 w-3.5 text-sky-400" />
                        Website Clicks
                      </div>
                      <p className="mt-1 text-xl font-bold text-white">
                        {kpis.website_clicks_30d.toLocaleString()}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        {formatGrowthBadge(kpis.website_clicks_growth_pct).label}
                      </p>
                    </div>

                    <div className="rounded-xl border border-white/10 bg-black/30 p-3.5">
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <Navigation className="h-3.5 w-3.5 text-indigo-400" />
                        Direction Requests
                      </div>
                      <p className="mt-1 text-xl font-bold text-white">
                        {kpis.direction_requests_30d.toLocaleString()}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        {formatGrowthBadge(kpis.direction_requests_growth_pct).label}
                      </p>
                    </div>
                  </div>

                  {showFormulas && (
                    <div className="mt-4 grid gap-2.5 border-t border-white/10 pt-4 sm:grid-cols-2">
                      {Object.entries(overview.formulas).map(([key, item]) => (
                        <div
                          key={key}
                          className="rounded-xl border border-white/10 bg-black/40 p-3 text-[11px]"
                        >
                          <p className="font-semibold text-sky-300">{item.label}</p>
                          <p className="mt-1 font-mono text-[10px] text-slate-300">
                            {item.formula}
                          </p>
                          <p className="mt-1 text-slate-400">
                            Inputs: {JSON.stringify(item.inputs)}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Polite Auto-Reply Settings Panel */}
                <div className="glass-panel rounded-2xl p-5">
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    <MessageSquareHeart className="h-4 w-4 text-emerald-400" />
                    Polite AI Review Auto-Reply
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
                    AI drafts warm, humble replies grounded in your Brand Kit tone and customer review language.
                  </p>

                  <div className="mt-4 space-y-3">
                    <label className="flex cursor-pointer items-center justify-between rounded-xl border border-white/10 bg-black/30 p-3 text-xs">
                      <span className="font-medium text-white">
                        Auto-Reply During Sync
                      </span>
                      <input
                        type="checkbox"
                        checked={profile?.auto_reply_enabled || false}
                        disabled={savingSettings}
                        onChange={(e) =>
                          handleSettingsChange({ auto_reply_enabled: e.target.checked })
                        }
                        className="h-4 w-4 accent-emerald-400"
                      />
                    </label>

                    <div>
                      <label className="text-[11px] text-slate-400">
                        Auto-Post Star Threshold
                      </label>
                      <select
                        value={profile?.auto_reply_min_stars ?? 4}
                        disabled={savingSettings}
                        onChange={(e) =>
                          handleSettingsChange({
                            auto_reply_min_stars: Number(e.target.value),
                          })
                        }
                        className="mt-1 w-full rounded-xl border border-white/15 bg-black/40 px-3 py-2 text-xs text-white outline-none"
                      >
                        <option value={4}>Auto-reply 4★–5★ (Hold ≤3★ for Approval)</option>
                        <option value={3}>Auto-reply 3★–5★ (Hold ≤2★ for Approval)</option>
                        <option value={5}>Auto-reply 5★ Only</option>
                        <option value={1}>Auto-reply All (1★–5★)</option>
                      </select>
                    </div>

                    {profile?.website_uri && (
                      <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-[11px] text-slate-400">
                        <p className="flex items-center gap-1.5 text-slate-300">
                          <MapPin className="h-3.5 w-3.5 text-sky-400" />
                          Verified Business Info Connected
                        </p>
                        <p className="mt-1 truncate">{profile.website_uri}</p>
                        {profile.phone_number && <p>{profile.phone_number}</p>}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Reviews Inbox */}
              <section className="glass-panel rounded-3xl p-6">
                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                  <div>
                    <h2 className="text-base font-semibold text-white">
                      Google Reviews &amp; Humble Reply Manager
                    </h2>
                    <p className="text-xs text-slate-400">
                      {kpis.unreplied_review_count} unreplied • {kpis.replied_review_count} replied
                      {kpis.critical_unreplied_count > 0 &&
                        ` • ${kpis.critical_unreplied_count} critical (≤3★) need attention`}
                    </p>
                  </div>

                  <div className="flex items-center gap-1.5 rounded-xl border border-white/10 bg-black/30 p-1 text-xs">
                    {(["ALL", "UNREPLIED", "REPLIED"] as const).map((tab) => (
                      <button
                        key={tab}
                        type="button"
                        onClick={() => setReviewFilter(tab)}
                        className={`rounded-lg px-3 py-1.5 font-medium transition ${
                          reviewFilter === tab
                            ? "bg-white/15 text-white"
                            : "text-slate-400 hover:text-white"
                        }`}
                      >
                        {tab === "ALL" ? "All" : tab === "UNREPLIED" ? "Needs Reply" : "Replied"}
                      </button>
                    ))}
                  </div>
                </div>

                {filteredReviews.length === 0 ? (
                  <p className="mt-6 rounded-2xl border border-white/10 bg-black/20 p-6 text-center text-xs text-slate-400">
                    No synced reviews match this filter yet. Click &ldquo;Sync Google Data&rdquo; after connecting an approved Business Profile.
                  </p>
                ) : (
                  <div className="mt-5 space-y-4">
                    {filteredReviews.map((rev) => {
                      const isBusy = busyReviewId === rev.id;
                      const currentDraft = draftInputs[rev.id] ?? "";
                      return (
                        <div
                          key={rev.id}
                          className="rounded-2xl border border-white/10 bg-white/[0.02] p-4"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-semibold text-white">
                                  {rev.reviewer_name}
                                </span>
                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold text-amber-300">
                                  {"★".repeat(Math.max(1, Math.min(5, rev.star_rating)))}{" "}
                                  ({rev.star_rating}/5)
                                </span>
                                <span
                                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                    rev.reply_comment
                                      ? "border border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                                      : rev.reply_status === "DRAFT_READY"
                                      ? "border border-sky-500/30 bg-sky-500/10 text-sky-300"
                                      : "border border-amber-500/30 bg-amber-500/10 text-amber-200"
                                  }`}
                                >
                                  {rev.reply_status}
                                </span>
                              </div>
                              <p className="mt-1.5 text-xs leading-relaxed text-slate-200">
                                {rev.comment || (
                                  <span className="italic text-slate-500">
                                    Customer left a star rating without text.
                                  </span>
                                )}
                              </p>
                            </div>

                            {rev.review_created_at && (
                              <span className="text-[11px] text-slate-500">
                                {new Date(rev.review_created_at).toLocaleDateString()}
                              </span>
                            )}
                          </div>

                          <div className="mt-3 space-y-2 border-t border-white/10 pt-3">
                            <textarea
                              rows={2}
                              value={currentDraft}
                              onChange={(e) =>
                                setDraftInputs((prev) => ({ ...prev, [rev.id]: e.target.value }))
                              }
                              placeholder="Click 'Draft Humble Reply' or write your polite response..."
                              className="w-full rounded-xl border border-white/15 bg-black/40 p-2.5 text-xs text-white placeholder-slate-500 outline-none focus:border-sky-400"
                            />
                            <div className="flex flex-wrap items-center gap-2">
                              <input
                                type="text"
                                value={toneInputs[rev.id] || ""}
                                onChange={(e) =>
                                  setToneInputs((prev) => ({ ...prev, [rev.id]: e.target.value }))
                                }
                                placeholder="Optional custom instruction (e.g. offer 10% visit apology)..."
                                className="min-w-[200px] flex-1 rounded-xl border border-white/10 bg-black/30 px-3 py-1.5 text-[11px] text-slate-200 placeholder-slate-500 outline-none"
                              />
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => handleDraftReply(rev)}
                                className="inline-flex items-center gap-1.5 rounded-xl border border-sky-500/30 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-200 hover:bg-sky-500/20 disabled:opacity-50"
                              >
                                {isBusy ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <Sparkles className="h-3.5 w-3.5" />
                                )}
                                Draft Humble Reply
                              </button>
                              <button
                                type="button"
                                disabled={isBusy || !currentDraft.trim()}
                                onClick={() => handlePublishReply(rev)}
                                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-500 px-3.5 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
                              >
                                <Send className="h-3.5 w-3.5" />
                                {rev.reply_comment ? "Update Google Reply" : "Post Live on Google"}
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            </>
          )
        )}
      </main>

      {/* Google Business Specialist Copilot Drawer */}
      {copilotOpen && (
        <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-white/10 bg-[#0b0f18]/95 shadow-2xl backdrop-blur-xl">
          <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-sky-400/30 to-emerald-500/30 text-sky-300">
                <Bot className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-white">Google Business Copilot</h3>
                <p className="text-[11px] text-slate-400">
                  {profile?.title || "Business Profile"} • Reviews, Growth &amp; Formulas
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setCopilotOpen(false)}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-white/5 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto p-5">
            {chatMessages.length === 0 && (
              <div className="space-y-2 rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-xs text-slate-300">
                <p className="font-semibold text-sky-300">
                  Ask anything about your Google Business performance or reviews:
                </p>
                {[
                  "Show my latest reviews and reply politely to the newest unreplied one",
                  "What is my 30-day growth rate and customer action conversion rate? Explain the formulas.",
                  "Summarize my 1-3 star critical feedback and how we should respond humbly",
                ].map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => handleSendCopilot(q)}
                    className="block w-full rounded-xl border border-white/10 bg-black/30 p-2.5 text-left text-[11px] text-slate-200 hover:border-sky-400/40"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}

            {chatMessages.map((msg, i) => (
              <div
                key={i}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[90%] rounded-2xl px-4 py-3 text-xs leading-relaxed ${
                    msg.role === "user"
                      ? "bg-sky-400 font-medium text-slate-950"
                      : "border border-white/10 bg-white/[0.04] text-slate-100"
                  }`}
                >
                  <p className="whitespace-pre-wrap">{msg.text}</p>
                  {msg.tokenUsage && (
                    <p className="mt-2 border-t border-white/10 pt-1.5 text-[10px] text-amber-300/90">
                      {msg.tokenUsage.model} • ⚡ {msg.tokenUsage.prompt_tokens.toLocaleString()} in +{" "}
                      {msg.tokenUsage.output_tokens.toLocaleString()} out ={" "}
                      {msg.tokenUsage.total_tokens.toLocaleString()} tokens
                    </p>
                  )}
                </div>
              </div>
            ))}

            {chatSending && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-400" />
                <span>Checking reviews, growth metrics &amp; formulas...</span>
              </div>
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendCopilot();
            }}
            className="flex items-center gap-2 border-t border-white/10 p-4"
          >
            <input
              type="text"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Ask about reviews, growth rates, formulas, or replies..."
              className="flex-1 rounded-xl border border-white/15 bg-black/40 px-3.5 py-2.5 text-xs text-white placeholder-slate-500 outline-none focus:border-sky-400"
            />
            <button
              type="submit"
              disabled={chatSending || !chatInput.trim()}
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-sky-400 text-slate-950 hover:bg-sky-300 disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

export default function GoogleBusinessPage() {
  return (
    <Suspense fallback={<div className="min-h-screen" />}>
      <GoogleBusinessWorkspace />
    </Suspense>
  );
}
