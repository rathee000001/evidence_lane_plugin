import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  currentProductContract,
  hookEvents,
  hostCapabilityProfiles,
} from "../app/_data/current-product-contract.ts";

const here = dirname(fileURLToPath(import.meta.url));
const adapterRoot = resolve(here, "..");
const pluginRoot = resolve(adapterRoot, "..", "..", "plugins", "evidence-lane-plugin");
const json = (path) => JSON.parse(readFileSync(path, "utf8"));
const text = (path) => readFileSync(path, "utf8");

const routing = json(resolve(pluginRoot, "skills/evi/references/mcp-tool-routing.v1.json"));
const hooks = json(resolve(pluginRoot, "hooks/hooks.json"));
const release = json(resolve(pluginRoot, "scripts/codex-release-channel.json"));
const laneArtifacts = json(resolve(adapterRoot, "app/_data/dummy-lane-artifacts.json"));
const registrySource = text(resolve(pluginRoot, "src/evidence_lane_plugin/public_surface_registry.py"));
const readRegistry = registrySource.match(/CODEX_READ_TOOL_NAMES\s*=\s*\(([\s\S]*?)\)\s*\n\n/);
assert.ok(readRegistry, "read-only action registry must be statically inspectable");
const readActions = [...readRegistry[1].matchAll(/"([a-z0-9_]+)"/g)].map((match) => match[1]);
const skillCount = readdirSync(resolve(pluginRoot, "skills"), { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(resolve(pluginRoot, "skills", entry.name, "SKILL.md")))
  .length;

assert.equal(currentProductContract.nativeMcp.totalActions, Object.keys(routing.tool_owners).length);
assert.equal(currentProductContract.nativeMcp.totalActions, routing.catalog_contract.tool_count);
assert.equal(currentProductContract.nativeMcp.readActions, readActions.length);
assert.equal(
  currentProductContract.nativeMcp.writeActions,
  Object.keys(routing.tool_owners).length - readActions.length,
);
assert.equal(currentProductContract.governedSkillCount, skillCount);
assert.equal(currentProductContract.canonicalLaneCount, laneArtifacts.lane_count);
assert.deepEqual(hookEvents, Object.keys(hooks.hooks));
assert.equal(currentProductContract.hookEventCount, Object.keys(hooks.hooks).length);
assert.equal(release.stable.native_tool_count, currentProductContract.nativeMcp.totalActions);
assert.equal(release.stable.native_read_tool_count, currentProductContract.nativeMcp.readActions);
assert.equal(release.stable.native_write_tool_count, currentProductContract.nativeMcp.writeActions);
assert.equal(release.stable.skill_count, currentProductContract.governedSkillCount);
assert.deepEqual(release.lifecycle_hook_matrix.required_events, [...hookEvents]);
assert.equal(release.lifecycle_hook_matrix.supported_handler_type, currentProductContract.hookHandlerType);
assert.equal(release.lifecycle_hook_matrix.hooks_own_lifecycle_transport_only, true);
assert.equal(release.behavior_ownership.hooks, "LIFECYCLE_CAPTURE_AND_SEALED_EVENTS_ONLY");

const expectedProfiles = [
  "desktop-durable",
  "cli-durable",
  "headless-persistent",
  "ephemeral-mounted",
  "ephemeral-connector",
  "interactive-ephemeral",
  "review-only",
];
assert.deepEqual(hostCapabilityProfiles.map((profile) => profile.id), expectedProfiles);
assert.equal(release.host_storage_tunnel_matrix.routing_axes_independent, true);
assert.equal(release.host_storage_tunnel_matrix.account_tier_affects_routing, false);
assert.equal(release.host_storage_tunnel_matrix.api_billing_affects_routing, false);
assert.equal(release.host_storage_tunnel_matrix.headless_api.tunnel_requirement, "NOT_REQUIRED_FOR_API_LAYER");
assert.equal(
  release.host_storage_tunnel_matrix.interactive_codex_app_local_or_persistent.host_tool_gap.tunnel_requirement,
  "REQUIRED_FOR_HOST_TOOL_GAP",
);
assert.equal(
  release.host_storage_tunnel_matrix.interactive_codex_app_ephemeral_vm.host_tool_gap.tunnel_setup_frequency,
  "ONCE_PER_EPHEMERAL_VM_INSTANCE",
);

const ownedPages = {
  home: text(resolve(adapterRoot, "app/page.tsx")),
  architecture: text(resolve(adapterRoot, "app/architecture/page.tsx")),
  lanes: text(resolve(adapterRoot, "app/lanes/page.tsx")),
  operators: text(resolve(adapterRoot, "app/operators/page.tsx")),
  connect: text(resolve(adapterRoot, "app/connect/page.tsx")),
  tunnel: text(resolve(adapterRoot, "app/tunnel/page.tsx")),
  support: text(resolve(adapterRoot, "app/support/page.tsx")),
  readme: text(resolve(adapterRoot, "app/readme/page.tsx")),
  mcp: text(resolve(adapterRoot, "app/mcp/page.tsx")),
  hooks: text(resolve(adapterRoot, "app/hooks/page.tsx")),
  release: text(resolve(adapterRoot, "app/release/page.tsx")),
  gitCi: text(resolve(adapterRoot, "app/git-ci/page.tsx")),
  studioCatalog: text(resolve(adapterRoot, "app/_data/studio-artifact-catalog.ts")),
  studioLab: text(resolve(adapterRoot, "app/_components/studio-artifact-lab.tsx")),
  studioRouteContext: text(resolve(adapterRoot, "app/_data/studio-route-context.ts")),
  hero: text(resolve(adapterRoot, "app/_components/hero-orbit.tsx")),
  hostMatrix: text(resolve(adapterRoot, "app/_components/host-capability-matrix.tsx")),
  laneHero: text(resolve(adapterRoot, "app/_components/lane-orbit-aside.tsx")),
};
const combined = Object.values(ownedPages).join("\n");
for (const stale of [/\b83\b/, /\b87\b/, /\b8 events\b/i, /eight (installed |registered )?hook/i]) {
  assert.equal(stale.test(combined), false, `stale capability claim remains: ${stale}`);
}
assert.match(ownedPages.home, /<HeroOrbit preset="home"/);
assert.match(ownedPages.architecture, /<HeroOrbit preset="architecture"/);
assert.match(ownedPages.lanes, /<LaneOrbitAside/);
assert.match(ownedPages.operators, /<HeroOrbit preset="operators"/);
assert.match(ownedPages.hostMatrix, /<GovernedPopup/);
assert.match(ownedPages.hostMatrix, /aria-expanded=/);
assert.match(ownedPages.laneHero, /onPointerMove=/);
assert.match(ownedPages.laneHero, /onWheel=/);
assert.match(ownedPages.laneHero, /onKeyDown=/);
assert.match(ownedPages.operators, /<ModeOperatorExplorer/);
assert.match(combined, /hooks (are|is|remain|only)/i);
assert.match(combined, /tool gap/i);
assert.match(combined, /Project Universe/);
assert.match(combined, /AI Learning/);
assert.match(combined, /Canon/);
assert.match(combined, /Project Memory|Memory/);

const capabilityMatrix = text(resolve(
  adapterRoot,
  "public/studio-artifacts/evidence-lane-capability-matrix-v300.csv",
)).trim().split(/\r?\n/);
assert.equal(capabilityMatrix.length, 4);
for (const row of capabilityMatrix.slice(1)) {
  const fields = row.split(",");
  assert.equal(Number(fields[3]), currentProductContract.nativeMcp.totalActions);
  assert.equal(Number(fields[4]), currentProductContract.nativeMcp.readActions);
  assert.equal(Number(fields[5]), currentProductContract.nativeMcp.writeActions);
  assert.equal(Number(fields[6]), currentProductContract.governedSkillCount);
  assert.equal(Number(fields[7]), currentProductContract.hookEventCount);
}

console.log(JSON.stringify({
  status: "PASS",
  catalog: currentProductContract.nativeMcp,
  skills: skillCount,
  hooks: hookEvents.length,
  lanes: laneArtifacts.lane_count,
  hostProfiles: hostCapabilityProfiles.length,
  routes: Object.keys(ownedPages).filter((name) => !["hero", "hostMatrix", "laneHero"].includes(name)),
}));
