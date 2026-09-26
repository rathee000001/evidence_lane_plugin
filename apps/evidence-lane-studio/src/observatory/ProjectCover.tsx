import type {CSSProperties} from 'react';
import type {Project} from '../types';
import {isDesignPreview} from '../api';

const previewCovers:Record<string,string>={'preview-coastal':'coastal-study.png','preview-notes':'research-notes.png','preview-product':'product-analysis.png'};
const previewAccents:Record<string,string>={'preview-coastal':'#93dccd','preview-notes':'#e7c291','preview-product':'#c7afea'};
export const projectAccent=(project:Project)=>previewAccents[project.project_id]??['#91dfcd','#edc294','#c3a4f1'][Math.abs([...project.project_id].reduce((v,c)=>v+c.charCodeAt(0),0))%3];

/** imageUrl is a presentation input for the engine owner's future cover contract. */
export function ProjectCover({project,compact=false,imageUrl}:{project:Project;compact?:boolean;imageUrl?:string}) {
  const name=project.source_root.split(/[\\/]/).filter(Boolean).at(-1)??'Project';
  const illustrative=import.meta.env.DEV&&isDesignPreview()?previewCovers[project.project_id]:undefined;
  const source=imageUrl??(illustrative?import.meta.env.BASE_URL+'assets/project-covers/'+illustrative:undefined);
  const initials=name.split(/\s+/).slice(0,2).map(part=>part[0]).join('');
  return <div className={`project-cover ${compact?'is-compact':''}`} style={{'--project-accent':projectAccent(project)} as CSSProperties}>{source?<img src={source} alt={`${illustrative?'Illustrative cover':'Project cover'} — ${name}`} loading="lazy"/>:<div className="project-cover-placeholder" role="img" aria-label={`Cover not supplied for ${name}`}><span>{initials}</span>{!compact&&<small>Project cover not supplied</small>}</div>}<i aria-hidden="true"/></div>;
}
