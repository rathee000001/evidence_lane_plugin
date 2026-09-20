import type * as Three from 'three';

/** Product: independent soft teal mist, depth-blurred blue/amber bokeh and sparse flares. No bitmap input. */
export function createProductUniverse(T:typeof Three){
 const uniforms={uTime:{value:0},uProgress:{value:0},uAspect:{value:1},uPointer:{value:new T.Vector2()}};
 const geometry=new T.PlaneGeometry(2,2);
 const material=new T.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',fragmentShader:`
 varying vec2 vUv;uniform float uTime,uProgress,uAspect;uniform vec2 uPointer;
 float hash(vec3 p){p=fract(p*.27183+vec3(.37,.13,.71));p*=19.17;return fract((p.x+p.y)*p.z);}
 float noise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
 float fbm(vec3 p){float value=0.,weight=.55;for(int i=0;i<4;i++){value+=noise(p)*weight;p=mat3(.91,.13,.39,-.25,.94,.22,-.34,-.31,.88)*p*2.07+vec3(9.1,3.7,17.3);weight*=.48;}return value;}
 void main(){vec2 p=(vUv-.5)*vec2(uAspect,1.)*2.;p+=uPointer*.09;vec3 colour=vec3(.002,.005,.009);float trans=1.;
 for(int i=0;i<7;i++){float z=float(i)*.35+uProgress*.021;vec3 q=vec3(p*1.3,z+uTime*.018);float warp=fbm(q*.72+21.);float cloud=fbm(q+vec3(warp*.8,warp*1.3,8.));float wisps=fbm(q*2.15+vec3(warp*1.7,4.,18.));float envelope=.35+.55*smoothstep(-1.4,.8,p.x)+.25*(1.-smoothstep(-.8,.7,p.y));float density=max(0.,cloud-.34)*envelope*.23;float softVeil=.4+pow(max(0.,1.-abs(wisps-.51)*2.),5.)*.8;vec3 mist=mix(vec3(.025,.12,.18),vec3(.10,.37,.40),smoothstep(.3,.68,cloud));mist+=vec3(.09,.05,.16)*smoothstep(.42,.7,warp);colour+=trans*mist*density*softVeil;trans*=1.-density*.32;}
 for(int i=0;i<42;i++){float seed=float(i);float depth=hash(vec3(seed,17.2,9.4));vec2 centre=vec2(hash(vec3(seed,3.1,8.7)),hash(vec3(seed,5.7,2.3)));centre+=uPointer*(.005+depth*.018)+vec2(sin(uTime*.018+seed)*.006,cos(uTime*.014+seed)*.007);vec2 delta=(vUv-centre)*vec2(uAspect,1.);float radius=mix(.003,.019,depth*depth);float d2=dot(delta,delta);float halo=exp(-d2/(radius*radius*5.))* .075;float disc=exp(-d2/(radius*radius))*(.16+.035*sin(uTime*.3+seed));float bright=step(.91,hash(vec3(seed,29.1,7.3)));float core=exp(-d2/(radius*radius*.12))*bright*.9;vec3 tint=mod(seed,3.)<1.?vec3(1.,.58,.25):vec3(.30,.59,.74);colour+=tint*(halo+disc+core);}
 gl_FragColor=vec4(colour,1.);}

 `});
 const scene=new T.Scene();scene.add(new T.Mesh(geometry,material));const camera=new T.Camera();const target=new T.WebGLRenderTarget(1,1,{depthBuffer:false});
 const displayMaterial=new T.ShaderMaterial({depthWrite:false,depthTest:false,uniforms:{map:{value:target.texture}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,.9999,1.);}',fragmentShader:'varying vec2 vUv;uniform sampler2D map;void main(){gl_FragColor=texture2D(map,vUv);}'});
 const display=new T.Mesh(geometry,displayMaterial);display.renderOrder=-1000;display.frustumCulled=false;
 return{display,uniforms,render(renderer:Three.WebGLRenderer){renderer.setRenderTarget(target);renderer.render(scene,camera);renderer.setRenderTarget(null)},resize(w:number,h:number){target.setSize(Math.max(1,Math.round(w*.5)),Math.max(1,Math.round(h*.5)));uniforms.uAspect.value=w/h},dispose(){target.dispose();geometry.dispose();material.dispose();displayMaterial.dispose()}};
}
