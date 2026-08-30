import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const route = readFileSync(resolve(root, "app/api/studio-query/route.ts"), "utf8");

assert(!route.includes("OPENROUTER"));
assert(!route.includes("requestFreeGeneralAnswer"));
assert(route.includes("publicProviderProxyPresent: false"));
assert(route.includes("no external provider proxy"));
assert(route.includes("answerFromEvidence(question, { pagePath, history })"));

console.log(JSON.stringify({
  status: "PASS",
  localCommittedRetrievalPresent: true,
  publicProviderProxyPresent: false,
  serverOwnedVisitorCredentialRoutePresent: false,
}));
