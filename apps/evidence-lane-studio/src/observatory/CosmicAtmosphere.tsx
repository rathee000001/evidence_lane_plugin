import {useEffect} from 'react';
import {useObserver} from './ObserverContext';
export {HomeNebula as CosmicAtmosphere} from './HomeNebula';

export function useGlassResponse(paused:boolean){
 useEffect(()=>{const root=document.querySelector('.observer-theme');if(!root||paused)return;let elements:HTMLElement[]=[],dirty=true,mx=innerWidth*.25,my=innerHeight*.2,frame=0,last=0,time=0;const observer=new MutationObserver(()=>{dirty=true});observer.observe(root,{childList:true,subtree:true});const move=(e:PointerEvent)=>{mx=e.clientX;my=e.clientY};window.addEventListener('pointermove',move,{passive:true});const tick=(now:number)=>{frame=requestAnimationFrame(tick);if(document.hidden||now-last<40)return;const dt=Math.min(.05,(now-last)/1000);last=now;if(!paused)time+=dt;if(dirty){elements=Array.from(root.querySelectorAll<HTMLElement>('.glass-icon-orb'));dirty=false}const rects=elements.map(e=>e.getBoundingClientRect());for(let i=0;i<elements.length;i++){const e=elements[i],r=rects[i];if(!r.width||r.bottom<0||r.top>innerHeight)continue;const x=paused?.32:Math.max(.18,Math.min(.82,.5+(mx-r.x-r.width/2)/Math.max(innerWidth,1)*.3+Math.sin(time*.5+i)*.08)),y=paused?.25:Math.max(.15,Math.min(.75,.4+(my-r.y-r.height/2)/Math.max(innerHeight,1)*.25+Math.cos(time*.4+i)*.06));e.style.setProperty('--observer-glare-x',x*100+'%');e.style.setProperty('--observer-glare-y',y*100+'%');e.style.setProperty('--observer-glint',(x*120-60)+'deg');e.style.setProperty('--observer-tilt',((x-.5)*9)+'deg')}};frame=requestAnimationFrame(tick);return()=>{cancelAnimationFrame(frame);observer.disconnect();window.removeEventListener('pointermove',move)};
 },[paused]);
}

export function MotionControl(){const{paused,toggleMotion}=useObserver();useGlassResponse(paused);return <button className="observer-motion" onClick={toggleMotion} aria-pressed={paused}>{paused?'Play visual motion':'Pause visual motion'}</button>}
