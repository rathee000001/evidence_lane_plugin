"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";

import { promptKnowledge, promptSuggestions } from "../_data/site";
import { GlassIconOrb, LaneAssetIcon, OfficialToolIcon } from "./evidence-assets";

type StudioSource = { label: string; href: string };
type StudioMessage = {
  id: number;
  role: "assistant" | "user";
  text: string;
  title?: string;
  sources?: readonly StudioSource[];
  grounded?: boolean;
};

const welcome: StudioMessage = {
  id: 0,
  role: "assistant",
  title: "Evidence Lane Studio",
  text: "Ask about the product, lifecycle, lanes, topology, privacy, or host connection model. Answers are grounded in this site's explicit knowledge map; no external model call is implied.",
  grounded: true,
  sources: [{ label: "Architecture", href: "/architecture" }],
};

const promptStopWords = new Set([
  "are",
  "can",
  "does",
  "for",
  "from",
  "how",
  "into",
  "the",
  "this",
  "what",
  "when",
  "where",
  "which",
  "with",
]);

function normalize(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function resolveKnowledge(question: string) {
  const normalized = normalize(question);
  const tokens = new Set(
    normalized
      .split(" ")
      .filter((token) => token.length > 2 && !promptStopWords.has(token)),
  );
  const scored = promptKnowledge.map((entry) => {
    const keywordScore = entry.keywords.reduce((score, keyword) => {
      const normalizedKeyword = normalize(keyword);
      return score + (normalized.includes(normalizedKeyword) ? 3 : tokens.has(normalizedKeyword) ? 2 : 0);
    }, 0);
    const titleScore = normalize(entry.title)
      .split(" ")
      .filter((token) => token.length > 2 && !promptStopWords.has(token))
      .reduce((score, token) => score + (tokens.has(token) ? 2 : 0), 0);
    return { entry, score: keywordScore + titleScore };
  }).sort((left, right) => right.score - left.score);

  if (scored[0]?.score > 0) return scored[0].entry;
  return null;
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
    const knowledge = resolveKnowledge(trimmed);
    const userMessage: StudioMessage = {
      id: sequence,
      role: "user",
      text: trimmed,
    };
    const assistantMessage: StudioMessage = knowledge
      ? {
          id: sequence + 1,
          role: "assistant",
          title: knowledge.title,
          text: knowledge.answer,
          sources: knowledge.sources,
          grounded: true,
        }
      : {
          id: sequence + 1,
          role: "assistant",
          title: "Outside the local knowledge boundary",
          text: "This preview cannot ground that question in the published Evidence Lane knowledge map. Try the lifecycle, eighteen lanes, topology, privacy, or Codex and ChatGPT connection model. A production AI route would require a separately configured provider and its own evidence receipt.",
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
    <div className="promptStudio rilStudio" aria-label="Grounded Evidence Lane Prompt Studio" data-grounding="LOCAL_SITE_KNOWLEDGE_MAP">
      <section className="promptStudioWorkspace" aria-label="Evidence Lane question workspace">
        <header className="promptStudioHeader">
          <div className="promptStudioIdentity">
            <span className="rilIconBadge studioIdentityOrb" aria-hidden="true">
              <LaneAssetIcon lane="chat_lineage" size={29} decorative />
            </span>
            <span>
              <small>Evidence AI Studio</small>
              <strong>Grounded product guide</strong>
            </span>
          </div>
          <div className="promptStudioMeta">
            <span><b>{promptKnowledge.length}</b> governed topics</span>
            <span>No external model</span>
            <span className={lastAssistant?.grounded ? "grounded" : "bounded"}>
              <i className="studioLiveDot" />
              {lastAssistant?.grounded ? "Grounded" : "Boundary shown"}
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
            <strong>Inspectable answer mode</strong>
          </div>
        </header>

        <div className="promptTranscript" aria-live="polite" aria-label="Prompt Studio transcript">
          {messages.map((message) => (
            <article className={`promptMessage is-${message.role}`} key={message.id}>
              <span>{message.role === "assistant" ? "EL" : "YOU"}</span>
              <div>
                {message.title && <strong>{message.title}</strong>}
                <p>{message.text}</p>
                {message.sources && (
                  <footer>
                    <small>{message.grounded ? "Published sources" : "Boundary reference"}</small>
                    {message.sources.map((source) => (
                      <Link href={source.href} key={`${message.id}-${source.href}`}>{source.label}</Link>
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
          <label htmlFor="evidence-studio-question">Ask Evidence Lane</label>
          <textarea
            id="evidence-studio-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            placeholder="Ask how Refresh preserves unchanged bytes..."
          />
          <div>
            <small>Ctrl/Cmd + Enter to send - answers cite published sections</small>
            <button className={`rilPill${question.trim() ? " active" : ""}`} type="submit" disabled={!question.trim()}>
              <GlassIconOrb color="#83ddb3" size={30} decorative><OfficialToolIcon tool="terminal" size={16} decorative /></GlassIconOrb>
              <span>Run grounded prompt <b aria-hidden="true">→</b></span>
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
