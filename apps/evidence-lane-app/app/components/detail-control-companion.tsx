'use client';
import type {CSSProperties} from 'react';
import {useLayoutEffect,useRef} from 'react';
import {useSceneFocus} from './scene-focus';
import {OriginalGlassIconOrb as Orb,OriginalGlassPill as Pill} from './original-glass';
import {SemanticIcon,semanticIconColors,type SemanticIconName} from './semantic-icon';
import {ToolIdentity} from './tool-identity';

export function DetailControlCompanion({onSelect}:{onSelect:(id:string)=>void}){
 const {state}=useSceneFocus();const group=useRef<HTMLElement>(null);
 useLayoutEffect(()=>{if(innerWidth>1000)return;const node=group.current,active=node?.querySelector<HTMLElement>('[aria-pressed=true]');if(node&&active)node.scrollLeft=active.offsetLeft-node.clientWidth/2+active.offsetWidth/2},[state.sourceAction,state.active]);
 if(!state.active||state.companion?.kind!=='controls')return null;
 return <nav ref={group} className="detail-control-companion" data-detail-companion aria-label="Related details" style={{left:`${state.dock.x*100}%`,top:`${state.dock.y*100}%`} as CSSProperties}>
 {state.companion.items.map((item,i)=>{const icon=(item.icon&&item.icon in semanticIconColors?item.icon:'records') as SemanticIconName;return <Pill key={item.action+'-'+i} active={state.sourceAction===item.action} aria-pressed={state.sourceAction===item.action} data-detail-name={item.title} data-detail-action={item.action} leading={item.toolId?<ToolIdentity id={item.toolId} size={46}/>:<Orb color={item.color??semanticIconColors[icon]} size={46} decorative>{item.image?<img src={item.image} alt=""/>:<SemanticIcon name={icon}/>}</Orb>} onClick={()=>onSelect(item.action)}>{item.title}</Pill>})}
 </nav>;
}
