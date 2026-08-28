import assert from "node:assert/strict";

import {
  externalGeneralConfiguration,
  OPENROUTER_ENDPOINT,
  OPENROUTER_FREE_MODEL,
  requestFreeGeneralAnswer,
} from "../app/api/studio-query/openrouter-general.ts";

const dummyKey = "dummy-local-openrouter-key-not-a-secret";
const generalQuestion = "How do I cook pasta al dente?";

assert.deepEqual(externalGeneralConfiguration({}), {
  enabled: false,
  apiKey: "",
});
assert.deepEqual(externalGeneralConfiguration({
  EVIDENCE_LANE_GENERAL_AI_ENABLED: "true",
  OPENROUTER_API_KEY: dummyKey,
}), {
  enabled: true,
  apiKey: dummyKey,
});

let observedRequest = null;
const success = await requestFreeGeneralAnswer({
  question: generalQuestion,
  requestOrigin: "https://evidencelane.org",
  apiKey: dummyKey,
  fetchImpl: async (input, init) => {
    observedRequest = { input: String(input), init };
    return new Response(JSON.stringify({
      model: "example/free-model",
      choices: [{ message: { content: "Use salted boiling water and stop at a firm bite." } }],
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  },
});

assert(observedRequest);
assert.equal(observedRequest.input, OPENROUTER_ENDPOINT);
assert.equal(observedRequest.init.method, "POST");
const headers = new Headers(observedRequest.init.headers);
assert.equal(headers.get("Authorization"), `Bearer ${dummyKey}`);
assert.equal(headers.get("HTTP-Referer"), "https://evidencelane.org");
const outbound = JSON.parse(String(observedRequest.init.body));
assert.deepEqual(Object.keys(outbound).sort(), [
  "max_tokens",
  "messages",
  "model",
  "temperature",
]);
assert.equal(outbound.model, OPENROUTER_FREE_MODEL);
assert.equal(outbound.messages.length, 2);
assert.equal(outbound.messages[1].content, generalQuestion);
assert(!JSON.stringify(outbound).includes("test-codex-evidence-lane-plugin"));
assert(!JSON.stringify(outbound).includes("PV10"));
assert.equal(success.status, 200);
assert.equal(success.payload.mode, "external_general_free");
assert.equal(success.payload.grounded, false);
assert.equal(
  success.payload.boundary,
  "GENERAL_ONLY_FREE_MODEL_NO_PROJECT_AUTHORITY_NO_PAID_FALLBACK",
);
assert(!JSON.stringify(success).includes(dummyKey));

const providerError = await requestFreeGeneralAnswer({
  question: generalQuestion,
  requestOrigin: "https://evidencelane.org",
  apiKey: dummyKey,
  fetchImpl: async () => new Response("", { status: 429 }),
});
assert.equal(providerError.status, 502);
assert.equal(providerError.payload.mode, "external_provider_error");
assert.equal(providerError.payload.model, OPENROUTER_FREE_MODEL);
assert.match(providerError.payload.answer, /no paid fallback/i);
assert(!JSON.stringify(providerError).includes(dummyKey));

const connectionError = await requestFreeGeneralAnswer({
  question: generalQuestion,
  requestOrigin: "https://evidencelane.org",
  apiKey: dummyKey,
  fetchImpl: async () => {
    throw new Error("synthetic local connection failure");
  },
});
assert.equal(connectionError.status, 502);
assert.equal(connectionError.payload.title, "Free general AI connection failed");
assert(!JSON.stringify(connectionError).includes(dummyKey));

const timedOut = await requestFreeGeneralAnswer({
  question: generalQuestion,
  requestOrigin: "https://evidencelane.org",
  apiKey: dummyKey,
  timeoutMs: 5,
  fetchImpl: async (_input, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => {
      const error = new Error("synthetic local timeout");
      error.name = "AbortError";
      reject(error);
    }, { once: true });
  }),
});
assert.equal(timedOut.status, 502);
assert.equal(timedOut.payload.title, "Free general AI timed out");
assert(!JSON.stringify(timedOut).includes(dummyKey));

console.log(JSON.stringify({
  status: "PASS",
  real_provider_calls: 0,
  model: OPENROUTER_FREE_MODEL,
  success_contract: true,
  provider_error_contract: true,
  connection_error_contract: true,
  timeout_contract: true,
  secret_returned: false,
}));
