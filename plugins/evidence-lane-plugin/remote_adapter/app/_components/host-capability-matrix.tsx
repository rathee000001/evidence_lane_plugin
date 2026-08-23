"use client";

import { useCallback, useState } from "react";

import {
  hostCapabilityProfiles,
  type HostCapabilityProfile,
} from "../_data/current-product-contract";
import { GlassIconOrb, OfficialToolIcon } from "./evidence-assets";
import { GovernedPopup } from "./governed-popup";

function HostProfileDetail({ profile }: { profile: HostCapabilityProfile }) {
  return (
    <article className="hostProfileDetail">
      <header>
        <span>Capability-conditioned route</span>
        <h3>{profile.label}</h3>
        <p>{profile.interaction}</p>
      </header>
      <dl>
        <div><dt>Project storage</dt><dd>{profile.storage}</dd></div>
        <div><dt>Native route</dt><dd>{profile.nativeRoute}</dd></div>
        <div><dt>Proven tool gap</dt><dd>{profile.toolGapRoute}</dd></div>
        <div><dt>Setup frequency</dt><dd>{profile.setupFrequency}</dd></div>
        <div><dt>Credentials</dt><dd>{profile.credentialBoundary}</dd></div>
        <div><dt>Authority boundary</dt><dd>{profile.lifecycleBoundary}</dd></div>
      </dl>
    </article>
  );
}

export function HostCapabilityMatrix({ compact = false }: { compact?: boolean }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [open, setOpen] = useState(false);
  const active = hostCapabilityProfiles[activeIndex];
  const close = useCallback(() => setOpen(false), []);

  return (
    <div className={`hostCapabilityMatrix${compact ? " hostCapabilityMatrix--compact" : ""}`}>
      <div className="hostCapabilityGrid" aria-label="Evidence Lane capability-conditioned host profiles">
        {hostCapabilityProfiles.map((profile, index) => (
          <button
            aria-controls="host-capability-detail"
            aria-expanded={open && activeIndex === index}
            className="hostCapabilityCard"
            id={`host-profile-${profile.id}`}
            key={profile.id}
            onClick={() => {
              setActiveIndex(index);
              setOpen(true);
            }}
            type="button"
          >
            <GlassIconOrb color={["#69d9f5", "#83ddb3", "#efca72", "#a99af7", "#f2a1c5", "#a8d878", "#9aaabd"][index]} size={36} decorative>
              <OfficialToolIcon tool={index === 6 ? "media" : index < 2 ? "terminal" : "database"} size={19} decorative />
            </GlassIconOrb>
            <span>
              <strong>{profile.label}</strong>
              <small>{profile.storage}</small>
            </span>
            <b aria-hidden="true">Open</b>
          </button>
        ))}
      </div>
      <p className="hostMatrixBoundary">
        Storage durability, interaction profile, VM lifetime, native capability,
        account tier, API billing, transport, and credential route are independent axes.
      </p>
      <GovernedPopup
        labelledBy={`host-profile-${active.id}`}
        onClose={close}
        open={open}
        panelId="host-capability-detail"
        size="wide"
      >
        <HostProfileDetail profile={active} />
      </GovernedPopup>
    </div>
  );
}
