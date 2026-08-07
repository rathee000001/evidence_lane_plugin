import "server-only";

import studioRagArtifact from "./studio-rag-index.json";
import { floatingStudioSuggestions, promptSuggestions } from "./site";

export type StudioSource = { label: string; href: string };

export type RetrievalReceipt = {
  bm25: number;
  tfidf: number;
  rrf: number;
  queryCoverage: number;
  specificCoverage: number;
  matchedTerms: readonly string[];
  unmatchedTerms: readonly string[];
  chunks: readonly string[];
  corpus: string;
};

export type EvidenceAnswer = {
  title: string;
  text: string;
  sources: readonly StudioSource[];
  retrieval: RetrievalReceipt;
};

type RagSource = {
  id: number;
  path: string;
  title: string;
  href: string;
  kind: string;
  sha256: string;
};

type RagChunk = {
  id: string;
  source_id: number;
  locator: string;
  text: string;
  sha256: string;
  token_count: number;
  tfidf: [string, number, number][];
};

type RagIndex = {
  schema: string;
  history_through_sha: string;
  corpus_sha256: string;
  source_count: number;
  chunk_count: number;
  average_chunk_tokens: number;
  document_frequency: Record<string, number>;
  sources: RagSource[];
  chunks: RagChunk[];
  tools: {
    chunker: string;
    lexical: string;
    tfidf: string;
    hybrid: string;
    provider: string;
  };
};

type RankedChunk = {
  chunk: RagChunk;
  source: RagSource;
  bm25: number;
  tfidf: number;
  rrf: number;
};

type RankedEvidence = {
  rows: RankedChunk[];
  queryTerms: string[];
};

const ragIndex = studioRagArtifact as unknown as RagIndex;
const sourceById = new Map(ragIndex.sources.map((source) => [source.id, source]));
const promptStopWords = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
  "explain", "find", "for", "from", "give", "how", "in", "into", "is", "it",
  "not", "of", "on", "or", "show", "tell", "that", "the", "this", "to", "was",
  "what", "when", "where", "which", "with",
]);

// These words identify the product but do not prove that a retrieved chunk
// addresses the user's actual subject. Confidence is therefore measured again
// over the remaining, question-specific terms.
const projectScaffoldTerms = new Set([
  "active", "current", "evidence", "evidencelane", "lane", "page", "plugin",
  "project", "public", "question", "system", "work",
]);

const evidenceLaneTerms = [
  "evidence lane",
  "accepted pointer",
  "exit slip",
  "state travel",
  "source intake",
  "chat lineage",
  "prompt studio",
  "plan lane",
  "approve_with_delta",
  "six-way hil",
  "pv5",
  "pv6",
  "codex plugin",
  "chatgpt plugin",
  "mcp app",
  "delta ledger",
] as const;

export const studioCorpus = {
  schema: ragIndex.schema,
  historyThroughSha: ragIndex.history_through_sha,
  corpusSha256: ragIndex.corpus_sha256,
  sourceCount: ragIndex.source_count,
  chunkCount: ragIndex.chunk_count,
  tools: ragIndex.tools,
} as const;

function tokenize(value: string) {
  return value
    .toLowerCase()
    .match(/[a-z0-9][a-z0-9._/-]{1,63}/g)
    ?.filter((token) => (
      !promptStopWords.has(token)
      && (token.length >= 3 || /^\d+$/.test(token))
    )) ?? [];
}

function excerpt(value: string) {
  const cleaned = value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[`#*_>{}\[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length <= 430) return cleaned;
  const boundary = cleaned.lastIndexOf(" ", 430);
  return `${cleaned.slice(0, boundary > 280 ? boundary : 430)}...`;
}

function rankEvidence(question: string): RankedEvidence {
  const queryTerms = [...new Set(tokenize(question))];
  const indexedTerms = queryTerms.filter(
    (term) => (ragIndex.document_frequency[term] ?? 0) > 0,
  );
  if (!indexedTerms.length) return { rows: [], queryTerms };

  const total = ragIndex.chunk_count;
  const averageLength = Math.max(ragIndex.average_chunk_tokens, 1);
  const scored = ragIndex.chunks.map((chunk) => {
    const terms = new Map(chunk.tfidf.map(([term, count, score]) => [term, { count, score }]));
    let bm25 = 0;
    let tfidf = 0;
    for (const term of indexedTerms) {
      const observed = terms.get(term);
      if (!observed) continue;
      const frequency = ragIndex.document_frequency[term];
      const idf = Math.log(1 + (total - frequency + 0.5) / (frequency + 0.5));
      const denominator = observed.count + 1.2 * (
        1 - 0.75 + 0.75 * chunk.token_count / averageLength
      );
      bm25 += idf * (observed.count * 2.2) / denominator;
      tfidf += observed.score;
    }
    return { chunk, bm25, tfidf };
  }).filter((row) => row.bm25 > 0 || row.tfidf > 0);

  const bm25Order = [...scored].sort(
    (left, right) => right.bm25 - left.bm25 || left.chunk.id.localeCompare(right.chunk.id),
  );
  const tfidfOrder = [...scored].sort(
    (left, right) => right.tfidf - left.tfidf || left.chunk.id.localeCompare(right.chunk.id),
  );
  const bm25Position = new Map(bm25Order.map((row, index) => [row.chunk.id, index + 1]));
  const tfidfPosition = new Map(tfidfOrder.map((row, index) => [row.chunk.id, index + 1]));

  const rows = scored
    .map((row) => {
      const source = sourceById.get(row.chunk.source_id);
      if (!source) return null;
      const rrf = 1 / (60 + (bm25Position.get(row.chunk.id) ?? total))
        + 1 / (60 + (tfidfPosition.get(row.chunk.id) ?? total));
      return { ...row, source, rrf };
    })
    .filter((row): row is RankedChunk => row !== null)
    .sort(
      (left, right) => right.rrf - left.rrf
        || right.bm25 - left.bm25
        || left.chunk.id.localeCompare(right.chunk.id),
    )
    .slice(0, 4);
  return { rows, queryTerms };
}

function termsInChunk(chunk: RagChunk) {
  return new Set(chunk.tfidf.map(([term]) => term));
}

function retrievalConfidence(queryTerms: string[], ranked: RankedChunk[]) {
  const candidateTerms = new Set<string>();
  for (const result of ranked) {
    for (const term of termsInChunk(result.chunk)) candidateTerms.add(term);
  }
  const matchedTerms = queryTerms.filter((term) => candidateTerms.has(term));
  const unmatchedTerms = queryTerms.filter((term) => !candidateTerms.has(term));
  const specificTerms = queryTerms.filter((term) => !projectScaffoldTerms.has(term));
  const matchedSpecificTerms = specificTerms.filter((term) => candidateTerms.has(term));
  const queryCoverage = queryTerms.length ? matchedTerms.length / queryTerms.length : 0;
  const specificCoverage = specificTerms.length
    ? matchedSpecificTerms.length / specificTerms.length
    : queryCoverage;
  const firstChunkTerms = ranked[0] ? termsInChunk(ranked[0].chunk) : new Set<string>();
  const firstChunkMatches = queryTerms.filter((term) => firstChunkTerms.has(term)).length;
  const requiredMatches = Math.min(2, queryTerms.length);
  const requiredSpecificMatches = Math.min(2, specificTerms.length);
  const accepted = queryTerms.length >= 2
    && matchedTerms.length >= requiredMatches
    && firstChunkMatches >= requiredMatches
    && queryCoverage >= 0.62
    && (
      specificTerms.length === 0
        ? queryCoverage === 1
        : matchedSpecificTerms.length >= requiredSpecificMatches && specificCoverage >= 0.67
    );
  return {
    accepted,
    matchedTerms,
    unmatchedTerms,
    queryCoverage,
    specificCoverage,
  };
}

export function answerFromEvidence(question: string): EvidenceAnswer | null {
  const search = rankEvidence(question);
  if (!search.rows.length) return null;
  const confidence = retrievalConfidence(search.queryTerms, search.rows);
  if (!confidence.accepted) return null;
  const selected: RankedChunk[] = [];
  const usedSources = new Set<number>();
  for (const result of search.rows) {
    if (usedSources.has(result.source.id) && selected.length >= 2) continue;
    selected.push(result);
    usedSources.add(result.source.id);
    if (selected.length === 3) break;
  }
  const sources = selected.map((result) => ({
    label: `${result.source.title} - ${result.chunk.locator}`,
    href: result.source.href,
  }));
  return {
    title: `Ranked local evidence: ${selected[0].source.title}`,
    text: selected.map((result) => excerpt(result.chunk.text)).join("\n\n"),
    sources,
    retrieval: {
      bm25: selected[0].bm25,
      tfidf: selected[0].tfidf,
      rrf: selected[0].rrf,
      queryCoverage: confidence.queryCoverage,
      specificCoverage: confidence.specificCoverage,
      matchedTerms: confidence.matchedTerms,
      unmatchedTerms: confidence.unmatchedTerms,
      chunks: selected.map((result) => `${result.chunk.id}:${result.chunk.sha256.slice(0, 12)}`),
      corpus: ragIndex.corpus_sha256,
    },
  };
}

export function isEvidenceLaneQuestion(question: string) {
  const normalized = question.toLowerCase();
  return evidenceLaneTerms.some((term) => normalized.includes(term))
    || /\b(?:pv\d+|hil|fuse|refresh|rollback|mcp|codex|chatgpt)\b/i.test(question)
    || /\b(?:candidate manifest|parallel lanes?|undetected lanes?|operators?|lane folders?|github agents?)\b/i.test(question);
}

export function verifyStudioRetrievalConfidence() {
  const displayedSuggestionCanaries = [
    ...Object.entries(floatingStudioSuggestions).flatMap(([surface, questions]) => (
      questions.map((question, index) => ({
        id: `floating-${surface}-${index + 1}`,
        question,
        expectedGrounded: true,
      }))
    )),
    ...promptSuggestions.map((question, index) => ({
      id: `prompt-studio-${index + 1}`,
      question,
      expectedGrounded: true,
    })),
  ];
  const canaries = [
    { id: "general-no-hit", question: "How do I cook pasta al dente?", expectedGrounded: false },
    { id: "project-nonsense-no-hit", question: "What is Evidence Lane quantum banana authority?", expectedGrounded: false },
    { id: "active-plan-hit", question: "What is active step 46 in the current 51-step execution Plan Lane?", expectedGrounded: true },
    { id: "pointer-hil-hit", question: "How do accepted pointers, Exit Slips, and HIL separate human input from AI work?", expectedGrounded: true },
    ...displayedSuggestionCanaries,
  ].map((canary) => {
    const actualGrounded = Boolean(answerFromEvidence(canary.question));
    return { ...canary, actualGrounded, pass: actualGrounded === canary.expectedGrounded };
  });
  return { pass: canaries.every((canary) => canary.pass), canaries } as const;
}
