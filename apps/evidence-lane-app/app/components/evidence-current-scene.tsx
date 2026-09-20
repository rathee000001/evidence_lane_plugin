'use client';
import {useEffect,useRef,useState} from 'react';
import type * as THREE from 'three';

type Props={progress:number;mode:number;paused:boolean;inspection:number;onInspect:()=>void;onUnavailable?:()=>void};
const iconFiles=['git.svg','github.png','python.svg','nodejs.svg','word.svg','excel.svg','powerpoint.svg'];
export function EvidenceCurrentScene(props:Props){
 const host=useRef<HTMLDivElement>(null);const live=useRef(props);const[ready,setReady]=useState(false);const[failed,setFailed]=useState(false);
 useEffect(()=>{live.current=props},[props]);
 useEffect(()=>{
  const element=host.current;if(!element)return;const el=element;let disposed=false;let cleanup=()=>{};
  async function setup(){try{
   const T=await import('three');const[{GLTFLoader},{RoomEnvironment}]=await Promise.all([import('three/examples/jsm/loaders/GLTFLoader.js'),import('three/examples/jsm/environments/RoomEnvironment.js')]);if(disposed)return;
   const renderer=new T.WebGLRenderer({antialias:true,alpha:true,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(devicePixelRatio,1.4));renderer.setClearColor(0x06131b,0);renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;el.appendChild(renderer.domElement);
   const scene=new T.Scene();const camera=new T.PerspectiveCamera(40,1,.1,90);camera.position.set(0,3,13.5);camera.lookAt(0,0,0);
   const pmrem=new T.PMREMGenerator(renderer);const room=new RoomEnvironment();const env=pmrem.fromScene(room,.04);scene.environment=env.texture;scene.environmentIntensity=.85;room.dispose();pmrem.dispose();
   scene.add(new T.HemisphereLight(0xbceaff,0x0a2532,1));const light=new T.DirectionalLight(0x85e5ff,4);light.position.set(-4,6,5);scene.add(light);const warm=new T.DirectionalLight(0xf9c383,2.6);warm.position.set(5,2,-2);scene.add(warm);
   const world=new T.Group();scene.add(world);let model:THREE.Group|undefined;let frame=0;let last=0;let time=0;let phase=0;let inView=true;let drag=false;let moved=false;let lastX=0;let lastY=0;let yaw=0;let pitch=0;let px=0;let py=0;let frames=0;let lastMode=-1;
   const packets:THREE.Object3D[]=[];const gates:THREE.Object3D[]=[];const morphs:THREE.Mesh[]=[];let result:THREE.Object3D|undefined;const original=new Map<THREE.Object3D,THREE.Vector3>();const textures:THREE.Texture[]=[];const extraMaterials:THREE.Material[]=[];
   let seed=723;const random=()=>{seed=seed*16807%2147483647;return(seed-1)/2147483646};const count=innerWidth<700?2800:6400;const pos=new Float32Array(count*3),target=new Float32Array(count*3),scatter=new Float32Array(count*3),sizes=new Float32Array(count),colors=new Float32Array(count*3);
   for(let i=0;i<count;i++){const a=random()*Math.PI*2;const r=2.5+random()*3.5;pos.set([Math.cos(a)*r,(random()-.5)*2.2,Math.sin(a)*r],i*3);const t=i/count;target.set([(t-.5)*14,.5*Math.sin(t*Math.PI*3)+(random()-.5)*.18,(random()-.5)*1.2],i*3);scatter.set([(random()-.5)*34,(random()-.5)*20,-3-random()*16],i*3);sizes[i]=random()>.965?3+random()*3:.6+random()*1.6;const c=new T.Color(i%7===0?0xffbf70:i%3===0?0xa6d5ed:0x52cae7);colors.set([c.r,c.g,c.b],i*3)}
   const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.BufferAttribute(pos,3));geometry.setAttribute('flow',new T.BufferAttribute(target,3));geometry.setAttribute('distant',new T.BufferAttribute(scatter,3));geometry.setAttribute('size',new T.BufferAttribute(sizes,1));geometry.setAttribute('color',new T.BufferAttribute(colors,3));
   const dustMat=new T.ShaderMaterial({transparent:true,depthWrite:false,blending:T.AdditiveBlending,vertexColors:true,uniforms:{time:{value:0},gather:{value:0},route:{value:0},ratio:{value:Math.min(devicePixelRatio,1.4)},pointer:{value:new T.Vector2()}},vertexShader:`attribute vec3 flow;attribute vec3 distant;attribute float size;uniform float time;uniform float gather;uniform float route;uniform float ratio;uniform vec2 pointer;varying vec3 vColor;varying float seed;void main(){seed=position.x*7.+position.z*3.;vec3 p=mix(distant,position,.48+gather*.40);vec3 road=flow;road.x=mod(flow.x+7.+time*.65,14.)-7.;road.y=.5*sin((road.x+7.)/14.*9.42)+flow.y*.15;p=mix(p,road,route*.9);p.y+=sin(time*.32+seed)*.055;p.x+=sin(time*.14+seed)*.045;float pull=exp(-length(p.xy-pointer)*.55);p.xy+=(pointer-p.xy)*pull*.16;vec4 mv=modelViewMatrix*vec4(p,1.);gl_PointSize=clamp(size*ratio*1.65*(13./-mv.z),.5,9.);vColor=color;gl_Position=projectionMatrix*mv;}`,fragmentShader:`varying vec3 vColor;varying float seed;uniform float time;void main(){vec2 p=gl_PointCoord*2.-1.;float d=dot(p,p);if(d>1.)discard;float glow=exp(-d*5.8);gl_FragColor=vec4(vColor,glow*(.72+.20*sin(time*.35+seed)));}`});scene.add(new T.Points(geometry,dustMat));
   const resize=()=>{renderer.setSize(el.clientWidth,el.clientHeight);camera.aspect=el.clientWidth/el.clientHeight;camera.updateProjectionMatrix()};resize();const ro=new ResizeObserver(resize);ro.observe(el);const io=new IntersectionObserver(es=>{inView=es[0].isIntersecting});io.observe(el);
   const down=(e:PointerEvent)=>{if(e.pointerType!=='mouse')return;drag=true;moved=false;lastX=e.clientX;lastY=e.clientY;el.setPointerCapture(e.pointerId)};
   const move=(e:PointerEvent)=>{const r=el.getBoundingClientRect();px=(e.clientX-r.left)/r.width-.5;py=(e.clientY-r.top)/r.height-.5;if(drag){const dx=e.clientX-lastX,dy=e.clientY-lastY;moved=moved||Math.abs(dx)+Math.abs(dy)>3;yaw+=dx*.003;pitch=Math.max(-.35,Math.min(.35,pitch+dy*.002));lastX=e.clientX;lastY=e.clientY}};
   const up=()=>{if(drag&&!moved)live.current.onInspect();drag=false};const key=(e:KeyboardEvent)=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();live.current.onInspect()}if(e.key.startsWith('Arrow')){e.preventDefault();yaw+=e.key==='ArrowLeft'?-.15:e.key==='ArrowRight'?.15:0;pitch+=e.key==='ArrowUp'?-.08:e.key==='ArrowDown'?.08:0}};const cancel=()=>{drag=false};
   el.addEventListener('pointerdown',down);el.addEventListener('pointermove',move);el.addEventListener('pointerup',up);el.addEventListener('pointercancel',cancel);el.addEventListener('keydown',key);
   const lost=(e:Event)=>{e.preventDefault();setFailed(true);setReady(false);live.current.onUnavailable?.();cancelAnimationFrame(frame)};renderer.domElement.addEventListener('webglcontextlost',lost);
   const disposeModel=(m:THREE.Group)=>m.traverse(o=>{if(o instanceof T.Mesh){o.geometry.dispose();(Array.isArray(o.material)?o.material:[o.material]).forEach(x=>x.dispose())}});
   cleanup=()=>{cancelAnimationFrame(frame);ro.disconnect();io.disconnect();el.removeEventListener('pointerdown',down);el.removeEventListener('pointermove',move);el.removeEventListener('pointerup',up);el.removeEventListener('pointercancel',cancel);el.removeEventListener('keydown',key);renderer.domElement.removeEventListener('webglcontextlost',lost);if(model)disposeModel(model);geometry.dispose();dustMat.dispose();textures.forEach(t=>t.dispose());extraMaterials.forEach(m=>m.dispose());env.dispose();renderer.dispose();renderer.domElement.remove()};
   const gltf=await new GLTFLoader().loadAsync('/home-current/evidence-current.glb');if(disposed){disposeModel(gltf.scene);return}model=gltf.scene;world.add(model);
   model.traverse(o=>{if(o.name.startsWith('Packet_')&&o.type==='Object3D'){packets.push(o);original.set(o,o.position.clone())}if(o.name.startsWith('Stage_')&&o.type==='Object3D'){gates.push(o);original.set(o,o.position.clone())}if(o.name==='Result'){result=o;original.set(o,o.position.clone())}if(o instanceof T.Mesh&&o.morphTargetInfluences)morphs.push(o)});
   const loaded=await Promise.allSettled(iconFiles.map(f=>new T.TextureLoader().loadAsync(`/brands/${f}`)));if(disposed){loaded.forEach(r=>{if(r.status==='fulfilled')r.value.dispose()});return}loaded.forEach(r=>{if(r.status==='fulfilled'){r.value.colorSpace=T.SRGBColorSpace;r.value.flipY=true;textures.push(r.value)}});
   el.dataset.packetCount=String(packets.length);el.dataset.morphCount=String(morphs.length);el.dataset.sceneReady="true";setReady(true);
   const smooth=(a:number,b:number,v:number)=>{const t=Math.max(0,Math.min(1,(v-a)/(b-a)));return t*t*(3-2*t)};
   function animate(now:number){if(disposed)return;frame=requestAnimationFrame(animate);const dt=Math.min(.05,(now-last)/1000);last=now;if(!inView||document.hidden||++frames%2)return;const settings=live.current;const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;if(!settings.paused&&!reduce)time+=dt*2;const targetPhase=settings.progress*5;phase+=(targetPhase-phase)*(settings.paused||reduce?1:.085);const flow=smooth(.9,2.15,phase)*(1-smooth(4.25,5,phase));const gather=smooth(0,.9,phase)*(1-smooth(4.4,5,phase));
    dustMat.uniforms.pointer.value.set(px*11,-py*7);dustMat.uniforms.time.value=time;dustMat.uniforms.gather.value=gather;dustMat.uniforms.route.value=flow;
    morphs.forEach(m=>{if(m.morphTargetInfluences)m.morphTargetInfluences[0]=flow});
    const mobile=camera.aspect<.85;const shift=phase<1.1?0:phase<2.1?2.4:phase<3.1?-2.0:phase<4.1?2.2:phase<4.75?-1.8:0;world.position.x+=(shift-world.position.x)*.04;world.position.y=mobile?-.8:phase<.6?-1.15:-.5;world.rotation.y+=(yaw+px*.08+phase*.13-world.rotation.y)*.045;world.rotation.x+=(pitch+py*.025-world.rotation.x)*.045;
    world.scale.setScalar(mobile?.69:1);camera.position.set(Math.sin(phase*.6)*.65,3.3-flow*1.3, mobile?16:13.8-flow*1.1);camera.lookAt(0,0,0);
    packets.forEach((o,i)=>{const p=original.get(o)!;const a=time*(.035+i*.0007)+i*.31;const floating=p.clone().applyAxisAngle(new T.Vector3(0,1,0),time*.045).add(new T.Vector3(Math.sin(a)*.14,Math.cos(a*.8)*.1,Math.sin(a*.5)*.14));const target=new T.Vector3((i%10-4.5)*.75,((i/10)|0)*.5-.3,.5);o.position.copy(floating.lerp(target,flow*.95));o.rotation.x=.45+Math.sin(a)*.1;o.rotation.y+=settings.paused||reduce?0:.0006;o.scale.setScalar(.85+gather*.12);o.visible=phase<4.6||i%3===0;});
    gates.forEach((o,i)=>{o.visible=flow>.08;o.position.set((i-2)*2.05,0,-.9);o.scale.setScalar(flow*.86);});
    if(result){const show=smooth(3.3,4.0,phase)*(1-smooth(4.55,5,phase));result.visible=show>.01&&settings.inspection===0;result.position.set(-.4,show*.8,1.8);result.rotation.set(.8,.1,-.2);result.scale.setScalar(show*1.35)}
    if(settings.mode!==lastMode&&textures.length===iconFiles.length){
     lastMode=settings.mode;const indices=settings.mode===0?[0,1,2,3]:settings.mode===1?[2,4,5]:[2,5,6];
     packets.forEach((o,i)=>{
      const texture=textures[indices[i%indices.length]];
      let face=o.getObjectByName('BrandFace') as THREE.Mesh|undefined;
      if(!face){
       face=new T.Mesh(new T.PlaneGeometry(.42,.56),new T.MeshBasicMaterial({map:texture,transparent:true,side:T.DoubleSide}));face.name='BrandFace';face.rotation.x=-Math.PI/2;face.position.set(0,.055,0);o.add(face);
       o.traverse(child=>{if(child.name.startsWith('Packet_trace')||child.name.startsWith('Packet_marker'))child.visible=false;});
      }else{const mat=face.material as THREE.MeshBasicMaterial;mat.map=texture;mat.needsUpdate=true;}
     });
    }
    light.color.set(phase>3.5&&settings.inspection===2?0xffc782:0x85e5ff);renderer.render(scene,camera);
   }frame=requestAnimationFrame(animate);
  }catch{setFailed(true);setReady(false);live.current.onUnavailable?.();cleanup()}}
  void setup();return()=>{disposed=true;cleanup()};
 },[]);
 return <div className="current-scene"><img className={ready&&!failed?'scene-poster is-hidden':'scene-poster'} src={props.progress>.35&&props.progress<.86?'/home-current/flow.png':'/home-current/gather.png'} alt=""/><div className="scene-renderer" ref={host} tabIndex={failed?-1:0} role={failed?"img":"group"} aria-label={failed?"Static scene. Explore with the chapter controls.":"Interactive evidence current. Drag or use arrow keys to rotate. Press Enter for the current chapter details."}/><span className="scene-accessibility">{failed?"Static artwork · 3D unavailable":"Interactive illustration"}</span></div>;
}
