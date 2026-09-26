import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {RecordDetails} from './RecordDetails';
import type {StudioSubject} from './subjects';

type Inspection={title:string;value:unknown;side:'left'|'right';subject?:StudioSubject};
const Context=createContext({paused:false,toggleMotion:()=>{},inspect:(_title:string,_value:unknown)=>{},inspectSubject:(_subject:StudioSubject)=>{}});
export const useObserver=()=>useContext(Context);

export function ObserverProvider({children}:{children:ReactNode}){
 const[paused,setPaused]=useState(false),[selected,setSelected]=useState<Inspection|null>(null);
 const dialog=useRef<HTMLDialogElement>(null),opener=useRef<HTMLElement|null>(null),closeButton=useRef<HTMLButtonElement>(null);
 useEffect(()=>{const media=matchMedia('(prefers-reduced-motion: reduce)');setPaused(media.matches);const change=()=>setPaused(media.matches);media.addEventListener('change',change);return()=>media.removeEventListener('change',change)},[]);
 const inspect=(title:string,value:unknown)=>{const element=document.activeElement instanceof HTMLElement?document.activeElement:null;if(!selected)opener.current=element;const rect=element?.getBoundingClientRect();setSelected({title,value,side:rect&&rect.left+rect.width/2>innerWidth/2?'left':'right'})};
 const inspectSubject=(subject:StudioSubject)=>{const element=document.activeElement instanceof HTMLElement?document.activeElement:null;if(!selected)opener.current=element;const rect=element?.getBoundingClientRect();setSelected({title:subject.title,value:subject.record,subject,side:rect&&rect.left+rect.width/2>innerWidth/2?'left':'right'})};
 const close=()=>{setSelected(null);dialog.current?.close();requestAnimationFrame(()=>{if(opener.current?.isConnected)opener.current.focus({preventScroll:true})})};
 useEffect(()=>{if(selected&&!dialog.current?.open){dialog.current?.showModal();closeButton.current?.focus()}},[selected]);
 useEffect(()=>{if(!selected)return;const area=dialog.current?.querySelector('.observer-inspector-scroll');area?.scrollTo({top:0})},[selected]);
 return <Context.Provider value={{paused,toggleMotion:()=>setPaused(p=>!p),inspect,inspectSubject}}><div className="observer-theme" data-motion-paused={paused}>{children}<dialog ref={dialog} className="observer-inspector" data-side={selected?.side} data-kind={selected?.subject?.kind} aria-labelledby="observer-inspector-title" onCancel={e=>{e.preventDefault();close()}} onClick={e=>{if(e.target!==dialog.current)return;const r=dialog.current.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close()}}><header><span>RECORDED OBSERVATION</span><button ref={closeButton} onClick={close} aria-label="Close observation details">Close <span aria-hidden="true">×</span></button></header><div className="observer-inspector-scroll"><h2 id="observer-inspector-title">{selected?.title}</h2><p className="observer-inspector-note">Read-only detail captured from the selected record. A later workspace refresh does not re-verify this open detail.</p>{selected&&<RecordDetails key={selected.title} value={selected.value} subject={selected.subject}/>}</div></dialog></div></Context.Provider>;
}
