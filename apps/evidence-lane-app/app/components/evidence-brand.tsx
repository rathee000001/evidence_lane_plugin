"use client";
import {useId} from 'react';
import {useMotion} from './experience';
import { CubeAppIcon } from "./evidence-cube";
import styles from "./evidence-brand.module.css";
export function EvidenceIcon({ large = false }: {large?:boolean}) { return <span className={styles.icon}><CubeAppIcon size={large?90:44}/></span>; }
// Preserve the original orbital layers and motion; the terminal badge uses the user's LANE monogram.
export function EvidenceBrand({ large = false }: {large?:boolean}) {
 const id=useId(),{paused}=useMotion();const play={animationPlayState:paused?'paused':'running'} as const;
 return <span className={`${styles.brand} ${large?styles.large:""}`} role="img" aria-label="Evidence Lane animated logo"><svg viewBox="-40 350 2500 1060" preserveAspectRatio="xMidYMid meet"><defs><mask id={id} maskUnits="userSpaceOnUse" x="0" y="0" width="2400" height="1792"><rect width="2400" height="1792" fill="white"/><path d="M2080 680H2400V1120H2080V1030H1990V755H2080Z" fill="black"/></mask></defs><g mask={`url(#${id})`}><image style={play} className={styles.energy} href="/brand-original/energy_routes.svg" width="2400" height="1792"/><image style={play} className={styles.left} href="/brand-original/left_core.svg" width="2400" height="1792"/><image style={play} className={styles.wordmark} href="/brand-original/wordmark.svg" width="2400" height="1792"/></g><image style={play} className={styles.terminal} href={'/brand-original/lane_core.svg'+(paused?'#still':'')} width="2400" height="1792"/></svg></span>;
}
