"use client";

import { Bookmark, Heart, MessageCircle, Play, Send, Sparkles } from "lucide-react";

interface InstagramPreviewProps {
  username: string;
  avatarUrl?: string;
  mediaType: "IMAGE" | "REELS" | "CAROUSEL";
  mediaUrls: string[];
  caption: string;
  hashtags: string[];
  scheduledLabel?: string;
}

export function InstagramPreview({
  username,
  avatarUrl,
  mediaType,
  mediaUrls,
  caption,
  hashtags,
  scheduledLabel,
}: InstagramPreviewProps) {
  const firstUrl = mediaUrls[0] || "";
  const isVideo =
    mediaType === "REELS" ||
    firstUrl.toLowerCase().split("?")[0].endsWith(".mp4") ||
    firstUrl.toLowerCase().split("?")[0].endsWith(".mov");

  const fullTags = hashtags
    .map((h) => (h.startsWith("#") ? h : `#${h}`))
    .join(" ");

  return (
    <div className="mx-auto w-full max-w-[330px] overflow-hidden rounded-[28px] border border-white/15 bg-[#0b0e16] shadow-2xl">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full bg-gradient-to-tr from-amber-400 via-pink-500 to-purple-600 p-[1.5px]">
            {avatarUrl ? (
              <img
                src={avatarUrl}
                alt={username}
                className="h-full w-full rounded-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center rounded-full bg-slate-950 text-xs font-bold text-white">
                {username.slice(0, 2).toUpperCase()}
              </div>
            )}
          </div>
          <div>
            <p className="text-xs font-semibold text-white">@{username || "your_brand"}</p>
            <p className="text-[10px] text-slate-400">
              {mediaType === "REELS" ? "Reels Preview" : mediaType === "CAROUSEL" ? `Carousel (${mediaUrls.length})` : "Feed Post"}
            </p>
          </div>
        </div>
        {scheduledLabel && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
            <Sparkles className="h-2.5 w-2.5" />
            {scheduledLabel}
          </span>
        )}
      </div>

      <div
        className={`relative flex items-center justify-center bg-slate-950 ${
          mediaType === "REELS" ? "aspect-[9/12]" : "aspect-square"
        }`}
      >
        {firstUrl ? (
          isVideo ? (
            <video
              src={firstUrl}
              controls
              muted
              className="h-full w-full object-cover"
            />
          ) : (
            <img
              src={firstUrl}
              alt="Post media preview"
              className="h-full w-full object-cover"
            />
          )
        ) : (
          <div className="flex flex-col items-center gap-2 px-6 text-center text-slate-500">
            <Play className="h-8 w-8 opacity-40" />
            <p className="text-xs">Upload a photo or Reel video to preview it live</p>
          </div>
        )}
        {mediaUrls.length > 1 && (
          <span className="absolute right-3 top-3 rounded-full bg-black/70 px-2.5 py-0.5 text-[11px] font-medium text-white">
            1/{mediaUrls.length}
          </span>
        )}
      </div>

      <div className="space-y-2 p-4">
        <div className="flex items-center justify-between text-slate-200">
          <div className="flex items-center gap-3.5">
            <Heart className="h-5 w-5" />
            <MessageCircle className="h-5 w-5" />
            <Send className="h-5 w-5" />
          </div>
          <Bookmark className="h-5 w-5" />
        </div>

        <div className="max-h-36 overflow-y-auto text-xs leading-relaxed text-slate-200">
          <span className="mr-1.5 font-semibold text-white">@{username || "your_brand"}</span>
          <span className="whitespace-pre-wrap">{caption || "Your AI-crafted caption will appear here..."}</span>
          {fullTags && (
            <p className="mt-2 break-words text-[11px] font-medium text-indigo-300">
              {fullTags}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
