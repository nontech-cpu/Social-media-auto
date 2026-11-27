import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SocialMediaAI — Instagram AI Planner & Auto-Poster",
  description:
    "Ocoya-inspired AI Instagram Business growth studio with analytics, best-time heatmaps, trending hashtags, and smart auto-posting.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="space-canvas min-h-screen antialiased selection:bg-emerald-500/30 selection:text-emerald-200">
        {children}
      </body>
    </html>
  );
}
