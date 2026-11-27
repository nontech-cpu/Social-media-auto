"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Bot,
  Calendar as CalendarIcon,
  CalendarPlus,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Edit3,
  ExternalLink,
  Film,
  Image as ImageIcon,
  Layers,
  ListOrdered,
  Loader2,
  Plus,
  Sparkles,
  Trash2,
  AlertTriangle,
} from "lucide-react";
import { api } from "@/lib/api";
import type { InstagramAccount, ScheduledPost } from "@/lib/types";
import { Navbar } from "@/components/Navbar";
import { PostComposerModal } from "@/components/PostComposerModal";
import { BatchQueueModal } from "@/components/BatchQueueModal";
import { CopilotDrawer } from "@/components/CopilotDrawer";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function statusBadge(status: ScheduledPost["status"]) {
  switch (status) {
    case "PUBLISHED":
      return "border-emerald-500/30 bg-emerald-500/15 text-emerald-300";
    case "QUEUED":
      return "border-indigo-500/30 bg-indigo-500/15 text-indigo-300";
    case "PUBLISHING":
      return "border-amber-500/30 bg-amber-500/15 text-amber-300";
    case "FAILED":
      return "border-rose-500/30 bg-rose-500/15 text-rose-300";
    default:
      return "border-white/15 bg-white/5 text-slate-300";
  }
}

export default function PlannerPage() {
  const [accounts, setAccounts] = useState<InstagramAccount[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [posts, setPosts] = useState<ScheduledPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<"calendar" | "queue">("calendar");
  const [monthCursor, setMonthCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const [composerOpen, setComposerOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [copilotOpen, setCopilotOpen] = useState(false);
  const [composerDraft, setComposerDraft] = useState<{
    post?: ScheduledPost;
    caption?: string;
    hashtags?: string[];
    mediaType?: "IMAGE" | "REELS" | "CAROUSEL";
    mediaUrls?: string[];
    scheduledFor?: string;
  } | null>(null);
  const [draggedPostId, setDraggedPostId] = useState<string | null>(null);
  const [liveBanner, setLiveBanner] = useState<{
    type: "published" | "failed";
    message: string;
    permalink?: string;
  } | null>(null);
  const prevStatusMapRef = useRef<Record<string, string>>({});

  const loadPosts = useCallback(async (accId: string, silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.listPosts(accId);
      const prevMap = prevStatusMapRef.current;
      const nextMap: Record<string, string> = {};
      for (const p of res.posts) {
        nextMap[p.id] = p.status;
        const oldStatus = prevMap[p.id];
        if (oldStatus && oldStatus !== p.status) {
          if (p.status === "PUBLISHED") {
            setLiveBanner({
              type: "published",
              message: `Your ${p.media_type} is now live on Instagram!`,
              permalink: p.published_permalink || undefined,
            });
          } else if (p.status === "FAILED") {
            setLiveBanner({
              type: "failed",
              message: `Scheduled ${p.media_type} failed: ${p.last_error || "Unknown error"}`,
            });
          }
        }
      }
      prevStatusMapRef.current = nextMap;
      setPosts(res.posts);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const hasPending = posts.some((p) => p.status === "QUEUED" || p.status === "PUBLISHING");
    if (!selectedId || (!hasPending && !copilotOpen)) return;
    const timer = setInterval(() => {
      loadPosts(selectedId, true);
    }, 8000);
    return () => clearInterval(timer);
  }, [selectedId, posts, copilotOpen, loadPosts]);

  useEffect(() => {
    api
      .listAccounts()
      .then(({ accounts: accs }) => {
        setAccounts(accs);
        if (accs.length > 0) {
          setSelectedId(accs[0].id);
          loadPosts(accs[0].id);
        } else {
          setLoading(false);
        }
      })
      .catch(() => setLoading(false));
  }, [loadPosts]);

  const activeAccount = accounts.find((a) => a.id === selectedId) || accounts[0] || null;

  async function handleDelete(postId: string) {
    await api.deletePost(postId);
    if (selectedId) loadPosts(selectedId);
  }

  async function handleDropOnDay(dayDate: Date) {
    if (!draggedPostId) return;
    const post = posts.find((p) => p.id === draggedPostId);
    setDraggedPostId(null);
    if (!post || post.status === "PUBLISHED") return;

    const oldDate = new Date(post.scheduled_for);
    const nextDate = new Date(
      dayDate.getFullYear(),
      dayDate.getMonth(),
      dayDate.getDate(),
      oldDate.getHours(),
      oldDate.getMinutes()
    );
    await api.updatePost(post.id, { scheduled_for: nextDate.toISOString() });
    if (selectedId) loadPosts(selectedId);
  }

  if (!loading && !activeAccount) {
    return (
      <div className="min-h-screen">
        <Navbar />
        <main className="mx-auto max-w-3xl px-4 py-20 text-center">
          <div className="glass-panel rounded-3xl p-10">
            <Sparkles className="mx-auto h-10 w-10 text-emerald-400" />
            <h1 className="mt-4 text-2xl font-bold text-white">
              Connect Instagram to Open the Smart Planner
            </h1>
            <p className="mt-2 text-sm text-slate-400">
              Complete the 3-step onboarding wizard to connect your Instagram Business account.
            </p>
            <Link
              href="/onboarding"
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-slate-950 hover:bg-emerald-400"
            >
              Start Setup →
            </Link>
          </div>
        </main>
      </div>
    );
  }

  // Build calendar cells for current month
  const year = monthCursor.getFullYear();
  const month = monthCursor.getMonth();
  const firstDay = new Date(year, month, 1);
  const startOffset = (firstDay.getDay() + 6) % 7; // Monday = 0
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const totalCells = Math.ceil((startOffset + daysInMonth) / 7) * 7;

  return (
    <div className="min-h-screen pb-16">
      <Navbar
        accounts={accounts}
        selectedAccountId={selectedId}
        onSelectAccount={(id) => {
          setSelectedId(id);
          loadPosts(id);
        }}
      />

      <main className="mx-auto max-w-7xl space-y-6 px-4 pt-8 sm:px-6">
        {/* Action Header */}
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div>
            <h1 className="text-2xl font-bold text-white">Smart Content Planner</h1>
            <p className="mt-1 text-xs text-slate-400">
              Drag &amp; drop queued posts across days, preview before publishing, or let AI Copilot space your Reels for maximum reach.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex rounded-xl border border-white/10 bg-white/[0.03] p-1">
              <button
                type="button"
                onClick={() => setViewMode("calendar")}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
                  viewMode === "calendar"
                    ? "bg-white/10 text-white"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <CalendarIcon className="h-3.5 w-3.5" /> Calendar
              </button>
              <button
                type="button"
                onClick={() => setViewMode("queue")}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
                  viewMode === "queue"
                    ? "bg-white/10 text-white"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <ListOrdered className="h-3.5 w-3.5" /> Queue ({posts.length})
              </button>
            </div>

            <button
              type="button"
              onClick={() => setCopilotOpen(true)}
              className="inline-flex items-center gap-2 rounded-xl border border-indigo-400/30 bg-indigo-500/15 px-3.5 py-2 text-xs font-semibold text-indigo-200 hover:bg-indigo-500/25"
            >
              <Bot className="h-3.5 w-3.5" /> AI Copilot
            </button>

            <button
              type="button"
              onClick={() => setBatchOpen(true)}
              className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-3.5 py-2 text-xs font-semibold text-white hover:bg-white/10"
            >
              <CalendarPlus className="h-3.5 w-3.5 text-emerald-400" /> Smart Batch Queue
            </button>

            <button
              type="button"
              onClick={() => {
                setComposerDraft(null);
                setComposerOpen(true);
              }}
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
            >
              <Plus className="h-4 w-4" /> New Post / Reel
            </button>
          </div>
        </div>

        {/* Live Auto-Publish Notification Banner */}
        {liveBanner && (
          <div
            className={`flex flex-wrap items-center justify-between gap-3 rounded-2xl border px-4 py-3 text-xs ${
              liveBanner.type === "published"
                ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200"
                : "border-rose-500/40 bg-rose-500/15 text-rose-200"
            }`}
          >
            <div className="flex items-center gap-2 font-medium">
              {liveBanner.type === "published" ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-rose-400" />
              )}
              <span>{liveBanner.message}</span>
            </div>
            <div className="flex items-center gap-3">
              {liveBanner.permalink && (
                <a
                  href={liveBanner.permalink}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-lg bg-emerald-500 px-3 py-1 text-[11px] font-semibold text-slate-950 hover:bg-emerald-400"
                >
                  Check Live on Instagram <ExternalLink className="h-3 w-3" />
                </a>
              )}
              <button
                type="button"
                onClick={() => setLiveBanner(null)}
                className="text-slate-400 hover:text-white"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex h-64 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-emerald-400" />
          </div>
        ) : viewMode === "calendar" ? (
          <section className="glass-panel overflow-hidden rounded-2xl">
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-3.5">
              <h2 className="text-sm font-bold text-white">
                {monthCursor.toLocaleString("en-US", { month: "long", year: "numeric" })}
              </h2>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setMonthCursor(new Date(year, month - 1, 1))}
                  className="rounded-lg border border-white/10 p-1.5 text-slate-300 hover:bg-white/5"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setMonthCursor(new Date(year, month + 1, 1))}
                  className="rounded-lg border border-white/10 p-1.5 text-slate-300 hover:bg-white/5"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="grid grid-cols-7 border-b border-white/10 bg-white/[0.02] text-center text-[11px] font-semibold text-slate-400">
              {WEEKDAYS.map((d) => (
                <div key={d} className="py-2.5">
                  {d}
                </div>
              ))}
            </div>

            <div className="grid grid-cols-7">
              {Array.from({ length: totalCells }, (_, cellIdx) => {
                const dayNum = cellIdx - startOffset + 1;
                const inMonth = dayNum >= 1 && dayNum <= daysInMonth;
                const cellDate = new Date(year, month, dayNum);
                const dayPosts = inMonth
                  ? posts.filter((p) => {
                      const d = new Date(p.scheduled_for);
                      return (
                        d.getFullYear() === cellDate.getFullYear() &&
                        d.getMonth() === cellDate.getMonth() &&
                        d.getDate() === cellDate.getDate()
                      );
                    })
                  : [];

                return (
                  <div
                    key={cellIdx}
                    onDragOver={(e) => inMonth && e.preventDefault()}
                    onDrop={() => inMonth && handleDropOnDay(cellDate)}
                    className={`min-h-[126px] border-b border-r border-white/[0.06] p-2 ${
                      inMonth ? "bg-transparent" : "bg-black/30 opacity-40"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-medium text-slate-400">
                        {inMonth ? dayNum : ""}
                      </span>
                      {inMonth && (
                        <button
                          type="button"
                          onClick={() => {
                            const preset = new Date(year, month, dayNum, 19, 0);
                            setComposerDraft({ scheduledFor: preset.toISOString() });
                            setComposerOpen(true);
                          }}
                          className="rounded p-0.5 text-slate-600 hover:bg-white/10 hover:text-emerald-400"
                          title="Schedule on this day"
                        >
                          <Plus className="h-3 w-3" />
                        </button>
                      )}
                    </div>

                    <div className="mt-1.5 space-y-1.5">
                      {dayPosts.map((p) => (
                        <div
                          key={p.id}
                          draggable={p.status !== "PUBLISHED"}
                          onDragStart={() => setDraggedPostId(p.id)}
                          onClick={() => {
                            if (p.status === "PUBLISHED" && p.published_permalink) {
                              window.open(p.published_permalink, "_blank", "noopener,noreferrer");
                            } else if (p.status !== "PUBLISHED") {
                              setComposerDraft({ post: p });
                              setComposerOpen(true);
                            }
                          }}
                          className={`cursor-pointer rounded-lg border p-1.5 text-[10px] transition hover:brightness-110 ${statusBadge(
                            p.status
                          )}`}
                        >
                          <div className="flex items-center justify-between gap-1 font-semibold">
                            <span className="inline-flex items-center gap-1">
                              {p.media_type === "REELS" ? (
                                <Film className="h-2.5 w-2.5" />
                              ) : p.media_type === "CAROUSEL" ? (
                                <Layers className="h-2.5 w-2.5" />
                              ) : (
                                <ImageIcon className="h-2.5 w-2.5" />
                              )}
                              {new Date(p.scheduled_for).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </span>
                            <span>{p.status}</span>
                          </div>
                          <p className="mt-0.5 truncate opacity-90">
                            {p.caption || "Untitled post"}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ) : (
          <section className="space-y-3">
            {posts.length === 0 ? (
              <div className="glass-panel rounded-2xl p-10 text-center text-sm text-slate-400">
                Your queue is empty. Click &ldquo;New Post / Reel&rdquo; or &ldquo;Smart Batch Queue&rdquo; to schedule with AI.
              </div>
            ) : (
              posts.map((p) => (
                <div
                  key={p.id}
                  className="glass-panel flex flex-col justify-between gap-4 rounded-2xl p-4 sm:flex-row sm:items-center"
                >
                  <div className="space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-full border px-2.5 py-0.5 text-[10px] font-semibold ${statusBadge(
                          p.status
                        )}`}
                      >
                        {p.status}
                      </span>
                      <span className="text-xs font-semibold text-white">{p.media_type}</span>
                      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                        <Clock className="h-3 w-3" />
                        {new Date(p.scheduled_for).toLocaleString()}
                      </span>
                      {p.predicted_score > 0 && (
                        <span className="rounded-md bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-300">
                          Reach Index {p.predicted_score.toFixed(1)}
                        </span>
                      )}
                    </div>
                    <p className="line-clamp-2 text-xs text-slate-200">
                      {p.caption || "(No caption)"}
                    </p>
                    {p.hashtags.length > 0 && (
                      <p className="text-[11px] text-indigo-300">{p.hashtags.join(" ")}</p>
                    )}
                    {p.ai_rationale && (
                      <p className="text-[11px] text-slate-400">{p.ai_rationale}</p>
                    )}
                    {p.last_error && (
                      <p className="flex items-center gap-1 text-[11px] text-rose-300">
                        <AlertTriangle className="h-3 w-3" /> {p.last_error}
                      </p>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center gap-2">
                    {p.published_permalink && (
                      <a
                        href={p.published_permalink}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-300"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" /> View Live{" "}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                    {p.status !== "PUBLISHED" && (
                      <button
                        type="button"
                        onClick={() => {
                          setComposerDraft({ post: p });
                          setComposerOpen(true);
                        }}
                        className="rounded-xl border border-white/15 p-2 text-slate-300 hover:bg-white/5"
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleDelete(p.id)}
                      className="rounded-xl border border-white/15 p-2 text-slate-400 hover:border-rose-500/40 hover:text-rose-300"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </section>
        )}
      </main>

      {activeAccount && (
        <>
          <PostComposerModal
            open={composerOpen}
            account={activeAccount}
            initialDraft={composerDraft}
            onClose={() => setComposerOpen(false)}
            onSaved={() => loadPosts(activeAccount.id)}
          />
          <BatchQueueModal
            open={batchOpen}
            account={activeAccount}
            onClose={() => setBatchOpen(false)}
            onQueued={() => loadPosts(activeAccount.id)}
          />
          <CopilotDrawer
            open={copilotOpen}
            account={activeAccount}
            onClose={() => setCopilotOpen(false)}
            onPostsUpdated={() => loadPosts(activeAccount.id)}
            onLoadDraft={(draft) => {
              setCopilotOpen(false);
              setComposerDraft(draft);
              setComposerOpen(true);
            }}
          />
        </>
      )}
    </div>
  );
}
