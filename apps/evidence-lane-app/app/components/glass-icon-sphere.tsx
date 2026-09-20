import type {CSSProperties, ReactNode} from 'react';
import './glass-icon-sphere.css';

/** The retained glass construction: rear volume, contained object, glass, rim, reflection. */
export function GlassIconSphere({children,color='#67d9f6',size=48,className=''}:{children:ReactNode;color?:string;size?:number;className?:string}){
 return <span className={`glass-sphere-icon ${className}`} style={{'--sphere-color':color,'--sphere-size':`${size}px`} as CSSProperties} aria-hidden="true"><span className="glass-sphere-icon__rear"/><span className="glass-sphere-icon__object">{children}</span><span className="glass-sphere-icon__glass"/><span className="glass-sphere-icon__rim"/><span className="glass-sphere-icon__reflection"/></span>;
}
