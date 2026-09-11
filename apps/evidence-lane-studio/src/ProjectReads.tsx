import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { RecordData, Snapshot } from './types';
import { Badge, date, Details, Empty, Pill, words } from './ui';
import './ProjectReads.css';

const names: Record<string, string> = { plan_read: 'Plans', memory_read: 'Project Memory',
  lineage_read: 'ChatLineage', learning_read: 'Learning', task_evidence_read: 'Task exchanges', project_status: 'Storage summary' };
const displayName = (source: string) => source.split(/[\\/]/).filter(Boolean).at(-1) ?? source;

function summaries(action: string, result: RecordData): string[] {
  if (action === 'plan_read') return result.tasks.map((item: RecordData) => `${item.definition.title} · ${words(item.state)}`);
  if (action === 'memory_read') return result.locators.map((item: RecordData) => item.locator.label);
  if (action === 'lineage_read') return result.events.map((item: RecordData) => String(item.payload.text ?? words(item.kind)));
  if (action === 'learning_read') return result.lessons.map((item: RecordData) => item.observation.summary);
  if (action === 'task_evidence_read') return result.exchanges.map((item: RecordData) => item.summary);
  return [`${result.object_count ?? 0} addressed objects`, `${result.receipt_count ?? 0} receipts`];
}

export function ProjectReads({ data, connected }: { data: Snapshot; connected: boolean }) {
  const project = data.project!;
  const [selected, setSelected] = useState<string[]>([]);
  const [action, setAction] = useState('plan_read');
  const [search, setSearch] = useState('');
  const [linkedOnly, setLinkedOnly] = useState(false);
  const [result, setResult] = useState<RecordData | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);
  const otherProjects = data.projects.filter(item => item.project_id !== project.project_id);
  const searchable = action === 'memory_read' || action === 'lineage_read';

  async function read() {
    const request = ++sequence.current;
    setBusy(true); setError(''); setResult(null);
    const argumentsValue = action === 'project_status' ? {} : {limit: 5, ...(searchable && search.trim() ? {query: search.trim()} : {})};
    try {
      const value = await api('read', { project_id: project.project_id, action: 'linked_project_evidence_query', arguments: {
        projects: [project.project_id, ...selected].map(project_id => ({ project_id, queries: [{action, arguments: argumentsValue}] })),
        require_links: linkedOnly, timeout_ms: 10000, max_bytes: 131072,
      }});
      if (request === sequence.current) setResult(value);
    } catch (reason) {
      if (request === sequence.current) setError(reason instanceof Error ? reason.message : 'The project read did not complete.');
    } finally {
      if (request === sequence.current) setBusy(false);
    }
  }

  return <section className="studio-cross-project" aria-label="Read across projects"><h2>Read across projects</h2>
    <p className="studio-muted">Include this project and choose up to three others. Results keep their source and remain separate.</p>
    {!!project.linked_projects.links.length && <div className="studio-linked-list" aria-label="Linked projects">{project.linked_projects.links.map(item => <div key={item.target_project_id}><Badge>{item.label}</Badge><small>{displayName(item.target_source_root)} · link v{item.version}</small><Details value={item} label="Recorded project link" /></div>)}</div>}
    {project.linked_projects.truncated && <p className="studio-muted">Showing the first 20 recorded links. Additional selected projects are checked when you read them.</p>}
    {!otherProjects.length ? <Empty title="Add another project to compare" detail="Connect a separate project from its existing state folder, then select it here." /> : <form onSubmit={event => { event.preventDefault(); void read(); }}>
      <fieldset disabled={busy || !connected} className="studio-project-choices"><legend>Additional projects</legend>{otherProjects.map(item => <label key={item.project_id}><input type="checkbox" checked={selected.includes(item.project_id)} disabled={!selected.includes(item.project_id) && selected.length >= 3} onChange={event => {
        setSelected(current => event.target.checked ? [...current, item.project_id] : current.filter(id => id !== item.project_id)); setResult(null);
      }} />{displayName(item.source_root)}<small>{item.source_root}</small></label>)}</fieldset>
      <div className="studio-filters"><label>Read<select value={action} disabled={busy} onChange={event => {setAction(event.target.value); setResult(null);}}>{data.actions.filter(item => item.cross_project_read).map(item => <option key={item.name} value={item.name}>{names[item.name] ?? words(item.name)}</option>)}</select></label>
        {searchable && <label className="studio-search-label">Search<input type="search" maxLength={500} value={search} disabled={busy} onChange={event => {setSearch(event.target.value); setResult(null);}} placeholder="Optional search terms" /></label>}
        <label className="studio-checkbox-label"><input type="checkbox" checked={linkedOnly} disabled={busy} onChange={event => {setLinkedOnly(event.target.checked); setResult(null);}} />Require recorded links</label>
        <Pill type="submit" disabled={busy || !connected || !selected.length}>{busy ? 'Reading…' : 'Read selected projects'}</Pill>
      </div>
    </form>}
    {error && <p role="alert" className="studio-error">{error}</p>}
    <div aria-live="polite" aria-busy={busy}>{result && <><p className="studio-muted">Observed {date(result.observed_at)}. Each result is a separate project snapshot.</p><div className="studio-project-grid">{result.projects.map((item: RecordData) => <article className="studio-observation" key={item.project_id}>
      <div className="studio-card-top"><h3>{displayName(item.source_root)}</h3><Badge>Read only</Badge></div><p className="studio-muted">{item.source_root}</p>
      {item.queries.map((view: RecordData) => <div key={view.action}><h4>{names[view.action] ?? words(view.action)}</h4>{summaries(view.action, view.result).length ? <ul className="studio-result-summaries">{summaries(view.action, view.result).map((text, index) => <li key={index}>{text}</li>)}</ul> : <p>No matching records.</p>}{view.result.truncated && <p className="studio-muted">Showing a bounded page. Open this project for more.</p>}</div>)}
      <Details value={item} label="Source, schema and result evidence" />
    </article>)}</div></>}</div>
  </section>;
}
