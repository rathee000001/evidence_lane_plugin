import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {createHomeOpticalRoute} from '../app/components/home-optical-routes.ts';
import {pageConnectionDesigns} from '../app/components/page-connection-designs.ts';

test('open optical routes remain docked, use broad Home surfaces and produce finite normals',()=>{
 const root=new THREE.Group(),route=createHomeOpticalRoute(THREE,root,96);
 const sample=(t,out)=>out.set(-3+6*t,Math.sin(Math.PI*t),.2*t);
 route.update(1.3,sample);
 const surfaces=root.children.filter(child=>child.isMesh),beams=root.children.filter(child=>child.isLine);
 assert.equal(surfaces.length,3);assert.equal(beams.length,7);
 for(const mesh of surfaces){
  const p=mesh.geometry.attributes.position,n=mesh.geometry.attributes.normal;
  assert.equal(p.count,97*6);
  for(const start of [0,96*6]){
   const left=new THREE.Vector3().fromBufferAttribute(p,start),right=new THREE.Vector3().fromBufferAttribute(p,start+5);
   assert.ok(left.distanceTo(right)>.03,'Glass must stay open at the docking point');
   const midpoint=left.add(right).multiplyScalar(.5),dock=sample(start===0?0:1,new THREE.Vector3());
   assert.ok(midpoint.distanceTo(dock)<.15,'Docked cross-section must stay close to the node edge');
  }
  assert.ok([...p.array,...n.array].every(Number.isFinite));
  const indices=mesh.geometry.index.array,triangle=(40*5+2)*6;
  const ia=indices[triangle],ib=indices[triangle+1],ic=indices[triangle+2];
  const a=new THREE.Vector3().fromBufferAttribute(p,ia),b=new THREE.Vector3().fromBufferAttribute(p,ib),c=new THREE.Vector3().fromBufferAttribute(p,ic);
  const geometric=b.sub(a).cross(c.sub(a)).normalize(),provided=new THREE.Vector3().fromBufferAttribute(n,ia);
  assert.ok(geometric.dot(provided)>0,'Lighting normal must agree with triangle winding');
  assert.equal(mesh.material.transmission,.6);assert.equal(mesh.material.roughness,.055);
 }
 const before=Float32Array.from(surfaces[1].geometry.attributes.position.array);
 route.update(1.3,sample);assert.deepEqual(surfaces[1].geometry.attributes.position.array,before);
 route.update(2.3,sample);assert.notDeepEqual(surfaces[1].geometry.attributes.position.array,before);
 route.update(2.3,sample,0);assert.ok(root.children.every(child=>!child.visible));
 route.dispose();assert.equal(root.children.length,0);
});

test('short panel links stay narrower than long routes without closing their ends',()=>{
 const widths=[];
 for(const length of [.4,1,4]){
  const root=new THREE.Group(),route=createHomeOpticalRoute(THREE,root);
  route.update(0,(t,out)=>out.set(t*length,0,0));
  const beams=root.children.filter(child=>child.isLine);
  const first=beams[0].geometry.attributes.position,last=beams.at(-1).geometry.attributes.position;
  const width=last.getY(0)-first.getY(0);widths.push(width);
  assert.ok(width>0&&width<length*.24,'Width must remain proportionate to available length');
  const middle=last.getY(48)-first.getY(48);
  assert.ok(middle/width<1.25,'The route must not bulge into a closed leaf shape');
  route.dispose();
 }
 assert.ok(widths[0]<widths[1]&&widths[1]<widths[2]);
});

test('each page retains its selected material grammar and disposes its geometry',()=>{
 for(const [page,design] of Object.entries(pageConnectionDesigns)){
  const root=new THREE.Group(),route=createHomeOpticalRoute(THREE,root,48,1,design);
  route.update(2,(t,out)=>out.set(4*t,Math.sin(t*Math.PI),0),.7);
  assert.equal(root.children.filter(item=>item.isMesh).length,design.bands,page);
  assert.equal(root.children.filter(item=>item.isLine).length,design.threads,page);
  for(const child of root.children){assert.ok([...child.geometry.attributes.position.array].every(Number.isFinite),page);assert.ok(child.material.opacity<=.7,page)}
  route.dispose();assert.equal(root.children.length,0,page);
 }
 assert.equal(pageConnectionDesigns.docs.bands,0,'Docs uses constellation tracks, not the product ribbon');
 assert.notEqual(pageConnectionDesigns.workflows.width,pageConnectionDesigns.studio.width);
});
