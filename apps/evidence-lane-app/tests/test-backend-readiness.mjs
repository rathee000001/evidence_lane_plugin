import assert from "node:assert/strict";

import {
  publicBackendContract,
  publicBackendReadiness,
} from "../app/_data/backend-readiness.ts";

assert.equal(publicBackendContract.surfaces.connect.status, "BACKEND_READY");
assert.equal(publicBackendContract.surfaces.connect.testing_path, "/api/github-app/testing");
assert.equal(publicBackendContract.surfaces.prompt_studio.query_path, "/api/studio-query");
assert.equal(publicBackendContract.surfaces.proof.public_path, "/proof");
assert.equal(publicBackendContract.surfaces.git_ci.public_path, "/git-ci");
assert.equal(publicBackendContract.presentation_deferrals.vercel_page_redesign, true);
assert.equal(publicBackendContract.presentation_deferrals.prompt_studio_rag_regeneration, true);
assert.equal(publicBackendContract.runtime_routing_boundary.remote_adapter_role, "PUBLIC_SAFE_TRANSPORT_ONLY");
assert.equal(publicBackendContract.runtime_routing_boundary.internal_sdk_role, "ALL_PLUGIN_BEHAVIOR_OWNER");
assert.equal(publicBackendContract.runtime_routing_boundary.toolchain_route, "ai_toolchain_route");
assert.equal(publicBackendContract.runtime_routing_boundary.project_sector_http_api_exposed, false);
assert.equal(publicBackendContract.runtime_routing_boundary.toolchain_http_api_exposed, false);

const readiness = publicBackendReadiness({
  EVIDENCE_LANE_GITHUB_APP_CLIENT_ID: "configured",
  EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET: "secret-value",
});
assert.equal(readiness.runtime_configuration.github_client_id_configured, true);
assert.equal(readiness.runtime_configuration.github_client_secret_configured, true);
assert.equal(readiness.runtime_configuration.github_webhook_secret_configured, false);
assert.equal(readiness.runtime_configuration.public_provider_proxy_present, false);
assert.equal(readiness.runtime_configuration.secret_values_returned, false);
assert.equal(JSON.stringify(readiness).includes("secret-value"), false);

console.log(JSON.stringify({
  status: "PASS",
  backend_surfaces: Object.keys(publicBackendContract.surfaces),
  page_redesign_performed: false,
  prompt_studio_rag_regenerated: false,
  secret_values_returned: false,
}));
