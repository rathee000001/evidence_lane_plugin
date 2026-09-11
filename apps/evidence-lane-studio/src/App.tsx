import { useCallback, useEffect, useRef, useState } from 'react';
import { initialize, snapshot } from './api';
import type { Snapshot } from './types';
import { WorkspaceShell } from './design-system/components/WorkspaceShell';
import { ExpandedSidePanelShell } from './design-system/components/ExpandedSidePanelShell';
import { CollapsedSidePanelShell } from './design-system/components/CollapsedSidePanelShell';
import { EvidenceHeaderShell } from './design-system/components/EvidenceHeaderShell';
import { PromptBarShell } from './design-system/components/PromptBarShell';
import { PanelStage } from './design-system/components/PanelStage';
import { GlassPill } from './design-system/components/GlassPill';
import { CubeAppIcon } from './design-system/components/CubeAppIcon';
import { Empty, Icon, words } from './ui';
import { ComputeView, ConnectionsView, DiagnosticsView, EvidenceView, JobsView, LearningView, PlanView, ProjectsView, ToolsView, WorkersView, projectName, type ViewActions } from './views';

const workflows = [ ['plan', 'Plan'], ['jobs', 'Jobs'], ['workers', 'Workers'], ['evidence', 'Evidence'], ['tools', 'Toolchains'], ['connections', 'Connections'], ['learning', 'Learning'], ['accelerators', 'Compute'], ['diagnostics', 'Diagnostics'] ] as const;
type Page = 'projects' | typeof workflows[number][0];
const initialPage = (): Page => workflows.some(([key]) => key === location.hash.slice(1)) ? location.hash.slice(1) as Page : 'projects';
function preference(key: string, fallback = '') { try { return sessionStorage.getItem(key) ?? fallback; } catch { return fallback; } }
function storePreference(key: string, value: string) { try { sessionStorage.setItem(key, value); } catch { /* Optional view preference. */ } }

export function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [projectId, setProjectId] = useState(() => preference('selected-project'));
  const [expanded, setExpanded] = useState(() => preference('studio-rail', 'expanded') !== 'collapsed');
  const [data, setData] = useState<Snapshot | null>(null);
  const [ready, setReady] = useState(false), [connected, setConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [projectFilter, setProjectFilter] = useState('');
  const sequence = useRef(0), projectSearch = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { panel.current?.scrollTo({top: 0, left: 0}); }, [page, projectId]);

  useEffect(() => {
    let active = true;
    void initialize().then(() => { if (active) setReady(true); }).catch(failure => { if (active) setError((failure as Error).message); });
    return () => { active = false; };
  }, []);
  const refresh = useCallback(async () => {
    if (!ready) return;
    const current = ++sequence.current; setRefreshing(true);
    try {
      const result = await snapshot(projectId);
      if (sequence.current !== current) return;
      setData(result); setConnected(true); setError('');
    } catch (failure) {
      if (sequence.current === current) { setConnected(false); setError((failure as Error).message); }
    } finally { if (sequence.current === current) setRefreshing(false); }
  }, [ready, projectId]);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!ready) return;
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
    return () => window.clearInterval(timer);
  }, [ready, refresh]);
  useEffect(() => {
    const changed = () => setPage(initialPage());
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  useEffect(() => { document.title = `${page === 'projects' ? 'Projects' : workflows.find(([key]) => key === page)?.[1]} · Evidence Lane Studio`; }, [page]);

  function navigate(next: Page) { location.hash = next; setPage(next); }
  function selectProject(id: string) {
    sequence.current += 1;
    setProjectId(id); storePreference('selected-project', id);
    if (page === 'projects' && id) navigate('plan');
  }
  function toggleRail() { setExpanded(value => { storePreference('studio-rail', value ? 'collapsed' : 'expanded'); return !value; }); }
  function selectAndRefresh(id: string) { selectProject(id); if (id === projectId) void refresh(); }
  const project = data?.projects.find(item => item.project_id === projectId);
  const selectedData = data && data.project?.project_id === projectId ? data : data ? { ...data, project: null } : null;
  const actions: ViewActions = { selectProject: selectAndRefresh };
  const projects = (data?.projects ?? []).filter(item => `${projectName(item)} ${item.source_root}`.toLowerCase().includes(projectFilter.toLowerCase()));

  const railContent = <><div className="studio-rail-body">{expanded && <><div className="studio-rail-heading"><span>PROJECTS</span></div><input ref={projectSearch} className="studio-project-search" aria-label="Search projects" type="search" placeholder="Find a project…" value={projectFilter} onChange={event => setProjectFilter(event.target.value)} /></>}
    <button className={`studio-project-item ${page === 'projects' ? 'selected' : ''}`} onClick={() => navigate('projects')} title="All projects"><Icon name="projects" size={32} />{expanded && <span>All projects<small>{data?.projects.length ?? 0} connected</small></span>}</button>
    <div className="studio-project-list" aria-label="Connected projects">{projects.map(item => <button className={`studio-project-item ${item.project_id === projectId && page !== 'projects' ? 'selected' : ''}`} key={item.project_id} aria-label={`${projectName(item)} project`} aria-pressed={item.project_id === projectId} title={`${projectName(item)}\n${item.source_root}`} onClick={() => selectAndRefresh(item.project_id)}><CubeAppIcon size={34} animated={false} />{expanded && <span>{projectName(item)}<small>{item.read_only ? 'Read only' : 'Project workspace'}</small></span>}</button>)}</div>
    {!projects.length && expanded && <p className="studio-rail-empty">{projectFilter ? 'No matching projects.' : 'Register a project through your connected Codex plugin.'}</p>}</div>
    <footer className="studio-rail-footer"><div className="studio-project-item" aria-label="Engine status"><Icon name="workers" size={30} />{expanded && <span>Your local engine<small><i className={`studio-live-dot ${connected ? '' : 'offline'}`} />{connected ? words(data?.engine.phase) : 'Disconnected'}</small></span>}</div>{expanded && <p>Evidence Lane · v{data?.engine.version ?? '4.0.0'}</p>}</footer></>;

  return <div className="studio-root evidence-theme--sqlite-glass"><a className="studio-skip-link" href="#studio-content">Skip to workspace</a><WorkspaceShell sideMode={expanded ? 'expanded' : 'collapsed'} className="studio-workspace">
    {expanded ? <ExpandedSidePanelShell className="studio-rail" onToggle={toggleRail}>{railContent}</ExpandedSidePanelShell> : <CollapsedSidePanelShell className="studio-rail" onToggle={toggleRail} onAction={action => { if (action === 'search') { setExpanded(true); window.setTimeout(() => projectSearch.current?.focus(), 0); } }}>{railContent}</CollapsedSidePanelShell>}
    <div className="studio-main"><EvidenceHeaderShell projectLabel={project ? projectName(project) : 'All projects'} sourceRoot={project?.source_root} enginePhase={words(data?.engine.phase)} connectionCount={data?.connections.length ?? 0} connected={connected} controls={<><button className={`studio-icon-button ${refreshing ? 'is-refreshing' : ''}`} aria-label="Refresh workspace" disabled={!ready || refreshing} onClick={() => void refresh()}><Icon name="refresh" size={34} /></button><small>{data ? `${connected ? 'Updated' : 'Last received'} ${new Date(data.observed_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}` : 'Connecting…'}</small></>} />
      <main id="studio-content" className="studio-content" tabIndex={-1}><PanelStage label={`${page} workspace`} className="studio-stage"><div className="studio-scroll-panel" ref={panel}>
        {error && <div className="studio-notice error" role="alert">{`${data ? 'Showing the last received state. ' : ''}${error}`}<button aria-label="Dismiss notification" onClick={() => setError('')}>×</button></div>}
        {!selectedData ? <Empty title={error ? 'Connect to your local engine' : 'Opening your workspace'} detail={error || 'Studio is reading the current engine state.'} /> : <div className="studio-page" key={`${projectId}:${page}`}>
          {page === 'projects' && <ProjectsView data={selectedData} actions={actions} />}{page === 'plan' && <PlanView data={selectedData} />}{page === 'jobs' && <JobsView data={selectedData} />}{page === 'workers' && <WorkersView data={selectedData} />}{page === 'evidence' && <EvidenceView data={selectedData} connected={connected} />}{page === 'tools' && <ToolsView data={selectedData} />}{page === 'connections' && <ConnectionsView data={selectedData} />}{page === 'learning' && <LearningView data={selectedData} />}{page === 'accelerators' && <ComputeView data={selectedData} />}{page === 'diagnostics' && <DiagnosticsView data={selectedData} connected={connected} />}
        </div>}
      </div></PanelStage></main>
      <PromptBarShell className="studio-workflows">{workflows.map(([key, label]) => <GlassPill key={key} className="studio-workflow-pill" leading={<Icon name={key} size={22} />} state={page === key ? 'active' : 'idle'} tone="cyan" aria-current={page === key ? 'page' : undefined} onClick={() => navigate(key)}>{label}</GlassPill>)}</PromptBarShell>
    </div>
  </WorkspaceShell>
  </div>;
}
