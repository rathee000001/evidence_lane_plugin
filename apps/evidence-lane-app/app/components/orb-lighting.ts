'use client';
import {useEffect} from 'react';

/** One lighting loop, with a separate incident-light direction for every visible lens. */
export function useOrbLighting(paused:boolean){
 useEffect(()=>{if(paused)return;let frame=0,last=0,x=innerWidth*.35,y=innerHeight*.2,px=x,py=y,scrollEnergy=0,previousScroll=scrollY,dirty=true;
 let orbs:HTMLElement[]=[];const impacts=new WeakMap<HTMLElement,number>();
 const pointer=(event:PointerEvent)=>{x=event.clientX;y=event.clientY};
 const press=(event:PointerEvent)=>{const el=event.target instanceof Element?event.target.closest('button,a,[role=button],.original-glass-orb'):null;el?.querySelectorAll<HTMLElement>('.original-glass-orb').forEach(orb=>impacts.set(orb,1));if(el?.matches('.original-glass-orb'))impacts.set(el as HTMLElement,1)};
 const scroll=()=>{scrollEnergy=Math.min(1,Math.abs(scrollY-previousScroll)/100);previousScroll=scrollY};
 const observer=new MutationObserver(()=>{dirty=true});observer.observe(document.body,{childList:true,subtree:true});
 const tick=(now:number)=>{frame=requestAnimationFrame(tick);if(document.hidden||now-last<32)return;last=now;if(dirty){orbs=Array.from(document.querySelectorAll<HTMLElement>('.original-glass-orb'));dirty=false}px+=(x-px)*.12;py+=(y-py)*.12;scrollEnergy*=.92;
 const transition=Number(document.querySelector<HTMLElement>('.flow-webgl')?.dataset.motionEnergy??0);const t=now*.001,energy=Math.max(scrollEnergy,transition),root=document.documentElement.style;
 root.setProperty('--orb-glint-angle',`${125+px/innerWidth*55+Math.sin(t*.35)*16}deg`);root.setProperty('--orb-shine',String(.7+energy*.2));
 // Read geometry first, then write all styles to avoid interleaved layout work.
 const visible=orbs.map((orb,index)=>({orb,index,r:orb.getBoundingClientRect()})).filter(({r})=>r.width&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth);
 for(const{orb,index,r}of visible){const cx=r.left+r.width/2,cy=r.top+r.height/2;const dx=Math.max(-1,Math.min(1,(px-cx)/(innerWidth*.52))),dy=Math.max(-1,Math.min(1,(py-cy)/(innerHeight*.52)));const phase=index*.71+cx*.002+cy*.003;const hovered=orb.matches(':hover')||Boolean(orb.closest('button:hover,a:hover,button:focus-visible,a:focus-visible'));const impact=(impacts.get(orb)??0)*.78;if(impact>.01)impacts.set(orb,impact);else impacts.delete(orb);const style=orb.style;
 style.setProperty('--orb-light-x',`${38+dx*21+Math.sin(t*.8+phase)*7}%`);style.setProperty('--orb-light-y',`${25+dy*15+Math.cos(t*.6+phase)*5}%`);
 style.setProperty('--orb-glint-angle',`${115+dx*55-dy*20+Math.sin(t*.45+phase)*23}deg`);style.setProperty('--orb-shine',String(.72+Math.sin(t*.7+phase)*.14+energy*.15+(hovered?.22:0)));
 style.setProperty('--orb-shadow-x',`${-dx*2}px`);style.setProperty('--orb-shadow-y',`${2-dy}px`);style.setProperty('--orb-icon-yaw',`${dx*(hovered?16:7)+Math.sin(t*.55+phase)*3}deg`);style.setProperty('--orb-icon-pitch',`${-dy*(hovered?12:5)}deg`);
 style.setProperty('--orb-impact',String(impact));style.setProperty('--orb-wobble',`${hovered?Math.sin(t*10+phase)*1.6:0}deg`);style.setProperty('--orb-hover-scale',String(1+(hovered?.035:0)-impact*.065));
 }
 };
 window.addEventListener('pointermove',pointer,{passive:true});window.addEventListener('pointerdown',press,{passive:true});window.addEventListener('scroll',scroll,{passive:true});frame=requestAnimationFrame(tick);
 return()=>{cancelAnimationFrame(frame);observer.disconnect();window.removeEventListener('pointermove',pointer);window.removeEventListener('pointerdown',press);window.removeEventListener('scroll',scroll);for(const orb of orbs){orb.style.setProperty('--orb-hover-scale','1');orb.style.setProperty('--orb-wobble','0deg')}};
 },[paused]);
}
