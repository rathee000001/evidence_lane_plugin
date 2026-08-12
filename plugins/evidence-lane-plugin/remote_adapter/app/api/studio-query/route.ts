import { NextRequest, NextResponse } from "next/server";

import {
  answerFromEvidence,
  isEvidenceLaneQuestion,
  type StudioHistoryTurn,
  studioCorpus,
  verifyStudioRetrievalConfidence,
} from "../../_data/studio-retrieval";
import { studioRetrievalServices } from "../../_data/studio-artifact-catalog";
import {
  studioRouteContexts,
  studioRouteContextFor,
  studioSuggestionsFor,
} from "../../_data/studio-route-context";
import {
  externalGeneralConfiguration,
  OPENROUTER_FREE_MODEL,
  requestFreeGeneralAnswer,
} from "./openrouter-general";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const MAX_QUESTION_LENGTH = 1_200;
const MAX_PAGE_PATH_LENGTH = 180;
const MAX_HISTORY_TURNS = 8;
const MAX_HISTORY_TEXT_LENGTH = 800;
const credentialPattern = /(?:sk-or-v1-|sk-|ghp_|github_pat_|xox[baprs]-|eyJ)[A-Za-z0-9._-]{16,}/;
const retrievalConfidenceReport = verifyStudioRetrievalConfidence();

function boundedPagePath(body: Record<string, unknown>) {
  const candidate = typeof body.pagePath === "string" ? body.pagePath.trim() : "/studio";
  if (!candidate.startsWith("/") || candidate.length > MAX_PAGE_PATH_LENGTH) return "/studio";
  return studioRouteContextFor(candidate).path;
}

function boundedHistory(body: Record<string, unknown>): StudioHistoryTurn[] {
  if (!Array.isArray(body.history)) return [];
  return body.history.slice(-MAX_HISTORY_TURNS).flatMap((value) => {
    if (!value || typeof value !== "object") return [];
    const role = "role" in value && (value.role === "assistant" || value.role === "user")
      ? value.role
      : null;
    const text = "text" in value && typeof value.text === "string"
      ? value.text.trim().slice(0, MAX_HISTORY_TEXT_LENGTH)
      : "";
    if (!role || !text || credentialPattern.test(text)) return [];
    return [{ role, text }];
  });
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
  const configuration = externalGeneralConfiguration();
  return NextResponse.json({
    status: retrievalConfidenceReport.pass ? "ready" : "blocked",
    route: "/api/studio-query",
    corpus: {
      sha256: studioCorpus.corpusSha256,
      sources: studioCorpus.sourceCount,
      chunks: studioCorpus.chunkCount,
    },
    localRetrieval: "BM25 + TF-IDF + RRF over the committed SQLite-derived projection",
    routeAwareness: {
      enabled: true,
      contexts: studioRouteContexts.map((context) => context.path),
      boundedHistoryTurns: MAX_HISTORY_TURNS,
    },
    services: studioRetrievalServices,
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

  const bodyRecord = typeof body === "object" && body !== null
    ? body as Record<string, unknown>
    : {};
  const question = "question" in bodyRecord
    ? String(bodyRecord.question).trim()
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

  const pagePath = boundedPagePath(bodyRecord);
  const history = boundedHistory(bodyRecord);
  const evidence = answerFromEvidence(question, { pagePath, history });
  if (evidence) {
    return NextResponse.json({
      answer: evidence.text,
      title: evidence.title,
      mode: "local_retrieval",
      provider: "committed_projection",
      grounded: true,
      sources: evidence.sources,
      retrieval: evidence.retrieval,
      context: evidence.context,
      suggestions: evidence.suggestions,
      boundary: "PROJECT_EVIDENCE_ONLY",
    });
  }

  if (isEvidenceLaneQuestion(question)) {
    return noHitAnswer(
      "project_no_hit",
      "The committed BM25 and TF-IDF projection found no supporting Evidence Lane chunk. The Studio will not send a project question to a general model or invent an answer. Add a public-safe source through the governed release and rebuild the SQLite authority and its projection.",
    );
  }

  const configuration = externalGeneralConfiguration();
  if (!configuration.enabled || !configuration.apiKey) {
    return noHitAnswer(
      "external_unavailable",
      "The committed corpus returned no hit. The optional free general-AI route is disabled until EVIDENCE_LANE_GENERAL_AI_ENABLED=true and a separate OPENROUTER_API_KEY are configured on this Vercel project. No paid model fallback exists.",
    );
  }

  const result = await requestFreeGeneralAnswer({
    question,
    requestOrigin: request.nextUrl.origin,
    apiKey: configuration.apiKey,
  });
  return NextResponse.json(result.payload, { status: result.status });
}
