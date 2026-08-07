import { NextRequest, NextResponse } from "next/server";

import {
  answerFromEvidence,
  isEvidenceLaneQuestion,
  studioCorpus,
  verifyStudioRetrievalConfidence,
} from "../../_data/studio-retrieval";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions";
const OPENROUTER_FREE_MODEL = "openrouter/free";
const MAX_QUESTION_LENGTH = 1_200;
const PROVIDER_TIMEOUT_MS = 18_000;
const credentialPattern = /(?:sk-or-v1-|sk-|ghp_|github_pat_|xox[baprs]-|eyJ)[A-Za-z0-9._-]{16,}/;
const retrievalConfidenceReport = verifyStudioRetrievalConfidence();

type OpenRouterResponse = {
  model?: string;
  choices?: Array<{ message?: { content?: string }; text?: string }>;
};

function externalConfiguration() {
  return {
    enabled: process.env.EVIDENCE_LANE_GENERAL_AI_ENABLED === "true",
    apiKey: process.env.OPENROUTER_API_KEY ?? "",
  };
}

function noHitAnswer(mode: "project_no_hit" | "external_unavailable", answer: string) {
  return NextResponse.json({
    title: mode === "project_no_hit"
      ? "Outside the committed project corpus"
      : "Free general AI is not configured",
    answer,
    mode,
    provider: "none",
    model: OPENROUTER_FREE_MODEL,
    grounded: false,
    sources: [{ label: "Evidence Lane proof boundary", href: "/proof" }],
    boundary: "NO_EXTERNAL_PROJECT_CLAIMS_NO_PAID_MODEL_FALLBACK",
  });
}

export async function GET() {
  const configuration = externalConfiguration();
  return NextResponse.json({
    status: retrievalConfidenceReport.pass ? "ready" : "blocked",
    route: "/api/studio-query",
    corpus: {
      sha256: studioCorpus.corpusSha256,
      sources: studioCorpus.sourceCount,
      chunks: studioCorpus.chunkCount,
    },
    localRetrieval: "BM25 + TF-IDF + RRF over the committed SQLite-derived projection",
    retrievalConfidenceGate: retrievalConfidenceReport,
    externalGeneralFallback: {
      enabled: configuration.enabled && Boolean(configuration.apiKey),
      model: OPENROUTER_FREE_MODEL,
      paidFallback: false,
      projectNoHitPolicy: "refuse",
    },
  });
}

export async function POST(request: NextRequest) {
  if (!retrievalConfidenceReport.pass) {
    return NextResponse.json({
      error: "RETRIEVAL_CONFIDENCE_GATE_FAILED",
      retrievalConfidenceGate: retrievalConfidenceReport,
    }, { status: 503 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "INVALID_JSON" }, { status: 400 });
  }

  const question = typeof body === "object" && body !== null && "question" in body
    ? String(body.question).trim()
    : "";
  if (!question) {
    return NextResponse.json({ error: "QUESTION_REQUIRED" }, { status: 400 });
  }
  if (question.length > MAX_QUESTION_LENGTH) {
    return NextResponse.json({ error: "QUESTION_TOO_LONG" }, { status: 413 });
  }
  if (credentialPattern.test(question)) {
    return NextResponse.json({ error: "CREDENTIAL_SHAPED_INPUT_REJECTED" }, { status: 400 });
  }

  const evidence = answerFromEvidence(question);
  if (evidence) {
    return NextResponse.json({
      answer: evidence.text,
      title: evidence.title,
      mode: "local_retrieval",
      provider: "committed_projection",
      grounded: true,
      sources: evidence.sources,
      retrieval: evidence.retrieval,
      boundary: "PROJECT_EVIDENCE_ONLY",
    });
  }

  if (isEvidenceLaneQuestion(question)) {
    return noHitAnswer(
      "project_no_hit",
      "The committed BM25 and TF-IDF projection found no supporting Evidence Lane chunk. The Studio will not send a project question to a general model or invent an answer. Add a public-safe source through the governed release and rebuild the SQLite authority and its projection.",
    );
  }

  const configuration = externalConfiguration();
  if (!configuration.enabled || !configuration.apiKey) {
    return noHitAnswer(
      "external_unavailable",
      "The committed corpus returned no hit. The optional free general-AI route is disabled until EVIDENCE_LANE_GENERAL_AI_ENABLED=true and a separate OPENROUTER_API_KEY are configured on this Vercel project. No paid model fallback exists.",
    );
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROVIDER_TIMEOUT_MS);
  try {
    const response = await fetch(OPENROUTER_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${configuration.apiKey}`,
        "Content-Type": "application/json",
        "HTTP-Referer": request.nextUrl.origin,
        "X-Title": "Evidence Lane Prompt Studio",
      },
      body: JSON.stringify({
        model: OPENROUTER_FREE_MODEL,
        messages: [
          {
            role: "system",
            content: "Answer the user's general-knowledge question concisely. This is explicitly outside Evidence Lane project evidence. Never claim access to project state, PVs, source files, private data, or current web facts. If the question is actually about Evidence Lane, Codex, ChatGPT plugin state, MCP state, HIL, PVs, or repository facts, refuse and direct the user back to governed retrieval.",
          },
          { role: "user", content: question },
        ],
        temperature: 0.2,
        max_tokens: 600,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      return NextResponse.json({
        title: "Free general AI provider unavailable",
        answer: `OpenRouter's free-model route returned HTTP ${response.status}. The project corpus remained untouched and no paid fallback was attempted.`,
        mode: "external_provider_error",
        provider: "openrouter",
        model: OPENROUTER_FREE_MODEL,
        grounded: false,
        sources: [{ label: "OpenRouter free-model router", href: "https://openrouter.ai/docs/guides/routing/routers/free-router" }],
        boundary: "FREE_MODEL_ONLY_NO_PAID_FALLBACK",
      }, { status: 502 });
    }

    const data = await response.json() as OpenRouterResponse;
    const answer = data.choices?.[0]?.message?.content?.trim()
      || data.choices?.[0]?.text?.trim()
      || "The free-model route returned no answer.";
    return NextResponse.json({
      title: "Free general AI / outside project evidence",
      answer,
      mode: "external_general_free",
      provider: "openrouter",
      model: data.model ?? OPENROUTER_FREE_MODEL,
      grounded: false,
      sources: [{ label: "OpenRouter free-model router", href: "https://openrouter.ai/docs/guides/routing/routers/free-router" }],
      boundary: "GENERAL_ONLY_FREE_MODEL_NO_PROJECT_AUTHORITY_NO_PAID_FALLBACK",
    });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "AbortError";
    return NextResponse.json({
      title: timedOut ? "Free general AI timed out" : "Free general AI connection failed",
      answer: "The optional provider did not return an answer. The project corpus remained untouched and no paid fallback was attempted.",
      mode: "external_provider_error",
      provider: "openrouter",
      model: OPENROUTER_FREE_MODEL,
      grounded: false,
      sources: [{ label: "OpenRouter free-model router", href: "https://openrouter.ai/docs/guides/routing/routers/free-router" }],
      boundary: "FREE_MODEL_ONLY_NO_PAID_FALLBACK",
    }, { status: 502 });
  } finally {
    clearTimeout(timeout);
  }
}
