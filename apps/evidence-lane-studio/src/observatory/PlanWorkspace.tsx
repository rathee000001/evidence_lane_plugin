import {useEffect,useRef,useState} from 'react';
import {api} from '../api';
import type {Snapshot,Task} from '../types';
import {Badge,Empty,Icon,Pill,Table,words} from '../ui';
import {useWorkspaceSelection} from './WorkspaceSelection';
import {subject} from './subjects';
import {TaskContract} from './TaskContract';

const taskSubject=(task:Task)=>subject('task',task,task.definition.title,'plan_read task page');
export function PlanView({data}:{data:Snapshot}) {
 const workspace=useWorkspaceSelection(),original=data.project?.plan;
 const[loaded,setLoaded]=useState<{revision:number|null;offset:number;page:NonNullable<typeof original>}|null>(null);
 const[loading,setLoading]=useState(false),[error,setError]=useState('');
 const sequence=useRef(0),table=useRef<HTMLDivElement>(null);
 const offset=loaded?.revision===original?.revision?loaded?.offset??0:0,page=offset>0?loaded!.page:original;
 useEffect(()=>{workspace?.publish((page?.tasks??[]).map(taskSubject))},[page,workspace?.publish]);
 useEffect(()=>()=>{sequence.current++},[]);
 useEffect(()=>{table.current?.querySelector('.studio-table-wrap')?.scrollTo({top:0})},[offset]);
 async function load(next:number){
  if(!data.project||!original)return;
  const request=++sequence.current;setLoading(true);setError('');
  try{const value=await api<NonNullable<typeof original>>('read',{project_id:data.project.project_id,action:'plan_read',arguments:{revision:original.revision,offset:next,limit:100}});if(request===sequence.current)setLoaded({revision:original.revision,offset:next,page:value})}
  catch(reason){if(request===sequence.current)setError(reason instanceof Error?reason.message:'Unable to read this task page.')}
  finally{if(request===sequence.current)setLoading(false)}
 }
 if(!page)return <Empty title="Select a project" detail="Choose a project to inspect its ordered work."/>;
 if(page.state==='no_plan')return <Empty icon="plan" title="No project Plan yet" detail="Create the work plan through your connected Codex plugin. Its ordered tasks and contracts will appear here."/>;
 const current=page.tasks.find(task=>task.state==='active'),selected=workspace?.selected?.kind==='task'?workspace.selected.record as Task:undefined;
 const selectedIndex=selected?page.tasks.findIndex(task=>task.definition.task_id===selected.definition.task_id):-1;
 const stripStart=Math.max(0,Math.floor(Math.max(0,selectedIndex)/12)*12),strip=page.tasks.slice(stripStart,stripStart+12);
 const complete=page.counts.completed??0,recovery=data.project?.recovery;
 const recoveryPending=recovery?.source_restore?.cleared_by_revision===null||recovery?.database_recovery?.cleared_by_revision===null;
 return <section className="plan-workspace" aria-label="Plan workspace">
  <header className="plan-overview plan-glass"><div><span className="plan-kicker">PLAN · ORDERED WORK AND TASK CONTRACT</span><h1>{page.title??'Project Plan'}</h1></div><div className="plan-progress"><span>Revision {page.revision}</span><strong>{complete} <small>of {page.total_tasks} complete</small></strong><div role="progressbar" aria-label="Completed Plan tasks" aria-valuemin={0} aria-valuemax={page.total_tasks} aria-valuenow={complete}><i style={{width:`${page.total_tasks?complete/page.total_tasks*100:0}%`}}/></div></div></header>
  {recoveryPending&&<p role="status" className="studio-notice">Recovery requires a new Plan revision before work can run. Review the restored source and project state with your connected agent.</p>}
  <div className="plan-columns">
   <section className="plan-ordered-work plan-glass" aria-label="Ordered tasks">
    <div className="plan-current"><div className="plan-current-subject"><Icon name="plan" size={38}/><div><span className="plan-kicker">CURRENT TASK</span><strong>{current?.definition.title??'No active task on this page'}</strong>{current&&<small>{words(current.definition.profile)}</small>}</div></div><div className="plan-current-outcome"><span>Requested outcome</span><p>{current?.definition.requested_outcome??'Select a task below to inspect its recorded contract.'}</p></div></div>
    {error&&<p role="alert" className="studio-error">{error}</p>}
    <div className="plan-task-table" ref={table} aria-busy={loading}><Table columns={['#','Task','Profile','State','Contract']} label="Plan tasks">{page.tasks.map(task=>{const s=taskSubject(task),active=selected?.definition.task_id===task.definition.task_id;return <tr key={s.key} data-task-state={task.state} data-selected={active}><td>{task.position}</td><td><button className="plan-task-select" aria-pressed={active} onClick={()=>workspace?.select(s)}>{task.definition.title}</button></td><td>{words(task.definition.profile)}</td><td><Badge tone={task.state==='completed'?'success':task.state==='active'?'active':''}>{words(task.state)}</Badge></td><td><button className="plan-contract-open" data-subject-key={s.key} onClick={()=>workspace?.open(s)} aria-label={`Open contract for task ${task.position}: ${task.definition.title}`}>{task.definition.task_id}<span aria-hidden="true"> ↗</span></button></td></tr>})}</Table></div>
    <footer className="plan-pagination"><span>{page.tasks.length?offset+1:0}–{Math.min(offset+page.tasks.length,page.total_tasks)} of {page.total_tasks}</span><Pill disabled={loading||!offset} onClick={()=>void load(Math.max(0,offset-100))}>Previous</Pill><Pill disabled={loading||offset+page.tasks.length>=page.total_tasks} onClick={()=>void load(offset+100)}>Next</Pill></footer>
   </section>
   <aside className="plan-inspection" aria-label="Selected task and contract">
    <section className="plan-order-strip plan-glass"><header><h2>Task order</h2><small>{strip[0]?.position??0}–{strip.at(-1)?.position??0} of {page.total_tasks}</small></header><div className="plan-order-nodes" aria-label="Task order near selection">{strip.map(task=><button key={task.definition.task_id} data-state={task.state} aria-pressed={selected?.definition.task_id===task.definition.task_id} aria-label={`Select task ${task.position}: ${task.definition.title}`} onClick={()=>workspace?.select(taskSubject(task))}><span className="plan-order-orb">{task.state==='completed'?'✓':task.state==='active'?'◉':''}</span><small>{task.position}</small></button>)}</div><p>Ordered tasks · select to inspect</p></section>
    <section className="plan-contract plan-glass" aria-label="Task contract"><header><div><span className="plan-kicker">TASK CONTRACT</span><h2>{selected?.definition.title??'Select a task'}</h2></div>{selected&&<button className="plan-expand" onClick={()=>workspace?.open(taskSubject(selected))} aria-label="Expand selected task contract">↗</button>}</header>{selected?<><div className="plan-contract-identity"><code>{selected.definition.task_id}</code><Badge tone={selected.state==='completed'?'success':selected.state==='active'?'active':''}>{words(selected.state)}</Badge></div><div className="plan-contract-scroll"><TaskContract task={selected}/></div></>:<p>Select an ordered task to see its outcome, dependencies and acceptance checks.</p>}</section>
   </aside>
  </div>
 </section>;
}
