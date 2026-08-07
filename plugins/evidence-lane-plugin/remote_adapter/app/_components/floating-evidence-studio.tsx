"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { RetrievalReceipt, StudioSource } from "../_data/studio-retrieval";
import { GlassIconOrb, LaneAssetIcon, OfficialToolIcon } from "./evidence-assets";

type FloatingMessage = {
  id: number;
  role: "assistant" | "user";
  text: string;
  title?: string;
  mode?: string;
  sources?: readonly StudioSource[];
  retrieval?: RetrievalReceipt;
};

type StudioQueryResponse = {
  answer?: string;
  title?: string;
  mode?: string;
  provider?: string;
  model?: string;
  sources?: StudioSource[];
  retrieval?: RetrievalReceipt;
};

const initialMessage: FloatingMessage = {
  id: 0,
  role: "assistant",
  title: "Evidence AI Studio",
  text: "Ask this page from the same committed retrieval corpus used by Prompt Studio. Project no-hits refuse; genuine general no-hits may use the separately configured openrouter/free route and are labeled outside project evidence.",
  mode: "governed_boundary",
};

function suggestionsFor(pathname: string) {
  if (pathname.startsWith("/architecture")) {
    return [
      "How do parallel lanes converge on serial authority?",
      "What separates a candidate from an accepted pointer?",
      "What is Source Intake responsible for?",
    ];
  }
  if (pathname.startsWith("/lanes")) {
    return [
      "What four files does each detected lane emit?",
      "When must an undetected lane have no PV folder?",
      "How is the Git test separated from non-Git tests?",
    ];
  }
  if (pathname.startsWith("/studio")) {
    return [
      "How is the Prompt Studio corpus built?",
      "What happens when project evidence is missing?",
      "Why is the external route not project authority?",
    ];
  }
  return [
    "What problem does Evidence Lane solve?",
    "How do HIL and accepted pointers differ?",
    "What is active step 46?",
  ];
}

export function FloatingEvidenceStudio() {
  const pathname = usePathname() || "/";
  const suggestions = useMemo(() => suggestionsFor(pathname), [pathname]);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sequence, setSequence] = useState(1);
  const [messages, setMessages] = useState<FloatingMessage[]>([initialMessage]);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onEscape);
    inputRef.current?.focus();
    return () => window.removeEventListener("keydown", onEscape);
  }, [open]);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length, open]);

  const ask = async (override?: string) => {
    const question = (override ?? input).trim();
    if (!question || busy) return;
    const userId = sequence;
    const assistantId = sequence + 1;
    setSequence((current) => current + 2);
    setMessages((current) => [...current.slice(-7), { id: userId, role: "user", text: question }]);
    setInput("");
    setBusy(true);
    try {
      const response = await fetch("/api/studio-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, pagePath: pathname }),
      });
      const result = await response.json() as StudioQueryResponse;
      const provider = result.mode === "external_general_free"
        ? ` Provider: ${result.provider ?? "OpenRouter"}; model: ${result.model ?? "openrouter/free"}.`
        : "";
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: "assistant",
          title: result.title ?? "Evidence Lane Studio",
          text: `${result.answer ?? "No answer was returned."}${provider}`,
          mode: result.mode ?? "boundary",
          sources: result.sources,
          retrieval: result.retrieval,
        },
      ]);
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: "assistant",
          title: "Studio connection unavailable",
          text: "The Studio route could not be reached. No project answer or provider result was invented.",
          mode: "connection_error",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    void ask();
  };

  return (
    <>
      <button
        className={`floatingStudioLauncher${open ? " is-hidden" : ""}`}
        type="button"
        aria-label="Open Evidence AI Studio"
        aria-expanded={open}
        aria-controls="floating-evidence-studio"
        onClick={() => setOpen(true)}
      >
        <GlassIconOrb color="#69d9f5" size={48} decorative>
          <LaneAssetIcon lane="chat_lineage" size={27} decorative />
        </GlassIconOrb>
        <span><small>Evidence AI</small><strong>Ask this page</strong></span>
      </button>

      {open ? (
        <aside
          className="floatingStudioPanel"
          id="floating-evidence-studio"
          aria-label="Floating Evidence AI Studio"
        >
          <header>
            <div>
              <GlassIconOrb color="#69d9f5" size={50} decorative>
                <LaneAssetIcon lane="chat_lineage" size={28} decorative />
              </GlassIconOrb>
              <span><small>Evidence Lane</small><strong>Evidence AI Studio</strong><p>Same committed RAG / explicit external boundary</p></span>
            </div>
            <div className="floatingStudioHeaderActions">
              <Link href="/studio" onClick={() => setOpen(false)}>Full Studio</Link>
              <button type="button" aria-label="Close Evidence AI Studio" onClick={() => setOpen(false)}>&times;</button>
            </div>
          </header>

          <div className="floatingStudioPolicy">
            <span>Local evidence first</span><span>Project no-hit refuses</span><span>Free model only</span>
          </div>

          <div className="floatingStudioTranscript" aria-live="polite">
            {messages.map((message) => (
              <article className={`floatingStudioMessage is-${message.role}`} key={message.id}>
                {message.title ? <strong>{message.title}</strong> : null}
                {message.mode ? <small>{message.mode.replaceAll("_", " ")}</small> : null}
                <p>{message.text}</p>
                {message.retrieval ? (
                  <code>RRF {message.retrieval.rrf.toFixed(6)} / coverage {(message.retrieval.queryCoverage * 100).toFixed(0)}% / corpus {message.retrieval.corpus.slice(0, 12)}</code>
                ) : null}
                {message.sources?.length ? (
                  <footer>{message.sources.slice(0, 4).map((source) => <Link href={source.href} key={`${message.id}-${source.href}-${source.label}`}>{source.label}</Link>)}</footer>
                ) : null}
              </article>
            ))}
            {busy ? <p className="floatingStudioBusy">Checking the governed boundary...</p> : null}
            <div ref={endRef} />
          </div>

          <div className="floatingStudioSuggestions" aria-label="Page-aware suggestions">
            {suggestions.map((suggestion) => (
              <button type="button" key={suggestion} disabled={busy} onClick={() => void ask(suggestion)}>{suggestion}</button>
            ))}
          </div>

          <div className="floatingStudioComposer">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="Ask this page or a general question..."
            />
            <button type="button" disabled={busy || !input.trim()} onClick={() => void ask()}>
              <OfficialToolIcon tool="terminal" size={17} decorative />
              <span>{busy ? "Checking" : "Send"}</span>
            </button>
          </div>
          <p className="floatingStudioBoundary">External answers are never project evidence, PV authority, source truth, or HIL approval.</p>
        </aside>
      ) : null}
    </>
  );
}
