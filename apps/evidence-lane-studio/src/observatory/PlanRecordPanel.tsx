import type {Snapshot} from '../types';
import {Details,words} from '../ui';
import {useObserver} from './ObserverContext';

export function PlanRecordPanel({data}:{data:Snapshot}) {
  const {inspect}=useObserver();
  const plan=data.project?.plan;
  if(!plan||plan.state==='no_plan')return null;
  const current=plan.tasks.find(task=>task.state==='active');
  const task=current?.definition;
  return <div className="observatory-plan-record"><section className="observatory-task-sequence" aria-label="Initial snapshot task order"><h3>Task order <small>First {Math.min(12,plan.tasks.length)} of {plan.total_tasks}</small></h3><div>{plan.tasks.slice(0,12).map(item=><button key={item.definition.task_id} data-state={item.state} title={`${item.position} · ${item.definition.title} · ${words(item.state)}`} aria-label={`Inspect task ${item.position}: ${item.definition.title}`} onClick={()=>inspect(`Task ${item.position} — ${item.definition.title}`,item)}><span>{item.state==='completed'?'✓':item.position}</span></button>)}</div></section>
    {task?<section><h3>Current task contract</h3><dl><dt>Subject</dt><dd className="observatory-contract-subject">{task.title}</dd><dt>Outcome</dt><dd>{task.requested_outcome}</dd><dt>Dependencies</dt><dd>{task.dependencies.join(', ')||'None declared'}</dd><dt>Permitted paths</dt><dd>{task.permitted_paths.join(', ')||'None declared'}</dd><dt>Tools</dt><dd>{task.permitted_tools.join(', ')||'None declared'}</dd><dt>Acceptance checks</dt><dd><ul>{task.acceptance_checks.map((check,i)=><li key={i}>{check}</li>)}</ul></dd><dt>Budget</dt><dd>{Object.entries(task.budget).map(([key,value])=>`${words(key)}: ${typeof value==='object'?JSON.stringify(value):String(value)}`).join(' · ')||'Not recorded'}</dd><dt>Stop condition</dt><dd>{task.stop_condition}</dd></dl><Details value={current} label="Inspect full task record" title={`Current task — ${task.title}`}/></section>:<p>The active task is outside the initial snapshot page or none is active. Use the complete Plan table to inspect it.</p>}
  </div>;
}
