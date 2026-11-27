"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle,
  Bell,
  Calendar,
  CheckCircle2,
  ExternalLink,
  Instagram,
  LayoutDashboard,
  Rocket,
  Sparkles,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { saveSelectedAccountId } from "@/lib/selectedAccount";
import type { InstagramAccount, PublishActivityEvent } from "@/lib/types";

interface NavbarProps {
  accounts?: InstagramAccount[];
  selectedAccountId?: string;
  onSelectAccount?: (id: string) => void;
}

export function Navbar({
  accounts = [],
  selectedAccountId,
  onSelectAccount,
}: NavbarProps) {
  const pathname = usePathname();
  const activeAccountId = selectedAccountId || accounts[0]?.id || "";

  const [events, setEvents] = useState<PublishActivityEvent[]>([]);
  const [seenKeys, setSeenKeys] = useState<Record<string, boolean>>({});
  const [bellOpen, setBellOpen] = useState(false);
  const [toastEvent, setToastEvent] = useState<PublishActivityEvent | null>(null);
  const [browserAlertsEnabled, setBrowserAlertsEnabled] = useState(false);
  const initializedForAccRef = useRef<string>("");

  useEffect(() => {
    if (typeof window !== "undefined" && "Notification" in window) {
      setBrowserAlertsEnabled(Notification.permission === "granted");
    }
  }, []);

  useEffect(() => {
    if (!activeAccountId) {
      setEvents([]);
      return;
    }
    const storageKey = `socialmediaai_seen_activity_${activeAccountId}`;
    let storedMap: Record<string, boolean> = {};
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) storedMap = JSON.parse(raw) as Record<string, boolean>;
    } catch {
      storedMap = {};
    }
    setSeenKeys(storedMap);
    initializedForAccRef.current = "";

    let cancelled = false;
    const poll = () => {
      api
        .getPublishActivity(activeAccountId)
        .then(({ events: list }) => {
          if (cancelled) return;
          setEvents(list);

          let currentSeen: Record<string, boolean> = {};
          try {
            const raw = window.localStorage.getItem(storageKey);
            if (raw) currentSeen = JSON.parse(raw) as Record<string, boolean>;
          } catch {
            currentSeen = {};
          }

          // On first-ever visit for this account, mark older (>10m) history read so old items don't spam
          if (!initializedForAccRef.current) {
            initializedForAccRef.current = activeAccountId;
            if (Object.keys(currentSeen).length === 0 && list.length > 0) {
              const tenMinsAgo = Date.now() - 10 * 60 * 1000;
              const seeded: Record<string, boolean> = {};
              for (const ev of list) {
                const ts = new Date(ev.published_at || ev.updated_at).getTime();
                if (ts < tenMinsAgo) {
                  seeded[`${ev.id}:${ev.status}`] = true;
                }
              }
              currentSeen = seeded;
              try {
                window.localStorage.setItem(storageKey, JSON.stringify(seeded));
              } catch {
                // ignore
              }
              setSeenKeys(seeded);
            }
          }

          const newestUnseen = list.find((ev) => !currentSeen[`${ev.id}:${ev.status}`]);
          if (newestUnseen) {
            setToastEvent((prev) => {
              if (prev && prev.id === newestUnseen.id && prev.status === newestUnseen.status) {
                return prev;
              }
              if (typeof window !== "undefined") {
                window.dispatchEvent(new CustomEvent("socialmediaai:activity-updated"));
                if (
                  "Notification" in window &&
                  Notification.permission === "granted"
                ) {
                  try {
                    const n = new Notification(
                      newestUnseen.status === "PUBLISHED"
                        ? `Live on @${newestUnseen.username}!`
                        : `Publish Failed (@${newestUnseen.username})`,
                      {
                        body:
                          newestUnseen.status === "PUBLISHED"
                            ? `Your scheduled ${newestUnseen.media_type} was auto-published to Instagram.`
                            : newestUnseen.last_error || "Scheduled post failed.",
                      }
                    );
                    if (newestUnseen.published_permalink) {
                      n.onclick = () => window.open(newestUnseen.published_permalink, "_blank");
                    }
                  } catch {
                    // ignore browser notification errors
                  }
                }
              }
              return newestUnseen;
            });
          }
        })
        .catch(() => {});
    };

    poll();
    const timer = setInterval(poll, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activeAccountId]);

  const unreadCount = events.filter((ev) => !seenKeys[`${ev.id}:${ev.status}`]).length;

  function markAllRead() {
    if (!activeAccountId) return;
    const next: Record<string, boolean> = { ...seenKeys };
    for (const ev of events) {
      next[`${ev.id}:${ev.status}`] = true;
    }
    setSeenKeys(next);
    setToastEvent(null);
    try {
      window.localStorage.setItem(
        `socialmediaai_seen_activity_${activeAccountId}`,
        JSON.stringify(next)
      );
    } catch {
      // ignore
    }
  }

  function dismissToast(ev: PublishActivityEvent) {
    setToastEvent(null);
    if (!activeAccountId) return;
    const next = { ...seenKeys, [`${ev.id}:${ev.status}`]: true };
    setSeenKeys(next);
    try {
      window.localStorage.setItem(
        `socialmediaai_seen_activity_${activeAccountId}`,
        JSON.stringify(next)
      );
    } catch {
      // ignore
    }
  }

  async function enableDesktopAlerts() {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    const perm = await Notification.requestPermission();
    setBrowserAlertsEnabled(perm === "granted");
  }

  const navItems = [
    { href: "/onboarding", label: "Setup & Brand", icon: Rocket },
    { href: "/dashboard", label: "Analytics & Best Times", icon: LayoutDashboard },
    { href: "/planner", label: "Smart Planner", icon: Calendar },
  ];

  return (
    <>
      <header className="sticky top-0 z-40 border-b border-white/10 bg-[#07090e]/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link href="/planner" className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-indigo-500 text-slate-950 shadow-lg shadow-emerald-500/20">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <span className="text-sm font-bold tracking-tight text-white">
                SocialMedia<span className="text-emerald-400">AI</span>
              </span>
              <span className="block text-[11px] text-slate-400">
                Instagram Growth &amp; Auto-Planner
              </span>
            </div>
          </Link>

          <nav className="flex items-center gap-1 rounded-xl border border-white/10 bg-white/[0.03] p-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = pathname?.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                    active
                      ? "bg-white/10 text-white shadow-sm"
                      : "text-slate-400 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="flex items-center gap-2">
            {accounts.length > 0 && (
              <div className="relative">
                <button
                  type="button"
                  onClick={() => {
                    const nextOpen = !bellOpen;
                    setBellOpen(nextOpen);
                    if (nextOpen && unreadCount > 0) markAllRead();
                  }}
                  title="Publish Notifications"
                  className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-slate-300 transition hover:bg-white/10 hover:text-white"
                >
                  <Bell className="h-4 w-4" />
                  {unreadCount > 0 && (
                    <span className="absolute -right-1 -top-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-emerald-500 px-1 text-[10px] font-bold text-slate-950">
                      {unreadCount}
                    </span>
                  )}
                </button>

                {bellOpen && (
                  <div className="absolute right-0 mt-2 w-80 overflow-hidden rounded-2xl border border-white/15 bg-[#0e131f] shadow-2xl">
                    <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5">
                      <span className="text-xs font-semibold text-white">
                        Publish Alerts
                      </span>
                      <div className="flex items-center gap-2">
                        {!browserAlertsEnabled &&
                          typeof window !== "undefined" &&
                          "Notification" in window && (
                            <button
                              type="button"
                              onClick={enableDesktopAlerts}
                              className="rounded bg-indigo-500/20 px-2 py-0.5 text-[10px] font-medium text-indigo-200 hover:bg-indigo-500/30"
                            >
                              Enable Browser Alerts
                            </button>
                          )}
                        <button
                          type="button"
                          onClick={() => setBellOpen(false)}
                          className="text-slate-400 hover:text-white"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                    <div className="max-h-80 divide-y divide-white/5 overflow-y-auto">
                      {events.length === 0 ? (
                        <p className="p-4 text-center text-xs text-slate-400">
                          No published or failed posts yet. When a scheduled post goes live, you&apos;ll see its link right here!
                        </p>
                      ) : (
                        events.map((ev) => (
                          <div key={`${ev.id}-${ev.status}`} className="p-3 text-xs">
                            <div className="flex items-center justify-between gap-2">
                              <span
                                className={`inline-flex items-center gap-1 font-semibold ${
                                  ev.status === "PUBLISHED"
                                    ? "text-emerald-300"
                                    : "text-rose-300"
                                }`}
                              >
                                {ev.status === "PUBLISHED" ? (
                                  <CheckCircle2 className="h-3.5 w-3.5" />
                                ) : (
                                  <AlertTriangle className="h-3.5 w-3.5" />
                                )}
                                {ev.status === "PUBLISHED"
                                  ? `${ev.media_type} is live!`
                                  : `${ev.media_type} failed`}
                              </span>
                              <span className="text-[10px] text-slate-500">
                                {new Date(ev.published_at || ev.updated_at).toLocaleTimeString([], {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </span>
                            </div>
                            {ev.caption_preview && (
                              <p className="mt-1 truncate text-[11px] text-slate-400">
                                {ev.caption_preview}
                              </p>
                            )}
                            {ev.last_error && (
                              <p className="mt-1 text-[11px] text-rose-300">{ev.last_error}</p>
                            )}
                            {ev.published_permalink && (
                              <a
                                href={ev.published_permalink}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-emerald-300 hover:underline"
                              >
                                View on Instagram <ExternalLink className="h-3 w-3" />
                              </a>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {accounts.length > 0 ? (
              <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5">
                <Instagram className="h-4 w-4 text-pink-400" />
                <select
                  value={selectedAccountId || accounts[0]?.id}
                  onChange={(e) => {
                    saveSelectedAccountId(e.target.value);
                    onSelectAccount?.(e.target.value);
                  }}
                  className="bg-transparent text-xs font-medium text-white outline-none"
                >
                  {accounts.map((acc) => (
                    <option key={acc.id} value={acc.id} className="bg-slate-900 text-white">
                      @{acc.username}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <Link
                href="/onboarding"
                className="rounded-xl bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400"
              >
                Connect IG Business
              </Link>
            )}
          </div>
        </div>
      </header>

      {/* Global Live Toast Notification Across All Pages */}
      {toastEvent && (
        <div className="fixed bottom-5 left-5 z-50 flex max-w-sm items-start gap-3 rounded-2xl border border-emerald-500/35 bg-[#0e1621]/95 p-3.5 text-xs shadow-2xl backdrop-blur-xl">
          {toastEvent.status === "PUBLISHED" ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
          ) : (
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
          )}
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-white">
              {toastEvent.status === "PUBLISHED"
                ? `Your ${toastEvent.media_type} is live on @${toastEvent.username}!`
                : `Scheduled ${toastEvent.media_type} failed`}
            </p>
            {toastEvent.caption_preview && (
              <p className="mt-0.5 truncate text-[11px] text-slate-400">
                {toastEvent.caption_preview}
              </p>
            )}
            {toastEvent.last_error && (
              <p className="mt-0.5 text-[11px] text-rose-300">{toastEvent.last_error}</p>
            )}
            {toastEvent.published_permalink && (
              <a
                href={toastEvent.published_permalink}
                target="_blank"
                rel="noreferrer"
                className="mt-1.5 inline-flex items-center gap-1 rounded-lg bg-emerald-500 px-2.5 py-1 text-[11px] font-semibold text-slate-950 hover:bg-emerald-400"
              >
                View Live Post <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
          <button
            type="button"
            onClick={() => dismissToast(toastEvent)}
            className="text-slate-400 hover:text-white"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </>
  );
}
