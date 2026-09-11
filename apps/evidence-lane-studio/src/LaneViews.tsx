import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { RecordData, Snapshot } from './types';
import { Badge, date, Details, Empty, Metric, Pill, words } from './ui';

const labels: Record<string,string> = {'plan.dependencies':'Plan dependencies','chat_lineage.ancestry':'Conversation ancestry',
  'memory.links':'Memory links','learning.provenance':'Learning provenance','canon.consequences':'task exchange authority consequences'};

export function LaneViews({data, connected}: {data: Snapshot; connected: boolean}) {
  const [viewId, setViewId] = useState('plan.dependencies');
  const [history, setHistory] = useState(false);
  const [search, setSearch] = useState('');
  const [preview, setPreview] = useState<RecordData | null>(null);
  const [saved, setSaved] = useState<RecordData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  useEffect(() => () => {sequence.current++;}, []);
  const projectId = data.project!.project_id;
  const spec = data.lane_views.find(item => item.view_id === viewId);

  async function run(operation: 'preview' | 'inspect') {
    const current = ++sequence.current;
    setBusy(true); setError('');
    try {
      if (operation === 'preview') {
        const value = await api('read', {project_id:projectId, action:'lane_view_preview', arguments:{view_id:viewId,
          scope:{node_limit:60,edge_limit:100,query:search.trim() || null,include_history:history}}});
        if (current === sequence.current) {setPreview(value); setSaved(null);}
      } else {
        const value = await api('read', {project_id:projectId,action:'lane_view_read',arguments:{view_id:viewId}});
        if (current === sequence.current) {setSaved(value); setPreview(null);}
      }
    } catch (reason) {
      if (current === sequence.current) setError(reason instanceof Error ? reason.message : 'The lane view was not confirmed.');
    } finally {if (current === sequence.current) setBusy(false);}
  }

  async function download(role: string, filename: string) {
    setBusy(true); setError('');
    try {
      const value = await api('read', {project_id:projectId,action:'lane_view_read',arguments:{view_id:viewId,snapshot_digest:saved!.snapshot_digest,include_content:true,max_bytes:262144}});
      const url = URL.createObjectURL(new Blob([value.contents[role]], {type:'text/plain;charset=utf-8'}));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {setError(reason instanceof Error ? reason.message : 'The saved file could not be verified.');}
    finally {setBusy(false);}
  }

  if (!spec) return <Empty title="Lane views unavailable" detail="Refresh Studio to read the installed view contracts." />;
  const graph = preview?.graph ?? saved?.manifest?.graph;
  return <section aria-label="Lane artifact views"><h2>Lane views</h2><p className="studio-muted">Preview current records and inspect or download already saved views. Create or refresh view files through your connected Codex plugin.</p>
    <form className="studio-filters" onSubmit={event => {event.preventDefault(); void run('preview');}}>
      <label>Relationship view<select value={viewId} disabled={busy} onChange={event => {setViewId(event.target.value); setPreview(null); setSaved(null); setHistory(false); setSearch(''); setError('');}}>{data.lane_views.map(item => <option key={item.view_id} value={item.view_id}>{labels[item.view_id] ?? words(item.view_id)}</option>)}</select></label>
      {spec.scope.query && <label className="studio-search-label">Search<input type="search" value={search} maxLength={500} disabled={busy} onChange={event => {setSearch(event.target.value); setPreview(null);}} /></label>}
      {spec.scope.include_history && <label>Scope<select value={history ? 'history':'current'} disabled={busy} onChange={event => {setHistory(event.target.value === 'history'); setPreview(null);}}><option value="current">Current records</option><option value="history">Include history</option></select></label>}
      <Pill type="submit" disabled={busy || !connected}>Preview records</Pill><Pill disabled={busy || !connected} onClick={() => void run('inspect')}>Inspect saved view</Pill>
    </form><p className="studio-muted">{spec.meaning}</p>
    {error && <p role="alert" className="studio-error">{error}</p>}
    <div aria-live="polite" aria-busy={busy}>
      {saved && <p><Badge tone={saved.state === 'fresh' ? 'success':'warning'}>{words(saved.state)} at last check</Badge> {saved.generation > 0 && `Generation ${saved.generation}`}<span className="studio-muted"> · Checked {date(saved.checked_at)}</span></p>}
      {graph && <><div className="studio-metrics"><Metric icon="evidence" label="Records in view" value={graph.nodes.length} /><Metric icon="connections" label="Relationships" value={graph.edges.length} /><Metric icon="plan" label="Coverage" value={graph.truncated ? 'Partial':'Selected scope'} /></div>
        {graph.nodes.length ? <Details value={graph} label="Records and typed relationships" /> : <p>No matching records.</p>}</>}
      {saved?.manifest && <><div className="studio-inline-tabs">{saved.manifest.files.map((item: RecordData) => <Pill key={item.role} disabled={busy || !connected} onClick={() => void download(item.role,item.filename)}>Download {item.filename}</Pill>)}</div><Details value={saved.manifest.binding} label="Source revision and snapshot binding" /></>}
    </div>
  </section>;
}
