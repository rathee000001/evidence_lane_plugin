import {GlassIconOrb} from '../design-system/components/GlassIconOrb';
export function BrainOrb({size=64}:{size?:number}){return <GlassIconOrb size={size} color="#bc9be9" decorative><span className="observer-brain-art"><img className="observer-brain" src={import.meta.env.BASE_URL+'assets/evidence-static-brain.png'} alt=""/></span></GlassIconOrb>}
