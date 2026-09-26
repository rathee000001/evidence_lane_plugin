import type * as Three from 'three';

/** Procedural volume rendered at half resolution; no bitmap or texture input. */
export function createProceduralGalaxy(T:typeof Three){
 const uniforms={uTime:{value:0},uProgress:{value:0},uAspect:{value:1},uPointer:{value:new T.Vector2()}};
 const geometry=new T.PlaneGeometry(2,2);
 const material=new T.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',fragmentShader:`
 varying vec2 vUv;uniform float uTime,uProgress,uAspect;uniform vec2 uPointer;
 float hash(vec3 p){p=fract(p*.3183099+vec3(.11,.23,.37));p*=17.;return fract(p.x*p.y*p.z*(p.x+p.y+p.z));}
 float noise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
 float fbm(vec3 p){float v=0.,a=.53;for(int i=0;i<5;i++){v+=noise(p)*a;p=mat3(.8,.0,.6,.36,.8,-.48,-.48,.6,.64)*p*2.13+5.2;a*=.49;}return v;}
 void main(){vec2 p=(vUv-.5)*vec2(uAspect,1.)*2.;p+=uPointer*.045+vec2(uProgress*.035,-uProgress*.028);vec3 sum=vec3(.002,.004,.011);float trans=1.;
 for(int i=0;i<10;i++){float z=float(i)*.28+uProgress*.05;vec3 q=vec3(p*2.,z+uTime*.007);float warp=fbm(q*.72+8.);float ridge=p.y-.23*p.x+.23+(.5-warp)*.65;float envelope=exp(-ridge*ridge*7.5);float n=fbm(q*1.35+vec3(warp*2.));float dust=fbm(q*4.+20.);float d=max(0.,n-.33)*envelope*.29;float filament=pow(max(0.,1.-abs(dust-.52)*2.),7.);vec3 cold=mix(vec3(.09,.12,.42),vec3(.18,.49,.79),smoothstep(.3,.7,n));vec3 warm=vec3(.60,.36,.17)*smoothstep(.9,2.3,p.x+z*.08);vec3 light=(cold+warm)*d*(.55+filament*1.9);sum+=trans*light;trans*=1.-d*.37;}
 float vignette=1.-smoothstep(.7,2.1,length(p*vec2(.58,.8)))*.35;gl_FragColor=vec4(sum*vignette,1.);}
 `});
 const scene=new T.Scene();scene.add(new T.Mesh(geometry,material));const camera=new T.Camera();const target=new T.WebGLRenderTarget(1,1,{depthBuffer:false});
 const displayMaterial=new T.ShaderMaterial({depthWrite:false,depthTest:false,uniforms:{map:{value:target.texture}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,.9999,1.);}',fragmentShader:'varying vec2 vUv;uniform sampler2D map;void main(){gl_FragColor=texture2D(map,vUv);}'});
 const display=new T.Mesh(geometry,displayMaterial);display.renderOrder=-1000;display.frustumCulled=false;
 return{display,uniforms,render(renderer:Three.WebGLRenderer){renderer.setRenderTarget(target);renderer.render(scene,camera);renderer.setRenderTarget(null)},resize(w:number,h:number){target.setSize(Math.max(1,Math.round(w*.5)),Math.max(1,Math.round(h*.5)));uniforms.uAspect.value=w/h},dispose(){target.dispose();geometry.dispose();material.dispose();displayMaterial.dispose()}};
}
