import type * as Three from 'three';

/** Coloured depth particles with local radial displacement and damped return. */
export function createInteractiveStarfield(T:typeof Three,count=3600,transferCount=180){
 const group=new T.Group(),geometry=new T.BufferGeometry();
 const rest=new Float32Array(count*3),positions=new Float32Array(count*3),velocity=new Float32Array(count*2),offset=new Float32Array(count*2),colors=new Float32Array(count*3),sizes=new Float32Array(count),softness=new Float32Array(count);
 let seed=9713;const random=()=>{seed=seed*16807%2147483647;return(seed-1)/2147483646};
 const palette=[0x72b3de,0x91c8e6,0xcddce5,0xd19a64,0x7195bc,0x65aabb];
 for(let i=0;i<count;i++){const z=-7-random()*64;const spread=(15-z)*.5;rest.set([(random()-.5)*spread*3.4,(random()-.5)*spread*2,z],i*3);const c=new T.Color(palette[Math.floor(random()*palette.length)]);colors.set([c.r,c.g,c.b],i*3);sizes[i]=random()>.91?4.5+random()*2.5:1.1+random()*2.6;softness[i]=random();}
 positions.set(rest);geometry.setAttribute('position',new T.BufferAttribute(positions,3));geometry.setAttribute('color',new T.BufferAttribute(colors,3));geometry.setAttribute('size',new T.BufferAttribute(sizes,1));geometry.setAttribute('softness',new T.BufferAttribute(softness,1));
 const material=new T.ShaderMaterial({transparent:true,depthWrite:false,vertexColors:true,blending:T.NormalBlending,uniforms:{pixelRatio:{value:Math.min(devicePixelRatio,1.5)}},vertexShader:`attribute float size;attribute float softness;uniform float pixelRatio;varying vec3 tint;varying float blur;void main(){tint=color;blur=softness;vec4 p=modelViewMatrix*vec4(position,1.);gl_Position=projectionMatrix*p;gl_PointSize=clamp(size*pixelRatio*(30./-p.z),.8,8.);}`,fragmentShader:`varying vec3 tint;varying float blur;void main(){vec2 q=gl_PointCoord-.5;float r=length(q)*2.;if(r>1.)discard;float core=mix(smoothstep(1.,.2,r),exp(-r*r*3.8),blur);float edge=1.-smoothstep(.6,1.,r);gl_FragColor=vec4(tint,core*edge*.78);
 #include <colorspace_fragment>
 }`});
 const points=new T.Points(geometry,material);points.frustumCulled=false;group.add(points);
 const transfer=new T.Vector3();
 let pointerX=3,pointerY=3,pressure=0,lastX=3,lastY=3,flowX=0,flowY=0,ambientTime=0;
 return{group,
  pointer(x:number,y:number){if(lastX<2){const dx=x-lastX,dy=y-lastY;pressure=Math.min(1.5,pressure+Math.hypot(dx,dy)*13);flowX=dx;flowY=dy;}pointerX=x;pointerY=y;lastX=x;lastY=y;},
  leave(){pointerX=3;pointerY=3;lastX=3;lastY=3;},
  update(dt:number,camera:Three.PerspectiveCamera,progress:number,paused:boolean,transition?:{amount:number;matrix:Three.Matrix4;point:(t:number)=>Three.Vector3}){
   if(paused)return 0;dt=Math.min(.035,dt);ambientTime+=dt;pressure*=Math.exp(-dt*3.2);flowX*=Math.exp(-dt*5);flowY*=Math.exp(-dt*5);group.position.z=progress*.34;let peak=0;
   const tan=Math.tan(camera.fov*Math.PI/360),active=pointerX<2&&pressure>.002;
   for(let i=0;i<count;i++){const j=i*3,k=i*2,depth=camera.position.z-rest[j+2]-group.position.z,halfH=tan*depth;const px=camera.position.x+pointerX*halfH*camera.aspect,py=camera.position.y+pointerY*halfH;
    let ax=-offset[k]*16-velocity[k]*7.8,ay=-offset[k+1]*16-velocity[k+1]*7.8;
    if(active){const dx=rest[j]+offset[k]-px,dy=rest[j+1]+offset[k+1]-py,radius=halfH*.29,dist=Math.hypot(dx,dy);if(dist<radius){const falloff=(1-dist/radius)**2,strength=pressure*falloff*radius*15,norm=Math.max(dist,.03);ax+=(dx/norm+flowX*3)*strength;ay+=(dy/norm+flowY*3)*strength;}}
    velocity[k]+=ax*dt;velocity[k+1]+=ay*dt;offset[k]+=velocity[k]*dt;offset[k+1]+=velocity[k+1]*dt;positions[j]=rest[j]+offset[k]+Math.sin(ambientTime*.10+i*.71)*halfH*.002;positions[j+1]=rest[j+1]+offset[k+1]+Math.cos(ambientTime*.08+i*.37)*halfH*.002;positions[j+2]=rest[j+2]+Math.sin(ambientTime*.06+i*.19)*.12;if(transition&&i<transferCount&&transition.amount>.001){transfer.copy(transition.point((i/transferCount+ambientTime*.035)%1)).applyMatrix4(transition.matrix);const mix=transition.amount*(.78+.22*softness[i]);positions[j]=T.MathUtils.lerp(positions[j],transfer.x,mix);positions[j+1]=T.MathUtils.lerp(positions[j+1],transfer.y,mix);positions[j+2]=T.MathUtils.lerp(positions[j+2],transfer.z-group.position.z,mix);}peak=Math.max(peak,Math.hypot(offset[k],offset[k+1])/halfH);
   }
   geometry.attributes.position.needsUpdate=true;return peak;
  },dispose(){geometry.dispose();material.dispose()}
 };
}
