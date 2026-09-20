"use client";
import { CubeAppIcon } from "./evidence-cube";
import styles from "./evidence-brand.module.css";
export function EvidenceIcon({ large = false }: {large?:boolean}) { return <span className={styles.icon}><CubeAppIcon size={large?90:44}/></span>; }
// Original layered identity from the existing EvidenceOS frontend, with its original phased energy sequence.
export function EvidenceBrand({ large = false }: {large?:boolean}) {
 return <span className={`${styles.brand} ${large?styles.large:""}`} role="img" aria-label="Evidence Lane animated logo"><svg viewBox="-40 350 2500 1060" preserveAspectRatio="xMidYMid meet"><image className={styles.energy} href="/brand-original/energy_routes.svg" width="2400" height="1792"/><image className={styles.left} href="/brand-original/left_core.svg" width="2400" height="1792"/><image className={styles.wordmark} href="/brand-original/wordmark.svg" width="2400" height="1792"/><image className={styles.terminal} href="/brand-original/s_core.svg" width="2400" height="1792"/></svg></span>;
}
