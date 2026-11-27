export default function DataDeletionPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16 text-slate-200">
      <h1 className="text-3xl font-bold text-white">User Data Deletion Instructions</h1>
      <div className="mt-6 space-y-4 text-sm leading-relaxed text-slate-300">
        <p>
          You can permanently remove all stored Instagram tokens, synced insights, and scheduled posts from SocialMediaAI in two ways:
        </p>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            <strong>In-App Disconnect:</strong> Open <em>Setup &amp; Brand → Connected Accounts</em> and disconnect your Instagram Business profile.
          </li>
          <li>
            <strong>Facebook / Instagram Settings:</strong> Remove SocialMediaAI under <em>Settings → Security and Login → Business Integrations</em>. Meta will automatically trigger our deletion callback endpoint to purge your account records.
          </li>
        </ol>
      </div>
    </main>
  );
}
