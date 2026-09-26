import type { ReactNode } from 'react';
import { GlassIconOrb } from './design-system/components/GlassIconOrb';
import { GlassPill } from './design-system/components/GlassPill';
import {useWorkspaceSelection} from './observatory/WorkspaceSelection';
import {subject as makeSubject,type SubjectKind,type StudioSubject} from './observatory/subjects';
import {useObserver} from './observatory/ObserverContext';

export const words = (value: unknown) => String(value ?? 'Unavailable').replaceAll('_', ' ');
export const date = (value: unknown) => value ? new Date(String(value)).toLocaleString() : 'Not recorded';
export const short = (value: unknown) => String(value ?? '').slice(0, 8);
export const bytes = (value: number) => value >= 1048576 ? `${(value / 1048576).toLocaleString(undefined, {maximumFractionDigits: 1})} MB` : `${(value / 1024).toLocaleString(undefined, {maximumFractionDigits: 1})} KB`;

const toolMarks:Record<string,string>={Python:'python.svg',Git:'git.svg',SQLite:'sqlite.svg'};
const paths: Record<string, ReactNode> = {
  operation: <><rect x="5" y="5" width="22" height="22" rx="4"/><path d="m10 11 5 5-5 5M18 21h5"/></>,
  plan: <><path d="M7 5h14v22H7zM11 11h6M11 16h6M11 21h4" /></>,
  jobs: <><path d="M5 10h22v17H5zM11 10V6h10v4M5 16h22M14 16v4h4v-4" /></>,
  workers: <><rect x="7" y="7" width="18" height="18" rx="4" /><path d="M12 12h8v8h-8zM12 3v4M20 3v4M12 25v4M20 25v4M3 12h4M3 20h4M25 12h4M25 20h4" /></>,
  evidence: <><path d="m16 3 12 7v13l-12 7-12-7V10zM4 10l12 7 12-7M16 17v13" /></>,
  tools: <><path d="M20 5a8 8 0 0 0-8 10L4 23l5 5 8-8a8 8 0 0 0 10-8l-6 6-7-7z" /></>,
  connections: <><path d="m11 11 5-5a6 6 0 0 1 9 9l-5 5M21 21l-5 5a6 6 0 0 1-9-9l5-5M11 21l10-10" /></>,
  learning: <><path d="M6 25V7c4-2 7-2 10 1 3-3 6-3 10-1v18c-4-2-7-2-10 1-3-3-6-3-10-1ZM16 8v18" /></>,
  accelerators: <path d="M18 3 6 19h9l-1 10 12-16h-9z" />,
  diagnostics: <><path d="M3 17h6l4-10 6 19 4-12h6" /></>,
  projects: <path d="M3 9h10l3 4h13v14H3zM3 9V5h10l3 4h11v4" />,
  refresh: <><path d="M26 13a11 11 0 1 0 0 9M26 5v8h-8" /></>,
  plus: <path d="M16 6v20M6 16h20" />,
  close: <path d="m8 8 16 16M24 8 8 24" />,
};
const iconColors:Record<string,string>={plan:'#80dafa',jobs:'#ba9af5',workers:'#ae9bec',evidence:'#77debf',tools:'#e6bd80',connections:'#78d5e9',learning:'#c89af1',accelerators:'#8bc8f3',diagnostics:'#ec99b4',projects:'#8acff0',brain:'#c49de9'};
export function Icon({ name, size = 28, tone = 'cyan',identity }: { name: string; size?: number; tone?: 'cyan' | 'gold';identity?:string }) {
  return <GlassIconOrb decorative size={size} color={tone === 'gold' ? '#e7bc64' : iconColors[name]??'#8adbf2'}>{identity&&toolMarks[identity]?<img className="studio-tool-mark" data-mark={identity} src={import.meta.env.BASE_URL+'assets/tool-marks/'+toolMarks[identity]} alt=""/>:name==='brain'||name==='learning'?<span className="observer-brain-art"><img className="observer-brain" src={import.meta.env.BASE_URL+'assets/evidence-static-brain.png'} alt=""/></span>:<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><g className="observatory-icon-extrusion" transform="translate(1.4 1.7)">{paths[name] ?? paths.projects}</g><g className="observatory-icon-face">{paths[name] ?? paths.projects}</g></svg>}</GlassIconOrb>;
}
export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) { return <span className={`studio-badge ${tone}`}>{children}</span>; }
export function Empty({ title, detail, children, icon = 'projects' }: { title: string; detail: string; children?: ReactNode; icon?: string }) {
  return <div className="studio-empty"><Icon name={icon} size={62} /><h2>{title}</h2><p>{detail}</p>{children}</div>;
}
export function Metric({ label, value, detail, icon }: { label: string; value: ReactNode; detail?: string; icon?: string }) {
  return <article className="studio-metric">{icon && <Icon name={icon} size={36} />}<span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</article>;
}
export function Details({value,label='Recorded details',title=label,kind='query-result',id,recordSubject}:{value:unknown;label?:string;title?:string;kind?:SubjectKind;id?:string;recordSubject?:StudioSubject}){
 const {inspectSubject}=useObserver();const workspace=useWorkspaceSelection();
 const selected=recordSubject??makeSubject(kind,value,title,workspace?`${workspace.page}.${workspace.section} selected record`:'Selected read result',id);
 return <button className="studio-details-button" type="button" data-subject-key={selected.key} aria-label={title} aria-haspopup="dialog" onClick={()=>workspace?workspace.open(selected):inspectSubject(selected)}>{label}<span aria-hidden="true">↗</span></button>;
}

export function Table({ columns, children, label }: { columns: string[]; children: ReactNode; label: string }) {
  return <div className="studio-table-wrap" tabIndex={0} aria-label={label}><table><thead><tr>{columns.map(column => <th scope="col" key={column}>{column}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
export function Pill({ children, onClick, disabled, icon, active, type = 'button' }: { children: ReactNode; onClick?: () => void; disabled?: boolean; icon?: string; active?: boolean; type?: 'button' | 'submit' | 'reset' }) {
  return <GlassPill type={type} className="studio-action" onClick={onClick} disabled={disabled} state={active ? 'active' : 'idle'} tone="cyan" leading={icon ? <Icon name={icon} size={24} /> : undefined}>{children}</GlassPill>;
}
