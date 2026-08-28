import { NextRequest, NextResponse } from "next/server";
import { type GitHubDeviceGrant, type GitHubUserSession, USER_SESSION_COOKIE, cookieOptions, githubJson, openCookie, requiredEnvironment, sealCookie } from "../_lib";
export const runtime = "nodejs"; export const dynamic = "force-dynamic";
export async function POST(request: NextRequest) {
  try {
    const body = await request.json() as Record<string, unknown>; const action = body.action === "poll" ? "poll" : "start"; const clientId = requiredEnvironment("EVIDENCE_LANE_GITHUB_APP_CLIENT_ID");
    if (action === "start") {
      const device = await githubJson<{ device_code?: string; user_code?: string; verification_uri?: string; expires_in?: number; interval?: number }>("https://github.com/login/device/code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_id: clientId }) });
      if (!device.device_code || !device.user_code || !device.verification_uri) throw new Error("GITHUB_APP_DEVICE_GRANT_INVALID");
      const intervalSeconds = Math.min(Math.max(Number(device.interval || 5), 5), 60);
      const expiresIn = Math.min(Math.max(Number(device.expires_in || 900), 300), 900);
      const grant: GitHubDeviceGrant = { deviceCode: device.device_code, intervalSeconds, nextPollAt: Date.now() + intervalSeconds * 1000, expiresAt: Date.now() + expiresIn * 1000 };
      return NextResponse.json({ status: "PENDING_USER_AUTHORIZATION", poll_token: sealCookie(grant), user_code: device.user_code, verification_uri: device.verification_uri, expires_in: expiresIn, interval: intervalSeconds, raw_device_code_returned: false });
    }
    const pollToken = typeof body.poll_token === "string" ? body.poll_token : "";
    const grant = openCookie<GitHubDeviceGrant>(pollToken);
    if (!grant || !grant.deviceCode || grant.expiresAt <= Date.now()) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_DEVICE_GRANT_INVALID_OR_EXPIRED" }, { status: 400 });
    if (Date.now() < grant.nextPollAt) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_DEVICE_POLL_TOO_FAST", retry_after_seconds: Math.ceil((grant.nextPollAt - Date.now()) / 1000) }, { status: 429 });
    const token = await githubJson<{ access_token?: string; expires_in?: number; error?: string; interval?: number }>("https://github.com/login/oauth/access_token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_id: clientId, device_code: grant.deviceCode, grant_type: "urn:ietf:params:oauth:grant-type:device_code" }) });
    if (!token.access_token) {
      const intervalSeconds = token.error === "slow_down" ? Math.min(grant.intervalSeconds + 5, 60) : grant.intervalSeconds;
      const next: GitHubDeviceGrant = { ...grant, intervalSeconds, nextPollAt: Date.now() + intervalSeconds * 1000 };
      return NextResponse.json({ status: "PENDING_USER_AUTHORIZATION", provider_error: token.error || "authorization_pending", poll_token: sealCookie(next), interval: intervalSeconds, raw_device_code_returned: false });
    }
    const user = await githubJson<{ id: number; login: string }>("https://api.github.com/user", { headers: { Authorization: `Bearer ${token.access_token}` } }); const expiresIn = Math.min(Math.max(Number(token.expires_in || 3600), 300), 28_800);
    const session: GitHubUserSession = { accessToken: token.access_token, userId: user.id, login: user.login, expiresAt: Date.now() + expiresIn * 1000 }; const response = NextResponse.json({ status: "AUTHORIZED", user_id: user.id, login: user.login, token_returned: false }); response.cookies.set(USER_SESSION_COOKIE, sealCookie(session), cookieOptions(expiresIn)); return response;
  } catch (error) { return NextResponse.json({ status: "BLOCKED", code: error instanceof Error ? error.message : "GITHUB_APP_DEVICE_FLOW_FAILED" }, { status: 400 }); }
}
