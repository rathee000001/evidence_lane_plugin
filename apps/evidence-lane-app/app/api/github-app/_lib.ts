import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const GITHUB_API_VERSION = "2026-03-10";
export const OAUTH_STATE_COOKIE = "evi_github_oauth_state";
export const USER_SESSION_COOKIE = "evi_github_user_session";
export const MAX_WEBHOOK_BYTES = 1024 * 1024;
export const ALLOWED_WEBHOOK_EVENTS = new Set(["check_run", "check_suite", "installation", "installation_repositories", "push", "workflow_run"]);
export type OAuthState = { state: string; returnTo: string; issuedAt: number; codeVerifier: string };
export type GitHubUserSession = { accessToken: string; userId: number; login: string; expiresAt: number };
export type GitHubDeviceGrant = { deviceCode: string; intervalSeconds: number; nextPollAt: number; expiresAt: number };

export function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`GITHUB_APP_ENV_REQUIRED:${name}`);
  return value;
}
export function appBaseUrl(): string {
  return (process.env.EVIDENCE_LANE_GITHUB_APP_BASE_URL || "https://evidencelane.org").replace(/\/+$/, "");
}
export function safeReturnPath(value: string | null): string {
  return value && ["/git-ci", "/connect", "/support"].includes(value) ? value : "/git-ci";
}
export function pkcePair(): { verifier: string; challenge: string } {
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier, "ascii").digest("base64url");
  return { verifier, challenge };
}
function encryptionKey(): Buffer {
  return createHash("sha256").update(requiredEnvironment("EVIDENCE_LANE_GITHUB_APP_SESSION_SECRET"), "utf8").digest();
}
export function sealCookie(value: object): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), iv);
  const ciphertext = Buffer.concat([cipher.update(Buffer.from(JSON.stringify(value), "utf8")), cipher.final()]);
  return [iv, cipher.getAuthTag(), ciphertext].map((part) => part.toString("base64url")).join(".");
}
export function openCookie<T>(value: string | undefined): T | null {
  if (!value) return null;
  try {
    const [ivText, tagText, ciphertextText, ...extra] = value.split(".");
    if (!ivText || !tagText || !ciphertextText || extra.length) return null;
    const decipher = createDecipheriv("aes-256-gcm", encryptionKey(), Buffer.from(ivText, "base64url"));
    decipher.setAuthTag(Buffer.from(tagText, "base64url"));
    const plaintext = Buffer.concat([decipher.update(Buffer.from(ciphertextText, "base64url")), decipher.final()]);
    return JSON.parse(plaintext.toString("utf8")) as T;
  } catch { return null; }
}
export function secureWebhookSignature(body: Buffer, supplied: string): boolean {
  if (!/^sha256=[a-f0-9]{64}$/i.test(supplied)) return false;
  const expected = `sha256=${createHmac("sha256", requiredEnvironment("EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET")).update(body).digest("hex")}`;
  const left = Buffer.from(expected, "ascii");
  const right = Buffer.from(supplied, "ascii");
  return left.length === right.length && timingSafeEqual(left, right);
}
export function sha256(value: Buffer | string): string { return createHash("sha256").update(value).digest("hex").toUpperCase(); }
export function cookieOptions(maxAge: number) { return { httpOnly: true, secure: true, sameSite: "lax" as const, path: "/api/github-app", maxAge }; }
export async function githubJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": GITHUB_API_VERSION, ...(init.headers || {}) }, cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`GITHUB_APP_PROVIDER_ERROR:${response.status}`);
  return body as T;
}
export function publicRouteInventory() {
  const base = appBaseUrl();
  return {
    oauthStartUrl: `${base}/api/github-app/oauth/start`,
    callbackUrl: `${base}/api/github-app/oauth/callback`,
    setupUrl: `${base}/api/github-app/setup`,
    deviceUrl: `${base}/api/github-app/device`,
    webhookUrl: `${base}/api/github-app/webhook`,
    testingUrl: `${base}/api/github-app/testing`,
    oauthOnInstallPrimary: true,
    setupRedirectAlternate: true,
    mutuallyExclusiveLiveProfiles: true,
  };
}

export function classifyWebhookPayload(event: string, payload: Record<string, unknown>) {
  const installation = payload.installation && typeof payload.installation === "object"
    ? payload.installation as Record<string, unknown>
    : {};
  const repository = payload.repository && typeof payload.repository === "object"
    ? payload.repository as Record<string, unknown>
    : {};
  const repositoriesAdded = Array.isArray(payload.repositories_added) ? payload.repositories_added : [];
  const repositoriesRemoved = Array.isArray(payload.repositories_removed) ? payload.repositories_removed : [];
  const action = typeof payload.action === "string" && payload.action.length <= 64 ? payload.action : null;
  return {
    event,
    action,
    installationIdHash: installation.id ? sha256(String(installation.id)) : null,
    repositoryIdentityHash: repository.full_name ? sha256(String(repository.full_name).toLowerCase()) : null,
    repositoriesAdded: repositoriesAdded.length,
    repositoriesRemoved: repositoriesRemoved.length,
    installationLifecycleHandled: event === "installation",
    repositorySelectionUpdateHandled: event === "installation_repositories",
    deliveryAuthorityEffect: "EVENT_RECOGNIZED_NO_PROJECT_MUTATION",
  };
}
