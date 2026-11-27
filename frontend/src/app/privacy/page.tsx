export default function PrivacyPolicyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16 text-slate-200">
      <h1 className="text-3xl font-bold text-white">Privacy Policy</h1>
      <p className="mt-2 text-xs text-slate-400">Effective Date: November 26, 2026</p>
      <div className="mt-8 space-y-5 text-sm leading-relaxed text-slate-300">
        <p>
          SocialMediaAI connects to your Instagram Business or Creator account exclusively via Meta&apos;s official Graph API with your explicit OAuth consent.
        </p>
        <h2 className="text-lg font-semibold text-white">1. Data We Access</h2>
        <p>
          We access your Instagram profile metadata, published media insights (reach, likes, comments, shares, saves), audience activity distributions, and media publishing permissions solely to provide analytics, best-time recommendations, and user-approved scheduling.
        </p>
        <h2 className="text-lg font-semibold text-white">2. Token Security &amp; Retention</h2>
        <p>
          OAuth access tokens are encrypted at rest using Fernet symmetric encryption and are never shared with third parties or used to post without your explicit confirmation.
        </p>
        <h2 className="text-lg font-semibold text-white">3. Data Deletion</h2>
        <p>
          You may disconnect your Instagram account at any time inside the dashboard or request complete data removal via our Data Deletion page.
        </p>
      </div>
    </main>
  );
}
