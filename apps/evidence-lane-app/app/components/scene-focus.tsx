"use client";
import {createContext,useCallback,useContext,useState,type ReactNode} from 'react';
export type DetailIdentity={title?:string;icon?:string;image?:string;toolId?:string;color?:string};
export type DetailControl=DetailIdentity&{action:string};
export type DetailCompanion={kind:'scene'|'controls';items:DetailControl[]};
export function triggerIdentity(element:Element|null):DetailIdentity{if(!element)return{};const title=element.getAttribute('data-detail-name')??element.getAttribute('data-scene-label')??element.querySelector('.original-pill__label')?.textContent??element.querySelector('strong')?.textContent??element.getAttribute('aria-label')?.replace(/^Explore /,'')??element.textContent;const image=element.querySelector('img')?.getAttribute('src')??undefined;return{color:element.querySelector<HTMLElement>('[data-glass-orb-host]')?.style.getPropertyValue('--glass-orb-color'),title:title?.replace(/[↗→↓]/g,'').replace(/\s+/g,' ').trim(),icon:element.querySelector('[data-semantic-icon]')?.getAttribute('data-semantic-icon')??undefined,image:element.querySelector('[data-semantic-icon=memory]')?undefined:image?.startsWith('/')?image:undefined,toolId:element.getAttribute('data-tool-id')??undefined}}
export type SceneFocusState={active:boolean;dock:{x:number;y:number};panel:{x:number;y:number};rotation:{x:number;y:number;z:number};layout:'left'|'right'|'above'|'below';origin:{x:number;y:number};frontal:boolean;keepHero:boolean;sourceAction?:string;identity?:DetailIdentity;companion?:DetailCompanion};
const initial:SceneFocusState={active:false,dock:{x:.18,y:.3},panel:{x:.73,y:.5},rotation:{x:0,y:0,z:0},layout:'left',origin:{x:.5,y:.5},frontal:true,keepHero:false};
const Context=createContext({state:initial,open:(_rect?:DOMRect,_hero?:DOMRect,_retarget?:boolean)=>{},select:(_action:string,_identity?:DetailIdentity)=>{},close:()=>{}});
const clamp=(v:number,a:number,b:number)=>Math.max(a,Math.min(b,v));
export function SceneFocusProvider({children}:{children:ReactNode}){const[state,setState]=useState(initial);const open=useCallback((rect?:DOMRect,hero?:DOMRect,retarget=false)=>{
 const trigger=document.activeElement;const fromScene=Boolean(trigger?.closest('.flow-object'));
 const group=!fromScene?trigger?.closest('[data-detail-group]'):null;
 const controls=group?Array.from(group.querySelectorAll<HTMLElement>('[data-detail-action]')):[];
 const controlGroup=!fromScene&&Boolean(rect);
 if(controlGroup){hero=undefined;rect=group?.getBoundingClientRect()??rect;}
 const x=rect?clamp((rect.left+rect.width/2)/innerWidth,.05,.95):.5,y=rect?clamp((rect.top+rect.height/2)/innerHeight,.05,.95):.5;
 const hx=hero?(hero.left+hero.width/2)/innerWidth:.5,hy=hero?(hero.top+hero.height/2)/innerHeight:.5;
 const canRetarget=retarget&&!!hero&&(Math.abs(x-hx)>.035||Math.abs(y-hy)>.035);const keepHero=!canRetarget&&!!hero&&(hx<.35||hx>.65||hy<.25||hy>.75);let layout:SceneFocusState['layout'],dock:SceneFocusState['dock'],panel:SceneFocusState['panel'];
 if(controlGroup){layout=x<.5?'left':'right';dock={x:x<.5?.18:.82,y:clamp(y,.3,.7)};panel={x:x<.5?.73:.27,y:.5}}
 else if(canRetarget){if(x<.5){layout='left';dock={x:.20,y:y<.5?.24:.76};panel={x:.73,y:.5}}else{layout='right';dock={x:.80,y:y<.5?.24:.76};panel={x:.27,y:.5}}}
 else if(keepHero){dock={x:clamp(hx,.12,.88),y:clamp(hy,.12,.88)};if(hx<.35){layout='left';panel={x:.73,y:.5}}else if(hx>.65){layout='right';panel={x:.27,y:.5}}else if(hy<.25){layout='above';panel={x:.5,y:.64}}else{layout='below';panel={x:.5,y:.36}}}
 else if(x<.4){layout='left';dock={x:.17,y:y<.5?.27:.73};panel={x:.73,y:.5}}
 else if(x>.6){layout='right';dock={x:.83,y:y<.5?.27:.73};panel={x:.27,y:.5}}
 else if(y<.38){layout='above';dock={x:.5,y:.14};panel={x:.5,y:.64}}
 else if(y>.62){layout='below';dock={x:.5,y:.86};panel={x:.5,y:.36}}
 else{layout='left';dock={x:.17,y:.5};panel={x:.73,y:.5}}
 dock={x:layout==='left'?.18:layout==='right'?.82:clamp(dock.x,.18,.82),y:layout==='above'?.17:layout==='below'?.83:clamp(dock.y,.27,.67)};if(layout==='above')panel={x:.5,y:.66};if(layout==='below')panel={x:.5,y:.33};
 const frontal=layout!=='left'&&layout!=='right'&&Math.abs(x-.5)<.13&&Math.abs(y-.5)<.14;
 setState(previous=>({...previous,active:true,companion:{kind:controlGroup?'controls':'scene',items:controlGroup?(controls.length?controls.map(el=>({...triggerIdentity(el),action:el.dataset.detailAction!})):[{...previous.identity,action:previous.sourceAction??''}]):[]},identity:previous.sourceAction?previous.identity:triggerIdentity(trigger),dock,panel,layout,origin:{x,y},frontal,keepHero,rotation:frontal?{x:0,y:0,z:0}:{x:0,y:layout==='left'?-5:layout==='right'?5:0,z:0}}))
 },[]);const select=useCallback((sourceAction:string,identity?:DetailIdentity)=>setState(s=>({...s,sourceAction,identity:identity??s.identity})),[]);const close=useCallback(()=>setState(s=>({...s,active:false,sourceAction:undefined,identity:undefined})),[]);return <Context.Provider value={{state,open,select,close}}>{children}</Context.Provider>}
export function useSceneFocus(){return useContext(Context)}
