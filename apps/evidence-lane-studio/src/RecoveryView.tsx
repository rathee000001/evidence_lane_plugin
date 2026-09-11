import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { RecordData, Snapshot } from './types';
import { Details, Empty, Metric, Pill } from './ui';

export function RecoveryView({data, connected}: {data: Snapshot; connected: boolean}) {
  const [inspection, setInspection] = useState<RecordData | null>(null);
  const [history, setHistory] = useState<RecordData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);
  const projectId = data.project?.project_id;

  async function inspect() {
    if (!projectId) return;
    const current = ++sequence.current;
    setBusy(true); setError('');
    try {
      const [files, source] = await Promise.all([
        api<RecordData>('read', {project_id:projectId,action:'project_recovery_inspect',arguments:{sample_limit:20}}),
        api<RecordData>('read', {project_id:projectId,action:'restoration_read',arguments:{limit:10}}),
      ]);
      if (current === sequence.current) { setInspection(files); setHistory(source); }
    } catch (reason) {
      if (current === sequence.current) setError(reason instanceof Error ? reason.message : 'The recovery records could not be read.');
    } finally {
      if (current === sequence.current) setBusy(false);
    }
  }

  if (!projectId) return <Empty title="Select a project" detail="Choose a project to inspect its recovery records." />;
  const recorded = data.project?.recovery;
  return <section aria-label="Project recovery observations">
    <div className="studio-section-heading"><div><h2>Project recovery</h2><p>Inspect saved project and source-recovery records. Create backups, restore source, or reconcile attempts through your connected Codex plugin.</p></div>
      <Pill disabled={busy || !connected} onClick={() => void inspect()}>{busy ? 'Inspecting…' : 'Inspect project files'}</Pill></div>
    {error && <p className="studio-notice error" role="alert">{error}</p>}
    {recorded && <Details value={recorded} label="Current recovery and Plan boundary" />}
    {inspection && <><div className="studio-metrics">
      <Metric label="Registered files" value={inspection.registered_file_count} icon="evidence" />
      <Metric label="Registered content" value={inspection.registered_bytes} icon="projects" />
      <Metric label="Unregistered files" value={inspection.unregistered_file_count} icon="diagnostics" detail="Reported; no files removed" />
    </div><Details value={inspection} label="File inspection record" /></>}
    {history && <Details value={history} label="Source restoration history and Plan boundary" />}
    {!inspection && !history && <p className="studio-muted">No recovery operation is available in Studio. This panel reads recorded state only.</p>}
  </section>;
}
