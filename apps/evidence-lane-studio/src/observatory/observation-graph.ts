import type {Snapshot,RecordData} from '../types';
import {subject,subjectsFor,defaultSection,displayWords,type StudioSubject} from './subjects';
export type ObservationNode={id:string;label:string;icon:string;color:string;x:number;y:number;value:unknown;subject:StudioSubject;note?:string};
export type ObservationGraph={title:string;note:string;nodes:ObservationNode[];edges:[number,number][];omitted?:number};
const colors:Record<string,string>={task:'#9bbdcc',job:'#b5accb',worker:'#a2b8bc','worker-pool':'#a2b8bc',receipt:'#a2c8be',tool:'#c7b694',action:'#a8bfce',workflow:'#b2bdd3',client:'#9cbfc4',lesson:'#bbafce',device:'#a7b7c9',provider:'#b5bfc9',project:'#b3c5be'};
const icons:Record<string,string>={task:'plan',job:'jobs',worker:'workers','worker-pool':'workers',receipt:'evidence',tool:'tools',action:'operation',workflow:'connections',client:'connections',lesson:'learning',device:'accelerators',provider:'workers',project:'projects',event:'connections',memory:'brain',exchange:'connections',grant:'tools','compute-settings':'accelerators','diagnostic-section':'evidence',reference:'evidence','query-result':'evidence'};
const node=(s:StudioSubject,x:number,y:number,note?:string):ObservationNode=>({id:s.key,label:s.title,icon:icons[s.kind]??'evidence',color:colors[s.kind]??'#aec1ce',x,y,value:s.record,subject:s,note});

/** Every luminous edge below has an explicit owning field in the returned data. */
export function buildObservationGraph(view:string,data:Snapshot,selected:StudioSubject|null=null,section=defaultSection(view),available=subjectsFor(view,section,data)):ObservationGraph{
 if(!selected){const visible=available.slice(0,9);return{title:'Choose a record',note:'Select a record here or in the panel. The same subject will remain selected in the scene and inspector.',nodes:visible.map((s,i)=>node(s,visible.length===1?50:20+i%3*30,22+Math.floor(i/3)*29)),edges:[],omitted:Math.max(0,available.length-visible.length)}}
 const r=selected.record&&typeof selected.record==='object'?selected.record as RecordData:{};
 let related:StudioSubject[]=[],note='This record has no relationship references supplied for this view.',reverse=false;
 if(selected.kind==='task'){
  const d=r.definition??r;related=(d.dependencies??[]).map((id:string)=>{const found=data.project?.plan.tasks.find(t=>t.definition.task_id===id);return found?subject('task',found,found.definition.title,'project.plan.tasks'):subject('reference',{task_id:id,relationship:'declared dependency'},id,'selected task dependencies',id)});note='Connections are the dependency IDs declared by this task.';
 }else if(selected.kind==='action'){
  related=(r.required_tools??[]).map((id:string)=>{const tool=data.tool_catalog.entries.find(t=>t.tool_id===id);return tool?subject('tool',tool,tool.name??id,'tool_catalog.entries'):subject('reference',{tool_id:id,relationship:'declared required tool'},id,'selected action required_tools',id)});note='Declared required-tool references. These links do not claim invocation or readiness.';
 }else if(selected.kind==='tool'){
  related=data.actions.filter(a=>a.required_tools?.includes(selected.id)).map(a=>subject('action',a,displayWords(a.name),'actions'));reverse=true;note='These returned operations declare this tool in required_tools.';
 }else if(selected.kind==='workflow'){
  related=(r.actions??[]).map((a:RecordData)=>subject('action',a,displayWords(a.name),'actions grouped by workflow'));note='Returned operation membership, not an execution timeline.';
 }else if(selected.kind==='client'){
  related=(r.project_ids??[]).map((id:string)=>{const p=data.projects.find(p=>p.project_id===id);return p?subject('project',p,p.source_root.split(/[\\/]/).filter(Boolean).at(-1)??id,'projects'):subject('reference',{project_id:id},id,'selected client project_ids',id)});note='Only the project references recorded by this client are connected.';
 }else if(selected.kind==='worker-pool'){
  related=(r.initialized_workers??[]).map((w:RecordData,i:number)=>subject('worker',w,w.pid!=null?`Process ${w.pid}`:`Process record ${i+1}`,'workers.initialized_workers'));note='Reported pool membership. No worker-to-job assignment is inferred.';
 }else if(selected.kind==='lesson'){
  related=(r.observation?.scope?.checks??[]).map((check:string,i:number)=>subject('reference',{check,recorded_in:selected.id},check,'selected lesson scope.checks',`${selected.id}:${i}`));note='Checks contained in this scoped observation, not replayed execution steps.';
 }else if(selected.kind==='diagnostic-section'){
  const membership=selected.id==='connections'?subjectsFor('connections','connections',data):selected.id==='tool_catalog'?subjectsFor('tools','tools',data):selected.id==='inventory'?subjectsFor('accelerators','accelerators',data):selected.id==='workers'?subjectsFor('workers','workers',data):[];
  if(membership.length)return{title:selected.title,note:'Separate returned records in this data section. No engine-to-record relationship is inferred.',nodes:membership.slice(0,9).map((s,i)=>node(s,20+i%3*30,22+Math.floor(i/3)*29)),edges:[],omitted:Math.max(0,membership.length-9)};
 }
 const shown=related.slice(0,8),nodes=[node(selected,shown.length?20:50,50),...shown.map((s,i)=>node(s,shown.length<3?78:57+(i%2)*29,shown.length===1?50:18+Math.floor(i/(shown.length<3?1:2))*21))];
 return{title:selected.title,note,nodes,edges:shown.map((_,i)=>reverse?[i+1,0]:[0,i+1]),omitted:Math.max(0,related.length-shown.length)};
}
