import { memo, useId } from "react";
import {useObserver} from '../../observatory/ObserverContext';
import leftCore from "../generated/logo/left_core.svg";
import energyRoutes from "../generated/logo/energy_routes.svg";
import legacyTerminal from "../generated/logo/s_core.svg";
import {LaneWireMark} from '../../observatory/LaneWireMark';

export type EvidenceOSLogoProps = { width?: number | string; className?: string; animated?: boolean };

function EvidenceOSLogoComponent({ width = "100%", className = "", animated = true }: EvidenceOSLogoProps) {
  const id=useId(),{paused}=useObserver();const moving=animated&&!paused;
  return (
    <div className={`header-evidence-logo ${moving ? "is-animated" : ""} ${className}`.trim()} style={{ width }} role="img" aria-label="Evidence Lane">
      <svg viewBox="0 425 2440 950" preserveAspectRatio="xMidYMid meet">
        <defs><mask id={id} maskUnits="userSpaceOnUse" x="0" y="0" width="2400" height="1792"><rect width="2400" height="1792" fill="white"/><path d="M2080 680H2400V1120H2080V1030H1990V755H2080Z" fill="black"/></mask></defs>
        <g mask={`url(#${id})`}><image className="header-logo__energy" href={energyRoutes} x="0" y="0" width="2400" height="1792" />
        <image className="header-logo__left" href={leftCore} x="0" y="0" width="2400" height="1792" />
        <text className="header-logo__wordmark-text" x="1010" y="930" textLength="900" lengthAdjust="spacingAndGlyphs">EVIDENCE</text><image href={legacyTerminal} width="2400" height="1792"/></g>
        <LaneWireMark moving={moving}/>
      </svg>
    </div>
  );
}

export const EvidenceOSLogo = memo(EvidenceOSLogoComponent);
