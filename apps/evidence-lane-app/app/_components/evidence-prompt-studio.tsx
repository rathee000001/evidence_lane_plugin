"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";

import {
  type RetrievalReceipt,
  type StudioSource,
} from "../_data/studio-retrieval";
import { promptSuggestions } from "../_data/site";
import { GlassIconOrb, LaneAssetIcon, OfficialToolIcon } from "./evidence-assets";

type StudioMessage = {
  id: number;
  role: "assistant" | "user";
  text: string;
  title?: string;
  sources?: readonly StudioSource[];
  grounded?: boolean;
  retrieval?: RetrievalReceipt;
  mode?: string;
};

type StudioQueryResponse = {
  answer?: string;
  title?: string;
  mode?: string;
  provider?: string;
  model?: string;
  sources?: StudioSource[];
  boundary?: string;
  grounded?: boolean;
  retrieval?: RetrievalReceipt;
  suggestions?: string[];
  context?: {
    id: string;
    path: string;
    title: string;
    purpose: string;
    currentCapability: string;
    evidenceBoundary: string;
  };
};

type StudioCorpusSummary = {
  sourceCount: number;
  chunkCount: number;
  historyThroughSha: string;
};

function welcomeMessage(corpus: StudioCorpusSummary): StudioMessage {
  return {
    id: 0,
    role: "assistant",
    title: "Welcome to the Evidence Lane business guide",
    text: "Ask about the problem Evidence Lane solves, the 18 source lanes, 17 plugin surfaces, lifecycle controls, Canon, Agent Learning, human decisions, host boundaries, proof, release, or the native Three.js/WebGL presentation. I will explain the business outcome first and keep the technical evidence receipt available for optional review.",
    grounded: true,
    mode: "local_retrieval",
    sources: [{ label: "Prompt Studio retrieval contract", href: "/studio" }],
  };
}

function externalTitle(result: StudioQueryResponse) {
  if (result.title) return result.title;
  if (result.mode === "project_no_hit") return "Outside the committed project corpus";
  if (result.mode === "external_unavailable") return "No public provider proxy";
  return "Governed no-hit boundary";
}

export function EvidencePromptStudio({ corpus }: { corpus: StudioCorpusSummary }) {
  const welcome = useMemo(() => welcomeMessage(corpus), [corpus]);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<StudioMessage[]>([welcome]);
  const [suggestions, setSuggestions] = useState<readonly string[]>(promptSuggestions);
  const [sequence, setSequence] = useState(1);
  const [busy, setBusy] = useState(false);

  const lastAssistant = useMemo(
    () => [...messages].reverse().find((message) => message.role === "assistant"),
    [messages],
  );

  const submitQuestion = async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || busy) return;

    const userId = sequence;
    const assistantId = sequence + 1;
    const userMessage: StudioMessage = { id: userId, role: "user", text: trimmed };
    const history = messages.slice(-8).map((message) => ({
      role: message.role,
      text: message.text,
    }));
    setMessages((current) => [...current.slice(-12), userMessage]);
    setSequence((current) => current + 2);
    setQuestion("");

    setBusy(true);
    try {
      const response = await fetch("/api/studio-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed, pagePath: "/studio", history }),
      });
      const result = await response.json() as StudioQueryResponse;
      const grounded = result.mode === "local_retrieval" && result.grounded === true;
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: "assistant",
          title: externalTitle(result),
          text: result.answer ?? "No answer was returned.",
          sources: result.sources ?? [{ label: "Proof boundary", href: "/proof" }],
          grounded,
          retrieval: result.retrieval,
          mode: result.mode ?? "external_unavailable",
        },
      ]);
      if (result.suggestions?.length) setSuggestions(result.suggestions.slice(0, 8));
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: "assistant",
          title: "General AI connection unavailable",
          text: "The committed corpus returned no hit and the optional free general-AI route could not be reached. No project answer was invented.",
          sources: [{ label: "Proof boundary", href: "/proof" }],
          grounded: false,
          mode: "external_error",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitQuestion(question);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
    event.preventDefault();
    void submitQuestion(question);
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
              <strong>Business guide to the whole plugin</strong>
            </span>
          </div>
          <div className="promptStudioMeta">
            <span><b>{corpus.sourceCount}</b> governed sources</span>
            <span><b>{corpus.chunkCount}</b> evidence sections</span>
            <span>Unsupported claims refuse</span>
            <span className={lastAssistant?.grounded ? "grounded" : "bounded"}>
              <i className="studioLiveDot" />
              {busy ? "Checking boundary" : lastAssistant?.grounded ? "Evidence found" : "Outside evidence"}
            </span>
            <button
              className="rilPill"
              type="button"
              onClick={() => {
                setMessages([welcome]);
                setQuestion("");
                setSuggestions(promptSuggestions);
              }}
            ><GlassIconOrb color="#f2a1c5" size={30} decorative><OfficialToolIcon tool="pulse" size={16} decorative /></GlassIconOrb><span>Clear</span></button>
          </div>
          <div className="promptStudioMode">
            <span className="studioLiveDot" />
            <strong>Business answer first / supporting sources visible / audit receipt on demand</strong>
          </div>
        </header>

        <div className="promptTranscript" aria-live="polite" aria-label="Prompt Studio transcript">
          {messages.map((message) => (
            <article className={`promptMessage is-${message.role}`} key={message.id}>
              <span>{message.role === "assistant" ? "EL" : "YOU"}</span>
              <div>
                {message.title ? <strong>{message.title}</strong> : null}
                {message.mode ? <small className="promptModeLabel">{message.mode.replaceAll("_", " ")}</small> : null}
                <p>{message.text}</p>
                {message.retrieval ? (
                  <details className="promptRetrievalReceipt">
                    <summary>Open evidence receipt</summary>
                    <div aria-label="Retrieval receipt">
                    <span>BM25 {message.retrieval.bm25.toFixed(4)}</span>
                    <span>TF-IDF {message.retrieval.tfidf.toFixed(4)}</span>
                    <span>RRF {message.retrieval.rrf.toFixed(6)}</span>
                    <span>Query coverage {(message.retrieval.queryCoverage * 100).toFixed(0)}%</span>
                    <span>Specific coverage {(message.retrieval.specificCoverage * 100).toFixed(0)}%</span>
                    <code>matched {message.retrieval.matchedTerms.join(", ")}</code>
                    {message.retrieval.unmatchedTerms.length ? <code>unmatched {message.retrieval.unmatchedTerms.join(", ")}</code> : null}
                    <code>{message.retrieval.chunks.join(" | ")}</code>
                    <code>corpus {message.retrieval.corpus.slice(0, 16)}</code>
                    <code>route {message.retrieval.routeContext} / history {message.retrieval.historyTurns}</code>
                    <code>guide {message.retrieval.answerGuideId}</code>
                    <code>artifacts {message.retrieval.artifactIds.join(", ") || "none"}</code>
                    <code>SQL {message.retrieval.sqlStatus}</code>
                    <code>vector {message.retrieval.vectorStatus}</code>
                    <code>generation {message.retrieval.generationStatus}</code>
                    </div>
                  </details>
                ) : null}
                {message.sources ? (
                  <footer>
                    <small>{message.grounded ? "Supporting business guidance sources" : "Boundary / provider reference"}</small>
                    {message.sources.map((source) => (
                      <Link href={source.href} key={`${message.id}-${source.href}-${source.label}`}>{source.label}</Link>
                    ))}
                  </footer>
                ) : null}
              </div>
            </article>
          ))}
          {busy ? <p className="promptBusy">Checking the governed guide and its evidence boundary...</p> : null}
        </div>

        <div className="promptSuggestionCluster" aria-label="Suggested Evidence Lane questions">
          {suggestions.map((suggestion) => (
            <button
              className="promptSuggestionCard"
              type="button"
              key={suggestion}
              disabled={busy}
              onClick={() => void submitQuestion(suggestion)}
            >
              <span className="suggestionCardMarker" aria-hidden="true" />
              <span>{suggestion}</span>
            </button>
          ))}
        </div>

        <form className="promptComposer" onSubmit={onSubmit}>
          <label htmlFor="evidence-studio-question">Ask a business question about Evidence Lane</label>
          <textarea
            id="evidence-studio-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            placeholder="What happens from Source Intake to an accepted project version?"
          />
          <div>
            <small>Ctrl/Cmd + Enter / supported project answers stay governed / unsupported claims refuse</small>
            <button className={`rilPill${question.trim() ? " active" : ""}`} type="submit" disabled={!question.trim() || busy}>
              <GlassIconOrb color="#83ddb3" size={30} decorative><OfficialToolIcon tool="terminal" size={16} decorative /></GlassIconOrb>
              <span>{busy ? "Checking" : "Ask Studio"} <b aria-hidden="true">&rarr;</b></span>
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
