import { NextRequest, NextResponse } from "next/server";

import {
  answerFromEvidence,
  isEvidenceLaneQuestion,
  studioCorpus,
  verifyStudioRetrievalConfidence,
} from "../../_data/studio-retrieval";
import {
  externalGeneralConfiguration,
  OPENROUTER_FREE_MODEL,
  requestFreeGeneralAnswer,
} from "./openrouter-general";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const MAX_QUESTION_LENGTH = 1_200;
const credentialPattern = /(?:sk-or-v1-|sk-|ghp_|github_pat_|xox[baprs]-|eyJ)[A-Za-z0-9._-]{16,}/;
const retrievalConfidenceReport = verifyStudioRetrievalConfidence();

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
