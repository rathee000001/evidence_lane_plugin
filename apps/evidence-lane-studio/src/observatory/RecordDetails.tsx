import {useState} from 'react';
import {SubjectRecord} from './SubjectRecord';
import {subject as makeSubject,type StudioSubject} from './subjects';
import {TaskContract} from './TaskContract';
import type {Task} from '../types';

export function RecordDetails({value,subject}:{value:unknown;subject?:StudioSubject}){
 const [raw,setRaw]=useState(false);
 const presented=subject??makeSubject('query-result',value,'Returned record','Selected read result');
 return <><div className="observer-record-mode" role="group" aria-label="Record presentation"><button aria-pressed={!raw} onClick={()=>setRaw(false)}>Record overview</button><button aria-pressed={raw} onClick={()=>setRaw(true)}>Exact record</button></div>{raw?<pre>{JSON.stringify(value,null,2)??'No value was provided.'}</pre>:presented.kind==='task'&&(presented.record as Task)?.definition?<TaskContract task={presented.record as Task}/>:<SubjectRecord subject={presented}/>}</>;
}
