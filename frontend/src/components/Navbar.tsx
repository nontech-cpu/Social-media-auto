"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Calendar, LayoutDashboard, Rocket, Sparkles, Instagram } from "lucide-react";
import { saveSelectedAccountId } from "@/lib/selectedAccount";
import type { InstagramAccount } from "@/lib/types";

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

  const navItems = [
    { href: "/onboarding", label: "Setup & Brand", icon: Rocket },
    { href: "/dashboard", label: "Analytics & Best Times", icon: LayoutDashboard },
    { href: "/planner", label: "Smart Planner", icon: Calendar },
  ];

  return (
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
  );
}
