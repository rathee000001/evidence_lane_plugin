import type {ReactNode} from 'react';
import {CubeAppIcon} from './evidence-cube';
export type NavigationIconName='home'|'product'|'workflows'|'studio'|'how'|'integrations'|'docs'|'download';
const shapes:Record<Exclude<NavigationIconName,'product'>,ReactNode>={
 home:<><path d="m3 10 9-7 9 7M5 9v12h14V9"/><path d="M9 21v-7h6v7"/></>,
 workflows:<><circle cx="5" cy="5" r="2.5"/><circle cx="19" cy="8" r="2.5"/><circle cx="9" cy="19" r="2.5"/><path d="M7.5 5H13a6 6 0 0 1 6 3M19 10.5V13a6 6 0 0 1-6 6h-1.5M5 7.5V12"/><path d="m3 10 2 2 2-2"/></>,
 studio:<><rect x="2.5" y="3.5" width="19" height="14" rx="2"/><path d="M8 3.5v14M8 21h8m-4-3.5V21M11 7h7m-7 4h5"/></>,
 how:<><circle cx="12" cy="12" r="9"/><path d="m10 7 7 5-7 5Z"/></>,
 integrations:<><path d="M8 3v5m8-5v5M5 8h14v3a7 7 0 0 1-14 0V8ZM12 18v4"/><path d="M3 8h18"/></>,
 docs:<><path d="M3 4h5a5 5 0 0 1 4 2 5 5 0 0 1 4-2h5v15h-5a5 5 0 0 0-4 2 5 5 0 0 0-4-2H3V4ZM12 6v15"/><path d="M6 8h3m6 0h3M6 12h3m6 0h3"/></>,
 download:<><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>
};
export function NavigationIcon({name}:{name:NavigationIconName}){return name==='product'?<span data-nav-icon={name} className="nav-product-mark" aria-hidden="true"><CubeAppIcon size="100%" animated={false}/></span>:<svg data-nav-icon={name} viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{shapes[name]}</svg>}
