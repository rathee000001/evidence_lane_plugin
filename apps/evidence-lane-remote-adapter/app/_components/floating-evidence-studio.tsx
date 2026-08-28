"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { floatingStudioSuggestions } from "../_data/site";
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
  suggestions?: string[];
};

const initialMessage: FloatingMessage = {
  id: 0,
  role: "assistant",
  title: "Evidence AI Studio",
  text: "Ask what this page means for the product, the operator, or the release decision. I will answer in business language and keep the supporting evidence receipt available for optional review.",
  mode: "governed_boundary",
};

function suggestionsFor(pathname: string) {
  if (pathname === "/") {
    return floatingStudioSuggestions.home;
  }
  const routeKey = pathname.split("/").filter(Boolean)[0];
  if (routeKey && routeKey in floatingStudioSuggestions) {
    return floatingStudioSuggestions[
      routeKey as keyof typeof floatingStudioSuggestions
    ];
  }
  return floatingStudioSuggestions.default;
}

export function FloatingEvidenceStudio() {
  const pathname = usePathname() || "/";
  const routeSuggestions = useMemo(() => suggestionsFor(pathname), [pathname]);
  const [suggestions, setSuggestions] = useState<readonly string[]>(routeSuggestions);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sequence, setSequence] = useState(1);
  const [messages, setMessages] = useState<FloatingMessage[]>([initialMessage]);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const hasRetainedState = input.length > 0 || messages.length > 1 || busy;

  useEffect(() => {
    setSuggestions(routeSuggestions);
  }, [routeSuggestions]);

  useEffect(() => {
    if (!open) return;
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onEscape);
    document.addEventListener("pointerdown", onOutsidePointer, true);
    inputRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onEscape);
      document.removeEventListener("pointerdown", onOutsidePointer, true);
    };
  }, [open]);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length, open]);

  const ask = async (override?: string) => {
    const question = (override ?? input).trim();
    if (!question || busy) return;
    const userId = sequence;
    const assistantId = sequence + 1;
    const history = messages.slice(-8).map((message) => ({
      role: message.role,
      text: message.text,
    }));
    setSequence((current) => current + 2);
    setMessages((current) => [...current.slice(-12), { id: userId, role: "user", text: question }]);
    setInput("");
    setBusy(true);
    try {
      const response = await fetch("/api/studio-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, pagePath: pathname, history }),
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
      if (result.suggestions?.length) setSuggestions(result.suggestions.slice(0, 8));
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
        aria-label={hasRetainedState ? "Restore Evidence AI Studio" : "Open Evidence AI Studio"}
        aria-expanded={open}
        aria-controls="floating-evidence-studio"
        data-retained-state={hasRetainedState ? "true" : "false"}
        onClick={() => setOpen(true)}
      >
        <GlassIconOrb color="#69d9f5" size={48} decorative>
          <LaneAssetIcon lane="chat_lineage" size={27} decorative />
        </GlassIconOrb>
        <span><small>Evidence AI</small><strong>Ask this page</strong></span>
      </button>

      {open ? (
        <aside
          ref={panelRef}
          className="floatingStudioPanel"
          id="floating-evidence-studio"
          aria-label="Floating Evidence AI Studio"
        >
          <header>
            <div>
              <GlassIconOrb color="#69d9f5" size={50} decorative>
                <LaneAssetIcon lane="chat_lineage" size={28} decorative />
              </GlassIconOrb>
              <span><small>Evidence Lane</small><strong>Evidence AI Studio</strong><p>Whole-plugin business guide / governed evidence</p></span>
            </div>
            <div className="floatingStudioHeaderActions">
              <Link
                className="universal-pill floatingStudioFullLink"
                data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"
                href="/studio"
                onClick={() => setOpen(false)}
              >
                <GlassIconOrb color="#8cdff2" size={28} decorative>
                  <OfficialToolIcon tool="pulse" size={15} decorative />
                </GlassIconOrb>
                <span>Full Studio</span>
              </Link>
              <button className="floatingStudioClose" type="button" aria-label="Minimize Evidence AI Studio" onClick={() => setOpen(false)}>
                <GlassIconOrb color="#efb75c" size={30} decorative><span aria-hidden="true">&times;</span></GlassIconOrb>
              </button>
            </div>
          </header>

          <div className="floatingStudioPolicy">
            <span><GlassIconOrb color="#70dff4" size={24} decorative><OfficialToolIcon tool="database" size={13} decorative /></GlassIconOrb><b>Business answer first</b></span>
            <span><GlassIconOrb color="#efb75c" size={24} decorative><OfficialToolIcon tool="pulse" size={13} decorative /></GlassIconOrb><b>Unsupported claims refuse</b></span>
            <span><GlassIconOrb color="#99e1bd" size={24} decorative><OfficialToolIcon tool="node" size={13} decorative /></GlassIconOrb><b>Evidence stays visible</b></span>
          </div>

          <div className="floatingStudioTranscript" aria-live="polite">
            {messages.map((message) => (
              <article className={`floatingStudioMessage is-${message.role}`} key={message.id}>
                {message.title ? <strong>{message.title}</strong> : null}
                {message.mode ? <small>{message.mode.replaceAll("_", " ")}</small> : null}
                <p>{message.text}</p>
                {message.retrieval ? (
                  <details className="floatingStudioReceipt">
                    <summary>Open evidence receipt</summary>
                    <code>RRF {message.retrieval.rrf.toFixed(6)} / coverage {(message.retrieval.queryCoverage * 100).toFixed(0)}% / corpus {message.retrieval.corpus.slice(0, 12)}</code>
                  </details>
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
              <button type="button" key={suggestion} disabled={busy} onClick={() => void ask(suggestion)}>
                <GlassIconOrb color="#70dff4" size={28} decorative><LaneAssetIcon lane="chat_lineage" size={15} decorative /></GlassIconOrb>
                <span>{suggestion}</span>
              </button>
            ))}
          </div>

          <div className="floatingStudioComposer">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="What does this mean for the business or operator?"
            />
            <button type="button" disabled={busy || !input.trim()} onClick={() => void ask()}>
              <GlassIconOrb color="#efb75c" size={30} decorative><OfficialToolIcon tool="terminal" size={16} decorative /></GlassIconOrb>
              <span>{busy ? "Checking" : "Send"}</span>
            </button>
          </div>
          <p className="floatingStudioBoundary">External answers are never project evidence, PV authority, source truth, or HIL approval.</p>
        </aside>
      ) : null}
    </>
  );
}
