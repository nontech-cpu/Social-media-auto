"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Globe,
  Instagram,
  Loader2,
  Palette,
  Share2,
  Sparkles,
  Store,
} from "lucide-react";
import { api } from "@/lib/api";
import { pickInitialAccountId, saveSelectedAccountId } from "@/lib/selectedAccount";
import type {
  BrandProfile,
  GoogleBusinessProfileSummary,
  InstagramAccount,
} from "@/lib/types";
import { Navbar } from "@/components/Navbar";

const TIMEZONES = [
  "Asia/Kolkata",
  "UTC",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/London",
  "Asia/Dubai",
  "Asia/Singapore",
];

function OnboardingWizard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [language, setLanguage] = useState("English");
  const [brand, setBrand] = useState<Partial<BrandProfile>>({
    brand_name: "",
    niche: "",
    description: "",
    tone_of_voice: "Authentic, engaging, value-driven",
    target_audience: "",
    language: "English",
    colors: [],
    content_pillars: [],
    posting_goal_per_week: 4,
  });
  const [accounts, setAccounts] = useState<InstagramAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [selectedTz, setSelectedTz] = useState("UTC");
  const [loading, setLoading] = useState(false);
  const [gbpLoading, setGbpLoading] = useState(false);
  const [error, setError] = useState("");
  const [metaConfigured, setMetaConfigured] = useState(true);
  const [gbpConfigured, setGbpConfigured] = useState(true);
  const [gbpProfiles, setGbpProfiles] = useState<GoogleBusinessProfileSummary[]>([]);

  useEffect(() => {
    try {
      const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (browserTz) setSelectedTz(browserTz);
    } catch {
      // Fallback remains UTC
    }
    const errParam = searchParams.get("error") || searchParams.get("gbp_error");
    if (errParam) {
      setError(errParam);
      setStep(3);
    }

    Promise.all([
      api.getBrand(),
      api.listAccounts(),
      api.health(),
      api.listGoogleBusinessProfiles().catch(() => ({ profiles: [] })),
    ])
      .then(([brandRes, accRes, healthRes, gbpRes]) => {
        if (brandRes.brand) {
          setBrand(brandRes.brand);
          setWebsiteUrl(brandRes.brand.website_url || "");
          setLanguage(brandRes.brand.language || "English");
        }
        setAccounts(accRes.accounts);
        if (accRes.accounts.length > 0) {
          setSelectedAccountId(pickInitialAccountId(accRes.accounts));
        }
        setMetaConfigured(healthRes.meta_configured);
        setGbpConfigured(Boolean(healthRes.google_business_configured));
        setGbpProfiles(gbpRes.profiles || []);
      })
      .catch(() => {
        // backend may still be starting
      });
  }, [searchParams]);

  async function handleFetchBrand() {
    if (!websiteUrl.trim()) {
      setError("Enter a website URL or click Continue to fill your Brand Kit manually.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const res = await api.fetchBrandFromWebsite(websiteUrl.trim(), language);
      setBrand(res.brand);
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch website metadata.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveBrandAndNext() {
    if (!brand.brand_name?.trim()) {
      setError("Please enter your Brand Name.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const res = await api.saveBrand({
        ...brand,
        brand_name: brand.brand_name.trim(),
        website_url: websiteUrl.trim(),
        language,
      });
      setBrand(res.brand);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save Brand Kit.");
    } finally {
      setLoading(false);
    }
  }

  async function handleConnectInstagram() {
    setError("");
    setLoading(true);
    try {
      const { oauth_url } = await api.getInstagramOAuthUrl(brand.id || "", selectedTz);
      window.location.href = oauth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start Instagram OAuth.");
      setLoading(false);
    }
  }

  async function handleConnectGoogleBusiness() {
    setError("");
    setGbpLoading(true);
    try {
      const { oauth_url } = await api.getGoogleBusinessOAuthUrl(brand.id || "", selectedTz);
      window.location.href = oauth_url;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not start Google Business OAuth."
      );
      setGbpLoading(false);
    }
  }

  const stepsMeta = [
    {
      num: 1 as const,
      title: "Website",
      desc: "AI reads your website and fills your Brand Kit automatically.",
      icon: Globe,
    },
    {
      num: 2 as const,
      title: "Your brand",
      desc: "Your name, niche, voice, language, and posting cadence.",
      icon: Palette,
    },
    {
      num: 3 as const,
      title: "Instagram Business",
      desc: "Direct Meta OAuth for Analytics, Hashtags & Auto-Posting.",
      icon: Share2,
    },
  ];

  return (
    <div className="min-h-screen">
      <Navbar
        accounts={accounts}
        selectedAccountId={selectedAccountId}
        onSelectAccount={(id) => {
          saveSelectedAccountId(id);
          setSelectedAccountId(id);
        }}
      />

      <main className="mx-auto flex max-w-5xl items-center justify-center px-4 py-12">
        <div className="glass-panel grid w-full overflow-hidden rounded-3xl shadow-2xl md:grid-cols-[270px_1fr]">
          {/* Left Ocoya-inspired Step Rail */}
          <aside className="flex flex-col justify-between border-b border-white/10 bg-white/[0.02] p-6 md:border-b-0 md:border-r">
            <div>
              <div className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                <Sparkles className="h-3 w-3" />
                AI Setup Wizard
              </div>
              <h1 className="text-lg font-bold text-white">Welcome to SocialMediaAI</h1>
              <p className="mt-1 text-xs leading-relaxed text-slate-400">
                Set up your brand in 3 short steps. Our AI uses this in every caption, hashtag &amp; schedule.
              </p>

              <div className="mt-8 space-y-5">
                {stepsMeta.map((item) => {
                  const Icon = item.icon;
                  const done = step > item.num;
                  const active = step === item.num;
                  return (
                    <button
                      key={item.num}
                      type="button"
                      onClick={() => setStep(item.num)}
                      className="flex w-full items-start gap-3 text-left"
                    >
                      <div
                        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs transition ${
                          done
                            ? "border-emerald-400 bg-emerald-500 text-slate-950"
                            : active
                              ? "border-emerald-400 bg-emerald-500/20 text-emerald-300"
                              : "border-white/15 bg-white/5 text-slate-400"
                        }`}
                      >
                        {done ? <CheckCircle2 className="h-4 w-4" /> : <Icon className="h-3.5 w-3.5" />}
                      </div>
                      <div>
                        <p
                          className={`text-xs font-semibold ${
                            active || done ? "text-white" : "text-slate-400"
                          }`}
                        >
                          {item.title}
                        </p>
                        <p className="mt-0.5 text-[11px] leading-snug text-slate-500">
                          {item.desc}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            <p className="mt-8 text-[11px] text-slate-500">Step {step} of 3</p>
          </aside>

          {/* Right Panel */}
          <section className="flex flex-col justify-between p-6 sm:p-8">
            <div>
              {error && (
                <div className="mb-5 flex items-start gap-2.5 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
                  <span>{error}</span>
                </div>
              )}

              {step === 1 && (
                <div className="space-y-5">
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    <Globe className="h-4 w-4 text-emerald-400" />
                    <span>Website</span>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-slate-300">
                      Website URL (Optional)
                    </label>
                    <p className="mt-1 text-xs text-slate-400">
                      We extract your logo, brand colors, and niche directly from your site. No website? Click Continue.
                    </p>
                    <div className="mt-3 flex flex-col gap-2.5 sm:flex-row">
                      <input
                        type="url"
                        value={websiteUrl}
                        onChange={(e) => setWebsiteUrl(e.target.value)}
                        placeholder="https://yourbrand.com"
                        className="flex-1 rounded-xl border border-white/15 bg-black/40 px-4 py-2.5 text-sm text-white placeholder-slate-500 outline-none focus:border-emerald-400"
                      />
                      <button
                        type="button"
                        onClick={handleFetchBrand}
                        disabled={loading}
                        className="inline-flex items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/10 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-white/15 disabled:opacity-50"
                      >
                        {loading ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Sparkles className="h-4 w-4 text-emerald-400" />
                        )}
                        Fetch brand
                      </button>
                    </div>
                  </div>

                  <div>
                    <label className="text-xs font-medium text-slate-300">
                      Preferred Content Language
                    </label>
                    <select
                      value={language}
                      onChange={(e) => setLanguage(e.target.value)}
                      className="mt-2 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2.5 text-sm text-white outline-none focus:border-emerald-400 sm:w-64"
                    >
                      <option value="English">English</option>
                      <option value="Hinglish">Hinglish (Hindi + English)</option>
                      <option value="Hindi">Hindi</option>
                    </select>
                  </div>
                </div>
              )}

              {step === 2 && (
                <div className="space-y-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    <Palette className="h-4 w-4 text-emerald-400" />
                    <span>Your Brand Kit</span>
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <label className="text-xs text-slate-400">Brand Name *</label>
                      <input
                        value={brand.brand_name || ""}
                        onChange={(e) => setBrand({ ...brand, brand_name: e.target.value })}
                        placeholder="e.g., FitVibe Studio"
                        className="mt-1.5 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-sm text-white outline-none focus:border-emerald-400"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-slate-400">Niche / Industry</label>
                      <input
                        value={brand.niche || ""}
                        onChange={(e) => setBrand({ ...brand, niche: e.target.value })}
                        placeholder="e.g., AI Tools, Fitness Coaching, D2C Fashion"
                        className="mt-1.5 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-sm text-white outline-none focus:border-emerald-400"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-slate-400">Tone of Voice</label>
                    <input
                      value={brand.tone_of_voice || ""}
                      onChange={(e) => setBrand({ ...brand, tone_of_voice: e.target.value })}
                      placeholder="Bold, practical, relatable storytelling"
                      className="mt-1.5 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-sm text-white outline-none focus:border-emerald-400"
                    />
                  </div>

                  <div>
                    <label className="text-xs text-slate-400">Target Audience</label>
                    <input
                      value={brand.target_audience || ""}
                      onChange={(e) => setBrand({ ...brand, target_audience: e.target.value })}
                      placeholder="Creators, founders & marketers in India aged 20-35"
                      className="mt-1.5 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-sm text-white outline-none focus:border-emerald-400"
                    />
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <label className="text-xs text-slate-400">
                        Weekly Posting Goal (used for smart queue spacing)
                      </label>
                      <input
                        type="number"
                        min={1}
                        max={14}
                        value={brand.posting_goal_per_week || 4}
                        onChange={(e) =>
                          setBrand({
                            ...brand,
                            posting_goal_per_week: Math.max(1, Math.min(14, Number(e.target.value) || 4)),
                          })
                        }
                        className="mt-1.5 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-sm text-white outline-none focus:border-emerald-400"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-slate-400">Extracted Brand Colors</label>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        {(brand.colors && brand.colors.length > 0
                          ? brand.colors
                          : ["#10B981", "#6366F1"]
                        ).map((hex) => (
                          <span
                            key={hex}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/40 px-2 py-1 text-[11px] text-slate-300"
                          >
                            <span
                              className="h-3 w-3 rounded-full"
                              style={{ backgroundColor: hex }}
                            />
                            {hex}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {step === 3 && (
                <div className="space-y-5">
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    <Share2 className="h-4 w-4 text-emerald-400" />
                    <span>Connect Instagram Business Channel</span>
                  </div>
                  <p className="text-xs leading-relaxed text-slate-400">
                    Connect your Instagram Business or Creator account (linked to a Facebook Page) via Meta OAuth so our AI can analyze your real reach, compute your best posting times, search trending hashtags, and auto-publish Reels &amp; Posts.
                  </p>

                  {!metaConfigured && (
                    <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs text-amber-200">
                      <strong>Setup Required in `.env`:</strong> `META_APP_ID` and `META_APP_SECRET` are currently empty. Add your Meta Developer App credentials to enable live Instagram login.
                    </div>
                  )}

                  <div>
                    <label className="text-xs text-slate-400">
                      Account Timezone (for Best-Time Heatmap &amp; Scheduling)
                    </label>
                    <select
                      value={selectedTz}
                      onChange={(e) => setSelectedTz(e.target.value)}
                      className="mt-1.5 w-full rounded-xl border border-white/15 bg-black/40 px-3.5 py-2 text-sm text-white outline-none focus:border-emerald-400 sm:w-72"
                    >
                      {(TIMEZONES.includes(selectedTz) ? TIMEZONES : [selectedTz, ...TIMEZONES]).map(
                        (tz) => (
                          <option key={tz} value={tz}>
                            {tz}
                          </option>
                        )
                      )}
                    </select>
                  </div>

                  <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                    <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
                      <div className="flex items-center gap-3.5">
                        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-tr from-amber-400 via-pink-500 to-purple-600 text-white shadow-lg">
                          <Instagram className="h-6 w-6" />
                        </div>
                        <div>
                          <h3 className="text-sm font-semibold text-white">
                            IG Business / Creator
                          </h3>
                          <p className="text-xs text-slate-400">
                            Facebook Login for Business • Insights + Hashtags + Reels Auto-Publish
                          </p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={handleConnectInstagram}
                        disabled={loading}
                        className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-slate-200 disabled:opacity-50"
                      >
                        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        Connect Instagram
                      </button>
                    </div>

                    {accounts.length > 0 && (
                      <div className="mt-4 space-y-2 border-t border-white/10 pt-4">
                        <p className="text-[11px] font-medium uppercase tracking-wider text-emerald-400">
                          Connected Accounts
                        </p>
                        {accounts.map((acc) => (
                          <div
                            key={acc.id}
                            className="flex items-center justify-between rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs"
                          >
                            <span className="font-medium text-white">
                              @{acc.username} ({acc.followers_count.toLocaleString()} followers)
                            </span>
                            <button
                              type="button"
                              onClick={() => router.push("/dashboard")}
                              className="text-emerald-300 hover:underline"
                            >
                              Open Analytics →
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Google Business Profile Connect Card */}
                  <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                    {!gbpConfigured && (
                      <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                        <strong>Setup Required in `.env`:</strong> `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are currently empty. Add Google Cloud OAuth credentials to enable live Google Business login.
                      </div>
                    )}
                    <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
                      <div className="flex items-center gap-3.5">
                        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-tr from-sky-500 via-blue-500 to-emerald-400 text-white shadow-lg">
                          <Store className="h-6 w-6" />
                        </div>
                        <div>
                          <h3 className="text-sm font-semibold text-white">
                            Google Business Profile
                          </h3>
                          <p className="text-xs text-slate-400">
                            Google OAuth • Maps &amp; Search Growth Metrics + Polite AI Review Replies
                          </p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={handleConnectGoogleBusiness}
                        disabled={gbpLoading}
                        className="inline-flex items-center gap-2 rounded-xl bg-sky-400 px-4 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-sky-300 disabled:opacity-50"
                      >
                        {gbpLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        Connect Google Business
                      </button>
                    </div>

                    {gbpProfiles.length > 0 && (
                      <div className="mt-4 space-y-2 border-t border-white/10 pt-4">
                        <p className="text-[11px] font-medium uppercase tracking-wider text-sky-400">
                          Connected Business Locations
                        </p>
                        {gbpProfiles.map((p) => (
                          <div
                            key={p.id}
                            className="flex items-center justify-between rounded-xl border border-sky-500/20 bg-sky-500/5 px-3 py-2 text-xs"
                          >
                            <span className="font-medium text-white">
                              {p.title} ({p.average_rating?.toFixed(1) || "—"}★ • {p.total_review_count || 0} reviews)
                            </span>
                            <button
                              type="button"
                              onClick={() => router.push("/google-business")}
                              className="text-sky-300 hover:underline"
                            >
                              Open GBP Dashboard →
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Bottom Navigation */}
            <div className="mt-8 flex items-center justify-between border-t border-white/10 pt-5">
              {step > 1 ? (
                <button
                  type="button"
                  onClick={() => setStep((step - 1) as 1 | 2)}
                  className="rounded-xl border border-white/15 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-white/5"
                >
                  ← Back
                </button>
              ) : (
                <span className="text-xs text-slate-500">Instagram Business Ready</span>
              )}

              {step === 1 && (
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="inline-flex items-center gap-2 rounded-xl bg-white px-5 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-slate-200"
                >
                  Continue <ArrowRight className="h-3.5 w-3.5" />
                </button>
              )}

              {step === 2 && (
                <button
                  type="button"
                  onClick={handleSaveBrandAndNext}
                  disabled={loading}
                  className="inline-flex items-center gap-2 rounded-xl bg-white px-5 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-slate-200 disabled:opacity-50"
                >
                  {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Save &amp; Continue <ArrowRight className="h-3.5 w-3.5" />
                </button>
              )}

              {step === 3 && accounts.length > 0 && (
                <button
                  type="button"
                  onClick={() => router.push("/planner")}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-5 py-2.5 text-xs font-semibold text-slate-950 transition hover:bg-emerald-400"
                >
                  Go to Smart Planner <ArrowRight className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense fallback={<div className="min-h-screen" />}>
      <OnboardingWizard />
    </Suspense>
  );
}
