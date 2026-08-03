"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";

import { promptKnowledge, promptSuggestions } from "../_data/site";

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

function normalize(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function resolveKnowledge(question: string) {
  const normalized = normalize(question);
  const tokens = new Set(normalized.split(" ").filter((token) => token.length > 2));
  const scored = promptKnowledge.map((entry) => {
    const keywordScore = entry.keywords.reduce((score, keyword) => {
      const normalizedKeyword = normalize(keyword);
      return score + (normalized.includes(normalizedKeyword) ? 3 : tokens.has(normalizedKeyword) ? 2 : 0);
    }, 0);
    const titleScore = normalize(entry.title).split(" ").reduce(
      (score, token) => score + (tokens.has(token) ? 1 : 0),
      0,
    );
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
    <div className="promptStudio" data-grounding="LOCAL_SITE_KNOWLEDGE_MAP">
      <aside className="promptStudioRail" aria-label="Prompt Studio capabilities">
        <div className="studioIdentityOrb" aria-hidden="true">
          <span />
          <svg viewBox="0 0 64 64" fill="none">
            <path d="M13 17h38v27H29l-10 8v-8h-6V17Z" />
            <path d="M21 26h22M21 33h15" />
          </svg>
        </div>
        <div>
          <small>Evidence AI Studio</small>
          <strong>Grounded product guide</strong>
        </div>
        <dl>
          <div><dt>Knowledge</dt><dd>{promptKnowledge.length} governed topics</dd></div>
          <div><dt>Provider</dt><dd>No external model</dd></div>
          <div><dt>Fallback</dt><dd>Refuse unsupported claims</dd></div>
        </dl>
        <button
          type="button"
          onClick={() => {
            setMessages([welcome]);
            setQuestion("");
          }}
        >Clear session</button>
      </aside>

      <section className="promptStudioWorkspace" aria-label="Evidence Lane question workspace">
        <header>
          <div>
            <span className="studioLiveDot" />
            <strong>Inspectable answer mode</strong>
          </div>
          <span className={lastAssistant?.grounded ? "grounded" : "bounded"}>
            {lastAssistant?.grounded ? "Grounded" : "Boundary shown"}
          </span>
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
            <button type="button" key={suggestion} onClick={() => submitQuestion(suggestion)}>
              {suggestion}
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
            placeholder="Ask how Refresh preserves unchanged bytes…"
          />
          <div>
            <small>Ctrl/⌘ + Enter to send · answers cite published sections</small>
            <button type="submit" disabled={!question.trim()}>Run grounded prompt <span aria-hidden="true">→</span></button>
          </div>
        </form>
      </section>
    </div>
  );
}
