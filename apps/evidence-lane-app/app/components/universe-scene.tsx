"use client";
import { useEffect, useRef } from "react";
import * as THREE from "three";

// Procedural layers only: volumetric-looking nebula noise and independently positioned stars.
export default function UniverseScene({ paused }: {active:number;paused:boolean;replay:number}) {
 const hostRef=useRef<HTMLDivElement>(null),live=useRef(paused);
 useEffect(()=>{live.current=paused;},[paused]);
 useEffect(()=>{
  const host=hostRef.current;if(!host)return;let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({antialias:false,alpha:false,powerPreference:"low-power"});}catch{return;}
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setClearColor(0x03050a);host.appendChild(renderer.domElement);
  const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);camera.position.z=2;
  const skyGeo=new THREE.PlaneGeometry(2,2),skyMat=new THREE.ShaderMaterial({uniforms:{time:{value:0},aspect:{value:innerWidth/innerHeight},cursor:{value:new THREE.Vector2()},scroll:{value:0}},vertexShader:`varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}`,fragmentShader:`varying vec2 vUv;uniform float time;uniform float aspect;uniform vec2 cursor;uniform float scroll;
   float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}float fbm(vec2 p){float a=.5,v=0.;mat2 m=mat2(.8,-.6,.6,.8);for(int i=0;i<5;i++){v+=a*noise(p);p=m*p*2.03+4.7;a*=.5;}return v;}
   void main(){vec2 p=(vUv-.5)*vec2(aspect,1.)*2.;p+=cursor*.04+vec2(scroll*.012,scroll*.007);float band=p.y+p.x*.27-.10;float density=exp(-band*band*4.);vec2 q=p*2.3+vec2(time*.002,0);float n=fbm(q+fbm(q*1.8));float dust=fbm(q*4.+8.);float mist=pow(n,2.4)*density;vec3 color=vec3(.004,.007,.014)+vec3(.075,.11,.17)*mist*1.9;color+=vec3(.064,.042,.065)*pow(fbm(q*1.4+22.),3.)*density;color*=1.-.55*pow(dust,1.5)*density;gl_FragColor=vec4(color,1.);}`,depthWrite:false,depthTest:false});
  const sky=new THREE.Mesh(skyGeo,skyMat);sky.renderOrder=-1;scene.add(sky);
  let seed=619;const random=()=>{seed=seed*16807%2147483647;return(seed-1)/2147483647;};
  const count=innerWidth<700?1000:2300,pos=new Float32Array(count*3),sizes=new Float32Array(count),phase=new Float32Array(count);
  for(let i=0;i<count;i++){pos.set([(random()-.5)*2.2,(random()-.5)*2.2,random()],i*3);sizes[i]=(random()>.975?3.2+random()*4:.7+Math.pow(random(),3)*2.2);phase[i]=random()*6.28;}
  const starGeo=new THREE.BufferGeometry();starGeo.setAttribute('position',new THREE.BufferAttribute(pos,3));starGeo.setAttribute('size',new THREE.BufferAttribute(sizes,1));starGeo.setAttribute('phase',new THREE.BufferAttribute(phase,1));
  const starMat=new THREE.ShaderMaterial({uniforms:{time:{value:0},cursor:{value:new THREE.Vector2()},scroll:{value:0},ratio:{value:Math.min(devicePixelRatio,1.5)}},vertexShader:`attribute float size;attribute float phase;varying float vPhase;uniform vec2 cursor;uniform float scroll;uniform float ratio;void main(){vPhase=phase;vec2 p=position.xy+cursor*(.008+position.z*.012);p.y+=sin(scroll*.03+phase)*.004;gl_Position=vec4(p,0.,1.);gl_PointSize=size*ratio;}`,fragmentShader:`varying float vPhase;uniform float time;void main(){vec2 p=gl_PointCoord*2.-1.;float d=dot(p,p);if(d>1.)discard;float a=exp(-d*5.)*(.55+.19*sin(time*.4+vPhase));vec3 col=mix(vec3(.7,.83,1.),vec3(1.,.85,.69),step(5.3,vPhase));gl_FragColor=vec4(col,a);}`,transparent:true,depthTest:false,depthWrite:false,blending:THREE.AdditiveBlending});scene.add(new THREE.Points(starGeo,starMat));
  const reduce=matchMedia('(prefers-reduced-motion: reduce)');let frame=0,time=0,last=0,mx=0,my=0,scroll=0,cx=0,cy=0;
  const pointer=(e:PointerEvent)=>{if(e.pointerType==='mouse'){mx=e.clientX/innerWidth-.5;my=.5-e.clientY/innerHeight;}};const onScroll=()=>{scroll=window.scrollY/Math.max(innerHeight,1);};
  const resize=()=>{renderer.setSize(innerWidth,innerHeight);skyMat.uniforms.aspect.value=innerWidth/innerHeight;};resize();window.addEventListener('resize',resize);window.addEventListener('pointermove',pointer,{passive:true});window.addEventListener('scroll',onScroll,{passive:true});
  const render=(now:number)=>{frame=requestAnimationFrame(render);const dt=Math.min((now-last)/1000,.05);last=now;if(document.hidden||renderer.getContext().isContextLost())return;if(!live.current&&!reduce.matches){time+=dt;cx+=(mx-cx)*.028;cy+=(my-cy)*.028;skyMat.uniforms.cursor.value.set(cx,cy);starMat.uniforms.cursor.value.set(cx,cy);skyMat.uniforms.time.value=time;starMat.uniforms.time.value=time;skyMat.uniforms.scroll.value=scroll;starMat.uniforms.scroll.value=scroll;}renderer.render(scene,camera);};frame=requestAnimationFrame(render);
  return()=>{cancelAnimationFrame(frame);window.removeEventListener('resize',resize);window.removeEventListener('pointermove',pointer);window.removeEventListener('scroll',onScroll);skyGeo.dispose();skyMat.dispose();starGeo.dispose();starMat.dispose();renderer.dispose();renderer.domElement.remove();};
 },[]);
 return <div ref={hostRef} aria-hidden="true" style={{width:'100%',height:'100%'}}/>;
}
