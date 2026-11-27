"use client";

import { useEffect, useState } from "react";
import {
  CalendarClock,
  Check,
  Hash,
  Loader2,
  Plus,
  Send,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  GeneratedPostPackage,
  InstagramAccount,
  ScheduledPost,
} from "@/lib/types";
import { InstagramPreview } from "./InstagramPreview";

interface PostComposerModalProps {
  open: boolean;
  account: InstagramAccount;
  initialDraft?: {
    post?: ScheduledPost;
    caption?: string;
    hashtags?: string[];
    mediaType?: "IMAGE" | "REELS" | "CAROUSEL" | "STORIES";
    mediaUrls?: string[];
    scheduledFor?: string;
  } | null;
  onClose: () => void;
  onSaved: () => void;
}

function toDatetimeLocalValue(iso?: string): string {
  const date = iso ? new Date(iso) : new Date(Date.now() + 3600 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

export function PostComposerModal({
  open,
  account,
  initialDraft,
  onClose,
  onSaved,
}: PostComposerModalProps) {
  const editingPost = initialDraft?.post || null;

  const [mediaType, setMediaType] = useState<"IMAGE" | "REELS" | "CAROUSEL" | "STORIES">("REELS");
  const [mediaUrls, setMediaUrls] = useState<string[]>([]);
  const [nonHttpsWarning, setNonHttpsWarning] = useState(false);
  const [mediaWarnings, setMediaWarnings] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [toneOverride, setToneOverride] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [customTagInput, setCustomTagInput] = useState("");
  const [timingMode, setTimingMode] = useState<"best" | "now" | "custom">("best");
  const [customDatetime, setCustomDatetime] = useState(toDatetimeLocalValue());
  const [aiPackage, setAiPackage] = useState<GeneratedPostPackage | null>(null);

  const [uploading, setUploading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    setMediaWarnings([]);
    setAiPackage(null);
    if (editingPost) {
      setMediaType(editingPost.media_type);
      setMediaUrls(editingPost.media_urls);
      setCaption(editingPost.caption);
      setHashtags(editingPost.hashtags);
      setTimingMode("custom");
      setCustomDatetime(toDatetimeLocalValue(editingPost.scheduled_for));
    } else {
      setMediaType(initialDraft?.mediaType || "REELS");
      setMediaUrls(initialDraft?.mediaUrls || []);
      setCaption(initialDraft?.caption || "");
      setHashtags(initialDraft?.hashtags || []);
      setTimingMode(initialDraft?.scheduledFor ? "custom" : "best");
      setCustomDatetime(toDatetimeLocalValue(initialDraft?.scheduledFor));
      setTopic("");
      setToneOverride("");
    }
  }, [open, editingPost, initialDraft]);

  if (!open) return null;

  async function handleFileUpload(files: FileList | File[] | null) {
    const fileArray = files ? Array.from(files) : [];
    if (fileArray.length === 0) return;
    setError("");
    setUploading(true);
    try {
      const uploadedUrls: string[] = [];
      const collectedWarnings: string[] = [];
      let detectedType = mediaType;
      let anyNonHttps = false;

      for (const file of fileArray) {
        const res = await api.uploadMedia(
          file,
          mediaType === "STORIES" ? "STORIES" : undefined
        );
        uploadedUrls.push(res.url);
        if (!res.is_public_https) anyNonHttps = true;
        if (res.warnings?.length) {
          collectedWarnings.push(...res.warnings);
        }
        if (mediaType !== "STORIES" && fileArray.length === 1) {
          detectedType = res.media_type;
        }
      }
      setNonHttpsWarning(anyNonHttps);
      setMediaWarnings(collectedWarnings);
      const merged =
        mediaType === "STORIES"
          ? uploadedUrls.slice(0, 1)
          : [...mediaUrls, ...uploadedUrls].slice(0, 10);
      setMediaUrls(merged);
      if (mediaType !== "STORIES") {
        if (merged.length > 1) {
          setMediaType("CAROUSEL");
        } else {
          setMediaType(detectedType);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
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
      handleFileUpload(files);
    }
  }

  async function handleGenerateAI() {
    if (!topic.trim()) {
      setError("Describe your Reel or Post topic first so AI can craft hooks & trending hashtags.");
      return;
    }
    setError("");
    setGenerating(true);
    try {
      const pkg = await api.generatePostPackage(account.id, {
        topic: topic.trim(),
        media_type: mediaType,
        tone_override: toneOverride.trim(),
        hashtags_count: mediaType === "STORIES" ? 3 : 15,
      });
      setAiPackage(pkg);
      setCaption(pkg.recommended_caption);
      setHashtags(pkg.hashtags.map((h) => h.hashtag));
      if (pkg.recommended_slot) {
        setCustomDatetime(toDatetimeLocalValue(pkg.recommended_slot.scheduled_for));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI generation failed.");
    } finally {
      setGenerating(false);
    }
  }

  function addCustomHashtag() {
    const clean = customTagInput.trim().replaceAll(" ", "").toLowerCase();
    if (!clean) return;
    const tag = clean.startsWith("#") ? clean : `#${clean}`;
    if (!hashtags.includes(tag)) {
      setHashtags([...hashtags, tag]);
    }
    setCustomTagInput("");
  }

  function toggleHashtag(tag: string) {
    if (hashtags.includes(tag)) {
      setHashtags(hashtags.filter((h) => h !== tag));
    } else {
      setHashtags([...hashtags, tag]);
    }
  }

  async function handleSubmit() {
    if (mediaUrls.length === 0) {
      setError("Upload at least one image or video (or paste a public HTTPS URL) before saving.");
      return;
    }
    if (mediaType === "CAROUSEL" && mediaUrls.length < 2) {
      setError("Carousel posts require at least 2 images/videos.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      if (editingPost) {
        await api.updatePost(editingPost.id, {
          caption,
          hashtags,
          media_urls: mediaUrls,
          scheduled_for: new Date(customDatetime).toISOString(),
        });
      } else if (timingMode === "now") {
        await api.publishNow(account.id, {
          media_type: mediaType,
          media_urls: mediaUrls,
          caption,
          hashtags,
          share_to_feed: true,
        });
      } else {
        const scheduledIso =
          timingMode === "custom"
            ? new Date(customDatetime).toISOString()
            : aiPackage?.recommended_slot?.scheduled_for || null;

        await api.schedulePosts(account.id, [
          {
            media_type: mediaType,
            media_urls: mediaUrls,
            caption,
            hashtags,
            share_to_feed: true,
            scheduled_for: scheduledIso,
            status: "QUEUED",
            ai_rationale: aiPackage?.recommended_slot?.rationale || "",
            predicted_score: aiPackage?.recommended_slot?.combined_score || 0,
          },
        ]);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save or publish post.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      onPaste={handlePaste}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
    >
      <div className="glass-panel flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-3xl shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
          <div>
            <h2 className="text-base font-bold text-white">
              {editingPost ? "Edit Scheduled Post" : "AI Post & Reel Studio"}
            </h2>
            <p className="text-xs text-slate-400">
              Preview everything live, edit caption or hashtags, then publish now or auto-queue.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-white/10 p-2 text-slate-400 hover:bg-white/5 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid flex-1 gap-6 overflow-y-auto p-6 lg:grid-cols-[1.25fr_350px]">
          {/* Left Editor */}
          <div className="space-y-5">
            {error && (
              <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
                {error}
              </div>
            )}

            {nonHttpsWarning && (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                Local preview mode active (`MEDIA_STORAGE_PROVIDER=local`). Before live Instagram publishing on Meta, set `MEDIA_STORAGE_PROVIDER=cloudinary` in `.env` so media has a public HTTPS URL.
              </div>
            )}

            {mediaWarnings.length > 0 && (
              <div className="space-y-1 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                <p className="font-semibold">Instagram Media Spec Check:</p>
                {mediaWarnings.map((warn, i) => (
                  <p key={i}>• {warn}</p>
                ))}
              </div>
            )}

            {/* Top Mode Tabs: Post / Reel vs Instagram Story (24h) */}
            {!editingPost && (
              <div className="grid grid-cols-2 gap-2 rounded-2xl border border-white/10 bg-black/40 p-1.5">
                <button
                  type="button"
                  onClick={() => {
                    if (mediaType === "STORIES") setMediaType("REELS");
                  }}
                  className={`rounded-xl px-3 py-2 text-xs font-semibold transition ${
                    mediaType !== "STORIES"
                      ? "bg-emerald-500 text-slate-950 shadow-sm"
                      : "text-slate-300 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  Feed Post / Reel / Carousel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMediaType("STORIES");
                    if (mediaUrls.length > 1) setMediaUrls(mediaUrls.slice(0, 1));
                  }}
                  className={`rounded-xl px-3 py-2 text-xs font-semibold transition ${
                    mediaType === "STORIES"
                      ? "bg-gradient-to-r from-amber-400 via-pink-500 to-purple-500 text-white shadow-sm"
                      : "text-slate-300 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  Instagram Story (24h)
                </button>
              </div>
            )}

            {/* Format + Upload */}
            <div className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.02] p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-semibold text-white">
                  1. {mediaType === "STORIES" ? "Story Visual (9:16 Photo or Video ≤ 60s)" : "Media & Format"}
                </span>
                {mediaType !== "STORIES" && (
                  <div className="flex gap-1 rounded-xl border border-white/10 bg-black/40 p-1">
                    {(["REELS", "IMAGE", "CAROUSEL"] as const).map((fmt) => (
                      <button
                        key={fmt}
                        type="button"
                        onClick={() => setMediaType(fmt)}
                        className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition ${
                          mediaType === fmt
                            ? "bg-emerald-500 text-slate-950"
                            : "text-slate-400 hover:text-white"
                        }`}
                      >
                        {fmt}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-white/20 bg-black/30 px-4 py-4 text-xs text-slate-300 transition hover:border-emerald-400/60">
                {uploading ? (
                  <Loader2 className="h-4 w-4 animate-spin text-emerald-400" />
                ) : (
                  <Upload className="h-4 w-4 text-emerald-400" />
                )}
                <span>
                  {uploading
                    ? "Uploading media..."
                    : "Click to upload Photo / Carousel / Reel (.mp4, .mov, .jpg, .png)"}
                </span>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
                  multiple={mediaType === "CAROUSEL"}
                  className="hidden"
                  onChange={(e) => handleFileUpload(e.target.files)}
                />
              </label>

              {mediaUrls.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {mediaUrls.map((url, idx) => (
                    <div
                      key={`${url}-${idx}`}
                      className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/40 px-2.5 py-1 text-[11px] text-slate-300"
                    >
                      <span className="max-w-[190px] truncate">{url}</span>
                      <button
                        type="button"
                        onClick={() => setMediaUrls(mediaUrls.filter((_, i) => i !== idx))}
                        className="text-slate-500 hover:text-rose-400"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* AI Prompt */}
            <div className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.02] p-4">
              <span className="text-xs font-semibold text-white">
                {mediaType === "STORIES"
                  ? "2. AI Story Concept, On-Screen Text & Sticker Ideas (Any Style)"
                  : "2. AI Caption & Trending Reach Hashtags"}
              </span>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder={
                    mediaType === "STORIES"
                      ? "Describe your Story idea (e.g., behind-the-scenes product launch poll)"
                      : "Describe your Reel/Post (e.g., 3 AI workflows for small businesses)"
                  }
                  className="flex-1 rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-xs text-white placeholder-slate-500 outline-none focus:border-emerald-400"
                />
                <button
                  type="button"
                  onClick={handleGenerateAI}
                  disabled={generating}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-emerald-500 to-indigo-500 px-4 py-2 text-xs font-semibold text-slate-950 disabled:opacity-50"
                >
                  {generating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  {mediaType === "STORIES" ? "Generate Story Ideas" : "Generate AI Copy"}
                </button>
              </div>
              <input
                value={toneOverride}
                onChange={(e) => setToneOverride(e.target.value)}
                placeholder={
                  mediaType === "STORIES"
                    ? "Custom style/vibe (optional: minimal quote, flash sale urgency, casual Hinglish poll...)"
                    : "Custom style/tone (optional: storytelling, bold founder take, Hinglish, educational...)"
                }
                className="w-full rounded-xl border border-white/10 bg-black/30 px-3.5 py-1.5 text-[11px] text-slate-200 placeholder-slate-500 outline-none focus:border-indigo-400"
              />

              {aiPackage && aiPackage.variants.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {aiPackage.variants.map((v) => (
                    <button
                      key={v.angle}
                      type="button"
                      onClick={() =>
                        setCaption(`${v.hook}\n\n${v.caption_body}\n\n${v.call_to_action}`)
                      }
                      className="rounded-lg border border-indigo-400/30 bg-indigo-500/10 px-2.5 py-1 text-[11px] font-medium text-indigo-200 hover:bg-indigo-500/20"
                    >
                      Use &ldquo;{v.angle}&rdquo; Hook
                    </button>
                  ))}
                </div>
              )}

              <div>
                <label className="text-[11px] text-slate-400">
                  {mediaType === "STORIES"
                    ? "On-Screen Story Text / Sticker Plan (previewed live — Meta publishes the Story image/video directly)"
                    : "Editable Caption"}
                </label>
                <textarea
                  rows={mediaType === "STORIES" ? 4 : 5}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                  placeholder={
                    mediaType === "STORIES"
                      ? "Write on-screen text or interactive poll/question sticker ideas for your Story..."
                      : "Write or refine your caption..."
                  }
                  className="mt-1 w-full rounded-xl border border-white/15 bg-black/40 p-3 text-xs leading-relaxed text-white outline-none focus:border-emerald-400"
                />
              </div>

              {/* Hashtag Editor */}
              <div>
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-1.5 text-[11px] text-slate-400">
                    <Hash className="h-3 w-3 text-emerald-400" />
                    Trending &amp; Niche Hashtags ({hashtags.length})
                  </label>
                  {aiPackage && (
                    <span className="text-[10px] text-slate-400">
                      Meta 7d quota: {aiPackage.meta_weekly_quota_used}/
                      {aiPackage.meta_weekly_quota_limit}
                    </span>
                  )}
                </div>

                {aiPackage && (
                  <p className="mt-1 text-[11px] text-emerald-300/90">
                    {aiPackage.hashtag_strategy_note}
                  </p>
                )}

                <div className="mt-2 flex flex-wrap gap-1.5">
                  {hashtags.map((tag) => {
                    const metaInfo = aiPackage?.hashtags.find((h) => h.hashtag === tag);
                    return (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => toggleHashtag(tag)}
                        title={
                          metaInfo
                            ? `${metaInfo.tier}${metaInfo.rankability ? ` • ${metaInfo.rankability}` : ""}${
                                metaInfo.historical_lift ? ` (${metaInfo.historical_lift}x lift)` : ""
                              } — ${metaInfo.why_it_boosts_reach}`
                            : "Click to remove"
                        }
                        className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200 hover:border-rose-400/50 hover:bg-rose-500/10"
                      >
                        <span>{tag}</span>
                        {metaInfo?.rankability && metaInfo.rankability !== "unknown" && (
                          <span
                            className={`rounded px-1 text-[9px] font-semibold uppercase ${
                              metaInfo.rankability === "winnable"
                                ? "bg-emerald-400/20 text-emerald-300"
                                : metaInfo.rankability === "stretch"
                                  ? "bg-amber-400/20 text-amber-300"
                                  : "bg-rose-400/20 text-rose-300"
                            }`}
                          >
                            {metaInfo.rankability}
                          </span>
                        )}
                        {metaInfo?.meta_validated && (
                          <Check className="h-2.5 w-2.5 text-emerald-400" />
                        )}
                        <X className="h-2.5 w-2.5 opacity-60" />
                      </button>
                    );
                  })}
                </div>

                <div className="mt-2 flex gap-2">
                  <input
                    value={customTagInput}
                    onChange={(e) => setCustomTagInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addCustomHashtag())}
                    placeholder="Add custom #hashtag"
                    className="flex-1 rounded-lg border border-white/10 bg-black/40 px-3 py-1.5 text-xs text-white outline-none focus:border-emerald-400"
                  />
                  <button
                    type="button"
                    onClick={addCustomHashtag}
                    className="inline-flex items-center gap-1 rounded-lg border border-white/15 px-3 py-1.5 text-xs text-slate-200 hover:bg-white/5"
                  >
                    <Plus className="h-3.5 w-3.5" /> Add
                  </button>
                </div>
              </div>
            </div>

            {/* Timing Selector */}
            <div className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.02] p-4">
              <span className="flex items-center gap-1.5 text-xs font-semibold text-white">
                <CalendarClock className="h-4 w-4 text-indigo-400" />
                3. When to Publish ({account.timezone})
              </span>

              <div className="grid gap-2 sm:grid-cols-3">
                {!editingPost && (
                  <button
                    type="button"
                    onClick={() => setTimingMode("best")}
                    className={`rounded-xl border px-3 py-2 text-left text-xs transition ${
                      timingMode === "best"
                        ? "border-emerald-400 bg-emerald-500/15 text-white"
                        : "border-white/10 bg-black/30 text-slate-400"
                    }`}
                  >
                    <p className="font-semibold">AI Best Time</p>
                    <p className="text-[10px] opacity-80">Peak reach slot</p>
                  </button>
                )}
                {!editingPost && (
                  <button
                    type="button"
                    onClick={() => setTimingMode("now")}
                    className={`rounded-xl border px-3 py-2 text-left text-xs transition ${
                      timingMode === "now"
                        ? "border-indigo-400 bg-indigo-500/15 text-white"
                        : "border-white/10 bg-black/30 text-slate-400"
                    }`}
                  >
                    <p className="font-semibold">Post Now</p>
                    <p className="text-[10px] opacity-80">Publish immediately</p>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setTimingMode("custom")}
                  className={`rounded-xl border px-3 py-2 text-left text-xs transition ${
                    timingMode === "custom"
                      ? "border-emerald-400 bg-emerald-500/15 text-white"
                      : "border-white/10 bg-black/30 text-slate-400"
                  }`}
                >
                  <p className="font-semibold">Custom Time</p>
                  <p className="text-[10px] opacity-80">Pick exact date/time</p>
                </button>
              </div>

              {timingMode === "best" && aiPackage?.recommended_slot && (
                <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-3 text-xs text-emerald-200">
                  <strong>Recommended: {aiPackage.recommended_slot.local_display}</strong>
                  <p className="mt-1 text-[11px] opacity-90">
                    {aiPackage.recommended_slot.rationale}
                  </p>
                </div>
              )}

              {timingMode === "custom" && (
                <input
                  type="datetime-local"
                  value={customDatetime}
                  onChange={(e) => setCustomDatetime(e.target.value)}
                  className="w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-xs text-white outline-none focus:border-emerald-400 sm:w-72"
                />
              )}
            </div>
          </div>

          {/* Right Live Preview */}
          <div className="space-y-4">
            <p className="text-center text-xs font-semibold uppercase tracking-wider text-slate-400">
              Pre-Publish Live Preview
            </p>
            <InstagramPreview
              username={account.username}
              avatarUrl={account.profile_picture_url}
              mediaType={mediaType}
              mediaUrls={mediaUrls}
              caption={caption}
              hashtags={hashtags}
              scheduledLabel={
                timingMode === "now"
                  ? "Publishing Now"
                  : timingMode === "best"
                    ? aiPackage?.recommended_slot?.local_display || "AI Optimal Slot"
                    : new Date(customDatetime).toLocaleDateString()
              }
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-white/10 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-white/15 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-white/5"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-5 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-50"
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
            {editingPost
              ? "Save Changes"
              : timingMode === "now"
                ? "Confirm & Post Now"
                : "Approve & Add to Queue"}
          </button>
        </div>
      </div>
    </div>
  );
}
