import type {Task} from '../types';
import {words} from '../ui';

/** A presentation of the supplied task contract; no inferred tools, paths or dependencies. */
export function TaskContract({task}:{task:Task}) {
 const d=task.definition;
 const list=(items:string[]|undefined)=>(items?.length?<ul>{items.map((value,index)=><li key={index}>{value}</li>)}</ul>:<span className="plan-unset">None declared</span>);
 return <dl className="plan-contract-fields">
  <div><dt>Outcome</dt><dd>{d.requested_outcome}</dd></div>
  <div><dt>Dependencies</dt><dd>{list(d.dependencies)}</dd></div>
  <div><dt>Permitted paths</dt><dd className="plan-paths">{list(d.permitted_paths)}</dd></div>
  <div><dt>Tools</dt><dd>{list(d.permitted_tools)}</dd></div>
  {d.allowed_actions?.length>0&&<div><dt>Allowed actions</dt><dd>{list(d.allowed_actions)}</dd></div>}
  <div><dt>Acceptance checks</dt><dd>{list(d.acceptance_checks)}</dd></div>
  <div><dt>Budget</dt><dd>{Object.keys(d.budget??{}).length?<ul>{Object.entries(d.budget).map(([key,value])=><li key={key}>{words(key)}: {typeof value==='object'?JSON.stringify(value):String(value)}</li>)}</ul>:<span className="plan-unset">Not supplied</span>}</dd></div>
  <div><dt>Stop condition</dt><dd>{d.stop_condition||<span className="plan-unset">Not supplied</span>}</dd></div>
 </dl>;
}
