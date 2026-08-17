"use client";

import { useCallback, useState, type KeyboardEvent } from "react";

import {
  GlassIconOrb,
  GlassPill,
  OfficialToolIcon,
  type OfficialToolIconName,
} from "./evidence-assets";
import { GovernedPopup } from "./governed-popup";

export type GovernedStoryItem = {
  id: string;
  label: string;
  summary: string;
  outcome: string;
  proof: string;
  boundary: string;
  details: readonly string[];
  icon: OfficialToolIconName;
  color: string;
};

function nextIndex(event: KeyboardEvent<HTMLButtonElement>, current: number, total: number) {
  if (event.key === "Home") return 0;
  if (event.key === "End") return total - 1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") return (current + 1) % total;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") return (current - 1 + total) % total;
  return null;
}

export function GovernedStoryExplorer({
  eyebrow,
  title,
  description,
  items,
}: {
  eyebrow: string;
  title: string;
  description: string;
  items: readonly GovernedStoryItem[];
}) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [popupOpen, setPopupOpen] = useState(false);
  const active = items[activeIndex];
  const closePopup = useCallback(() => setPopupOpen(false), []);

  const activate = (index: number) => {
    setActiveIndex(index);
    setPopupOpen(true);
  };

  return (
    <div className="governedStoryExplorer">
      <header className="governedStoryHeader">
        <div><span className="kicker">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>
        <div className="governedStoryStats" aria-label="Explorer contract summary">
          <span><strong>{items.length}</strong> inspectable stages</span>
          <span><strong>1</strong> exact authority chain</span>
          <span><strong>0</strong> inferred approvals</span>
        </div>
      </header>

      <div
        className="governedStoryPills"
        role="tablist"
        aria-label={`${title} stages`}
        data-universal-pill-cluster="governed-story-explorer"
      >
        {items.map((item, index) => (
          <GlassPill
            id={`governed-story-${item.id}`}
            key={item.id}
            active={index === activeIndex}
            tone={index === activeIndex ? "gold" : "cyan"}
            role="tab"
            aria-selected={index === activeIndex}
            aria-expanded={index === activeIndex && popupOpen}
            aria-controls="governed-story-popup"
            tabIndex={index === activeIndex ? 0 : -1}
            leading={<GlassIconOrb color={item.color} size={38} decorative><OfficialToolIcon tool={item.icon} size={20} decorative /></GlassIconOrb>}
            onClick={() => activate(index)}
            onFocus={() => setActiveIndex(index)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activate(index);
                return;
              }
              if (event.key === "Escape") {
                setPopupOpen(false);
                return;
              }
              const next = nextIndex(event, index, items.length);
              if (next === null) return;
              event.preventDefault();
              setActiveIndex(next);
              document.getElementById(`governed-story-${items[next].id}`)?.focus();
            }}
          >
            <span>{String(index + 1).padStart(2, "0")}<small>{item.label}</small></span>
          </GlassPill>
        ))}
      </div>

      <article className="governedStoryPreview" aria-live="polite">
        <div className="governedStoryIdentity">
          <GlassIconOrb color={active.color} size={58} decorative><OfficialToolIcon tool={active.icon} size={30} decorative /></GlassIconOrb>
          <div><span>Selected governed stage</span><h3>{active.label}</h3><p>{active.summary}</p></div>
        </div>
        <dl>
          <div><dt>Produces</dt><dd>{active.outcome}</dd></div>
          <div><dt>Evidence</dt><dd>{active.proof}</dd></div>
          <div><dt>Boundary</dt><dd>{active.boundary}</dd></div>
        </dl>
        <GlassPill
          tone="gold"
          aria-controls="governed-story-popup"
          aria-expanded={popupOpen}
          leading={<GlassIconOrb color={active.color} size={34} decorative><OfficialToolIcon tool={active.icon} size={18} decorative /></GlassIconOrb>}
          onClick={() => setPopupOpen(true)}
        >
          Open full stage contract
        </GlassPill>
      </article>

      <GovernedPopup
        labelledBy={`governed-story-${active.id}`}
        onClose={closePopup}
        open={popupOpen}
        panelId="governed-story-popup"
        size="wide"
      >
        <article className="governedStoryPopup" role="document">
          <div className="governedStoryIdentity">
            <GlassIconOrb color={active.color} size={64} decorative><OfficialToolIcon tool={active.icon} size={34} decorative /></GlassIconOrb>
            <div><span>{eyebrow} · stage {String(activeIndex + 1).padStart(2, "0")}</span><h3>{active.label}</h3><p>{active.summary}</p></div>
          </div>
          <div className="governedStoryDetailGrid">
            <section><h4>How it works</h4><ol>{active.details.map((detail) => <li key={detail}>{detail}</li>)}</ol></section>
            <section><h4>Inspectable result</h4><dl><div><dt>Produces</dt><dd>{active.outcome}</dd></div><div><dt>Evidence</dt><dd>{active.proof}</dd></div><div><dt>Authority boundary</dt><dd>{active.boundary}</dd></div></dl></section>
          </div>
        </article>
      </GovernedPopup>
    </div>
  );
}
