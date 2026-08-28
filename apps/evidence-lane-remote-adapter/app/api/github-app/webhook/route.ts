import { NextRequest, NextResponse } from "next/server";
import { ALLOWED_WEBHOOK_EVENTS, MAX_WEBHOOK_BYTES, classifyWebhookPayload, secureWebhookSignature, sha256 } from "../_lib";
export const runtime = "nodejs"; export const dynamic = "force-dynamic";
export async function POST(request: NextRequest) {
  if ((request.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase() !== "application/json") return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_WEBHOOK_CONTENT_TYPE_INVALID" }, { status: 415 });
  const body = Buffer.from(await request.arrayBuffer()); if (!body.length || body.length > MAX_WEBHOOK_BYTES) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_WEBHOOK_BODY_BOUND" }, { status: 413 });
  if (!secureWebhookSignature(body, request.headers.get("x-hub-signature-256") || "")) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_WEBHOOK_SIGNATURE_INVALID" }, { status: 401 });
  const event = request.headers.get("x-github-event") || ""; const delivery = request.headers.get("x-github-delivery") || ""; if (!ALLOWED_WEBHOOK_EVENTS.has(event) || !/^[A-Za-z0-9-]{8,80}$/.test(delivery)) return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_WEBHOOK_HEADERS_INVALID" }, { status: 400 });
  let payload: Record<string, unknown>; try { payload = JSON.parse(body.toString("utf8")) as Record<string, unknown>; } catch { return NextResponse.json({ status: "BLOCKED", code: "GITHUB_APP_WEBHOOK_JSON_INVALID" }, { status: 400 }); }
  const handled = classifyWebhookPayload(event, payload);
  return NextResponse.json({ schema: "evidence-lane.github-app-webhook-public-receipt.v1", status: "AUTHENTICATED_AND_CLASSIFIED", event, action: handled.action, delivery_sha256: sha256(delivery), body_sha256: sha256(body), installation_id_sha256: handled.installationIdHash, repository_identity_sha256: handled.repositoryIdentityHash, repositories_added: handled.repositoriesAdded, repositories_removed: handled.repositoriesRemoved, installation_lifecycle_handled: handled.installationLifecycleHandled, repository_selection_update_handled: handled.repositorySelectionUpdateHandled, delivery_authority_effect: handled.deliveryAuthorityEffect, signature_valid: true, payload_parsed_after_signature: true, webhook_secret_persisted: false, raw_payload_persisted: false });
}
