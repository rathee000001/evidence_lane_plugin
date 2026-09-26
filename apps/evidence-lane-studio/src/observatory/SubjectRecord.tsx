import type {RecordData} from '../types';
import {displayWords,type StudioSubject} from './subjects';

export type SubjectField={label:string;value:unknown};
export function subjectFields(subject:StudioSubject):SubjectField[]{
  const r=subject.record&&typeof subject.record==='object'?subject.record as RecordData:{};
  const fields:SubjectField[]=[];
  const add=(label:string,value:unknown)=>{if(value!==undefined)fields.push({label,value})};
  const keys=(object:RecordData,pairs:[string,string][])=>pairs.forEach(([key,label])=>add(label,object[key]));
  switch(subject.kind){
    case 'task':{const d=r.definition??r;keys(r,[['position','Position'],['state','State']]);keys(d,[['requested_outcome','Requested outcome'],['profile','Work profile'],['dependencies','Dependencies'],['permitted_paths','Permitted paths'],['permitted_tools','Permitted tools'],['allowed_actions','Allowed actions'],['acceptance_checks','Acceptance checks'],['budget','Budget'],['stop_condition','Stop condition']]);break}
    case 'job':keys(r,[['job_id','Job'],['action','Operation'],['phase','Phase'],['state','State'],['updated_at','Updated'],['task_id','Task reference'],['plan_revision','Plan revision']]);break;
    case 'worker':keys(r,[['pid','Process ID'],['state','State'],['startup','Startup'],['started_at','Started'],['initialized_at','Initialized'],['executable','Executable']]);break;
    case 'worker-pool':keys(r,[['state','Pool state'],['accepting','Accepting work'],['outstanding','Outstanding operations'],['succeeded_operations','Successful operations']]);add('Initialized process records',r.initialized_workers?.length);break;
    case 'receipt':keys(r,[['receipt_id','Receipt'],['kind','Recorded change'],['created_at','Recorded at']]);break;
    case 'action':keys(r,[['name','Operation'],['description','Purpose'],['workflow','Owning workflow'],['profile','Profile'],['permission','Permission'],['mutates','Declares a mutation'],['required_tools','Required tools'],['worker_operations','Worker operations'],['studio_read','Available to Studio reads'],['queryable_in_delta','Queryable during work'],['requires_delta','Requires a work boundary']]);break;
    case 'tool':keys(r,[['name','Tool'],['description','Purpose'],['kind','Role'],['version','Version'],['lanes','Source lanes'],['action_classes','Action classes'],['readiness','Readiness'],['execution_state','Execution observation'],['primary','Primary roles'],['fallback','Fallback roles'],['v4_operation_qualification','Operation qualification']]);break;
    case 'workflow':add('Workflow identifier',r.workflow);add('Returned operations',r.actions?.map((a:RecordData)=>a.name));break;
    case 'client':keys(r,[['label','Client'],['client_id','Client ID'],['project_ids','Recorded project references'],['expires_at','Session expiry']]);keys(r.host_observation?.client??{},[['protocol','Protocol'],['configured_profile','Observed host profile']]);break;
    case 'lesson':keys(r,[['version_id','Version ID'],['version','Version'],['state','State']]);keys(r.observation??{},[['summary','Observation']]);keys(r.observation?.scope??{},[['action','Action scope'],['checks','Recorded checks']]);break;
    case 'device':keys(r,[['name','Device'],['device_id','Device ID'],['vendor','Vendor'],['total_vram_mib','Total memory (MiB)'],['used_vram_mib','Used memory (MiB)'],['temperature_c','Temperature (°C)'],['driver_version','Driver'],['observed_at','Observed at']]);break;
    case 'provider':keys(r,[['runtime_id','Provider environment'],['state','Installation observation'],['execution_state','Execution observation']]);break;
    case 'compute-settings':add('Settings revision',r.revision);if(r.config&&typeof r.config==='object')Object.entries(r.config).forEach(([key,value])=>add(displayWords(key),value));else add('Settings',r.config);break;
    case 'project':keys(r,[['project_id','Project reference'],['source_root','Source folder'],['state_root','Project state folder'],['read_only','Read-only connection']]);break;
    case 'memory':keys(r,[['locator_id','Reference'],['source_state','Source state'],['source_client_id','Recorded by'],['suppressed','Suppressed']]);keys(r.locator??{},[['label','Source label'],['reference','Owning source reference']]);break;
    case 'exchange':keys(r,[['exchange_id','Exchange'],['kind','Kind'],['summary','Summary'],['state','Admission state'],['version','Version'],['sender_id','Sender'],['receiver_id','Receiver'],['created_at','Recorded at']]);break;
    case 'event':keys(r,[['event_id','Event'],['kind','Kind'],['provenance','Provenance'],['created_at','Recorded at'],['source_client_id','Source client']]);break;
    case 'grant':keys(r,[['plugin_id','Grant reference'],['name','Name'],['purpose','Purpose'],['active','Active'],['grant_live','Grant live'],['scope','Scope']]);break;
    case 'continuation':keys(r,[['continuation_id','Continuation'],['state','State'],['expired','Expired'],['binding','Recorded binding']]);break;
    case 'reference':Object.entries(r).forEach(([key,value])=>add(displayWords(key),value));break;
    case 'diagnostic-section':{
      if(subject.id==='engine')keys(r,[['version','Engine version'],['phase','Phase'],['previous_shutdown','Previous shutdown']]);
      else if(subject.id==='workers'){keys(r,[['state','Pool state'],['accepting','Accepting work'],['outstanding','Outstanding operations'],['succeeded_operations','Successful operations']]);add('Initialized process records',r.initialized_workers?.length)}
      else if(subject.id==='tool_catalog'){add('Returned tool rows',r.entries?.length);add('Catalogue counts',r.counts)}
      else if(subject.id==='inventory'){add('Returned devices',r.devices?.length);add('Returned provider environments',r.providers?.length)}
      else if(Array.isArray(subject.record))add('Returned records',subject.record.length);
      break;
    }
    case 'query-result':keys(r,[['state','State'],['summary','Summary'],['message','Message'],['total_rows','Total rows'],['truncated','Result truncated'],['error','Returned error']]);if(Array.isArray(r.rows))add('Rows in this result',r.rows.length);break;
  }
  return fields;
}

function Value({value}:{value:unknown}){
  if(value===null)return <span className="subject-unknown">Not supplied</span>;
  if(typeof value==='boolean')return <span>{value?'Yes':'No'}</span>;
  if(Array.isArray(value))return value.length?<ul>{value.map((item,i)=><li key={i}>{typeof item==='object'?JSON.stringify(item):displayWords(item)}</li>)}</ul>:<span className="subject-unknown">None in this record</span>;
  if(value&&typeof value==='object')return <dl className="subject-inline-fields">{Object.entries(value).map(([key,item])=><div key={key}><dt>{displayWords(key)}</dt><dd>{typeof item==='object'?JSON.stringify(item):String(item)}</dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

export function SubjectRecord({subject,compact=false}:{subject:StudioSubject;compact?:boolean}){
  const fields=subjectFields(subject);
  return <div className={`subject-record ${compact?'is-compact':''}`} data-subject-key={subject.key}>{fields.length?<dl className="subject-fields">{fields.map(field=><div key={field.label}><dt>{field.label}</dt><dd><Value value={field.value}/></dd></div>)}</dl>:<p className="subject-unknown">The returned structure is available in Exact record.</p>}<small className="subject-source">Source: {subject.source}</small></div>;
}
