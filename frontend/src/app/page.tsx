"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    Promise.all([
      api.listAccounts().catch(() => ({ accounts: [] })),
      api.listGoogleBusinessProfiles().catch(() => ({ profiles: [] })),
    ])
      .then(([{ accounts }, { profiles }]) => {
        if (accounts.length > 0) {
          router.replace("/planner");
        } else if (profiles.length > 0) {
          router.replace("/google-business");
        } else {
          router.replace("/onboarding");
        }
      })
      .catch(() => router.replace("/onboarding"));
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="flex items-center gap-3 text-sm text-slate-300">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-400" />
        <span>Loading your Instagram AI workspace...</span>
      </div>
    </main>
  );
}
