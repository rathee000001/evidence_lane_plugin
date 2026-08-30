import { NextResponse } from "next/server";
import { publicRouteInventory } from "../_lib";
export const runtime = "nodejs"; export const dynamic = "force-dynamic";
export async function GET() {
  return NextResponse.json({ schema: "evidence-lane.github-app-testing-links.v1", status: "BACKEND_ROUTES_READY", routes: publicRouteInventory(), primaryLiveProfile: "OAUTH_ON_INSTALL", alternateProfile: "SETUP_REDIRECT_ON_UPDATE", githubControlsMutuallyExclusive: true, deviceFlowEnabledByContract: true, webhookActiveByContract: false, webhookActivationPolicy: "OPTIONAL_CONDITION_BOUND", webhookUnselectedStatus: "OPTIONAL_NOT_SELECTED", webhookFallbackReadback: "GITHUB_ACTIONS_CHECKS_DEPLOYMENTS_POLLING", clientIdConfigured: Boolean(process.env.EVIDENCE_LANE_GITHUB_APP_CLIENT_ID), clientSecretConfigured: Boolean(process.env.EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET), webhookSecretConfigured: Boolean(process.env.EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET), sessionSecretConfigured: Boolean(process.env.EVIDENCE_LANE_GITHUB_APP_SESSION_SECRET), secretValuesReturned: false });
}
