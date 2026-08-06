"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";

import studioRagArtifact from "../_data/studio-rag-index.json";
import { promptSuggestions } from "../_data/site";
import { GlassIconOrb, LaneAssetIcon, OfficialToolIcon } from "./evidence-assets";

type StudioSource = { label: string; href: string };
type RetrievalReceipt = {
  bm25: number;
  tfidf: number;
  rrf: number;
  chunks: readonly string[];
  corpus: string;
};
type StudioMessage = {
  id: number;
  role: "assistant" | "user";
  text: string;
  title?: string;
  sources?: readonly StudioSource[];
  grounded?: boolean;
  retrieval?: RetrievalReceipt;
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

const ragIndex = studioRagArtifact as unknown as RagIndex;
const sourceById = new Map(ragIndex.sources.map((source) => [source.id, source]));
const promptStopWords = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
  "for", "from", "how", "in", "into", "is", "it", "of", "on", "or", "that",
  "the", "this", "to", "was", "what", "when", "where", "which", "with",
]);

const welcome: StudioMessage = {
  id: 0,
  role: "assistant",
  title: "Evidence Lane local RAG",
  text: `Search ${ragIndex.source_count} public-safe source records and ${ragIndex.chunk_count} LlamaIndex chunks, including Git history through ${ragIndex.history_through_sha.slice(0, 12)}. Answers are extractive and ranked locally; no external model or hidden provider call is implied.`,
  grounded: true,
  sources: [{ label: "Prompt Studio retrieval contract", href: "/studio" }],
};

function tokenize(value: string) {
  return value
    .toLowerCase()
    .match(/[a-z0-9][a-z0-9._/-]{1,63}/g)
    ?.filter((token) => !promptStopWords.has(token)) ?? [];
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

function rankEvidence(question: string): RankedChunk[] {
  const queryTerms = [...new Set(tokenize(question))].filter(
    (term) => (ragIndex.document_frequency[term] ?? 0) > 0,
  );
  if (!queryTerms.length) return [];

  const total = ragIndex.chunk_count;
  const averageLength = Math.max(ragIndex.average_chunk_tokens, 1);
  const scored = ragIndex.chunks.map((chunk) => {
    const terms = new Map(chunk.tfidf.map(([term, count, score]) => [term, { count, score }]));
    let bm25 = 0;
    let tfidf = 0;
    for (const term of queryTerms) {
      const observed = terms.get(term);
      if (!observed) continue;
      const frequency = ragIndex.document_frequency[term];
      const idf = Math.log(1 + (total - frequency + 0.5) / (frequency + 0.5));
      const denominator = observed.count + 1.2 * (1 - 0.75 + 0.75 * chunk.token_count / averageLength);
      bm25 += idf * (observed.count * 2.2) / denominator;
      tfidf += observed.score;
    }
    return { chunk, bm25, tfidf };
  }).filter((row) => row.bm25 > 0 || row.tfidf > 0);

  const bm25Order = [...scored].sort((left, right) => right.bm25 - left.bm25 || left.chunk.id.localeCompare(right.chunk.id));
  const tfidfOrder = [...scored].sort((left, right) => right.tfidf - left.tfidf || left.chunk.id.localeCompare(right.chunk.id));
  const bm25Position = new Map(bm25Order.map((row, index) => [row.chunk.id, index + 1]));
  const tfidfPosition = new Map(tfidfOrder.map((row, index) => [row.chunk.id, index + 1]));

  return scored
    .map((row) => {
      const source = sourceById.get(row.chunk.source_id);
      if (!source) return null;
      const rrf = 1 / (60 + (bm25Position.get(row.chunk.id) ?? total))
        + 1 / (60 + (tfidfPosition.get(row.chunk.id) ?? total));
      return { ...row, source, rrf };
    })
    .filter((row): row is RankedChunk => row !== null)
    .sort((left, right) => right.rrf - left.rrf || right.bm25 - left.bm25 || left.chunk.id.localeCompare(right.chunk.id))
    .slice(0, 4);
}

function answerFromEvidence(question: string) {
  const ranked = rankEvidence(question);
  if (!ranked.length) return null;
  const selected: RankedChunk[] = [];
  const usedSources = new Set<number>();
  for (const result of ranked) {
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
      chunks: selected.map((result) => `${result.chunk.id}:${result.chunk.sha256.slice(0, 12)}`),
      corpus: ragIndex.corpus_sha256,
    },
  };
}

export function EvidencePromptStudio() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<StudioMessage[]>([welcome]);
  const [sequence, setSequence] = useState(1);

  const lastAssistant = useMemo(
    () => [...messages].reverse().find((message) => message.role === "assistant"),
    [messages],
  );

  const submitQuestion = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    const evidence = answerFromEvidence(trimmed);
    const userMessage: StudioMessage = { id: sequence, role: "user", text: trimmed };
    const assistantMessage: StudioMessage = evidence
      ? {
          id: sequence + 1,
          role: "assistant",
          title: evidence.title,
          text: evidence.text,
          sources: evidence.sources,
          grounded: true,
          retrieval: evidence.retrieval,
        }
      : {
          id: sequence + 1,
          role: "assistant",
          title: "Outside the committed local corpus",
          text: "The browser's committed BM25 and TF-IDF projection found no supporting chunk. The studio will not invent an answer or imply a provider call. Add a public-safe source through the governed release, rebuild the SQLite authority and its browser projection, and ask again.",
          grounded: false,
          sources: [{ label: "Proof boundary", href: "/proof" }],
        };
    setMessages((current) => [...current.slice(-6), userMessage, assistantMessage]);
    setSequence((current) => current + 2);
    setQuestion("");
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitQuestion(question);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
    event.preventDefault();
    submitQuestion(question);
  };

  return (
    <div className="promptStudio rilStudio" aria-label="Grounded Evidence Lane Prompt Studio" data-grounding="LOCAL_BM25_TFIDF_RRF_PROJECTION_OF_SQLITE_FTS5_CORPUS">
      <section className="promptStudioWorkspace" aria-label="Evidence Lane question workspace">
        <header className="promptStudioHeader">
          <div className="promptStudioIdentity">
            <span className="rilIconBadge studioIdentityOrb" aria-hidden="true">
              <LaneAssetIcon lane="chat_lineage" size={29} decorative />
            </span>
            <span>
              <small>Evidence AI Studio</small>
              <strong>Local hybrid retrieval</strong>
            </span>
          </div>
          <div className="promptStudioMeta">
            <span><b>{ragIndex.source_count}</b> sources</span>
            <span><b>{ragIndex.chunk_count}</b> chunks</span>
            <span>No external model</span>
            <span className={lastAssistant?.grounded ? "grounded" : "bounded"}>
              <i className="studioLiveDot" />
              {lastAssistant?.grounded ? "Evidence found" : "Boundary shown"}
            </span>
            <button
              className="rilPill"
              type="button"
              onClick={() => {
                setMessages([welcome]);
                setQuestion("");
              }}
            ><GlassIconOrb color="#f2a1c5" size={30} decorative><OfficialToolIcon tool="pulse" size={16} decorative /></GlassIconOrb><span>Clear session</span></button>
          </div>
          <div className="promptStudioMode">
            <span className="studioLiveDot" />
            <strong>BM25 + TF-IDF + RRF · SQLite authority</strong>
          </div>
        </header>

        <div className="promptTranscript" aria-live="polite" aria-label="Prompt Studio transcript">
          {messages.map((message) => (
            <article className={`promptMessage is-${message.role}`} key={message.id}>
              <span>{message.role === "assistant" ? "EL" : "YOU"}</span>
              <div>
                {message.title && <strong>{message.title}</strong>}
                <p>{message.text}</p>
                {message.retrieval && (
                  <div className="promptRetrievalReceipt" aria-label="Retrieval receipt">
                    <span>BM25 {message.retrieval.bm25.toFixed(4)}</span>
                    <span>TF-IDF {message.retrieval.tfidf.toFixed(4)}</span>
                    <span>RRF {message.retrieval.rrf.toFixed(6)}</span>
                    <code>{message.retrieval.chunks.join(" | ")}</code>
                    <code>corpus {message.retrieval.corpus.slice(0, 16)}</code>
                  </div>
                )}
                {message.sources && (
                  <footer>
                    <small>{message.grounded ? "Ranked source chunks" : "Boundary reference"}</small>
                    {message.sources.map((source) => (
                      <Link href={source.href} key={`${message.id}-${source.href}-${source.label}`}>{source.label}</Link>
                    ))}
                  </footer>
                )}
              </div>
            </article>
          ))}
        </div>

        <div className="promptSuggestionCluster" aria-label="Suggested Evidence Lane questions">
          {promptSuggestions.map((suggestion) => (
            <button className="rilPill" type="button" key={suggestion} onClick={() => submitQuestion(suggestion)}>
              <GlassIconOrb color="#69d9f5" size={30} decorative><OfficialToolIcon tool="node" size={16} decorative /></GlassIconOrb>
              <span>{suggestion}</span>
            </button>
          ))}
        </div>

        <form className="promptComposer" onSubmit={onSubmit}>
          <label htmlFor="evidence-studio-question">Search the committed Evidence Lane corpus</label>
          <textarea
            id="evidence-studio-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            placeholder="Which commit and contract define Refresh byte reuse?"
          />
          <div>
            <small>Ctrl/Cmd + Enter to send - every result exposes rank and chunk identity</small>
            <button className={`rilPill${question.trim() ? " active" : ""}`} type="submit" disabled={!question.trim()}>
              <GlassIconOrb color="#83ddb3" size={30} decorative><OfficialToolIcon tool="terminal" size={16} decorative /></GlassIconOrb>
              <span>Run local retrieval <b aria-hidden="true">&rarr;</b></span>
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
