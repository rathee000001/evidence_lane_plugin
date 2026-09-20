"use client";

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {motion,useMotionValue,useSpring} from "framer-motion";
import {useMotion} from "./experience";
import {useSceneFocus} from "./scene-focus";

import { OriginalGlassIconOrb, OriginalGlassPill } from "./original-glass";

export function OriginalGlassPopup({
  children,
  labelledBy,
  onClose,
  open,
  panelId,
  size = "default",
  companion,
}: {
  children: ReactNode;
  labelledBy: string;
  onClose: () => void;
  open: boolean;
  panelId: string;
  size?: "default" | "wide";
  companion?:ReactNode;
}) {
  const {state:sceneFocus,open:focusScene,close:restoreScene}=useSceneFocus();
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const {paused}=useMotion();const[flat,setFlat]=useState(false),[compact,setCompact]=useState(false);const pitch=useMotionValue(0),yaw=useMotionValue(0);const smoothPitch=useSpring(pitch,{stiffness:100,damping:22}),smoothYaw=useSpring(yaw,{stiffness:100,damping:22});const still=flat||paused;
  useEffect(()=>{const media=matchMedia('(prefers-reduced-motion: reduce)');const update=()=>{setFlat(media.matches||innerWidth<=1000);setCompact(innerWidth<=1000)};update();media.addEventListener('change',update);window.addEventListener('resize',update);return()=>{media.removeEventListener('change',update);window.removeEventListener('resize',update)}},[]);


  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    // Some scene owners capture the trigger before replacing their normal scene
    // with the retained companion. Preserve that exact opening geometry.
    if(!sceneFocus.active)focusScene(previousFocusRef.current?.getBoundingClientRect(),document.querySelector('[data-scene-centre]')?.getBoundingClientRect());
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const visibleCompanionRects=()=>Array.from(document.querySelectorAll<HTMLElement>('.flow-object,.flow-object-label,[data-detail-companion]')).filter(node=>getComputedStyle(node).visibility!=='hidden').map(node=>node.getBoundingClientRect()).filter(r=>r.width&&r.height&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth);
    const initialCompanionRects=visibleCompanionRects();
    const closeOutside = (event: globalThis.MouseEvent) => {
      if(dialogRef.current?.contains(event.target as Node))return;
      if(event.target instanceof Element&&event.target.closest('.flow-object,.flow-object-label,[data-detail-companion]'))return;
      const rects=visibleCompanionRects();
      if(document.querySelector('.flow-webgl')?.getAttribute('data-detail-ready')!=='true')rects.push(...initialCompanionRects);
      if(rects.length){
        const left=Math.min(...rects.map(r=>r.left))-32,right=Math.max(...rects.map(r=>r.right))+32;
        const top=Math.min(...rects.map(r=>r.top))-36,bottom=Math.max(...rects.map(r=>r.bottom))+65;
        if(event.clientX>=left&&event.clientX<=right&&event.clientY>=top&&event.clientY<=bottom)return;
      }
      onClose();
    };

    window.scrollTo({top:window.scrollY,behavior:"instant"});
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("mousedown",closeOutside);
    window.requestAnimationFrame(() => dialogRef.current?.focus());
    return () => {
      restoreScene();
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("mousedown",closeOutside);
      previousFocusRef.current?.focus({preventScroll:true});
    };
  }, [onClose, open,focusScene,restoreScene]);
  useEffect(()=>{pitch.set(sceneFocus.rotation.x);yaw.set(sceneFocus.rotation.y)},[sceneFocus.rotation.x,sceneFocus.rotation.y,pitch,yaw]);

  if (!open || !sceneFocus.active) return null;

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
      className={`flowPopupOverlay focus-layout-${sceneFocus.layout}`}
      data-popup-fade-schema="T023_UNIVERSAL_POPUP_FADE_V001"
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      onKeyDown={keepFocusInside}
    >
      {companion}
      <motion.section
        initial={{opacity:0,z:still?0:-180,x:still?0:(sceneFocus.origin.x-.5)*90,y:still?0:(sceneFocus.origin.y-.5)*55}}
        animate={{opacity:1,z:0,x:0,y:0}}
        transition={{duration:still?0:.38,ease:[.22,1,.36,1]}}
        style={{rotateX:still||sceneFocus.frontal?0:smoothPitch,rotateY:still||sceneFocus.frontal?0:smoothYaw,rotateZ:still||sceneFocus.frontal?0:sceneFocus.rotation.z,transformPerspective:1600,left:compact?undefined:`${sceneFocus.panel.x*100}%`,top:compact?undefined:`${sceneFocus.panel.y*100}%`}}
        data-popup-depth={still?'flat':'perspective'}
        ref={dialogRef}
        className={`flowPopupMotion flowPopupMotion--${size}`}
        id={panelId}
        tabIndex={-1}
        data-popup-schema="T023_UNIVERSAL_FROSTED_POPUP_V001"
      >
        <OriginalGlassPill
          className="flowPopupClose"
          tone="neutral"
          aria-label="Close detail popup"
          leading={
            <OriginalGlassIconOrb color="#aebdca" size={30} decorative>
              <span aria-hidden="true">×</span>
            </OriginalGlassIconOrb>
          }
          onClick={onClose}
        >
          Close detail
        </OriginalGlassPill>
        <div className="flowPopupScroll" data-universal-pill-cluster="popup-website-detail">
          {children}
        </div>
      </motion.section>
    </div>,
    document.body,
  );
}
