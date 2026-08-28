import { NextRequest, NextResponse } from "next/server";
import { type GitHubUserSession, type OAuthState, OAUTH_STATE_COOKIE, USER_SESSION_COOKIE, appBaseUrl, cookieOptions, githubJson, openCookie, requiredEnvironment, sealCookie } from "../../_lib";
export const runtime = "nodejs"; export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  try {
    const code = request.nextUrl.searchParams.get("code") || "";
    const state = request.nextUrl.searchParams.get("state") || "";
    const installationId = request.nextUrl.searchParams.get("installation_id");
    const stored = openCookie<OAuthState>(request.cookies.get(OAUTH_STATE_COOKIE)?.value);
    if (!code || !state || !stored || stored.state !== state || Date.now() - stored.issuedAt > 600_000) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_OAUTH_STATE_INVALID" }, { status: 400 });
    if (!/^[A-Za-z0-9_-]{43,128}$/.test(stored.codeVerifier)) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_OAUTH_PKCE_STATE_INVALID" }, { status: 400 });
    const token = await githubJson<{ access_token?: string; expires_in?: number; error?: string }>("https://github.com/login/oauth/access_token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_id: requiredEnvironment("EVIDENCE_LANE_GITHUB_APP_CLIENT_ID"), client_secret: requiredEnvironment("EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET"), code, code_verifier: stored.codeVerifier, redirect_uri: `${appBaseUrl()}/api/github-app/oauth/callback` }) });
    if (!token.access_token || token.error) throw new Error("GITHUB_APP_OAUTH_EXCHANGE_FAILED");
    const auth = { Authorization: `Bearer ${token.access_token}` };
    const user = await githubJson<{ id: number; login: string }>("https://api.github.com/user", { headers: auth });
    if (!Number.isInteger(user.id) || !user.login) throw new Error("GITHUB_APP_USER_IDENTITY_INVALID");
    if (installationId) {
      const installations = await githubJson<{ installations?: Array<{ id: number }> }>("https://api.github.com/user/installations?per_page=100", { headers: auth });
      if (!installations.installations?.some((row) => String(row.id) === installationId)) throw new Error("GITHUB_APP_INSTALLATION_NOT_AUTHORIZED_FOR_USER");
    }
    const expiresIn = Math.min(Math.max(Number(token.expires_in || 3600), 300), 28_800);
    const session: GitHubUserSession = { accessToken: token.access_token, userId: user.id, login: user.login, expiresAt: Date.now() + expiresIn * 1000 };
    const destination = new URL(stored.returnTo, appBaseUrl()); destination.searchParams.set("github_app", "authorized"); if (installationId) destination.searchParams.set("installation_id", installationId);
    const response = NextResponse.redirect(destination); response.cookies.delete(OAUTH_STATE_COOKIE); response.cookies.set(USER_SESSION_COOKIE, sealCookie(session), cookieOptions(expiresIn)); return response;
  } catch (error) { return NextResponse.json({ status: "BLOCKED", code: error instanceof Error ? error.message : "GITHUB_APP_OAUTH_CALLBACK_FAILED" }, { status: 400 }); }
}
