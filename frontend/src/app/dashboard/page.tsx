"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  BarChart3,
  Clock,
  Eye,
  Flame,
  Hash,
  Loader2,
  RefreshCw,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { api } from "@/lib/api";
import type { AnalyticsOverview, InstagramAccount } from "@/lib/types";
import { Navbar } from "@/components/Navbar";

const SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function scoreToColor(score: number): string {
  if (score <= 0) return "bg-white/[0.03] border-white/5";
  if (score >= 75) return "bg-emerald-400/80 border-emerald-300/60";
  if (score >= 55) return "bg-emerald-500/50 border-emerald-400/40";
  if (score >= 35) return "bg-indigo-500/45 border-indigo-400/30";
  return "bg-slate-700/50 border-white/10";
}

export default function DashboardPage() {
  const [accounts, setAccounts] = useState<InstagramAccount[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [auditing, setAuditing] = useState(false);
  const [error, setError] = useState("");
  const [hoveredCell, setHoveredCell] = useState<AnalyticsOverview["heatmap"][number] | null>(null);

  const loadOverview = useCallback(async (accId: string) => {
    setLoading(true);
    setError("");
    try {
      const data = await api.getOverview(accId);
      setOverview(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    api
      .listAccounts()
      .then(({ accounts: accs }) => {
        setAccounts(accs);
        if (accs.length > 0) {
          setSelectedId(accs[0].id);
          loadOverview(accs[0].id);
        } else {
          setLoading(false);
        }
      })
      .catch(() => setLoading(false));
  }, [loadOverview]);

  async function handleSync() {
    if (!selectedId) return;
    setSyncing(true);
    setError("");
    try {
      await api.syncAccount(selectedId);
      await loadOverview(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  async function handleRunAudit() {
    if (!selectedId) return;
    setAuditing(true);
    setError("");
    try {
      const res = await api.runAudit(selectedId);
      setOverview((prev) => (prev ? { ...prev, ai_audit: res.audit } : prev));
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI audit failed.");
    } finally {
      setAuditing(false);
    }
  }

  if (!loading && accounts.length === 0) {
    return (
      <div className="min-h-screen">
        <Navbar />
        <main className="mx-auto max-w-3xl px-4 py-20 text-center">
          <div className="glass-panel rounded-3xl p-10">
            <Sparkles className="mx-auto h-10 w-10 text-emerald-400" />
            <h1 className="mt-4 text-2xl font-bold text-white">
              Connect your Instagram Business Account
            </h1>
            <p className="mt-2 text-sm text-slate-400">
              Once connected, we sync your real posts &amp; Reels, calculate your 7×24 Best-Time Reach Heatmap, and unlock AI Growth Audits.
            </p>
            <Link
              href="/onboarding"
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-slate-950 hover:bg-emerald-400"
            >
              Start Onboarding →
            </Link>
          </div>
        </main>
      </div>
    );
  }

  const maxTimelineReach = Math.max(
    ...(overview?.timeline.map((t) => t.reach) || [1]),
    1
  );

  return (
    <div className="min-h-screen pb-16">
      <Navbar
        accounts={accounts}
        selectedAccountId={selectedId}
        onSelectAccount={(id) => {
          setSelectedId(id);
          loadOverview(id);
        }}
      />

      <main className="mx-auto max-w-7xl space-y-6 px-4 pt-8 sm:px-6">
        {/* Header Bar */}
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div>
            <h1 className="text-2xl font-bold text-white">
              Instagram Analytics &amp; Reach Intelligence
            </h1>
            <p className="mt-1 text-xs text-slate-400">
              {overview
                ? `@${overview.account.username} • ${overview.account.followers_count.toLocaleString()} followers • Timezone: ${overview.account.timezone}`
                : "Loading account metrics..."}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={handleSync}
              disabled={syncing || !selectedId}
              className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-xs font-semibold text-white hover:bg-white/10 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
              Sync IG Insights
            </button>
            <button
              type="button"
              onClick={handleRunAudit}
              disabled={auditing || !selectedId}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-emerald-500 to-indigo-500 px-4 py-2 text-xs font-semibold text-slate-950 shadow-lg shadow-emerald-500/20 hover:opacity-95 disabled:opacity-50"
            >
              {auditing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              Run AI Account Audit
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
            {error}
          </div>
        )}

        {loading || !overview ? (
          <div className="flex h-64 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-emerald-400" />
          </div>
        ) : (
          <>
            {/* KPI Strip */}
            <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[
                {
                  label: "Total Synced Reach",
                  value: overview.kpis.total_reach.toLocaleString(),
                  sub: `Avg ${overview.kpis.avg_reach_per_post.toLocaleString()} / post`,
                  icon: Eye,
                },
                {
                  label: "Avg Engagement Rate",
                  value: `${overview.kpis.avg_engagement_rate}%`,
                  sub: `${overview.kpis.total_likes.toLocaleString()} likes • ${overview.kpis.total_comments.toLocaleString()} comments`,
                  icon: Activity,
                },
                {
                  label: "Saves + Shares (Viral Signal)",
                  value: (
                    overview.kpis.total_saves + overview.kpis.total_shares
                  ).toLocaleString(),
                  sub: `${overview.kpis.total_saves} saves • ${overview.kpis.total_shares} shares`,
                  icon: TrendingUp,
                },
                {
                  label: "Next Optimal Post Slot",
                  value:
                    overview.recommendations?.next_recommended_slot?.local_display ||
                    overview.top_windows[0]?.label ||
                    "Run Sync",
                  sub: overview.recommendations?.next_recommended_slot
                    ? `${overview.recommendations.next_recommended_slot.slot_type.toUpperCase()} • ${overview.recommendations.confidence_tier.replace("_", " ")} (${overview.recommendations.min_gap_hours}h spacing)`
                    : "Need historical posts",
                  icon: Clock,
                },
              ].map((kpi) => {
                const Icon = kpi.icon;
                return (
                  <div key={kpi.label} className="glass-panel rounded-2xl p-5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-slate-400">{kpi.label}</span>
                      <Icon className="h-4 w-4 text-emerald-400" />
                    </div>
                    <p className="mt-2 text-2xl font-bold text-white">{kpi.value}</p>
                    <p className="mt-1 text-[11px] text-slate-400">{kpi.sub}</p>
                  </div>
                );
              })}
            </section>

            {/* Best Time Heatmap + Top Windows */}
            <section className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
              <div className="glass-panel rounded-2xl p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
                        <Flame className="h-4 w-4 text-emerald-400" />
                        Dynamic 7×24 Best-Time-to-Post Heatmap
                      </h2>
                      {overview.recommendations && (
                        <span className="rounded-full border border-indigo-400/30 bg-indigo-500/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-200">
                          {overview.recommendations.confidence_tier.replace("_", " ")} •{" "}
                          {overview.recommendations.posts_analyzed_count} posts
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-400">
                      7-stage Bayesian + UCB engine (format-normalized, MAD outlier-clipped,{" "}
                      {overview.recommendations?.prior_source.replaceAll("_", " ") || "dynamic prior"}).
                    </p>
                  </div>
                  {hoveredCell && (
                    <span className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-[11px] text-emerald-200">
                      {hoveredCell.day_name} {String(hoveredCell.hour_of_day).padStart(2, "0")}:00 •{" "}
                      {hoveredCell.slot_type || "slot"} • Score {hoveredCell.combined_score}/100 (
                      {hoveredCell.sample_size} posts)
                    </span>
                  )}
                </div>

                <div className="mt-4 overflow-x-auto">
                  <div className="min-w-[600px]">
                    <div className="mb-1 grid grid-cols-[44px_repeat(24,minmax(0,1fr))] gap-1 text-center text-[10px] text-slate-500">
                      <span />
                      {Array.from({ length: 24 }, (_, h) => (
                        <span key={h}>{h % 3 === 0 ? `${h}` : ""}</span>
                      ))}
                    </div>
                    <div className="space-y-1">
                      {SHORT_DAYS.map((dayLabel, dow) => (
                        <div
                          key={dayLabel}
                          className="grid grid-cols-[44px_repeat(24,minmax(0,1fr))] items-center gap-1"
                        >
                          <span className="text-[11px] font-medium text-slate-400">{dayLabel}</span>
                          {Array.from({ length: 24 }, (_, hour) => {
                            const cell = overview.heatmap.find(
                              (c) => c.day_of_week === dow && c.hour_of_day === hour
                            );
                            const score = cell?.combined_score || 0;
                            return (
                              <div
                                key={hour}
                                onMouseEnter={() => cell && setHoveredCell(cell)}
                                className={`h-6 rounded border transition hover:scale-110 ${scoreToColor(score)}`}
                                title={`${dayLabel} ${String(hour).padStart(2, "0")}:00 — Score ${score}`}
                              />
                            );
                          })}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Recommended Weekly Schedule + Avoid Windows */}
              <div className="glass-panel rounded-2xl p-5">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
                  <Clock className="h-4 w-4 text-indigo-400" />
                  Recommended Weekly Schedule
                </h2>
                <div className="mt-3 space-y-2.5">
                  {(overview.recommendations?.weekly_schedule || []).length > 0 ? (
                    overview.recommendations!.weekly_schedule.map((slot, idx) => (
                      <div
                        key={slot.datetime_utc}
                        className="rounded-xl border border-white/10 bg-white/[0.02] px-3.5 py-2.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-semibold text-white">
                            #{idx + 1} {slot.local_display}
                          </p>
                          <span
                            className={`rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase ${
                              slot.slot_type === "proven"
                                ? "bg-emerald-500/20 text-emerald-300"
                                : slot.slot_type === "benchmark"
                                  ? "bg-indigo-500/20 text-indigo-300"
                                  : "bg-amber-500/20 text-amber-300"
                            }`}
                          >
                            {slot.slot_type}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] text-slate-400">{slot.reason}</p>
                      </div>
                    ))
                  ) : (
                    <p className="text-xs text-slate-400">
                      No historical post insights synced yet. Click &ldquo;Sync IG Insights&rdquo; to calibrate.
                    </p>
                  )}
                </div>

                {overview.recommendations && overview.recommendations.avoid_windows.length > 0 && (
                  <div className="mt-4 border-t border-white/10 pt-3.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-rose-300">
                      Windows to Avoid
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {overview.recommendations.avoid_windows.slice(0, 4).map((w) => (
                        <span
                          key={w.label}
                          title={w.reason}
                          className="rounded-lg border border-rose-500/25 bg-rose-500/10 px-2 py-1 text-[10px] text-rose-200"
                        >
                          {w.label} ({w.lift_vs_baseline >= 0 ? "+" : ""}
                          {w.lift_vs_baseline}%)
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>

            {/* Reach Timeline & Format Breakdown */}
            <section className="grid gap-6 lg:grid-cols-2">
              <div className="glass-panel rounded-2xl p-5">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
                  <BarChart3 className="h-4 w-4 text-emerald-400" />
                  Recent Post Reach Trend
                </h2>
                {overview.timeline.length === 0 ? (
                  <p className="mt-6 text-xs text-slate-400">No post history synced yet.</p>
                ) : (
                  <div className="mt-5 flex h-44 items-end gap-1.5">
                    {overview.timeline.map((pt, i) => {
                      const heightPct = Math.max(6, Math.round((pt.reach / maxTimelineReach) * 100));
                      return (
                        <div
                          key={`${pt.date}-${i}`}
                          className="group relative flex flex-1 flex-col items-center justify-end"
                        >
                          <div
                            style={{ height: `${heightPct}%` }}
                            className="w-full rounded-t-md bg-gradient-to-t from-indigo-500/70 to-emerald-400/90 transition group-hover:brightness-125"
                            title={`${pt.date} (${pt.media_type}): ${pt.reach.toLocaleString()} reach • ${pt.engagement_rate}% ER`}
                          />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="glass-panel rounded-2xl p-5">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
                  <Hash className="h-4 w-4 text-indigo-400" />
                  Format Performance &amp; Top Hashtags
                </h2>
                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                  {overview.format_breakdown.map((fmt) => (
                    <div
                      key={fmt.format}
                      className="rounded-xl border border-white/10 bg-white/[0.03] p-3"
                    >
                      <p className="text-[11px] font-semibold text-emerald-300">{fmt.format}</p>
                      <p className="mt-1 text-lg font-bold text-white">
                        {fmt.avg_reach.toLocaleString()}
                      </p>
                      <p className="text-[10px] text-slate-400">
                        avg reach • {fmt.avg_engagement_rate}% ER ({fmt.count} posts)
                      </p>
                    </div>
                  ))}
                </div>

                {overview.hashtag_leaderboard.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {overview.hashtag_leaderboard.slice(0, 10).map((tag) => (
                      <span
                        key={tag.hashtag}
                        className="rounded-lg border border-indigo-400/20 bg-indigo-500/10 px-2.5 py-1 text-[11px] text-indigo-200"
                      >
                        {tag.hashtag} • {tag.avg_reach.toLocaleString()} reach
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </section>

            {/* AI Account Audit */}
            {overview.ai_audit && (
              <section className="glass-panel rounded-2xl border-emerald-500/20 p-6">
                <div className="flex items-center gap-2 text-sm font-semibold text-emerald-300">
                  <Sparkles className="h-4 w-4" />
                  <span>AI Account Growth Audit</span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-slate-200">
                  {overview.ai_audit.summary}
                </p>
                <div className="mt-4 grid gap-4 md:grid-cols-3">
                  <div className="rounded-xl border border-white/10 bg-black/30 p-4">
                    <h3 className="text-xs font-semibold text-emerald-300">Top Strengths</h3>
                    <ul className="mt-2 space-y-1.5 text-xs text-slate-300">
                      {overview.ai_audit.top_strengths.map((s) => (
                        <li key={s}>• {s}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl border border-white/10 bg-black/30 p-4">
                    <h3 className="text-xs font-semibold text-amber-300">Missed Opportunities</h3>
                    <ul className="mt-2 space-y-1.5 text-xs text-slate-300">
                      {overview.ai_audit.missed_opportunities.map((s) => (
                        <li key={s}>• {s}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl border border-white/10 bg-black/30 p-4">
                    <h3 className="text-xs font-semibold text-indigo-300">7-Day Action Plan</h3>
                    <ul className="mt-2 space-y-1.5 text-xs text-slate-300">
                      {overview.ai_audit.next_7_days_action_plan.map((s) => (
                        <li key={s}>• {s}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
