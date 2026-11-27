import type { InstagramAccount } from "./types";

const STORAGE_KEY = "socialmediaai_selected_account_id";

/** Read persisted active Instagram account ID from localStorage safely on client. */
export function getStoredAccountId(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

/** Persist active Instagram account ID so selection stays consistent across pages. */
export function saveSelectedAccountId(accountId: string): void {
  if (typeof window === "undefined" || !accountId) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, accountId);
  } catch {
    // ignore storage quota / privacy mode errors
  }
}

/** Pick previously selected account if still connected, else default to the first account. */
export function pickInitialAccountId(accounts: InstagramAccount[]): string {
  if (accounts.length === 0) return "";
  const stored = getStoredAccountId();
  const match = accounts.find((a) => a.id === stored);
  const chosen = match ? match.id : accounts[0].id;
  saveSelectedAccountId(chosen);
  return chosen;
}
