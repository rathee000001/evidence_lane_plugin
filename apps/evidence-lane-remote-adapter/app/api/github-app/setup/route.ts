import { NextRequest, NextResponse } from "next/server";
import { type GitHubUserSession, USER_SESSION_COOKIE, appBaseUrl, githubJson, openCookie, safeReturnPath } from "../_lib";
export const runtime = "nodejs"; export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  const installationId = request.nextUrl.searchParams.get("installation_id") || "";
  const returnTo = safeReturnPath(request.nextUrl.searchParams.get("return_to"));
  const session = openCookie<GitHubUserSession>(request.cookies.get(USER_SESSION_COOKIE)?.value);
  if (!session || session.expiresAt <= Date.now()) { const start = new URL("/api/github-app/oauth/start", appBaseUrl()); start.searchParams.set("return_to", returnTo); return NextResponse.redirect(start); }
  if (!/^\d{1,20}$/.test(installationId)) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_INSTALLATION_ID_INVALID" }, { status: 400 });
  try {
    const installations = await githubJson<{ installations?: Array<{ id: number }> }>("https://api.github.com/user/installations?per_page=100", { headers: { Authorization: `Bearer ${session.accessToken}` } });
    if (!installations.installations?.some((row) => String(row.id) === installationId)) throw new Error("GITHUB_APP_INSTALLATION_NOT_AUTHORIZED_FOR_USER");
    const destination = new URL(returnTo, appBaseUrl()); destination.searchParams.set("github_app", "installation_verified"); destination.searchParams.set("installation_id", installationId); return NextResponse.redirect(destination);
  } catch (error) { return NextResponse.json({ status: "BLOCKED", code: error instanceof Error ? error.message : "GITHUB_APP_SETUP_FAILED" }, { status: 403 }); }
}
