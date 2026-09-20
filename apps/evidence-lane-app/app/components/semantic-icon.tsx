"use client";
import {useId,useLayoutEffect,useRef,type ReactNode} from 'react';
export type SemanticIconName='plan'|'sources'|'results'|'memory'|'work'|'connections'|'tools'|'studio'|'workflow'|'project'|'guide'|'download'|'checks'|'chat'|'learning'|'records'|'resume'|'recovery'|'worker'|'engine'|'plugin'|'compute'|'api';
export const semanticIconColors:Record<SemanticIconName,string>={plan:'#bc96ff',sources:'#63dafa',results:'#73e3b0',memory:'#f391cc',work:'#f4bf69',connections:'#ebcd81',tools:'#bb91fc',studio:'#8cc7ff',workflow:'#78e7b9',project:'#73d9f5',guide:'#acc7ff',download:'#efd27e',checks:'#79e1b0',chat:'#5dbeed',learning:'#74da9c',records:'#87c5f2',resume:'#74dbc6',recovery:'#ecc276',worker:'#e9ac6a',engine:'#8abcec',plugin:'#bba0f2',compute:'#e2d080',api:'#9db5ed'};
// Plan, data and artifact shapes adapt the retained SourceLaneIcon component from the earlier website.
const shapes:Record<SemanticIconName,ReactNode>={
 chat:<><path d="M3 3h14v11H9l-5 4v-4H3Z"/><path d="M17 7h4v12h-4l-4 3v-3h-3v-3"/><path d="M7 7h6M7 10h4"/></>,
 learning:<><path d="M12 21V10M12 15C3 15 2 8 3 4c6 0 9 3 9 9M12 12c0-7 5-9 9-9 1 6-2 10-9 11M7 21h10"/></>,
 records:<><rect x="4" y="8" width="16" height="13" rx="2"/><path d="M6 5h12M8 2h8M8 12h8M8 16h5"/></>,
 resume:<><path d="M4 9a9 9 0 1 1 0 7M4 3v6h6"/><path d="m10 8 6 4-6 4Z"/></>,
 recovery:<><path d="M3 6h18v15H3ZM2 3h20v3H2Z"/><path d="M12 18V9m-4 4 4-4 4 4"/></>,
 worker:<><rect x="6" y="5" width="12" height="13" rx="2"/><path d="M9 9h6M9 13h6M3 8h3M3 15h3m12-7h3m-3 7h3M9 18v3m6-3v3M9 2v3m6-3v3"/></>,
 engine:<><rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6h1m3 0h6M7 17h1m3 0h6M12 10v4"/></>,
 plugin:<><path d="M4 3h6a3 3 0 1 0 6 0h5v6a3 3 0 1 0 0 6v6h-6a3 3 0 1 0-6 0H3v-6a3 3 0 1 0 0-6V3Z"/></>,
 compute:<><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M8 1v4m4-4v4m4-4v4M8 19v4m4-4v4m4-4v4M1 8h4m-4 4h4m-4 4h4m14-8h4m-4 4h4m-4 4h4"/></>,
 api:<><path d="m7 5-6 7 6 7m10-14 6 7-6 7M14 3l-4 18"/></>,

 guide:<><path d="M3 4h7a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H3Zm18 0h-5a3 3 0 0 0-3 3v14a4 4 0 0 1 4-2h4Z"/></>,
 download:<><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
 checks:<><path d="m12 2 8 4v6c0 5-5 8-8 10-3-2-8-5-8-10V6Z"/><path d="m8 12 3 3 5-6"/></>,
 plan:<><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M8 9h8M8 13h4m3 0 1 1 2-3M8 17h8"/></>,
 sources:<><ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></>,
 results:<><path d="m4 7 8-4 8 4-8 4ZM4 7v10l8 4 8-4V7M12 11v10"/><path d="m8 14 2 2 4-4"/></>,
 memory:<><path d="M12 5a4 4 0 0 0-7-1 4 4 0 0 0-2 7 4 4 0 0 0 3 7c1 4 6 3 6 0V5Zm0 0a4 4 0 0 1 7-1 4 4 0 0 1 2 7 4 4 0 0 1-3 7c-1 4-6 3-6 0V5Z"/><path d="m7 8 2 2-1 4m9-6-2 2 1 4"/></>,
 work:<><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3m6 0h4"/></>,
 connections:<><path d="m10 14 4-4m-5 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 8a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-2 2"/></>,
 tools:<><path d="M14 3a6 6 0 0 0-7 7L3 18l3 3 8-8a6 6 0 0 0 7-7l-4 3-3-3 3-3Z"/></>,
 studio:<><rect x="2" y="3" width="20" height="15" rx="2"/><path d="M8 22h8m-4-4v4M6 7h4v7H6Zm8 0h4m-4 4h4"/></>,
 workflow:<><circle cx="5" cy="5" r="2"/><circle cx="19" cy="7" r="2"/><circle cx="12" cy="19" r="2"/><path d="M7 5h5a7 7 0 0 1 7 0M5 7v5a7 7 0 0 0 5 7m9-10v3a7 7 0 0 1-5 7"/></>,
 project:<><path d="m4 7 8-4 8 4-8 4ZM4 7v10l8 4 8-4V7M12 11v10"/></>
};
export function SemanticIcon({name,size=24}:{name:SemanticIconName;size?:number}){const id=useId();const art=useRef<SVGGElement>(null);useLayoutEffect(()=>{const g=art.current;if(!g)return;const b=g.getBBox();if(b.width&&b.height)g.setAttribute('transform',`translate(${12-b.x-b.width/2},${12-b.y-b.height/2})`)},[name]);const tone=semanticIconColors[name];if(name==='memory')return <span className="memory-brain-art" data-semantic-icon="memory" style={{width:size,height:size}} aria-hidden="true"><img src="/assets/evidence-static-brain.png" alt=""/></span>;return <svg width={size} height={size} viewBox="0 0 24 24" fill={`url(#${id})`} stroke={tone} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" data-semantic-icon={name}><defs><linearGradient id={id} x1="0" y1="0" x2="1" y2="1"><stop stopColor={`var(--glass-orb-color,${tone})`} stopOpacity=".95"/><stop offset="1" stopColor="#183c66" stopOpacity=".8"/></linearGradient></defs><g ref={art}><g className="semantic-icon-depth" transform="translate(1.15 1.3)" fill="#06182d" stroke="#0a263d">{shapes[name]}</g><g className="semantic-icon-face">{shapes[name]}</g></g></svg>}
