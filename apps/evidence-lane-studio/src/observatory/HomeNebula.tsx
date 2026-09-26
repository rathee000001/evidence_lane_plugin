import {useEffect,useRef} from 'react';
import {useObserver} from './ObserverContext';
import {studioWorld} from './worlds';
import {createProceduralGalaxy} from './website/procedural-galaxy';
import {createInteractiveStarfield} from './website/interactive-starfield';

/** The website Home nebula and starfield, unchanged; this adapter only contains and schedules them for Studio. */
export function HomeNebula({view}:{view:string}){
  const mount=useRef<HTMLDivElement>(null),{paused}=useObserver();
  const live=useRef({view,paused});live.current={view,paused};
  useEffect(()=>{
    const host=mount.current;if(!host)return;
    let gone=false,dispose=()=>{};
    void import('three').then(T=>{
      if(gone)return;
      let renderer:InstanceType<typeof T.WebGLRenderer>;
      try{renderer=new T.WebGLRenderer({alpha:true,antialias:false,powerPreference:'low-power'})}catch{host.dataset.fallback='true';return}
      renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;renderer.setClearColor(0x000000,0);host.append(renderer.domElement);
      const scene=new T.Scene(),camera=new T.PerspectiveCamera(38,1,.1,140);camera.position.set(0,0,15);
      const galaxy=createProceduralGalaxy(T),stars=createInteractiveStarfield(T,host.clientWidth<1000?1200:3600,150);scene.add(galaxy.display,stars.group);
      let frame=0,last=0,time=0,phase=studioWorld(live.current.view).id*.45,target=phase,mix=1,previousView=live.current.view,mx=0,my=0,dirty=true,previousPaused=false;
      const resize=()=>{const w=Math.max(1,host.clientWidth),h=Math.max(1,host.clientHeight);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();galaxy.resize(w,h);dirty=true};
      const size=new ResizeObserver(resize);size.observe(host);resize();
      const pointer=(e:PointerEvent)=>{if(live.current.paused)return;const r=host.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom){stars.leave();return}mx=(e.clientX-r.left)/r.width-.5;my=(e.clientY-r.top)/r.height-.5;stars.pointer(mx*2,-my*2)};
      const identity=new T.Matrix4(),point=new T.Vector3();
      const tick=(now:number)=>{
        frame=requestAnimationFrame(tick);if(document.hidden){last=now;return}if(now-last<33)return;
        const dt=Math.min(.05,(now-last)/1000);last=now;const state=live.current;
        if(previousView!==state.view){previousView=state.view;target=studioWorld(state.view).id*.45;mix=0;dirty=true}
        if(state.paused&&previousPaused&&!dirty)return;previousPaused=state.paused;
        if(!state.paused){time+=dt;phase+=(target-phase)*.06;mix=Math.min(1,mix+dt/ .75)}else{phase=target;mix=1}
        const amount=Math.sin(mix*Math.PI)**2*.45;
        const r=host.getBoundingClientRect(),halfH=Math.tan(camera.fov*Math.PI/360)*camera.position.z;
        const subjects=amount>.001?Array.from(host.parentElement!.querySelectorAll<HTMLElement>('.observer-scene-node:not([hidden])')).slice(0,8).map(e=>{const b=e.getBoundingClientRect();return new T.Vector3(((b.left+b.width/2-r.left)/r.width*2-1)*halfH*camera.aspect,(1-(b.top+b.height/2-r.top)/r.height*2)*halfH,0)}):[];
        stars.update(dt,camera,phase,state.paused,subjects.length?{amount,matrix:identity,point:(t)=>{const n=t*subjects.length,i=Math.floor(n)%subjects.length;return point.copy(subjects[i]).lerp(subjects[(i+1)%subjects.length],n-Math.floor(n))}}:undefined);
        galaxy.uniforms.uTime.value=time;galaxy.uniforms.uProgress.value=phase;galaxy.uniforms.uPointer.value.set(state.paused?0:mx,state.paused?0:my);galaxy.render(renderer);renderer.render(scene,camera);dirty=false;
        host.dataset.nebula='website-home';host.dataset.world=state.view;host.dataset.particleTransfer=amount.toFixed(3);
      };
      const lost=(e:Event)=>{e.preventDefault();cancelAnimationFrame(frame);host.dataset.fallback='true'};
      renderer.domElement.addEventListener('webglcontextlost',lost);window.addEventListener('pointermove',pointer,{passive:true});window.addEventListener('blur',stars.leave);frame=requestAnimationFrame(tick);
      dispose=()=>{cancelAnimationFrame(frame);size.disconnect();window.removeEventListener('pointermove',pointer);window.removeEventListener('blur',stars.leave);renderer.domElement.removeEventListener('webglcontextlost',lost);galaxy.dispose();stars.dispose();renderer.dispose();renderer.domElement.remove()};
    }).catch(()=>{if(!gone)host.dataset.fallback='true'});
    return()=>{gone=true;dispose()};
  },[]);
  return <div className="observer-universe" ref={mount} aria-hidden="true"/>;
}
