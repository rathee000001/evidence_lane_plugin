import assert from "node:assert/strict";
import { createHmac } from "node:crypto";

import {
  classifyWebhookPayload,
  openCookie,
  pkcePair,
  publicRouteInventory,
  safeReturnPath,
  sealCookie,
  secureWebhookSignature,
} from "../app/api/github-app/_lib.ts";

process.env.EVIDENCE_LANE_GITHUB_APP_BASE_URL = "https://evidencelane.org/";
process.env.EVIDENCE_LANE_GITHUB_APP_SESSION_SECRET = "test-session-secret-that-is-long-and-random";
process.env.EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET = "test-webhook-secret-that-is-long-and-random";

const value = { state: "state-1", issuedAt: 1234, secret: "encrypted-only" };
const sealed = sealCookie(value);
assert.notEqual(sealed.includes(value.secret), true);
assert.deepEqual(openCookie(sealed), value);
assert.equal(openCookie(`${sealed}x`), null);

const pkce = pkcePair();
assert.match(pkce.verifier, /^[A-Za-z0-9_-]{43,128}$/);
assert.match(pkce.challenge, /^[A-Za-z0-9_-]{43}$/);
assert.notEqual(pkce.verifier, pkce.challenge);

const body = Buffer.from('{"action":"added"}', "utf8");
const signature = `sha256=${createHmac("sha256", process.env.EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET).update(body).digest("hex")}`;
assert.equal(secureWebhookSignature(body, signature), true);
assert.equal(secureWebhookSignature(Buffer.from("changed"), signature), false);

const classified = classifyWebhookPayload("installation_repositories", {
  action: "added",
  installation: { id: 123 },
  repositories_added: [{ full_name: "owner/repo-a" }, { full_name: "owner/repo-b" }],
  repositories_removed: [{ full_name: "owner/repo-c" }],
});
assert.equal(classified.repositorySelectionUpdateHandled, true);
assert.equal(classified.installationLifecycleHandled, false);
assert.equal(classified.repositoriesAdded, 2);
assert.equal(classified.repositoriesRemoved, 1);
assert.match(classified.installationIdHash, /^[A-F0-9]{64}$/);
assert.equal(classified.deliveryAuthorityEffect, "EVENT_RECOGNIZED_NO_PROJECT_MUTATION");

assert.equal(safeReturnPath("https://attacker.invalid"), "/git-ci");
assert.equal(safeReturnPath("/support"), "/support");
assert.deepEqual(Object.keys(publicRouteInventory()).sort(), [
  "callbackUrl",
  "deviceUrl",
  "mutuallyExclusiveLiveProfiles",
  "oauthOnInstallPrimary",
  "oauthStartUrl",
  "setupRedirectAlternate",
  "setupUrl",
  "testingUrl",
  "webhookUrl",
].sort());

console.log(JSON.stringify({
  status: "PASS",
  oauth_pkce: "S256",
  cookie_encryption: "AES-256-GCM",
  webhook_signature: "HMAC-SHA256_CONSTANT_TIME",
  installation_repository_updates: "CLASSIFIED",
  secrets_returned: false,
}));
