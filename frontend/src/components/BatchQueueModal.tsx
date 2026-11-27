"use client";

import { useState } from "react";
import { CalendarPlus, Loader2, Plus, Sparkles, Trash2, Upload, X } from "lucide-react";
import { api } from "@/lib/api";
import type { InstagramAccount } from "@/lib/types";

interface BatchItemDraft {
  topic: string;
  media_type: "REELS" | "IMAGE" | "CAROUSEL";
  media_urls: string[];
  caption: string;
  hashtags: string[];
  scheduled_for: string;
  local_display: string;
  rationale: string;
  combined_score: number;
}

interface BatchQueueModalProps {
  open: boolean;
  account: InstagramAccount;
  onClose: () => void;
  onQueued: () => void;
}

export function BatchQueueModal({
  open,
  account,
  onClose,
  onQueued,
}: BatchQueueModalProps) {
  const [items, setItems] = useState<BatchItemDraft[]>([
    {
      topic: "",
      media_type: "REELS",
      media_urls: [],
      caption: "",
      hashtags: [],
      scheduled_for: "",
      local_display: "",
      rationale: "",
      combined_score: 0,
    },
    {
      topic: "",
      media_type: "REELS",
      media_urls: [],
      caption: "",
      hashtags: [],
      scheduled_for: "",
      local_display: "",
      rationale: "",
      combined_score: 0,
    },
  ]);
  const [planning, setPlanning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadingIdx, setUploadingIdx] = useState<number | null>(null);
  const [error, setError] = useState("");

  if (!open) return null;

  function addRow() {
    setItems([
      ...items,
      {
        topic: "",
        media_type: "REELS",
        media_urls: [],
        caption: "",
        hashtags: [],
        scheduled_for: "",
        local_display: "",
        rationale: "",
        combined_score: 0,
      },
    ]);
  }

  async function handleUploadForRow(idx: number, file: File | undefined) {
    if (!file) return;
    setUploadingIdx(idx);
    setError("");
    try {
      const res = await api.uploadMedia(file);
      setItems((prev) =>
        prev.map((row, i) =>
          i === idx
            ? { ...row, media_urls: [res.url], media_type: res.media_type }
            : row
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploadingIdx(null);
    }
  }

  async function handleSmartPlanAll() {
    if (items.some((it) => !it.topic.trim())) {
      setError("Please add a topic description for every post in the batch.");
      return;
    }
    setError("");
    setPlanning(true);
    try {
      const res = await api.planBatch(account.id, {
        items: items.map((it) => ({
          topic: it.topic.trim(),
          media_type: it.media_type,
          media_urls: it.media_urls,
        })),
        generate_copy: true,
      });
      setItems((prev) =>
        prev.map((row, i) => {
          const planned = res.planned_items[i];
          if (!planned) return row;
          return {
            ...row,
            caption: planned.recommended_caption || row.caption,
            hashtags: planned.hashtags.map((h) => h.hashtag),
            scheduled_for: planned.scheduled_for,
            local_display: planned.local_display,
            rationale: planned.rationale,
            combined_score: planned.combined_score,
          };
        })
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to plan batch.");
    } finally {
      setPlanning(false);
    }
  }

  async function handleQueueAll() {
    if (items.some((it) => it.media_urls.length === 0)) {
      setError("Every post needs an uploaded photo or Reel video before queuing.");
      return;
    }
    setError("");
    setSaving(true);
    try {
      await api.schedulePosts(
        account.id,
        items.map((it) => ({
          media_type: it.media_type,
          media_urls: it.media_urls,
          caption: it.caption,
          hashtags: it.hashtags,
          scheduled_for: it.scheduled_for || null,
          status: "QUEUED",
          ai_rationale: it.rationale,
          predicted_score: it.combined_score,
        }))
      );
      onQueued();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to queue batch.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
      <div className="glass-panel flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-3xl shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
          <div>
            <h2 className="flex items-center gap-2 text-base font-bold text-white">
              <CalendarPlus className="h-4 w-4 text-emerald-400" />
              Smart Multi-Post Batch Queue
            </h2>
            <p className="text-xs text-slate-400">
              Bot automatically spaces multiple posts across your account&apos;s highest-reach days to avoid audience fatigue.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-white/10 p-2 text-slate-400 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {error && (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
              {error}
            </div>
          )}

          {items.map((row, idx) => (
            <div
              key={idx}
              className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.02] p-4"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-emerald-300">Post #{idx + 1}</span>
                {items.length > 1 && (
                  <button
                    type="button"
                    onClick={() => setItems(items.filter((_, i) => i !== idx))}
                    className="text-slate-500 hover:text-rose-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
                <input
                  value={row.topic}
                  onChange={(e) =>
                    setItems((prev) =>
                      prev.map((r, i) => (i === idx ? { ...r, topic: e.target.value } : r))
                    )
                  }
                  placeholder="Topic / Reel hook (e.g., Behind the scenes of our workflow)"
                  className="rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-xs text-white outline-none focus:border-emerald-400"
                />
                <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-3.5 py-2 text-xs text-slate-200 hover:bg-white/10">
                  {uploadingIdx === idx ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-400" />
                  ) : (
                    <Upload className="h-3.5 w-3.5 text-emerald-400" />
                  )}
                  <span>{row.media_urls.length > 0 ? "Media Ready ✓" : "Upload Media"}</span>
                  <input
                    type="file"
                    accept="image/*,video/mp4,video/quicktime"
                    className="hidden"
                    onChange={(e) => handleUploadForRow(idx, e.target.files?.[0])}
                  />
                </label>
              </div>

              {row.local_display && (
                <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-3 text-xs text-emerald-200">
                  <strong>Recommended Slot: {row.local_display}</strong>
                  <p className="mt-1 text-[11px] opacity-90">{row.rationale}</p>
                </div>
              )}

              {row.caption && (
                <textarea
                  rows={3}
                  value={row.caption}
                  onChange={(e) =>
                    setItems((prev) =>
                      prev.map((r, i) => (i === idx ? { ...r, caption: e.target.value } : r))
                    )
                  }
                  className="w-full rounded-xl border border-white/10 bg-black/40 p-2.5 text-xs text-slate-200 outline-none focus:border-emerald-400"
                />
              )}
              {row.hashtags.length > 0 && (
                <p className="text-[11px] text-indigo-300">{row.hashtags.join(" ")}</p>
              )}
            </div>
          ))}

          <button
            type="button"
            onClick={addRow}
            className="inline-flex items-center gap-1.5 rounded-xl border border-dashed border-white/20 px-4 py-2 text-xs text-slate-300 hover:border-emerald-400/50 hover:text-white"
          >
            <Plus className="h-3.5 w-3.5" /> Add Another Post
          </button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 px-6 py-4">
          <button
            type="button"
            onClick={handleSmartPlanAll}
            disabled={planning}
            className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-emerald-500 px-4 py-2.5 text-xs font-semibold text-slate-950 disabled:opacity-50"
          >
            {planning ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" />
            )}
            AI Smart-Space &amp; Draft All
          </button>

          <button
            type="button"
            onClick={handleQueueAll}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-5 py-2.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
          >
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Approve &amp; Queue All ({items.length})
          </button>
        </div>
      </div>
    </div>
  );
}
