"use client";

import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { GlassIconOrb, GlassPill } from "./evidence-assets";

export function GovernedPopup({
  children,
  labelledBy,
  onClose,
  open,
  panelId,
  size = "default",
}: {
  children: ReactNode;
  labelledBy: string;
  onClose: () => void;
  open: boolean;
  panelId: string;
  size?: "default" | "wide";
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    window.requestAnimationFrame(() => dialogRef.current?.focus());
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      previousFocusRef.current?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;

  const closeFromBackdrop = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose();
  };

  const keepFocusInside = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusable.length === 0) {
      event.preventDefault();
      event.currentTarget.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return createPortal(
    <div
      className="governedPopupOverlay"
      data-popup-fade-schema="T023_UNIVERSAL_POPUP_FADE_V001"
      onMouseDown={closeFromBackdrop}
    >
      <section
        ref={dialogRef}
        className={`governedPopupMotion governedPopupMotion--${size}`}
        id={panelId}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onKeyDown={keepFocusInside}
        data-popup-schema="T023_UNIVERSAL_FROSTED_POPUP_V001"
      >
        <GlassPill
          className="governedPopupClose"
          tone="neutral"
          aria-label="Close detail popup"
          leading={
            <GlassIconOrb color="#aebdca" size={30} decorative>
              <span aria-hidden="true">×</span>
            </GlassIconOrb>
          }
          onClick={onClose}
        >
          Close detail
        </GlassPill>
        <div className="governedPopupScroll" data-universal-pill-cluster="popup-website-detail">
          {children}
        </div>
      </section>
    </div>,
    document.body,
  );
}
