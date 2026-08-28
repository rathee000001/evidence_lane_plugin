import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { OAUTH_STATE_COOKIE, appBaseUrl, cookieOptions, pkcePair, requiredEnvironment, safeReturnPath, sealCookie } from "../../_lib";
export const runtime = "nodejs"; export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  try {
    const state = randomUUID();
    const pkce = pkcePair();
    const returnTo = safeReturnPath(request.nextUrl.searchParams.get("return_to"));
    const authorize = new URL("https://github.com/login/oauth/authorize");
    authorize.searchParams.set("client_id", requiredEnvironment("EVIDENCE_LANE_GITHUB_APP_CLIENT_ID"));
    authorize.searchParams.set("redirect_uri", `${appBaseUrl()}/api/github-app/oauth/callback`);
    authorize.searchParams.set("state", state);
    authorize.searchParams.set("code_challenge", pkce.challenge);
    authorize.searchParams.set("code_challenge_method", "S256");
    const response = NextResponse.redirect(authorize);
    response.cookies.set(OAUTH_STATE_COOKIE, sealCookie({ state, returnTo, issuedAt: Date.now(), codeVerifier: pkce.verifier }), cookieOptions(600));
    return response;
  } catch (error) { return NextResponse.json({ status: "BLOCKED", code: error instanceof Error ? error.message : "GITHUB_APP_OAUTH_START_FAILED" }, { status: 503 }); }
}
