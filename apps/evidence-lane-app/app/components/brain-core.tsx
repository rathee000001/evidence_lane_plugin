"use client";
import Image from "next/image";
import type{CSSProperties}from"react";
import "./brain-core.css";
type BrainSphereStyle=CSSProperties & {"--brain-orb-color"?:string};
type PulseStyle=CSSProperties & {"--brain-intensity":number;"--brain-orb-color":string};
type BrainMotionState="idle"|"refresh-pulse"|"success-flash";
export function BrainGlassSphere({
  size = 360,
  className = "",
  color = "#67d9f6",
  decorative = false,
}: {
  size?: number | string;
  className?: string;
  color?: string;
  decorative?: boolean;
}) {
  const style: BrainSphereStyle = {
    width: size,
    height: size,
    "--brain-orb-color": color,
  };

  return (
    <div
      className={`brain-sphere ${className}`.trim()}
      style={style}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : "Evidence Lane brain in a glass sphere"}
      aria-hidden={decorative || undefined}
    >
      <span className="brain-sphere__rear" aria-hidden="true" />
      <Image
        className="brain-sphere__brain"
        src="/assets/evidence-static-brain.png"
        alt=""
        width={1142}
        height={1035}
        sizes="(max-width: 820px) 54vw, 320px"
      />
      <span className="brain-sphere__glass" aria-hidden="true" />
      <span className="brain-sphere__rim" aria-hidden="true" />
      <span className="brain-sphere__reflection" aria-hidden="true" />
    </div>
  );
}

export function PulsatingBrain({
  size = 380,
  active = true,
  intensity = 1,
  className = "",
  color = "#67d9f6",
  motionState = "idle",
  decorative = false,
}: {
  size?: number | string;
  active?: boolean;
  intensity?: number;
  className?: string;
  color?: string;
  motionState?: BrainMotionState;
  decorative?: boolean;
}) {
  const style: PulseStyle = {
    width: size,
    height: size,
    "--brain-intensity": Math.max(0.35, Math.min(intensity, 1.8)),
    "--brain-orb-color": color,
  };

  return (
    <div
      className={`pulsating-brain ${active || motionState !== "idle" ? "is-active" : ""} is-${motionState} ${className}`.trim()}
      data-brain-motion-state={motionState}
      style={style}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : "Animated Evidence Lane brain"}
      aria-hidden={decorative || undefined}
    >
      <svg className="pulsating-brain__energy" viewBox="0 0 400 400" aria-hidden="true">
        <g className="pulsating-brain__arcs pulsating-brain__arcs--cool">
          <path d="M82 92a158 158 0 0 1 76-43" />
          <path d="M69 76a178 178 0 0 1 82-47" />
          <path d="M82 308a158 158 0 0 0 76 43" />
          <path d="M69 324a178 178 0 0 0 82 47" />
        </g>
        <g className="pulsating-brain__arcs pulsating-brain__arcs--warm">
          <path d="M242 49a158 158 0 0 1 76 43" />
          <path d="M249 29a178 178 0 0 1 82 47" />
          <path d="M242 351a158 158 0 0 0 76-43" />
          <path d="M249 371a178 178 0 0 0 82-47" />
        </g>
        <path className="pulsating-brain__wave pulsating-brain__wave--cool" d="M0 200h18l8-19 10 58 13-91 11 88 13-56 10 20h22" />
        <path className="pulsating-brain__wave pulsating-brain__wave--warm" d="M295 200h22l10-20 13 56 11-88 13 91 10-58 8 19h18" />
      </svg>
      <span className="pulsating-brain__halo" aria-hidden="true" />
      <BrainGlassSphere
        size="67%"
        color={color}
        className="pulsating-brain__orb"
        decorative
      />
    </div>
  );
}
