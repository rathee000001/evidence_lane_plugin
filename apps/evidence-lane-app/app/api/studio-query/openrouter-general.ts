export const OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions";
export const OPENROUTER_FREE_MODEL = "openrouter/free";
export const PROVIDER_TIMEOUT_MS = 18_000;

const GENERAL_SYSTEM_PROMPT = [
  "Answer the user's general-knowledge question concisely.",
  "This is explicitly outside Evidence Lane project evidence.",
  "Never claim access to project state, PVs, source files, private data, or current web facts.",
  "If the question is actually about Evidence Lane, Codex plugin state, MCP state, HIL, PVs, or repository facts, refuse and direct the user back to governed retrieval.",
].join(" ");

export type ExternalGeneralConfiguration = {
  enabled: boolean;
  apiKey: string;
};

export type ExternalGeneralPayload = {
  title: string;
  answer: string;
  mode: "external_general_free" | "external_provider_error";
  provider: "openrouter";
  model: string;
  grounded: false;
  sources: Array<{ label: string; href: string }>;
  boundary:
    | "GENERAL_ONLY_FREE_MODEL_NO_PROJECT_AUTHORITY_NO_PAID_FALLBACK"
    | "FREE_MODEL_ONLY_NO_PAID_FALLBACK";
};

type OpenRouterResponse = {
  model?: string;
  choices?: Array<{ message?: { content?: string }; text?: string }>;
};

type FetchLike = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

export function externalGeneralConfiguration(
  environment: NodeJS.ProcessEnv = process.env,
): ExternalGeneralConfiguration {
  return {
    enabled: environment.EVIDENCE_LANE_GENERAL_AI_ENABLED === "true",
    apiKey: environment.OPENROUTER_API_KEY ?? "",
  };
}

function providerSource() {
  return [{
    label: "OpenRouter free-model router",
    href: "https://openrouter.ai/docs/guides/routing/routers/free-router",
  }];
}

function providerFailure(
  title: string,
  answer: string,
): { status: 502; payload: ExternalGeneralPayload } {
  return {
    status: 502,
    payload: {
      title,
      answer,
      mode: "external_provider_error",
      provider: "openrouter",
      model: OPENROUTER_FREE_MODEL,
      grounded: false,
      sources: providerSource(),
      boundary: "FREE_MODEL_ONLY_NO_PAID_FALLBACK",
    },
  };
}

export async function requestFreeGeneralAnswer({
  question,
  requestOrigin,
  apiKey,
  fetchImpl = fetch,
  timeoutMs = PROVIDER_TIMEOUT_MS,
}: {
  question: string;
  requestOrigin: string;
  apiKey: string;
  fetchImpl?: FetchLike;
  timeoutMs?: number;
}): Promise<
  | { status: 200; payload: ExternalGeneralPayload }
  | { status: 502; payload: ExternalGeneralPayload }
> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(OPENROUTER_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "HTTP-Referer": requestOrigin,
        "X-Title": "Evidence Lane Prompt Studio",
      },
      body: JSON.stringify({
        model: OPENROUTER_FREE_MODEL,
        messages: [
          { role: "system", content: GENERAL_SYSTEM_PROMPT },
          { role: "user", content: question },
        ],
        temperature: 0.2,
        max_tokens: 600,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      return providerFailure(
        "Free general AI provider unavailable",
        `OpenRouter's free-model route returned HTTP ${response.status}. The project corpus remained untouched and no paid fallback was attempted.`,
      );
    }

    const data = await response.json() as OpenRouterResponse;
    const answer = data.choices?.[0]?.message?.content?.trim()
      || data.choices?.[0]?.text?.trim()
      || "The free-model route returned no answer.";
    return {
      status: 200,
      payload: {
        title: "Free general AI / outside project evidence",
        answer,
        mode: "external_general_free",
        provider: "openrouter",
        model: data.model ?? OPENROUTER_FREE_MODEL,
        grounded: false,
        sources: providerSource(),
        boundary: "GENERAL_ONLY_FREE_MODEL_NO_PROJECT_AUTHORITY_NO_PAID_FALLBACK",
      },
    };
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "AbortError";
    return providerFailure(
      timedOut ? "Free general AI timed out" : "Free general AI connection failed",
      "The optional provider did not return an answer. The project corpus remained untouched and no paid fallback was attempted.",
    );
  } finally {
    clearTimeout(timeout);
  }
}
