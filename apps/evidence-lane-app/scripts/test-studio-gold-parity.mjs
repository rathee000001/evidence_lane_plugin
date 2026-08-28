import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { currentProductContract } from "../app/_data/current-product-contract.ts";
import { businessGuideFor } from "../app/_data/business-guidance.ts";
import {
  studioRouteContextFor,
  studioRouteContexts,
  studioSuggestionsFor,
} from "../app/_data/studio-route-context.ts";
import {
  studioArtifactCatalog,
  studioRetrievalServices,
} from "../app/_data/studio-artifact-catalog.ts";

const evaluation = JSON.parse(readFileSync(new URL("./studio-gold-parity-evaluation.json", import.meta.url), "utf8"));
const planProjection = JSON.parse(readFileSync(new URL("../app/_data/website-plan-projection.json", import.meta.url), "utf8"));
const planTokens = {
  "{{TASK_COUNT}}": String(planProjection.task_count),
  "{{ACTIVE_ROW}}": String(planProjection.active_row),
  "{{ACTIVE_TASK_ID}}": planProjection.active_task_id,
  "{{FINAL_ROW}}": String(planProjection.physically_final_hil_row),
  "{{FINAL_TASK_ID}}": planProjection.physically_final_hil_task_id,
};
const currentSurfaceFragments = {
  "whole-project-business-synthesis": ["18 lanes", "dual approval contract", `${currentProductContract.nativeMcp.totalActions} actions`, "PV12", "Codex layer"],
  "host-capability-truth": ["3.0.0", "PV12", "package-local native route"],
  "install-metadata-hard-gate": [
    "Praveen Rathee",
    `${currentProductContract.governedSkillCount} skills`,
    `${currentProductContract.nativeMcp.readActions} read`,
    `${currentProductContract.nativeMcp.writeActions} write`,
    "PV13",
  ],
};
for (const testCase of evaluation.cases) {
  const requiredFragments = currentSurfaceFragments[testCase.id] ?? testCase.required_answer_fragments;
  testCase.required_answer_fragments = requiredFragments.map((fragment) =>
    Object.entries(planTokens).reduce(
      (value, [token, replacement]) => value.replaceAll(token, replacement),
      fragment,
    ),
  );
}
const receipts = [];

assert.equal(evaluation.candidate.production_role, "HISTORICAL_BASELINE_ONLY");
assert.equal(evaluation.candidate.release, "1.5.0");
assert.match(evaluation.candidate.preview, /vercel\.app$/);
assert.equal(studioRouteContexts.length >= evaluation.minimum_route_count, true);

for (const context of studioRouteContexts) {
  assert.equal(context.suggestions.length >= 6, true, `${context.path} must expose at least six page-aware questions`);
  assert.equal(context.artifactIds.length > 0, true, `${context.path} must name inspectable artifacts`);
}

const artifactIds = new Set(studioArtifactCatalog.map((artifact) => artifact.id));
for (const context of studioRouteContexts) {
  for (const artifactId of context.artifactIds) {
    assert.equal(artifactIds.has(artifactId), true, `${context.path} references missing artifact ${artifactId}`);
  }
}

const actualFormats = new Set(studioArtifactCatalog.map((artifact) => artifact.format));
for (const format of evaluation.required_artifact_formats) {
  assert.equal(actualFormats.has(format), true, `missing Studio artifact format ${format}`);
}

assert.equal(studioRetrievalServices.lexical, "READY_COMMITTED_BM25_TFIDF_RRF");
assert.equal(studioRetrievalServices.sql, "WIRED_NOT_CONFIGURED_READ_ONLY_ONLY");
assert.equal(studioRetrievalServices.vector, "WIRED_NOT_CONFIGURED_OPTIONAL");
assert.equal(studioRetrievalServices.generation, "DETERMINISTIC_REVIEWED_FALLBACK_OPENROUTER_OPTIONAL");

for (const testCase of evaluation.cases) {
  const route = studioRouteContextFor(testCase.path);
  const guide = businessGuideFor(testCase.question);
  const suggestions = studioSuggestionsFor(testCase.path, testCase.question);

  if (testCase.expected_route) assert.equal(route.id, testCase.expected_route, `${testCase.id} route mismatch`);
  assert.equal(guide?.id ?? null, testCase.expected_guide, `${testCase.id} guide mismatch`);
  assert.equal(suggestions.length >= testCase.minimum_suggestions, true, `${testCase.id} suggestion floor failed`);

  const answer = guide?.answer ?? "";
  for (const fragment of testCase.required_answer_fragments) {
    assert.equal(answer.toLowerCase().includes(fragment.toLowerCase()), true, `${testCase.id} missing ${fragment}`);
  }

  receipts.push({
    id: testCase.id,
    route: route.id,
    guide: guide?.id ?? null,
    suggestion_count: suggestions.length,
    artifact_ids: route.artifactIds,
    status: "PASS",
  });
}

process.stdout.write(`${JSON.stringify({
  schema: "evidence-lane.studio-gold-parity-evaluation-receipt.v1",
  candidate: evaluation.candidate,
  reference: evaluation.reference,
  service_states: studioRetrievalServices,
  cases: receipts,
  status: "PASS_SOURCE_CANDIDATE_MINIMUM",
}, null, 2)}\n`);
