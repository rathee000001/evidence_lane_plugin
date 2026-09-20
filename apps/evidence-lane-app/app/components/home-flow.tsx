"use client";
import {useEffect,useRef,useState,useMemo,useCallback,type CSSProperties} from 'react';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import {motion} from 'framer-motion';
import {useMotion} from './experience';
import {OriginalGlassPill as Pill,OriginalGlassIconOrb as Orb} from './original-glass';
import {atlasSections,chapterNodes,sourceGroups,type SceneNode} from '../data/home-atlas-story';
import {HomeAtlasDetails,AtlasIcon,SourceGroupIcon} from './home-atlas-details';
import './home-flow.css';
import './neon-glass.css';
import{useSceneFocus,triggerIdentity}from'./scene-focus';
import{ToolLogoRow}from'./tool-identity';
import {studioViewIcons} from './studio-view-detail';
import {semanticIconColors,SemanticIcon} from './semantic-icon';
import {SourceLaneIcon} from './source-lane-icon';
const Scene=dynamic(()=>import('./infinity-flow-scene').then(m=>m.InfinityFlowScene),{ssr:false});
const studioViews=['Plan','Jobs','Workers','Evidence','Toolchains','Connections','Learning','Compute','Diagnostics'];
function StaticProject({nodes,onSelect}:{nodes:SceneNode[];onSelect:(id:string)=>void}){return <div className="atlas-static-project" data-detail-group><img src="/evidence-lane-icon.png" alt="Evidence Lane"/><div>{nodes.filter(n=>n.id!=='project').map(n=><Pill key={n.id} data-detail-action={n.action} leading={<Orb color={n.color} decorative>{n.brand?<img src={'/brands/'+n.brand} alt=""/>:n.lane?<SourceLaneIcon lane={n.lane} decorative/>:<SemanticIcon name={n.icon}/>}</Orb>} onClick={()=>onSelect(n.action)}>{n.label}</Pill>)}</div></div>}
export function HomeFlow(){
 const sceneFocus=useSceneFocus();const {paused,toggle}=useMotion();const[reduced,setReduced]=useState(false);useEffect(()=>{const media=matchMedia('(prefers-reduced-motion: reduce)');const update=()=>setReduced(media.matches);update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[]);const[staticView,setStaticView]=useState(false),[progress,setProgress]=useState(0),[selected,setSelected]=useState<string|null>(null),[source,setSource]=useState(0),[toolView,setToolView]=useState('tools'),[result,setResult]=useState(0),[studioView,setStudioView]=useState('Plan'),[withinStory,setWithinStory]=useState(true);const story=useRef<HTMLDivElement>(null);const detailOpen=useRef(false);detailOpen.current=Boolean(selected);const still=Boolean(reduced)||staticView,active=Math.min(7,Math.floor(progress));const nodes=useMemo(()=>chapterNodes(active,source,toolView,studioView),[active,source,toolView,studioView]);
 useEffect(()=>{let frame=0;const update=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{const root=story.current;if(!root||detailOpen.current)return;let p=0;root.querySelectorAll<HTMLElement>('[data-flow-chapter]').forEach((node,i)=>{const r=node.getBoundingClientRect();if(r.top<=innerHeight*.42)p=i+Math.max(0,Math.min(.999,(innerHeight*.42-r.top)/r.height))});setProgress(Math.min(7.999,p));setWithinStory(root.getBoundingClientRect().bottom>innerHeight*.4)});};update();window.addEventListener('scroll',update,{passive:true});window.addEventListener('resize',update);return()=>{cancelAnimationFrame(frame);window.removeEventListener('scroll',update);window.removeEventListener('resize',update)}},[]);
 const openDetails=useCallback((id:string)=>{sceneFocus.select(id,triggerIdentity(document.activeElement));setSelected(id)},[sceneFocus.select]);
 useEffect(()=>{if(selected)sceneFocus.select(selected)},[selected,sceneFocus.select]);
 const closeDetails=useCallback(()=>setSelected(null),[]);
 const go=(i:number)=>{const target=document.querySelector(`#flow-chapter-${i} .flow-copy`);if(target){const offset=(document.querySelector('.site-header')?.getBoundingClientRect().height??110)+28;window.scrollTo({top:target.getBoundingClientRect().top+scrollY-offset,behavior:still?'instant':'smooth'})}};
 const focus=active===2?sourceGroups[source].id:active===3?'tool':active===4?['output','source-trail','checks'][result]:active===6?'selected':undefined;
 return <main id="main" className="home-flow atlas-home" data-static={still}>
  <div className="flow-story" ref={story}>
   {!still&&<div className="flow-scene-boundary"><Scene showAssembly={withinStory} progress={progress} paused={paused} nodes={nodes} focus={focus} onSelect={openDetails} onUnavailable={()=>setStaticView(true)}/></div>}
   {withinStory&&<nav className="flow-chapters" aria-label="Home story">{atlasSections.map((s,i)=><a key={s.label} href={`#flow-chapter-${i}`} onClick={e=>{e.preventDefault();go(i)}} aria-current={i===active?'step':undefined}><span>{String(i).padStart(2,'0')}</span><i/><b>{s.label}</b></a>)}</nav>}
   {atlasSections.map((s,i)=><section key={s.label} id={`flow-chapter-${i}`} data-flow-chapter className={`flow-section ${i===0?'flow-hero':''} ${s.side==='right'?'flow-right':''}`}>
    <motion.div className="flow-copy" initial={false} whileInView={{opacity:1,y:0}}>
     <p className="flow-kicker">{i===0?'EVIDENCE LANE FOR CODEX':`${String(i).padStart(2,'0')} / ${s.label.toUpperCase()}`}</p>
     {i===0?<h1>{s.heading}<br/><em>{s.accent}</em></h1>:<h2>{s.heading}<br/><em>{s.accent}</em></h2>}
     <p className={i===0?'flow-intro':''}>{s.body}</p>
     {i===1&&<div className="atlas-entry-summary" data-detail-group><Pill leading={<Orb color="#72cafa" decorative><img src="/brands/openai.svg" alt=""/></Orb>} data-detail-action="host:stable" onClick={()=>openDetails('host:stable')}>Codex Desktop Stable</Pill><Pill leading={<Orb color="#c5a1f1" decorative><img src="/brands/openai.svg" alt=""/></Orb>} data-detail-action="host:beta" onClick={()=>openDetails('host:beta')}>Codex Desktop Beta</Pill><p>One persistent local Windows PC. The engine and shared tools support the selected project.</p></div>}
     {i===2&&<><div className="atlas-home-source-groups">{sourceGroups.map((group,j)=><Pill key={group.id} leading={<SourceGroupIcon index={j}/>} active={source===j} aria-pressed={source===j} onClick={()=>setSource(j)}>{group.title}</Pill>)}</div><div className="atlas-inline-result"><strong>{sourceGroups[source].operation}</strong><ToolLogoRow tools={sourceGroups[source].members}/></div></>}
     {i===3&&<><div className="atlas-segment">{(['tools','compute','connections'] as const).map((name,j)=><Pill key={name} leading={<AtlasIcon name={name==='connections'?'connections':'tools'}/>} active={toolView===name} aria-pressed={toolView===name} onClick={()=>setToolView(name)}>{['Tools','Compute','Access'][j]}</Pill>)}</div><div className="atlas-inline-card" data-detail-group><AtlasIcon name={toolView==='connections'?'connections':'tools'} size={50}/><div><strong>{toolView==='tools'?'The operation selects its tools':toolView==='compute'?'Use compatible, measured compute':'Use explicitly scoped connections'}</strong><div>{toolView==='tools'?<ToolLogoRow tools={sourceGroups[source].tools} onSelect={id=>openDetails('tool:'+id)}/>:toolView==='compute'?'CPU · CUDA · ROCm · DirectML':'Configure · scope · use · revoke'}</div></div><Pill leading={<AtlasIcon name={toolView==='compute'?'compute':toolView==='connections'?'connections':'tools'}/>} data-detail-action={toolView} onClick={()=>openDetails(toolView)}>Explore {toolView} ↗</Pill></div></>}
     {i===4&&<><div className="atlas-segment">{(['results','sources','checks'] as const).map((name,j)=><Pill key={name} leading={<AtlasIcon name={name}/>} active={result===j} aria-pressed={result===j} onClick={()=>setResult(j)}>{['Output','Source trail','Checks'][j]}</Pill>)}</div><div className="atlas-inline-result" aria-live="polite"><strong>{['What was produced','What informed it','What was verified'][result]}</strong><p>{['The output stays attached to the work that produced it.','References point back to the selected material and its recorded snapshot.','Read the recorded observations, coverage and limits.'][result]}</p></div></>}
     {i===5&&<div className="atlas-continuity-actions" data-detail-group>{[['Return','resume'],['Steer the Plan','steer-plan'],['Inspect records','records']].map(([label,id])=><Pill key={id} leading={<AtlasIcon name={id==='steer-plan'?'plan':id==='resume'?'resume':'records'}/>} data-detail-action={id} onClick={()=>openDetails(id)}>{label}</Pill>)}</div>}
     {i===6&&<div className="atlas-studio-inline"><header><AtlasIcon name="studio"/><span>Studio</span><small>READ-ONLY PREVIEW</small></header><nav aria-label="Studio scene view">{studioViews.map(view=><button key={view} style={{'--item-color':semanticIconColors[studioViewIcons[view]]} as CSSProperties} aria-pressed={studioView===view} onClick={()=>setStudioView(view)}><AtlasIcon name={studioViewIcons[view]} size={28}/>{view}</button>)}</nav><button className="atlas-studio-selected" data-detail-action={'studio:'+studioView} data-detail-name={studioView} onClick={()=>openDetails('studio:'+studioView)}><span><AtlasIcon name={studioViewIcons[studioView]} size={38}/>{studioView}</span><span>Explore this view ↗</span></button></div>}

     <div className="atlas-section-actions" data-detail-group><Pill leading={<AtlasIcon name={i===1?'project':i===2?'sources':i===3?'work':i===4?'results':i===5?'memory':i===6?'studio':'workflow'}/>} trailing="↗" data-detail-action={s.action} onClick={()=>openDetails(s.action)}>{s.button}</Pill><Link href={s.href} className="flow-page-link">{s.link} →</Link></div>
     {i===0&&<><button className="atlas-story-start" onClick={()=>go(1)}>Follow the project journey <span>↓</span></button><p className="flow-host-note">Codex Desktop on Windows · Stable & Beta</p></>}
    </motion.div>
    {!still&&<div className="atlas-mobile-scene-slot" aria-hidden="true"/>}{still&&<StaticProject nodes={chapterNodes(i,source,toolView,studioView)} onSelect={openDetails}/>}
   </section>)}
  </div>
  <section className="flow-finish"><p className="flow-kicker">EVIDENCE LANE</p><h2>Bring the work together.<br/><em>Keep it moving with context.</em></h2><div className="atlas-section-actions" data-detail-group><Link href="/download" className="flow-link-pill" style={{'--item-color':semanticIconColors.download} as CSSProperties}><AtlasIcon name="download"/>Get Evidence Lane <span>↗</span></Link><Link href="/docs" className="flow-page-link">Read the getting-started guide →</Link></div></section>
  {withinStory&&<div className="flow-controls"><button onClick={toggle} aria-pressed={paused} disabled={reduced}>{paused?'Play motion':'Pause motion'}</button><button disabled={Boolean(reduced)} onClick={()=>setStaticView(!staticView)}>{reduced?'Reduced motion':staticView?'3D view':'Static view'}</button></div>}
  <HomeAtlasDetails nodes={nodes} selected={selected} source={source} onSource={setSource} onClose={closeDetails} onNavigate={openDetails}/>
 </main>;
}
