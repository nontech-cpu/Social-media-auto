"use client";

import { useEffect, useRef, useState } from "react";
import {
  Bot,
  CalendarCheck,
  ImagePlus,
  Loader2,
  Send,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type { AIUsageSummary, CopilotMessage, InstagramAccount } from "@/lib/types";

interface CopilotDrawerProps {
  open: boolean;
  account: InstagramAccount;
  onClose: () => void;
  onPostsUpdated: () => void;
  onLoadDraft: (draft: {
    caption: string;
    hashtags: string[];
    mediaType: "REELS" | "IMAGE" | "CAROUSEL" | "STORIES";
    mediaUrls?: string[];
    scheduledFor?: string;
  }) => void;
}

const STARTER_PROMPTS = [
  "I want to post a Reel today — suggest trending hashtags and the best reach time",
  "Create a bold behind-the-scenes Instagram Story with an interactive poll idea",
  "Should I post today or wait for a better reach window?",
];

export function CopilotDrawer({
  open,
  account,
  onClose,
  onPostsUpdated,
  onLoadDraft,
}: CopilotDrawerProps) {
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [input, setInput] = useState("");
  const [attachedUrls, setAttachedUrls] = useState<string[]>([]);
  const [attachedType, setAttachedType] = useState<
    "" | "REELS" | "IMAGE" | "CAROUSEL" | "STORIES"
  >("");
  const [mediaWarnings, setMediaWarnings] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState("");
  const [error, setError] = useState("");
  const [aiUsage, setAiUsage] = useState<AIUsageSummary | null>(null);
  const [showUsageDetails, setShowUsageDetails] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    const fetchUsage = () => {
      api
        .getAiUsage(account.id)
        .then((u) => {
          if (active) setAiUsage(u);
        })
        .catch(() => {});
    };
    const fetchHistory = () => {
      api
        .getChatHistory(account.id)
        .then((res) => {
          if (!active) return;
          setMessages((prev) => {
            if (res.messages.length !== prev.length) {
              const latest = res.messages[res.messages.length - 1];
              if (latest?.structured_payload?.scheduled_post) {
                onPostsUpdated();
              }
            }
            return res.messages;
          });
        })
        .catch(() => {});
    };
    fetchHistory();
    fetchUsage();
    const onActivity = () => {
      fetchHistory();
      fetchUsage();
    };
    window.addEventListener("socialmediaai:activity-updated", onActivity);
    const timer = setInterval(fetchHistory, 6000);
    return () => {
      active = false;
      window.removeEventListener("socialmediaai:activity-updated", onActivity);
      clearInterval(timer);
    };
  }, [open, account.id, onPostsUpdated]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  if (!open) return null;

  async function handleAttachFiles(files: FileList | File[] | null) {
    const fileArray = files ? Array.from(files) : [];
    if (fileArray.length === 0) return;
    setError("");
    setUploading(true);
    try {
      const urls: string[] = [];
      const warnings: string[] = [];
      let detected: "REELS" | "IMAGE" | "CAROUSEL" = "IMAGE";
      for (const f of fileArray) {
        const res = await api.uploadMedia(f, undefined, account.id);
        urls.push(res.url);
        if (res.warnings?.length) warnings.push(...res.warnings);
        detected = res.media_type;
      }
      const merged = [...attachedUrls, ...urls].slice(0, 10);
      setAttachedUrls(merged);
      setAttachedType(merged.length > 1 ? "CAROUSEL" : detected);
      setMediaWarnings(warnings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Media upload failed.");
    } finally {
      setUploading(false);
    }
  }

  function handlePasteMedia(e: React.ClipboardEvent) {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === "file" && (item.type.startsWith("image/") || item.type.startsWith("video/"))) {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      handleAttachFiles(files);
    }
  }

  async function handleSend(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    if (!text || sending) return;
    setError("");
    setStatusNote("");
    if (!overrideText) setInput("");

    const sendUrls = [...attachedUrls];
    const sendType = attachedType;
    setAttachedUrls([]);
    setAttachedType("");
    setMediaWarnings([]);

    const optimistic: CopilotMessage = {
      id: `tmp-${Date.now()}`,
      role: "user",
      content: text,
      structured_payload: sendUrls.length
        ? { media_urls: sendUrls, media_type: sendType || "REELS" }
        : null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
    setSending(true);
    try {
      const reply = await api.sendChatMessage(account.id, text, {
        media_urls: sendUrls,
        media_type: sendType,
      });
      setMessages((prev) => [...prev, reply]);
      api.getAiUsage(account.id).then(setAiUsage).catch(() => {});
      if (reply.structured_payload?.scheduled_post) {
        onPostsUpdated();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Copilot failed to respond.");
    } finally {
      setSending(false);
    }
  }

  async function handleQuickScheduleOrPublish(
    msg: CopilotMessage,
    mode: "schedule" | "now"
  ) {
    const draft = msg.structured_payload?.draft_post;
    const urls = draft?.media_urls || [];
    if (!draft || urls.length === 0) {
      onLoadDraft({
        caption: draft?.caption || "",
        mediaType: draft?.media_type || "REELS",
        mediaUrls: urls,
        hashtags: msg.structured_payload?.hashtags || [],
        scheduledFor: msg.structured_payload?.recommended_slots?.[0]?.scheduled_for,
      });
      return;
    }

    setError("");
    setStatusNote("");
    setActingId(msg.id);
    try {
      if (mode === "now") {
        const res = await api.publishNow(account.id, {
          media_type: draft.media_type,
          media_urls: urls,
          caption: draft.caption,
          hashtags: msg.structured_payload?.hashtags || [],
          share_to_feed: true,
        });
        setStatusNote(
          res.post.status === "PUBLISHED"
            ? "Post Instagram par live publish ho gayi!"
            : `Publish status: ${res.post.status} ${res.post.last_error || ""}`
        );
      } else {
        const slot = msg.structured_payload?.recommended_slots?.[0];
        await api.schedulePosts(account.id, [
          {
            media_type: draft.media_type,
            media_urls: urls,
            caption: draft.caption,
            hashtags: msg.structured_payload?.hashtags || [],
            share_to_feed: true,
            scheduled_for: slot?.scheduled_for || null,
            status: "QUEUED",
            ai_rationale: slot?.rationale || "",
            predicted_score: slot?.combined_score || 0,
          },
        ]);
        setStatusNote(
          slot
            ? `Queue ho gaya: ${slot.local_display}`
            : "Post smart queue me add ho gaya!"
        );
      }
      onPostsUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setActingId(null);
    }
  }

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-white/10 bg-[#0b0f18]/95 shadow-2xl backdrop-blur-xl">
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400/30 to-indigo-500/30 text-emerald-300">
            <Bot className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-white">AI Social Media Manager</h3>
            <p className="text-[11px] text-slate-400">
              @{account.username} • Attach Reel/Photo &amp; chat naturally
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {aiUsage && (
            <button
              type="button"
              onClick={() => setShowUsageDetails((v) => !v)}
              title="View Gemini token usage breakdown"
              className="inline-flex items-center gap-1 rounded-lg border border-amber-400/30 bg-amber-500/10 px-2.5 py-1 text-[11px] font-semibold text-amber-200 hover:bg-amber-500/20"
            >
              <Zap className="h-3 w-3 text-amber-300" />
              {aiUsage.total_tokens.toLocaleString()} tokens
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-white/5 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {showUsageDetails && aiUsage && (
        <div className="border-b border-white/10 bg-black/40 px-5 py-3 text-[11px] text-slate-300">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-amber-200">
              Gemini Token Usage ({aiUsage.request_count} calls)
            </span>
            <span className="text-slate-400">
              {aiUsage.prompt_tokens.toLocaleString()} in
              {aiUsage.cached_tokens > 0
                ? ` (${aiUsage.cached_tokens.toLocaleString()} cached)`
                : ""}{" "}
              + {aiUsage.output_tokens.toLocaleString()} out
            </span>
          </div>
          {Object.keys(aiUsage.by_feature).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(aiUsage.by_feature).map(([feat, stats]) => (
                <span
                  key={feat}
                  className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300"
                >
                  {feat.split("_").join(" ")}: {stats.total_tokens.toLocaleString()} ({stats.requests}x)
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {messages.length === 0 && (
          <div className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-xs leading-relaxed text-slate-300">
            <p className="font-semibold text-emerald-300">
              Attach or paste a Photo/Reel and talk naturally:
            </p>
            <p className="text-slate-400">
              Use the <ImagePlus className="inline h-3.5 w-3.5 text-emerald-400" /> button or press <kbd className="rounded bg-white/10 px-1 py-0.5 text-[10px]">Ctrl+V</kbd> to attach media, then ask in any language — the bot analyzes your content, historical reach, and queue to suggest the best caption, trending hashtags, and optimal slot.
            </p>
            <div className="flex flex-wrap gap-1.5 pt-1">
              {STARTER_PROMPTS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => handleSend(p)}
                  className="rounded-lg border border-white/10 bg-black/40 px-2.5 py-1.5 text-left text-[11px] text-slate-300 hover:border-emerald-400/50 hover:text-white"
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => {
          const mediaUrls =
            m.structured_payload?.draft_post?.media_urls ||
            m.structured_payload?.media_urls ||
            [];
          return (
            <div
              key={m.id}
              className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}
            >
              <div
                className={`max-w-[92%] rounded-2xl px-4 py-3 text-xs leading-relaxed ${
                  m.role === "user"
                    ? "bg-emerald-500 font-medium text-slate-950"
                    : "border border-white/10 bg-white/[0.04] text-slate-200"
                }`}
              >
                {mediaUrls.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {mediaUrls.map((u) => (
                      <span
                        key={u}
                        className={`rounded-md px-2 py-0.5 text-[10px] ${
                          m.role === "user"
                            ? "bg-black/20 text-slate-950"
                            : "border border-white/10 bg-black/40 text-emerald-300"
                        }`}
                      >
                        Media attached ✓
                      </span>
                    ))}
                  </div>
                )}

                <p className="whitespace-pre-wrap">{m.content}</p>

                {m.structured_payload?.draft_post?.caption && (
                  <div className="mt-3 rounded-xl border border-white/10 bg-black/40 p-3 text-[11px] text-slate-200">
                    <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                      Proposed Caption ({m.structured_payload.draft_post.media_type})
                    </p>
                    <p className="whitespace-pre-wrap">
                      {m.structured_payload.draft_post.caption}
                    </p>
                  </div>
                )}

                {m.structured_payload?.hashtags &&
                  m.structured_payload.hashtags.length > 0 && (
                    <p className="mt-2 text-[11px] text-indigo-300">
                      {m.structured_payload.hashtags.join(" ")}
                    </p>
                  )}

                {m.structured_payload?.recommended_slots &&
                  m.structured_payload.recommended_slots.length > 0 && (
                    <div className="mt-3 rounded-xl border border-emerald-500/25 bg-emerald-500/10 p-2.5 text-[11px] text-emerald-200">
                      <p className="font-semibold">
                        Best Slot: {m.structured_payload.recommended_slots[0].local_display}
                      </p>
                      <p className="mt-0.5 opacity-85">
                        {m.structured_payload.recommended_slots[0].rationale}
                      </p>
                    </div>
                  )}

                {m.structured_payload?.scheduled_post && (
                  <div
                    className={`mt-3 rounded-xl border p-2.5 text-[11px] ${
                      m.structured_payload.scheduled_post.status === "FAILED"
                        ? "border-rose-500/30 bg-rose-500/10 text-rose-200"
                        : "border-emerald-500/30 bg-emerald-500/15 text-emerald-200"
                    }`}
                  >
                    <p className="font-semibold">
                      Status: {m.structured_payload.scheduled_post.status} •{" "}
                      {m.structured_payload.scheduled_post.local_display}
                    </p>
                    {m.structured_payload.scheduled_post.published_permalink && (
                      <a
                        href={m.structured_payload.scheduled_post.published_permalink}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 inline-block underline"
                      >
                        View on Instagram →
                      </a>
                    )}
                    {m.structured_payload.scheduled_post.last_error && (
                      <p className="mt-1">{m.structured_payload.scheduled_post.last_error}</p>
                    )}
                  </div>
                )}

                {m.structured_payload?.draft_post && !m.structured_payload?.scheduled_post && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        onLoadDraft({
                          caption: m.structured_payload?.draft_post?.caption || "",
                          mediaType:
                            m.structured_payload?.draft_post?.media_type || "REELS",
                          mediaUrls: m.structured_payload?.draft_post?.media_urls || [],
                          hashtags: m.structured_payload?.hashtags || [],
                          scheduledFor:
                            m.structured_payload?.recommended_slots?.[0]?.scheduled_for,
                        })
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-[11px] font-semibold text-slate-950 hover:bg-slate-200"
                    >
                      <Sparkles className="h-3 w-3" /> Preview &amp; Edit
                    </button>

                    {(m.structured_payload.draft_post.media_urls?.length || 0) > 0 && (
                      <>
                        <button
                          type="button"
                          disabled={actingId === m.id}
                          onClick={() => handleQuickScheduleOrPublish(m, "schedule")}
                          className="inline-flex items-center gap-1 rounded-lg border border-emerald-400/40 bg-emerald-500/20 px-3 py-1.5 text-[11px] font-semibold text-emerald-200 hover:bg-emerald-500/30 disabled:opacity-50"
                        >
                          <CalendarCheck className="h-3 w-3" /> Approve &amp; Queue
                        </button>
                        <button
                          type="button"
                          disabled={actingId === m.id}
                          onClick={() => handleQuickScheduleOrPublish(m, "now")}
                          className="inline-flex items-center gap-1 rounded-lg border border-indigo-400/40 bg-indigo-500/20 px-3 py-1.5 text-[11px] font-semibold text-indigo-200 hover:bg-indigo-500/30 disabled:opacity-50"
                        >
                          <Zap className="h-3 w-3" /> Post Now
                        </button>
                      </>
                    )}
                  </div>
                )}

                {m.role === "assistant" && m.structured_payload?.token_usage && (
                  <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 border-t border-white/10 pt-2 text-[10px] text-slate-400">
                    <span>
                      {m.structured_payload.token_usage.model}
                      {m.structured_payload.token_usage.tool_calls &&
                      m.structured_payload.token_usage.tool_calls.length > 0
                        ? ` • ${m.structured_payload.token_usage.tool_calls.join(", ")}`
                        : ""}
                    </span>
                    <span className="font-medium text-amber-300/90">
                      ⚡ {m.structured_payload.token_usage.prompt_tokens.toLocaleString()} in
                      {m.structured_payload.token_usage.cached_tokens
                        ? ` (${m.structured_payload.token_usage.cached_tokens.toLocaleString()} cached)`
                        : ""}{" "}
                      + {m.structured_payload.token_usage.output_tokens.toLocaleString()} out ={" "}
                      {m.structured_payload.token_usage.total_tokens.toLocaleString()} tokens
                    </span>
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {sending && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-400" />
            <span>Thinking — checking your reach windows, trends &amp; queue...</span>
          </div>
        )}
        {statusNote && (
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-2.5 text-xs text-emerald-200">
            {statusNote}
          </div>
        )}
        {error && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-2.5 text-xs text-rose-200">
            {error}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Attachment preview + Chat Input */}
      <div className="border-t border-white/10 p-4">
        {mediaWarnings.length > 0 && (
          <div className="mb-2 space-y-1 rounded-xl border border-amber-500/30 bg-amber-500/10 p-2.5 text-[11px] text-amber-200">
            {mediaWarnings.map((warn, i) => (
              <p key={i}>• {warn}</p>
            ))}
          </div>
        )}

        {attachedUrls.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-[11px] text-emerald-200">
              {attachedType} ({attachedUrls.length} file ready)
              <button
                type="button"
                onClick={() => {
                  setAttachedUrls([]);
                  setAttachedType("");
                  setMediaWarnings([]);
                }}
                className="hover:text-rose-300"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          </div>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
          className="flex items-center gap-2"
        >
          <label
            title="Attach Photo, Carousel or Reel"
            className="flex cursor-pointer items-center justify-center rounded-xl border border-white/15 bg-white/5 p-2.5 text-slate-300 hover:border-emerald-400/50 hover:text-emerald-300"
          >
            {uploading ? (
              <Loader2 className="h-4 w-4 animate-spin text-emerald-400" />
            ) : (
              <ImagePlus className="h-4 w-4" />
            )}
            <input
              type="file"
              accept="image/*,video/mp4,video/quicktime"
              multiple
              className="hidden"
              onChange={(e) => handleAttachFiles(e.target.files)}
            />
          </label>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={handlePasteMedia}
            placeholder="Describe your post or paste an image (Ctrl+V) in any language..."
            className="flex-1 rounded-xl border border-white/15 bg-black/50 px-3.5 py-2.5 text-xs text-white placeholder-slate-500 outline-none focus:border-emerald-400"
          />
          <button
            type="submit"
            disabled={sending || uploading}
            className="rounded-xl bg-emerald-500 p-2.5 text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>
      </div>
    </div>
  );
}
