"use client";
import type{CSSProperties,ReactNode,ButtonHTMLAttributes}from"react";
import {useLayoutEffect,useRef} from 'react';
import "./original-glass.css";
type OriginalGlassPillTone="neutral"|"cyan"|"gold"|"green"|"rose"|"violet";
type OriginalGlassIconOrbStyle = CSSProperties & {
  "--glass-orb-color"?: string;
  "--glass-orb-size"?: string;
};

export function OriginalGlassIconOrb({
  children,
  className = "",
  color = "#69d9f5",
  decorative = false,
  label,
  size = 36,
}: {
  children: ReactNode;
  className?: string;
  color?: string;
  decorative?: boolean;
  label?: string;
  size?: number | string;
}) {
  const resolvedSize = typeof size === "number" ? `${size}px` : size;
  const style: OriginalGlassIconOrbStyle = {
    "--glass-orb-color": color,
    "--glass-orb-size": resolvedSize,
  };

  return (
    <span
      className={`original-glass-orb ${className}`.trim()}
      style={style}
      data-glass-orb-host="true"
      data-universal-orb-schema="T023_UNIVERSAL_GLASS_ORB_V001"
      data-glass-orb-content-origin="0,0"
      role={!decorative && label ? "img" : undefined}
      aria-label={!decorative ? label : undefined}
      aria-hidden={decorative || undefined}
    >
      <span className="orb-reflected-rim" aria-hidden="true" />
      <span className="orb-specular-light" aria-hidden="true" />
      <span className="orb-caustic-light" aria-hidden="true" />
      <span className="original-glass-orb__content">
        <span className="original-glass-orb__payload">{children}</span>
      </span>
    </span>
  );
}

export function OriginalGlassPill({
  children,
  leading,
  trailing,
  active = false,
  tone = "neutral",
  className = "",
  type = "button",
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  children: ReactNode;
  leading?: ReactNode;
  trailing?: ReactNode;
  active?: boolean;
  tone?: OriginalGlassPillTone;
}) {
  const buttonRef=useRef<HTMLButtonElement>(null);
  useLayoutEffect(()=>{const button=buttonRef.current;const orb=button?.querySelector<HTMLElement>('[data-glass-orb-host]');if(button&&orb)button.style.setProperty('--item-color',orb.style.getPropertyValue('--glass-orb-color'))});
  return (
    <button
      ref={buttonRef}
      {...props}
      type={type}
      className={`original-glass-pill original-pill original-pill--${tone}${active ? " is-active" : ""} ${className}`.trim()}
      data-original-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"
      data-pill-content-mode="text"
      data-pill-geometry-schema="universal"
      data-pill-has-leading={leading ? "true" : "false"}
      data-pill-has-trailing={trailing ? "true" : "false"}
      data-pill-containment="no-overlap"
      data-pill-edge-inset-policy="symmetric-content-box"
      data-pill-inline-layout={leading && trailing ? "leading-label-trailing" : leading ? "leading-label" : trailing ? "label-trailing" : "label-only"}
    >
      <span className="original-pill__content original-glass-pill__content">
        {leading ? <span className="original-pill__slot original-pill__slot--leading">{leading}</span> : null}
        <span className="original-pill__label original-glass-pill__label">{children}</span>
        {trailing ? <span className="original-pill__slot original-pill__slot--trailing">{trailing}</span> : null}
      </span>
    </button>
  );
}
