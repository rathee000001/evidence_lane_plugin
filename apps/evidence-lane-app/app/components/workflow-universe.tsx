'use client';
import {useEffect,useRef} from 'react';
import {useMotion} from './experience';
import {createUniverseParticles} from './universe-particles';

/** Procedural atmosphere, lit planets and foreground rock belt; no background bitmap. */
export function WorkflowUniverse(){
 const host=useRef<HTMLDivElement>(null),motion=useRef(false);const{paused}=useMotion();motion.current=paused;
 useEffect(()=>{let dispose=()=>{},cancelled=false;
 import('three').then(T=>{if(cancelled||!host.current)return;
 let renderer:InstanceType<typeof T.WebGLRenderer>;
 try{renderer=new T.WebGLRenderer({antialias:false,alpha:false,powerPreference:'low-power'})}catch{return}
 renderer.setPixelRatio(1);host.current.append(renderer.domElement);
 const uniforms={time:{value:0},aspect:{value:1},pointer:{value:new T.Vector2()},scroll:{value:0}};
 const geometry=new T.PlaneGeometry(2,2),material=new T.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,
 vertexShader:'varying vec2 vUv;void main(){vUv=position.xy*.5+.5;gl_Position=vec4(position,1.);}',
 fragmentShader:`varying vec2 vUv;uniform float time,aspect,scroll;uniform vec2 pointer;
 float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
 float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1)),f.x),f.y);}
 float fbm(vec2 p){float s=0.,a=.5;for(int j=0;j<5;j++){s+=a*noise(p);p=mat2(.8,-.6,.6,.8)*p*2.08+13.7;a*=.5;}return s;}
 void main(){vec2 p=vec2(vUv.x*aspect,vUv.y);vec2 q=p+pointer*.012;float t=time*.008;float n=fbm(q*2.5+vec2(t,scroll*.03));float warp=fbm(q*3.1+n*2.);float cloud=fbm(q*4.+warp*2.);float ribbon=exp(-pow((vUv.y-.2-.20*sin(vUv.x*6.+.4))*3.2,2.));
 vec3 c=vec3(.002,.004,.012);float band=exp(-pow((vUv.y-.22-.45*vUv.x-.11*sin(q.x*5.))*4.,2.));float turbulent=fbm(q*7.+vec2(warp*3.,cloud*2.));float ridges=pow(1.-abs(turbulent-.53)*2.,7.);float detail=fbm(q*25.+turbulent*2.);c+=mix(vec3(.035,.06,.15),vec3(.34,.17,.53),cloud)*band*(.18+ridges*.65)*(.35+detail);c+=vec3(.06,.24,.39)*ribbon*pow(warp,3.)*1.6;c+=vec3(.31,.12,.42)*pow(detail,5.)*band;
 for(int i=0;i<100;i++){float k=float(i);vec2 s=vec2(hash(vec2(k,2.))*aspect,hash(vec2(k,8.)));float d=length(q-s);float radius=.0005+hash(vec2(k,4.))*.0014;vec3 tint=mix(vec3(.4,.68,1.),vec3(1.,.64,.3),step(.79,hash(vec2(k,7.))));c+=tint*exp(-d*d/(radius*radius))*(.35+.15*sin(time*.6+k));if(i<24){float r=.004+hash(vec2(k,3.))*.01;c+=tint*exp(-d*d/(r*r*3.))*.07;}}
 for(int i=0;i<6;i++){float k=float(i);vec2 centre;float r;if(i==0){centre=vec2(-.035,.86);r=.16;}else if(i==1){centre=vec2(.89,.80);r=.065;}else if(i==2){centre=vec2(.93,.35);r=.095;}else if(i==3){centre=vec2(.17,.08);r=.07;}else if(i==4){centre=vec2(.45,.79);r=.026;}else{centre=vec2(.72,-.06);r=.15;}vec2 delta=(vUv-centre-pointer*.002*(k+1.))*vec2(aspect,1.);float d=length(delta)/r;float rim=exp(-abs(d-1.)*70.);c+=vec3(.11,.19,.45)*rim*.42;if(d<1.){vec3 normal=vec3(delta/r,sqrt(1.-d*d));float light=max(0.,dot(normal,normalize(vec3(-.7,.8,.45))));float terrain=fbm(normal.xy*6.+k*21.);vec3 planet=mix(vec3(.008,.012,.026),vec3(.13,.12,.23),terrain)*(.14+light*1.3);planet+=vec3(.25,.15,.12)*pow(terrain,6.)*light;c=mix(c,planet,smoothstep(1.,.985,d));}}
 for(int i=0;i<12;i++){float k=float(i);vec2 centre=vec2(hash(vec2(k,47.)),.07+hash(vec2(k,13.))*.17);float r=.003+hash(vec2(k,18.))*.009;vec2 delta=(vUv-centre)*vec2(aspect,1.);float d=length(delta);float crag=1.+.12*sin(atan(delta.y,delta.x)*7.+k)+.07*sin(atan(delta.y,delta.x)*13.);if(d<r*crag){vec3 normal=vec3(delta/r,sqrt(max(0.,1.-pow(d/r,2.))));float lit=max(0.,dot(normal,normalize(vec3(-.5,.8,.3))));float grain=noise(delta/r*14.+k*2.);c=mix(vec3(.009,.012,.025),vec3(.23,.20,.31),grain)*(.15+lit*.8);}}
 c*=.72+.28*smoothstep(0.,.5,vUv.x);gl_FragColor=vec4(c,1.);}`});
 const scene=new T.Scene();scene.add(new T.Mesh(geometry,material));const camera=new T.PerspectiveCamera(38,1,.1,120);camera.position.z=16;const particles=createUniverseParticles(T,scene,camera,host.current);let frame=0,last=0,elapsed=0;
 const resize=()=>{renderer.setSize(Math.round(innerWidth*.65),Math.round(innerHeight*.65),false);uniforms.aspect.value=innerWidth/innerHeight;camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix()};resize();
 const pointer=(e:PointerEvent)=>{if(!motion.current)uniforms.pointer.value.set(e.clientX/innerWidth-.5,.5-e.clientY/innerHeight)};
 const tick=(now:number)=>{frame=requestAnimationFrame(tick);if(document.hidden){last=now;return}if(now-last<32)return;const delta=Math.min(.05,(now-last)/1000);last=now;if(!motion.current)elapsed+=delta;uniforms.time.value=elapsed;uniforms.scroll.value=window.scrollY/innerHeight;particles.update(delta,motion.current);renderer.render(scene,camera)};
 frame=requestAnimationFrame(tick);window.addEventListener('resize',resize);window.addEventListener('pointermove',pointer,{passive:true});
 dispose=()=>{cancelAnimationFrame(frame);window.removeEventListener('resize',resize);window.removeEventListener('pointermove',pointer);particles.dispose();geometry.dispose();material.dispose();renderer.dispose();renderer.domElement.remove()};
 });return()=>{cancelled=true;dispose()};
 },[]);
 return <div ref={host} className="workflow-universe" data-universe="workflow-violet-planets" aria-hidden="true"/>;
}
