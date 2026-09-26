import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api, isDesignPreview } from './api';
import { ProjectReads } from './ProjectReads';
import { LaneViews } from './LaneViews';
import { RecoveryView } from './RecoveryView';
import {useWorkspaceSelection} from './observatory/WorkspaceSelection';
import {subject as makeSubject} from './observatory/subjects';
import {ProjectCover,projectAccent} from './observatory/ProjectCover';
import type {CSSProperties} from 'react';
import { SnapshotSections } from './observatory/SnapshotSections';
import type { Project, RecordData, Snapshot, Task } from './types';
import { Badge, bytes, date, Details, Empty, Icon, Metric, Pill, short, Table, words } from './ui';

export const projectName = (project: Project) => project.source_root.split(/[\\/]/).filter(Boolean).at(-1) ?? project.project_id;
export type ViewActions = { selectProject: (id: string) => void; };
const needsProject = <Empty title="Select a project" detail="Choose a project in the side rail to see its work and evidence." />;

export function ProjectsView({ data, actions }: { data: Snapshot; actions: ViewActions }) {
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">YOUR WORKSPACE</span><h1>Projects</h1><p>Source, work, and evidence. Project registration stays with your connected Codex plugin.</p></div></div>
    {!data.projects.length ? <Empty title="No registered projects" detail="Use your connected Codex plugin to register a source folder and its separate project-state folder." /> : <div className="studio-project-grid">{data.projects.map(project => <article className="studio-project-card" key={project.project_id} style={{'--project-accent':projectAccent(project)} as CSSProperties}><ProjectCover project={project}/>
      <div className="studio-card-top"><Badge>{isDesignPreview()?'Preview project':project.read_only ? 'Read only connection' : 'Connected'}</Badge></div>
      <h2>{projectName(project)}</h2><div className="studio-project-path"><span>Source folder</span><code>{project.source_root}</code></div><div className="studio-project-path"><span>Project state</span><code>{project.state_root}</code></div>
      <Pill onClick={() => actions.selectProject(project.project_id)} icon="plan">Open project</Pill>
    </article>)}</div>}
    <div className="studio-bottom-note"><span className="studio-live-dot" /> Your engine stays available when this window is closed.</div></>;
}

export {PlanView} from './observatory/PlanWorkspace';

export function JobsView({ data }: { data: Snapshot }) {
  if (!data.project) return needsProject;
  const { jobs, control } = data.project;
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">PROJECT EXECUTION</span><h1>Jobs & checkpoints</h1></div>{control.paused && <Badge tone="warning">Project paused</Badge>}</div>
    <div className="studio-metrics"><Metric icon="jobs" label="In progress" value={(jobs.counts.running ?? 0) + (jobs.counts.queued ?? 0)} detail="Running and queued" /><Metric icon="plan" label="Checkpointed" value={jobs.counts.checkpointed ?? 0} detail="Saved at a work boundary" /><Metric icon="diagnostics" label="Needs attention" value={(jobs.counts.failed ?? 0) + (jobs.counts.uncertain ?? 0)} detail="Review recorded outcomes" /></div>
    {jobs.recent.length ? <Table columns={['Operation', 'Phase', 'State', 'Updated', 'Record']} label="Project jobs">{jobs.recent.map(job => <tr key={job.job_id}><td><strong>{words(job.action)}</strong><small>{short(job.job_id)}</small></td><td>{words(job.phase)}</td><td><Badge tone={job.state === 'succeeded' ? 'success' : job.state === 'uncertain'||job.state==='failed' ? 'warning' : job.state==='running'?'active':''}>{words(job.state)}</Badge></td><td>{date(job.updated_at)}</td><td><Details kind="job" value={job} label="Inspect" title={`Job observation — ${words(job.action)}`} /></td></tr>)}</Table> : <Empty icon="jobs" title="No jobs recorded" detail="Project operations appear here when the engine accepts them." />}</>;
}

export function WorkersView({ data }: { data: Snapshot }) {
  const workers = data.workers;
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">LOCAL EXECUTION</span><h1>OS workers</h1><p>Owned processes that carry out tool operations.</p></div><Badge tone={workers.accepting ? 'success' : 'warning'}>{workers.accepting ? 'Accepting work' : words(workers.state)}</Badge></div>
    <div className="studio-metrics"><Metric icon="workers" label="Initialized processes" value={workers.initialized_workers?.length ?? 0} detail="Returned pool process records" /><Metric icon="jobs" label="Outstanding operations" value={workers.outstanding ?? 0} detail="Submitted and not yet finished" /><Metric icon="evidence" label="Successful operations" value={workers.succeeded_operations ?? 0} detail="Returned a successful worker result" /></div>
    <div className="studio-worker-grid">{(workers.initialized_workers ?? []).map((worker: RecordData, index: number) => <article className="studio-worker-card" key={worker.pid}><Icon name="workers" size={46} /><div><h2>Worker {index + 1}</h2><code>PID {worker.pid}</code>{worker.startup!==undefined&&<p>Startup: {words(worker.startup)}</p>}</div><Badge>{words(worker.state)}</Badge><Details kind="worker" value={worker} label="Inspect process" title={`Worker process — PID ${worker.pid}`} /></article>)}</div>
    {!workers.initialized_workers?.length && <Empty icon="workers" title="No initialized processes observed" detail="A process appears after the engine records its startup acknowledgement." />}
    {data.active_executions.length > 0 && <><h2 className="studio-subheading">Owned jobs</h2><Table columns={['Task', 'Revision', 'Pending operations']} label="Active executions">{data.active_executions.map(job => <tr key={job.job_id}><td>{job.task_id ?? short(job.job_id)}</td><td>{job.plan_revision ?? '—'}</td><td>{job.worker_operations_pending}</td></tr>)}</Table></>}
    <Details kind="worker-pool" value={workers} label="Worker pool record" /></>;
}

export function EvidenceView({ data, connected }: { data: Snapshot; connected: boolean }) {
  const workspace=useWorkspaceSelection();const tab=workspace?.section??'receipts';const setTab=(next:string)=>workspace?.setSection(next);
  if (!data.project) return needsProject;
  const evidence = data.project.evidence, lineage = data.project.lineage;
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">PROJECT HISTORY</span><h1>Evidence</h1></div></div><div className="studio-metrics"><Metric label="Addressed objects" value={evidence.object_count} icon="evidence" /><Metric label="Stored content" value={bytes(evidence.total_bytes)} icon="projects" /><Metric label="Visible conversation events" value={lineage?.total_events ?? 0} icon="connections" /></div>
    <div className="studio-inline-tabs"><Pill active={tab === 'receipts'} onClick={() => setTab('receipts')}>Receipts</Pill><Pill active={tab === 'lineage'} onClick={() => setTab('lineage')}>ChatLineage</Pill><Pill active={tab === 'memory'} onClick={() => setTab('memory')}>Project Memory</Pill><Pill active={tab === 'canon'} onClick={() => setTab('canon')}>Task exchanges</Pill><Pill active={tab === 'projects'} onClick={() => setTab('projects')}>Project reads</Pill><Pill active={tab === 'views'} onClick={() => setTab('views')}>Lane views</Pill></div>
    {tab === 'views' && <LaneViews key={data.project.project_id} data={data} connected={connected} />}
    {tab === 'projects' && <ProjectReads key={data.project.project_id} data={data} connected={connected} />}
    {tab === 'projects' || tab === 'views' ? null : tab === 'canon' ? <CanonView key={data.project.project_id} project={data.project} /> : tab === 'memory' ? <MemoryView key={data.project.project_id} project={data.project} /> : tab === 'receipts' ? (evidence.receipts.length ? <Table columns={['Recorded change', 'When', 'Reference']} label="Recent receipts">{evidence.receipts.map(receipt => <tr key={receipt.receipt_id}><td>{words(receipt.kind)}</td><td>{date(receipt.created_at)}</td><td><code>{receipt.receipt_id}</code><Details kind="receipt" value={receipt} label={`Receipt details — ${words(receipt.kind)}`} /></td></tr>)}</Table> : <Empty icon="evidence" title="No receipts yet" detail="Verified changes and recorded outcomes will leave a receipt here." />) : lineage?.events.length ? <div className="studio-stack">{lineage.events.map(event => <article className="studio-observation" key={event.event_id}><div className="studio-card-top"><strong>{words(event.kind)}</strong><Badge>{words(event.provenance)}</Badge></div><Details kind="event" value={event} label="Visible event and source" /></article>)}</div> : <Empty icon="connections" title="No visible events captured" detail="Conversation events appear after a connected client binds capture for this project." />}</>;
}

function CanonView({ project }: { project: NonNullable<Snapshot['project']> }) {
  const [loaded, setLoaded] = useState<typeof project.canon | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const top = useRef<HTMLElement>(null);
  useEffect(() => () => { sequence.current++; }, []);
  useLayoutEffect(() => { top.current?.closest('.studio-scroll-panel')?.scrollTo({top: 0, left: 0}); }, [loaded?.last_sequence]);
  const canon = loaded ?? project.canon;
  async function page(after: number) {
    const request = ++sequence.current;
    setBusy(true); setError('');
    try {
      const result = await api<typeof project.canon>('read', {project_id: project.project_id, action: 'task_evidence_read', arguments: {after_sequence: after, limit: 20}});
      if (request === sequence.current) setLoaded(result);
    } catch (reason) { if (request === sequence.current) setError(reason instanceof Error ? reason.message : 'Unable to read the next exchanges.'); }
    finally { if (request === sequence.current) setBusy(false); }
  }
  const names = new Map(canon?.participants.map(item => [item.participant_id, item.label]) ?? []);
  return <section ref={top} aria-label="Task exchanges"><h2>Task exchanges</h2><p className="studio-muted">Inspect the sender, receiver, and admission state of each recorded input.</p>
    {canon?.exchanges.length ? <div className="studio-stack">{canon.exchanges.map(item => <article className="studio-observation" key={item.exchange_id}>
      <div className="studio-card-top"><h3>{words(item.kind)}</h3><Badge tone={item.state === 'admitted' ? 'success' : item.state === 'needs_clarification' ? 'warning' : ''}>{words(item.state)} · v{item.version}</Badge></div>
      <p>{item.summary}</p><p className="studio-muted">{names.get(item.sender_id) ?? short(item.sender_id)} → {names.get(item.receiver_id) ?? short(item.receiver_id)} · {date(item.created_at)}</p>
      <Details kind="exchange" value={item} label="Exchange identity and source" /></article>)}</div> : <Empty icon="connections" title="No task exchanges yet" detail="Connected tasks can exchange bounded requirements, evidence, corrections, and results here." />}
    {error && <p role="alert" className="studio-error">{error}</p>}
    <div className="studio-inline-tabs">{loaded && <Pill disabled={busy} onClick={() => void page(0)}>First exchanges</Pill>}{canon?.truncated && <Pill disabled={busy} onClick={() => void page(canon.last_sequence)}>More exchanges</Pill>}</div>
    {!!canon?.contracts.length && <><h3 className="studio-subheading">Expected inputs</h3>{canon.contracts.map(item => <article className="studio-observation" key={item.contract_digest}>
      <div className="studio-card-top"><strong>{names.get(item.contract.receiver_id) ?? short(item.contract.receiver_id)} · {words(item.contract.contract_key)}</strong><Badge>v{item.version}</Badge></div>
      <p>{item.contract.active ? item.contract.auto_admit ? 'Matching inputs are admitted automatically.' : 'The receiver reviews matching inputs.' : 'This contract is inactive.'}</p>
      <Details value={item} label="Expected fields and source restrictions" /></article>)}</>}
    {(canon?.participants_truncated || canon?.contracts_truncated) && <p className="studio-muted">This view contains a bounded participant and contract slice.</p>}
    {!!project.continuity?.offers.length && <><h3 className="studio-subheading">Project continuations</h3>{project.continuity.offers.map(item => <article className="studio-observation" key={item.continuation_id}>
      <div className="studio-card-top"><strong>{item.binding.task.key} · Plan revision {item.binding.task.revision}</strong><Badge tone={item.state === 'accepted' ? 'success' : ''}>{words(item.state)}{item.expired && item.state === 'offered' ? ' · expired' : ''}</Badge></div>
      <p>{item.state === 'accepted' ? 'Project participants transferred. The source client has read-only closeout access.' : item.state === 'cancelled' ? 'The source client cancelled this continuation.' : item.expired ? 'This continuation offer expired.' : 'Waiting for the registered destination client.'}</p>
      <p className="studio-muted">This record confirms project client ownership. Native host task identity is not verified.</p>
      <Details kind="continuation" value={item} label="Continuation ownership and context" /></article>)}</>}
  </section>;
}

function MemoryView({ project }: { project: NonNullable<Snapshot['project']> }) {
  const workspace=useWorkspaceSelection();
  const [draft, setDraft] = useState('');
  const [filters, setFilters] = useState<{query: string; history: boolean; request: number} | null>(null);
  const [result, setResult] = useState<typeof project.memory | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!filters) return;
    let cancelled = false;
    setLoading(true); setError('');
    api<typeof project.memory>('read', {project_id: project.project_id, action: 'memory_read', arguments: {
      query: filters.query.trim() || null, include_history: filters.history, limit: 20,
    }}).then(value => { if (!cancelled) setResult(value); }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : 'The search could not be completed.');
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [filters, project.project_id]);
  const page = result ?? project.memory;
  useEffect(()=>{workspace?.publish((page?.locators??[]).map(item=>makeSubject('memory',item,item.locator.label,'memory_read references')))},[page,workspace?.publish]);
  return <section aria-label="Project Memory references"><h2>Attributed references</h2><p className="studio-muted">Find recorded source links. Each result keeps its original owner and revision.</p>
    <form className="studio-filters" onSubmit={event => { event.preventDefault(); setFilters(current => ({query: draft, history: current?.history ?? false, request: (current?.request ?? 0) + 1})); }}>
      <label className="studio-search-label">Search Project Memory<input type="search" maxLength={500} value={draft} onChange={event => setDraft(event.target.value)} placeholder="A task, source, or recorded result…" /></label>
      <label>Reference scope<select value={filters?.history ? 'history' : 'current'} onChange={event => setFilters(current => ({query: draft, history: event.target.value === 'history', request: (current?.request ?? 0) + 1}))}><option value="current">Current references</option><option value="history">Include history</option></select></label>
      <Pill type="submit" disabled={loading}>{loading ? 'Searching…' : 'Search references'}</Pill>
    </form>
    {error && <p role="alert" className="studio-error">{error}</p>}
    <div aria-live="polite" aria-busy={loading}>{page?.locators.length ? <div className="studio-stack">{page.locators.map(item => <article className="studio-observation" key={item.locator_id}>
      <div className="studio-card-top"><h3>{item.locator.label}</h3><Badge>{item.suppressed ? 'Suppressed in Memory' : words(item.source_state)}</Badge></div>
      <p>{words(item.locator.reference.kind)}{item.locator.reference.revision ? ` · revision ${item.locator.reference.revision}` : ''}</p>
      <p className="studio-muted">Recorded by {item.source_client_id}</p><Details kind="memory" value={item} label="Source identity and attributed links" />
    </article>)}</div> : <Empty icon="evidence" title="No matching references" detail="References appear when connected workflows index their source records. Try another search or include history." />}
    {page?.truncated && <p className="studio-muted">Showing a bounded slice. Narrow the search to inspect more references.</p>}</div>
  </section>;
}

export function ToolsView({ data }: { data: Snapshot }) {
  const workspace=useWorkspaceSelection();const section=workspace?.section??'tools';const setSection=(next:string)=>workspace?.setSection(next);
  const [filters, setFilters] = useState({search: '', lane: '', action: '', kind: '', layer: '', lifecycle: 'retained'});
  const [page, setPage] = useState(0);
  const [operationSearch, setOperationSearch] = useState('');
  const [operationPage, setOperationPage] = useState(0);
  const catalog = data.tool_catalog;
  const all:RecordData[]=catalog?.entries??[];
  const filtered = useMemo(() => all.filter(tool => (!filters.search || `${tool.name} ${tool.tool_id} ${tool.description}`.toLowerCase().includes(filters.search.toLowerCase())) && (!filters.lane || tool.lanes.includes(filters.lane)) && (!filters.action || tool.action_classes.includes(filters.action)) && (!filters.kind || tool.kind === filters.kind) && (!filters.layer || tool.layer === filters.layer) && (!filters.lifecycle || tool.lifecycle === filters.lifecycle)), [all, filters]);
  const operations = useMemo(() => data.actions.filter(operation => !operationSearch || `${operation.name} ${operation.description} ${operation.workflow} ${operation.profile}`.toLowerCase().includes(operationSearch.toLowerCase())), [data.actions, operationSearch]);
  useEffect(()=>{if(section==='tools')workspace?.publish(filtered.slice(page*25,page*25+25).map(tool=>makeSubject('tool',tool,tool.name??tool.tool_id,'filtered tool_catalog.entries')));else if(section==='operations')workspace?.publish(operations.slice(operationPage*25,operationPage*25+25).map(operation=>makeSubject('action',operation,words(operation.name),'filtered actions')))},[section,filtered,page,operations,operationPage,workspace?.publish]);
  function filter(key: keyof typeof filters, value: string) { setFilters(current => ({...current, [key]: value})); setPage(0); }
  const selectFilter = (key: keyof typeof filters, label: string, values: string[]) => <label>{label}<select value={filters[key]} onChange={event => filter(key, event.target.value)}><option value="">All</option>{values.map(value => <option value={value} key={value}>{words(value)}</option>)}</select></label>;
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">TOOLS & ROUTING</span><h1>Toolchains</h1><p>Inspect each tool’s role, lane, and primary or fallback route.</p></div></div>
    <div className="observatory-inner-tabs" role="group" aria-label="Toolchain views">{(['tools','workflows','operations','grants'] as const).map(key=><button key={key} aria-pressed={section===key} onClick={()=>setSection(key)}>{key==='tools'?'Tools':key==='workflows'?'Workflows':key==='operations'?'Operations':'Project grants'}</button>)}</div>
    {section==='tools'&&<>
    <div className="observatory-catalogue-summary"><span><strong>{all.length}</strong> tools</span><span><strong>{all.filter(tool=>tool.lifecycle==='retained').length}</strong> retained</span><span><strong>{data.actions.length}</strong> operations</span><span>Office: Word, PowerPoint, Excel</span></div>
    <p className="studio-info">Office coverage is Word, PowerPoint, and Excel. OneDrive is storage. Excluded Office rows remain outside this catalog.</p>

    <p className="studio-info">Catalog membership, package availability, and verified execution are separate states.</p>
    <div className="studio-filters" data-tool-filters><label className="studio-search-label">Search tools<input type="search" placeholder="Git, FastMCP, OCR…" value={filters.search} onChange={event => filter('search', event.target.value)} /></label>
      {selectFilter('lane', 'Lane', [...new Set<string>(all.flatMap(tool => tool.lanes))].sort())}{selectFilter('action', 'Action class', [...new Set<string>(all.flatMap(tool => tool.action_classes))].sort())}
      {selectFilter('kind', 'Tool role', [...new Set<string>(all.map(tool => tool.kind))].sort())}{selectFilter('layer', 'Layer', [...new Set<string>(all.map(tool => tool.layer))].sort())}{selectFilter('lifecycle', 'Catalog scope', ['retained', 'retired'])}</div>
    <div className="studio-section-heading compact"><h2>Lane and action routes</h2><span id="tool-result-count">{filtered.length} matching entries</span></div>
    {!filtered.length ? <Empty icon="tools" title="No tools match" detail="Adjust the search or filters to see another route." /> : <Table columns={['Tool & role', 'Lane & route', 'Readiness', 'Execution']} label="Tool catalog">{filtered.slice(page * 25, page * 25 + 25).map(tool => <tr key={tool.tool_id}><td><strong>{tool.name}</strong><small>{words(tool.kind)}{tool.version ? ` · ${tool.version}` : ''}</small><Details kind="tool" value={tool} label="Inspect tool" title={`Role details — ${tool.name}`} /></td><td><p>{tool.lanes.map(words).join(', ') || 'Shared'}</p><Details kind="tool" value={tool} label="Inspect route" title={`Primary / fallback — ${tool.name}`} /></td><td><Badge tone={['package_not_detected', 'connection_unverified'].includes(tool.readiness) ? 'warning' : ''}>{words(tool.readiness)}</Badge></td><td><Badge tone={tool.execution_state === 'verified' ? 'success' : ''}>{words(tool.execution_state)}</Badge><small>Operation checks: {words(tool.v4_operation_qualification)}</small></td></tr>)}</Table>}
    {filtered.length > 25 && <div className="studio-pagination"><span>{page * 25 + 1}–{Math.min((page + 1) * 25, filtered.length)} of {filtered.length}</span><Pill disabled={!page} onClick={() => setPage(page - 1)}>Previous</Pill><Pill disabled={(page + 1) * 25 >= filtered.length} onClick={() => setPage(page + 1)}>Next</Pill></div>}
    </>}
    {section==='workflows'&&<><p className="studio-info">Workflow groups are derived from the operations returned by the engine.</p><div className="observatory-workflow-grid">{(workspace?.subjects??[]).map(item=><article className="studio-observation" key={item.key}><h2>{item.title}</h2><p>{(item.record as RecordData).actions.length} returned operations</p><Details value={item.record} recordSubject={item} label="Inspect workflow" title={item.title}/></article>)}</div></>}
    {section==='operations'&&<>
    <div className="studio-section-heading"><div><span className="studio-eyebrow">CURRENT ENGINE</span><h2>Operations</h2><p>Every registered plugin operation, its workflow and the authority required to run it.</p></div><Badge>{data.actions.length} registered</Badge></div>
    <div className="studio-filters"><label className="studio-search-label">Search operations<input type="search" placeholder="plan, source, GitHub, observability…" value={operationSearch} onChange={event => {setOperationSearch(event.target.value); setOperationPage(0);}} /></label></div>
    {!operations.length ? <Empty icon="tools" title="No operations match" detail="Adjust the operation search." /> : <Table columns={['Operation', 'Workflow', 'Permission', 'Execution contract']} label="Engine operations">{operations.slice(operationPage * 25, operationPage * 25 + 25).map(operation => <tr key={operation.name}><td><strong>{words(operation.name)}</strong><small>{operation.name}</small><p>{operation.description}</p></td><td><strong>{words(operation.workflow)}</strong><small>{words(operation.profile)} profile</small></td><td><Badge tone={operation.permission === 'read' ? '' : 'warning'}>{words(operation.permission)}</Badge><small>{operation.mutates ? 'Changes recorded state or an external service' : 'No mutation declared'}</small></td><td><Details kind="action" value={operation} label="Inspect contract" title={`Execution requirements — ${operation.name}`} /></td></tr>)}</Table>}
    {operations.length > 25 && <div className="studio-pagination"><span>{operationPage * 25 + 1}–{Math.min((operationPage + 1) * 25, operations.length)} of {operations.length}</span><Pill disabled={!operationPage} onClick={() => setOperationPage(operationPage - 1)}>Previous</Pill><Pill disabled={(operationPage + 1) * 25 >= operations.length} onClick={() => setOperationPage(operationPage + 1)}>Next</Pill></div>}
    </>}
    {section==='grants'&&<>
    <div className="studio-section-heading"><div><span className="studio-eyebrow">OPTIONAL EXTENSIONS</span><h2>Project grants</h2><p>Recorded connector and toolchain access. Configure or revoke grants through your connected Codex plugin.</p></div></div>
    {!data.project ? <p>Select a project to inspect its grants.</p> : !data.project.plugins.length ? <p className="studio-info">No additional plugin grants are recorded.</p> : <div className="studio-stack">{data.project.plugins.map(plugin => <article className="studio-observation" key={plugin.plugin_id}><div className="studio-card-top"><h3>{plugin.name}</h3><Badge>{!plugin.active ? 'Revoked' : plugin.grant_live ? 'Active grant' : 'Expired'}</Badge></div><p>{plugin.purpose}</p><Details kind="grant" value={plugin} label="Grant scope" /></article>)}</div>}</>}</>;
}

export function ConnectionsView({ data }: { data: Snapshot }) {
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">HOST CLIENTS</span><h1>Connections</h1><p>Client sessions connected to your engine.</p></div><Badge>{data.connections.length} connected</Badge></div>
    {data.connections.length ? <div className="studio-stack">{data.connections.map(client => <article className="studio-observation" key={client.client_id}><div className="studio-card-top"><h2>{client.label}</h2><Badge>{words(client.host_observation?.client?.protocol)}</Badge></div><dl className="studio-definition-list"><dt>Host profile</dt><dd>{words(client.host_observation?.client?.configured_profile)}</dd><dt>Projects</dt><dd>{client.project_ids.map((id: string) => data.projects.find(project => project.project_id === id)).map((project: Project | undefined) => project ? projectName(project) : 'Unknown project').join(', ') || 'None selected'}</dd><dt>Session expiry</dt><dd>{date(client.expires_at)}</dd></dl><Details kind="client" value={client} label={`Connection observations — ${client.label}`} /></article>)}</div> : <Empty icon="connections" title="No MCP clients connected" detail="Your agent’s session appears here after connecting to this engine. Studio uses its separate local owner connection." />}</>;
}

export function LearningView({ data }: { data: Snapshot }) {
  if (!data.project) return needsProject;
  const lessons = data.project.learning?.lessons ?? [];
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">PROJECT LEARNING</span><h1>Verified observations</h1><p>What passed, in which scope, with the supporting evidence.</p></div><Badge>{lessons.filter(lesson => lesson.state === 'active').length} active in this page</Badge></div>
    {!lessons.length ? <Empty icon="learning" title="Learning follows verified work" detail="Scoped observations are recorded automatically when a Delta passes its checks." /> : <Table columns={['Action & version', 'State', 'Observation', 'Recorded checks']} label="Project learning observations">{lessons.map(lesson => <tr key={lesson.version_id}><td><strong>{words(lesson.observation.scope.action)}</strong><small>Version {lesson.version}</small><Details kind="lesson" value={lesson} label="Scope and evidence" title={`Scope and evidence — ${words(lesson.observation.scope.action)} · v${lesson.version}`} /></td><td><Badge tone={lesson.state === 'active' ? 'success' : ''}>{words(lesson.state)}</Badge></td><td>{lesson.observation.summary}</td><td><ul className="observer-check-list">{lesson.observation.scope.checks.map((check:string,i:number)=><li key={i}>{words(check)}</li>)}</ul></td></tr>)}</Table>}
    {data.project.learning?.truncated && <p>Showing the most recent 20 observations. Use a scoped query for older entries.</p>}</>;
}

export function ComputeView({ data }: { data: Snapshot }) {
  const devices: RecordData[] = data.inventory.devices ?? [], providers: RecordData[] = data.inventory.providers ?? [];
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">COMPUTE</span><h1>CPU & GPU</h1><p>Measured devices and the project’s compute preference. Change settings through your connected Codex plugin.</p></div></div><div className="studio-metrics"><Metric label="Project settings" value={!data.project?'Select project':data.project.accelerator.config?'Recorded':'Not set'} icon="plan" detail={data.project?`Revision ${data.project.accelerator.revision}`:undefined} /><Metric label="Observed GPUs" value={devices.length} icon="accelerators" /><Metric label="Provider environments" value={providers.length} icon="tools" /></div>
    <div className="studio-worker-grid">{devices.map(device => <article className="studio-observation" key={device.device_id}><div className="studio-card-top"><h2>{device.name}</h2><Badge>{words(device.vendor)}</Badge></div><dl className="studio-definition-list"><dt>Memory</dt><dd>{device.used_vram_mib ?? 'Unknown'} / {device.total_vram_mib ?? 'Unknown'} MiB</dd><dt>Temperature</dt><dd>{device.temperature_c ?? 'Unknown'} °C</dd><dt>Driver</dt><dd>{device.driver_version ?? 'Unknown'}</dd><dt>Observed</dt><dd>{date(device.observed_at)}</dd></dl><Details kind="device" value={device} label="Device observation" title={`Device observation — ${device.name}`} /></article>)}</div>
    {!devices.length && <p className="studio-info">No GPU observation is included in this snapshot.</p>}
    <h2 className="studio-subheading">Provider environments</h2>{providers.length ? <Table columns={['Runtime', 'Installation', 'Execution']} label="Compute providers">{providers.map(provider => <tr key={provider.runtime_id}><td>{provider.runtime_id}<Details kind="provider" value={provider} label="Provider observation" title={`Provider observation — ${provider.runtime_id}`} /></td><td><Badge>{words(provider.state)}</Badge></td><td>{words(provider.execution_state)}</td></tr>)}</Table> : <p>No optional provider environment is configured.</p>}
    {data.project?.accelerator.config && <Details kind="compute-settings" id={data.project.project_id} value={data.project.accelerator} label="Current project compute settings" />}</>;
}

export function DiagnosticsView({ data, connected }: { data: Snapshot; connected: boolean }) {
  const workspace=useWorkspaceSelection();const tab=workspace?.section??'runtime';const setTab=(next:string)=>workspace?.setSection(next);
  function save() { const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'})); const link = document.createElement('a'); link.href = url; link.download = 'evidence-lane-diagnostics.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  return <><div className="studio-section-heading"><div><span className="studio-eyebrow">RUNTIME DETAILS</span><h1>Diagnostics</h1><p>Recorded engine, tool, and connection observations.</p></div><Pill onClick={save}>Save diagnostics</Pill></div>
    <div className="studio-filter-tabs" role="group" aria-label="Diagnostic views"><Pill active={tab === 'runtime'} onClick={() => setTab('runtime')}>Runtime</Pill><Pill active={tab === 'recovery'} onClick={() => setTab('recovery')}>Recovery</Pill></div>
    {tab === 'recovery' ? <RecoveryView data={data} connected={connected} /> : <><div className="studio-metrics"><Metric label="Engine" value={isDesignPreview()?'Preview engine':`v${data.engine.version}`} icon="brain" detail={words(data.engine.phase)} /><Metric label="Previous shutdown" value={words(data.engine.previous_shutdown)} icon="evidence" /><Metric label="Client observations" value={data.connections.length} icon="connections" detail="Returned engine sessions" /></div><p className="studio-info">The exported snapshot includes local project paths and client labels.</p><SnapshotSections data={data}/><Details kind="diagnostic-section" id="snapshot" value={data} label="Full recorded snapshot" /></>}</>;
}

