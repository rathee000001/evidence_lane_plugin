"use client";

import { useCallback, useState, type KeyboardEvent } from "react";

import { pluginSurfaces } from "../_data/plugin-surfaces";
import {
  GlassIconOrb,
  GlassPill,
  OfficialToolIcon,
  type OfficialToolIconName,
} from "./evidence-assets";
import { GovernedPopup } from "./governed-popup";

const familyColors = {
  Router: "#69d9f5",
  Lifecycle: "#efca72",
  Session: "#83ddb3",
  Mode: "#b6a0ff",
  Connector: "#f2a1c5",
  Storage: "#7fc9ef",
} as const;

const familyIcons: Record<(typeof pluginSurfaces)[number]["family"], OfficialToolIconName> = {
  Router: "pulse",
  Lifecycle: "package",
  Session: "terminal",
  Mode: "node",
  Connector: "docker",
  Storage: "database",
};

function nextIndex(
  event: KeyboardEvent<HTMLButtonElement>,
  current: number,
  total: number,
) {
  if (event.key === "Home") return 0;
  if (event.key === "End") return total - 1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") return (current + 1) % total;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") return (current - 1 + total) % total;
  return null;
}

export function PluginSurfaceCatalog() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [popupOpen, setPopupOpen] = useState(false);
  const active = pluginSurfaces[activeIndex];
  const closePopup = useCallback(() => setPopupOpen(false), []);

  const activate = (index: number) => {
    setActiveIndex(index);
    setPopupOpen(true);
  };

  return (
    <div className="pluginSurfaceCatalog">
      <div className="pluginSurfaceSummary" aria-label="Plugin surface counts">
        <div><strong>15</strong><span>installed surfaces</span></div>
        <div><strong>6</strong><span>primary lifecycle controls</span></div>
        <div><strong>9</strong><span>bounded routers and sidecars</span></div>
      </div>

      <div
        className="pluginSurfacePills"
        role="tablist"
        aria-label="Evidence Lane plugin settings"
        data-universal-pill-cluster="website-plugin-settings"
      >
        {pluginSurfaces.map((surface, index) => (
          <GlassPill
            id={`plugin-surface-${index}`}
            key={surface.id}
            className="pluginSurfacePill"
            active={index === activeIndex && popupOpen}
            tone={surface.primaryControl ? "gold" : "cyan"}
            role="tab"
            aria-selected={index === activeIndex && popupOpen}
            aria-expanded={index === activeIndex && popupOpen}
            aria-controls="plugin-surface-popup"
            tabIndex={index === activeIndex ? 0 : -1}
            leading={
              <GlassIconOrb color={familyColors[surface.family]} size={38} decorative>
                <OfficialToolIcon tool={familyIcons[surface.family]} size={21} decorative />
              </GlassIconOrb>
            }
            onClick={() => activate(index)}
            onFocus={() => setActiveIndex(index)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setPopupOpen(false);
                return;
              }
              const next = nextIndex(event, index, pluginSurfaces.length);
              if (next === null) return;
              event.preventDefault();
              activate(next);
              document.getElementById(`plugin-surface-${next}`)?.focus();
            }}
          >
            <span>{surface.label}<small>{surface.command}</small></span>
          </GlassPill>
        ))}
      </div>

      <GovernedPopup
        labelledBy={`plugin-surface-${activeIndex}`}
        onClose={closePopup}
        open={popupOpen}
        panelId="plugin-surface-popup"
      >
        <article
          className="pluginSurfacePopup"
          role="document"
        >
          <div className="pluginPopupIdentity">
            <GlassIconOrb color={familyColors[active.family]} size={58} decorative>
              <OfficialToolIcon tool={familyIcons[active.family]} size={30} decorative />
            </GlassIconOrb>
            <div>
              <span>{active.family}{active.primaryControl ? " · primary control" : " · bounded sidecar"}</span>
              <h3>{active.label}</h3>
              <code>{active.command}</code>
            </div>
          </div>
          <p>{active.description}</p>
          <dl>
            <div><dt>Setting</dt><dd>{active.setting}</dd></div>
            <div><dt>Produces</dt><dd>{active.produces}</dd></div>
            <div><dt>Boundary</dt><dd>{active.boundary}</dd></div>
          </dl>
        </article>
      </GovernedPopup>
      {!popupOpen ? <p className="pluginPopupHint">Choose any pill to open its exact settings and authority boundary.</p> : null}
    </div>
  );
}
