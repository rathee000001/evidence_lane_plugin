import type {RecordData,Snapshot} from '../types';

export type SubjectKind='project'|'task'|'job'|'worker'|'worker-pool'|'receipt'|'event'|'memory'|'exchange'|'continuation'|'tool'|'action'|'workflow'|'grant'|'client'|'lesson'|'device'|'provider'|'compute-settings'|'diagnostic-section'|'reference'|'query-result';
export type StudioSubject={key:string;kind:SubjectKind;id:string;title:string;record:unknown;source:string};
export const displayWords=(value:unknown)=>String(value??'').replace(/[_-]/g,' ');
const identityFields:Partial<Record<SubjectKind,string>>={project:'project_id',task:'task_id',job:'job_id',worker:'pid',receipt:'receipt_id',event:'event_id',memory:'locator_id',exchange:'exchange_id',continuation:'continuation_id',tool:'tool_id',action:'name',workflow:'workflow',grant:'plugin_id',client:'client_id',lesson:'version_id',device:'device_id',provider:'runtime_id'};

export function subject(kind:SubjectKind,record:unknown,title:string,source:string,id?:string):StudioSubject{
  const object=record&&typeof record==='object'?record as RecordData:{};
  const owning=kind==='task'?(object.definition??object):object;
  const identity=id??String(owning[identityFields[kind]??'']??source);
  return{key:`${kind}:${identity}`,kind,id:identity,title,record,source};
}
export const defaultSection=(page:string)=>(({evidence:'receipts',tools:'tools',diagnostics:'runtime'} as Record<string,string>)[page]??page);

/** Stable UI subjects from returned records. These keys are not native host identity attestations. */
export function subjectsFor(page:string,section:string,data:Snapshot):StudioSubject[]{
  const p=data.project;
  const list=(rows:RecordData[]|undefined,kind:SubjectKind,path:string,title:(row:RecordData,i:number)=>string)=>
    (rows??[]).map((row,i)=>subject(kind,row,title(row,i),`${path}[${i}]`));
  if(page==='projects')return list(data.projects,'project','projects',r=>r.source_root.split(/[\\/]/).filter(Boolean).at(-1)??r.project_id);
  if(page==='plan')return list(p?.plan.tasks,'task','project.plan.tasks',r=>r.definition.title);
  if(page==='jobs')return list(p?.jobs.recent,'job','project.jobs.recent',r=>displayWords(r.action));
  if(page==='workers')return [subject('worker-pool',data.workers,'Worker pool','workers','pool'),...list(data.workers.initialized_workers,'worker','workers.initialized_workers',(r,i)=>r.pid!=null?`Process ${r.pid}`:`Process record ${i+1}`)];
  if(page==='evidence'){
    if(section==='receipts')return list(p?.evidence.receipts,'receipt','project.evidence.receipts',r=>displayWords(r.kind));
    if(section==='lineage')return list(p?.lineage.events,'event','project.lineage.events',r=>displayWords(r.kind));
    if(section==='memory')return list(p?.memory.locators,'memory','project.memory.locators',r=>r.locator.label);
    if(section==='canon')return [...list(p?.canon.exchanges,'exchange','project.canon.exchanges',r=>r.summary??displayWords(r.kind)),...list(p?.continuity.offers,'continuation','project.continuity.offers',r=>r.binding?.task?.key??'Continuation')];
    return [];
  }
  if(page==='tools'){
    if(section==='tools')return list(data.tool_catalog.entries,'tool','tool_catalog.entries',r=>r.name??r.tool_id);
    if(section==='operations')return list(data.actions,'action','actions',r=>displayWords(r.name));
    if(section==='workflows'){
      const groups=new Map<string,RecordData[]>();for(const action of data.actions){const key=String(action.workflow??'');if(!key)continue;groups.set(key,[...(groups.get(key)??[]),action])}
      return [...groups].map(([workflow,actions])=>subject('workflow',{workflow,actions},displayWords(workflow),'actions grouped by returned workflow',workflow));
    }
    return list(p?.plugins,'grant','project.plugins',r=>r.name??r.plugin_id);
  }
  if(page==='connections')return list(data.connections,'client','connections',r=>r.label??r.client_id);
  if(page==='learning')return list(p?.learning.lessons,'lesson','project.learning.lessons',r=>`${displayWords(r.observation?.scope?.action)} · v${r.version}`);
  if(page==='accelerators')return [...list(data.inventory.devices,'device','inventory.devices',r=>r.name??r.device_id),...list(data.inventory.providers,'provider','inventory.providers',r=>r.runtime_id),...(p?[subject('compute-settings',p.accelerator,'Project compute settings','project.accelerator',p.project_id)]:[])];
  if(page==='diagnostics'&&section==='runtime')return [['engine','Engine observation'],['connections','Client observations'],['workers','Worker pool'],['tool_catalog','Tool catalogue'],['inventory','Compute inventory'],['active_executions','Active executions']].map(([key,title])=>subject('diagnostic-section',data[key as keyof Snapshot],title,key,key));
  return [];
}

export function initialSubject(page:string,subjects:StudioSubject[]):string|null{
  if(page==='plan')return subjects.find(s=>(s.record as RecordData).state==='active')?.key??null;
  if(page==='workers')return subjects.find(s=>s.kind==='worker-pool')?.key??null;
  return null;
}
