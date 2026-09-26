import type * as Three from 'three';

/** Home's authored three-strip cross-section and runtime material/motion, adapted to docked open routes.
 * Sources: scripts/create-optical-infinity.py point()/mesh loop; infinity-flow-scene.tsx OpticalRibbon material and beam update.
 * Open routes retain an open cross-section at their docking points. Width follows route length;
 * displacement settles at the endpoints without pinching the glass into a leaf shape.
 */
export function createHomeOpticalRoute(T:typeof Three,parent:Three.Object3D,segments=96,scale=1){
 const across=5,centers=new Float32Array((segments+1)*3),arc=new Float32Array(segments+1),point=new T.Vector3();
 const materials=[0x8bd5fa,0xb7a1ed,0xf0d4ab].map(color=>new T.MeshPhysicalMaterial({color,metalness:.08,roughness:.055,transparent:true,opacity:.48,transmission:.6,thickness:.16,ior:1.42,clearcoat:1,clearcoatRoughness:.04,side:T.DoubleSide,depthWrite:false,envMapIntensity:1.3,iridescence:.65,iridescenceIOR:1.3}));
 const strips=materials.map(material=>{
  const positions=new Float32Array((segments+1)*(across+1)*3),normals=new Float32Array(positions.length),indices:number[]=[];
  for(let i=0;i<segments;i++)for(let k=0;k<across;k++){const a=i*(across+1)+k;indices.push(a,a+1,a+across+2,a,a+across+2,a+across+1)}
  const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.BufferAttribute(positions,3));geometry.setAttribute('normal',new T.BufferAttribute(normals,3));geometry.setIndex(indices);
  const mesh=new T.Mesh(geometry,material);mesh.frustumCulled=false;parent.add(mesh);return{positions,normals,geometry,mesh};
 });
 const beams=Array.from({length:7},(_,j)=>{
  const positions=new Float32Array((segments+1)*3),geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.BufferAttribute(positions,3));
  const material=new T.LineBasicMaterial({color:j%3===0?0xffdfac:j%2?0x89dfff:0xb8aaff,transparent:true,opacity:.45,blending:T.AdditiveBlending,depthWrite:false});
  const line=new T.Line(geometry,material);line.frustumCulled=false;parent.add(line);return{positions,geometry,material,line};
 });
 let clock=0,routeScale=scale,sampler:(t:number,target:Three.Vector3)=>void=(_t,target)=>target.set(0,0,0);
 const pointAt=(t:number,target:Three.Vector3)=>{sampler(t,target);const envelope=Math.sin(Math.PI*t)**2;target.y+=Math.sin(target.x*1.9+clock*.9)*.055*envelope*routeScale;target.z+=Math.cos(target.x*1.4-clock*.7)*.07*envelope*routeScale;return target};
 return{
  pointAt,
  update(time:number,sample:(t:number,target:Three.Vector3)=>void,alpha=1){
   clock=time;sampler=sample;
   strips.forEach((strip,j)=>{strip.mesh.visible=alpha>.005;materials[j].opacity=.48*alpha});beams.forEach((beam,j)=>{beam.line.visible=alpha>.005;beam.material.opacity=(.45+Math.sin(time*.8+j)*.18)*alpha});
   if(alpha<=.005)return;
   let routeLength=0;const previous=new T.Vector3();
   for(let i=0;i<=16;i++){sample(i/16,point);if(i)routeLength+=point.distanceTo(previous);previous.copy(point)}
   routeScale=Math.min(.78,Math.max(.28,routeLength*.13),routeLength*.55)*scale;
   for(let i=0;i<=segments;i++){pointAt(i/segments,point);centers.set([point.x,point.y,point.z],i*3);arc[i]=i?arc[i-1]+Math.hypot(centers[i*3]-centers[(i-1)*3],centers[i*3+1]-centers[(i-1)*3+1],centers[i*3+2]-centers[(i-1)*3+2]):0}
   for(let i=0;i<=segments;i++){
    const t=i/segments,a=arc[i]/23.300899478*Math.PI*2,envelope=.90+.10*Math.sin(Math.PI*t)**2,prev=Math.max(0,i-1)*3,next=Math.min(segments,i+1)*3;
    const dx=centers[next]-centers[prev],dy=centers[next+1]-centers[prev+1],dz=centers[next+2]-centers[prev+2],length=Math.hypot(dx,dy)||1,nx=-dy/length,ny=dx/length;
    for(let j=0;j<3;j++){
     const strip=strips[j],twist=.32*Math.sin(a*2+time*.16+j*.3),cx=Math.cos(twist),sz=-Math.sin(twist),width=(j===1?.21:.115)*(.78+.22*Math.sin(a*3+time*.18+j*.9)**2)*routeScale;
     const wx=nx*cx,wy=ny*cx,wz=sz,n0=dy*wz-dz*wy,n1=dz*wx-dx*wz,n2=dx*wy-dy*wx,norm=Math.hypot(n0,n1,n2)||1;
     for(let k=0;k<=across;k++){const at=(i*(across+1)+k)*3,offset=((k/across-.5)*width+(j-1)*.12*routeScale)*envelope;
      strip.positions[at]=centers[i*3]+wx*offset;strip.positions[at+1]=centers[i*3+1]+wy*offset;strip.positions[at+2]=centers[i*3+2]+wz*offset-(j-1)*.035*routeScale*envelope;
      strip.normals[at]=-n0/norm;strip.normals[at+1]=-n1/norm;strip.normals[at+2]=-n2/norm;
     }
    }
    beams.forEach((beam,j)=>{const at=i*3,spread=(j-3)*.056*(.94+.06*Math.sin(a*2+time*.3))*envelope*routeScale;beam.positions[at]=centers[at]+nx*spread;beam.positions[at+1]=centers[at+1]+ny*spread;beam.positions[at+2]=centers[at+2]+(j-3)*.025*Math.sin(a*2+time*.16)*envelope*routeScale});
   }
   strips.forEach(s=>{s.geometry.attributes.position.needsUpdate=true;s.geometry.attributes.normal.needsUpdate=true});beams.forEach(b=>{b.geometry.attributes.position.needsUpdate=true});
  },
  dispose(){strips.forEach(s=>{parent.remove(s.mesh);s.geometry.dispose()});materials.forEach(m=>m.dispose());beams.forEach(b=>{parent.remove(b.line);b.geometry.dispose();b.material.dispose()})}
 };
}
