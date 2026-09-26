import {useWorkspaceSelection} from './WorkspaceSelection';
import {SubjectRecord} from './SubjectRecord';

export function ViewRecordPanel(){
 const workspace=useWorkspaceSelection();if(!workspace)return null;
 const selected=workspace.selected;
 const choices=selected&&!workspace.subjects.some(s=>s.key===selected.key)?[selected,...workspace.subjects]:workspace.subjects;
 return <section className="observatory-record-context" data-selected-subject={selected?.key??''}><h3>{selected?.title??'Select a record'}</h3>{choices.length>0&&<label>Record<select aria-label="Selected record" value={selected?.key??''} onChange={e=>{const found=choices.find(s=>s.key===e.target.value);if(found)workspace.select(found)}}><option value="" disabled>Choose a record</option>{choices.map(s=><option key={s.key} value={s.key}>{s.title}</option>)}</select></label>}{selected?<><SubjectRecord subject={selected} compact/><button className="studio-details-button" data-subject-key={selected.key} aria-haspopup="dialog" onClick={()=>workspace.open(selected)}>Open record inspector <span aria-hidden="true">↗</span></button></>:<p>Select a table row, card or scene subject to inspect the same returned record here.</p>}</section>;
}
