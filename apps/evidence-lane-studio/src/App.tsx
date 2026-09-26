import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { initialize, snapshot, isDesignPreview, designPreviewState } from './api';
import {EvidenceOSLogo} from './design-system/components/EvidenceOSLogo';
import {ProjectCover,projectAccent} from './observatory/ProjectCover';
import {WorkspaceSelectionContext} from './observatory/WorkspaceSelection';
import {defaultSection,initialSubject,subjectsFor,type StudioSubject} from './observatory/subjects';
import {useObserver} from './observatory/ObserverContext';
import {studioWorld} from './observatory/worlds';
import type {CSSProperties} from 'react';
import type { Snapshot } from './types';
import { Empty, Icon, words } from './ui';
import {CosmicAtmosphere,MotionControl} from './observatory/CosmicAtmosphere';
import {ObservationScene} from './observatory/ObservationScene';
import { ComputeView, ConnectionsView, DiagnosticsView, EvidenceView, JobsView, LearningView, PlanView, ProjectsView, ToolsView, WorkersView, projectName, type ViewActions } from './views';

const workflows = [ ['plan', 'Plan'], ['jobs', 'Jobs'], ['workers', 'Workers'], ['evidence', 'Evidence'], ['tools', 'Toolchains'], ['connections', 'Connections'], ['learning', 'Learning'], ['accelerators', 'Compute'], ['diagnostics', 'Diagnostics'] ] as const;
type Page = 'projects' | typeof workflows[number][0];
const initialPage = (): Page => workflows.some(([key]) => key === location.hash.slice(1)) ? location.hash.slice(1) as Page : 'projects';
function preference(key: string, fallback = '') { try { return sessionStorage.getItem(key) ?? fallback; } catch { return fallback; } }
function storePreference(key: string, value: string) { try { sessionStorage.setItem(key, value); } catch { /* Optional view preference. */ } }

export function App() {
  const {inspectSubject}=useObserver();
  const [sectionState,setSectionState]=useState({scope:'',value:''});
  const [visibleSubjects,setVisibleSubjects]=useState<{scope:string;items:StudioSubject[]}|null>(null);
  const [picked,setPicked]=useState<{scope:string;key:string;extra:StudioSubject|null}>({scope:'',key:'',extra:null});
  const [page, setPage] = useState<Page>(initialPage);
  const [projectId, setProjectId] = useState(() => isDesignPreview() ? designPreviewState()==='empty'?'':preference('selected-project','preview-coastal') : preference('selected-project'));
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
      setData(result); setConnected(designPreviewState()!=='disconnected'); setError(designPreviewState()==='disconnected'?'Design preview: disconnected; showing the last illustrative snapshot.':'');
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
  const world=studioWorld(page);
  const project = data?.projects.find(item => item.project_id === projectId);
  const selectedData = data && data.project?.project_id === projectId ? data : data ? { ...data, project: null } : null;
  const pageScope=`${projectId}:${page}`;
  const section=sectionState.scope===pageScope?sectionState.value:defaultSection(page);
  const scope=`${pageScope}:${section}:${page==='plan'?selectedData?.project?.plan.revision??'none':''}`;
  const baseSubjects=useMemo(()=>selectedData?subjectsFor(page,section,selectedData):[],[page,section,selectedData]);
  const subjects=visibleSubjects?.scope===scope?visibleSubjects.items:baseSubjects;
  const publish=useCallback((items:StudioSubject[])=>setVisibleSubjects(current=>current?.scope===scope&&current.items.length===items.length&&current.items.every((item,i)=>item.key===items[i].key&&item.record===items[i].record)?current:{scope,items}),[scope]);
  const initial=initialSubject(page,subjects);
  const selected=picked.scope===scope?(subjects.find(s=>s.key===picked.key)??picked.extra):subjects.find(s=>s.key===initial)??null;
  const select=(value:StudioSubject)=>setPicked({scope,key:value.key,extra:subjects.some(s=>s.key===value.key)?null:value});
  const selection={page,section,subjects,selected,publish,setSection:(value:string)=>{setSectionState({scope:pageScope,value});setPicked({scope:'',key:'',extra:null})},select,open:(value:StudioSubject)=>{select(value);inspectSubject(value)}};
  const actions: ViewActions = { selectProject: selectAndRefresh };
  const projects = (data?.projects ?? []).filter(item => `${projectName(item)} ${item.source_root}`.toLowerCase().includes(projectFilter.toLowerCase()));

  const railContent = <><div className="studio-rail-body">{expanded && <><div className="studio-rail-heading"><span>PROJECTS</span></div><input ref={projectSearch} className="studio-project-search" aria-label="Search projects" type="search" placeholder="Find a project…" value={projectFilter} onChange={event => setProjectFilter(event.target.value)} /></>}
    <button className={`studio-project-item ${page === 'projects' ? 'selected' : ''}`} onClick={() => { selectProject(''); navigate('projects'); }} title="All projects"><Icon name="projects" size={32} />{expanded && <span>All projects<small>{data?.projects.length ?? 0} connected</small></span>}</button>
    <div className="studio-project-list" aria-label="Connected projects">{projects.map(item => <button className={`studio-project-item ${item.project_id === projectId && page !== 'projects' ? 'selected' : ''}`} key={item.project_id} style={{'--project-accent':projectAccent(item)} as CSSProperties} aria-label={`${projectName(item)} project`} aria-pressed={item.project_id === projectId} title={`${projectName(item)}\n${item.source_root}`} onClick={() => selectAndRefresh(item.project_id)}><ProjectCover project={item} compact/>{expanded && <span>{projectName(item)}<small>{isDesignPreview() ? 'Preview project' : 'Project workspace'}</small></span>}</button>)}</div>
    {!projects.length && expanded && <p className="studio-rail-empty">{projectFilter ? 'No matching projects.' : 'Register a project through your connected Codex plugin.'}</p>}</div>
    <footer className="studio-rail-footer"><div className="studio-project-item" aria-label="Engine status"><Icon name="brain" size={30} />{expanded && <span>Your local engine<small><i className={`studio-live-dot ${connected ? '' : 'offline'}`} />{connected ? isDesignPreview()?'Preview data':words(data?.engine.phase) : 'Disconnected'}</small></span>}</div>{expanded && <p>Evidence Lane · {isDesignPreview()?'Studio preview':data?.engine.version ? `v${data.engine.version}` : 'Version not observed'}</p>}</footer></>;

  return <WorkspaceSelectionContext.Provider value={selection}><div className="studio-root observatory-rebuilt" data-view={page} data-universe-style="website-home" data-design-preview={isDesignPreview()} style={{'--view-accent':world.accent} as CSSProperties}>
    {isDesignPreview()&&<div className="observer-preview-banner" role="status">STUDIO DESIGN PREVIEW · ILLUSTRATIVE DATA · ENGINE PAIRING PENDING <nav aria-label="Preview state">{['populated','empty','loading','error','disconnected'].map(state=><a key={state} href={'?design-preview=1&state='+state+location.hash}>{state}</a>)}</nav></div>}
    <a className="studio-skip-link" href="#studio-content">Skip to workspace</a>
    <section className="observatory-window" data-expanded={expanded} aria-label="Evidence Lane Studio workspace"><CosmicAtmosphere view={page}/>
      <header className="observatory-titlebar"><div className="observatory-brand"><EvidenceOSLogo/></div><div className="observatory-project-context"><strong>{project?projectName(project):'All projects'}</strong><span>{isDesignPreview()?'Illustrative workspace':'Read-only observer'}</span><small>{data?`${connected?'Updated':'Last received'} ${new Date(data.observed_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`:error?'Connection unavailable':'Connecting…'}</small></div><div className="observatory-title-actions"><MotionControl/><span className="observatory-connection"><i className={`studio-live-dot ${connected?'':'offline'}`}/>{isDesignPreview()?'Design preview':connected?'Connected':'Disconnected'}</span><button className="studio-icon-button" aria-label="Refresh workspace" disabled={!ready||refreshing} onClick={()=>void refresh()}><Icon name="refresh" size={30}/></button></div></header>
      <aside className="observatory-project-rail" aria-label="Projects"><button className="observatory-collapse" aria-label={expanded?'Collapse sidebar':'Expand sidebar'} onClick={toggleRail}>{expanded?'‹':'›'}</button>{railContent}</aside>
      <main id="studio-content" className="studio-content observatory-workspace" tabIndex={-1} aria-label={`${page} workspace`}><div className="studio-scroll-panel" ref={panel}>
        {error && <div className="studio-notice error" role="alert">{`${data ? 'Showing the last received state. ' : ''}${error}`}<button aria-label="Dismiss notification" onClick={() => setError('')}>×</button></div>}
        {!selectedData ? <Empty title={error ? 'Connect to your local engine' : 'Opening your workspace'} detail={error || 'Studio is reading the current engine state.'} /> : <div className="observer-content-layout"><div className="studio-page" data-view={page} key={`${projectId}:${page}`}>
          {page === 'projects' && <ProjectsView data={selectedData} actions={actions} />}{page === 'plan' && <PlanView data={selectedData} />}{page === 'jobs' && <JobsView data={selectedData} />}{page === 'workers' && <WorkersView data={selectedData} />}{page === 'evidence' && <EvidenceView data={selectedData} connected={connected} />}{page === 'tools' && <ToolsView data={selectedData} />}{page === 'connections' && <ConnectionsView data={selectedData} />}{page === 'learning' && <LearningView data={selectedData} />}{page === 'accelerators' && <ComputeView data={selectedData} />}{page === 'diagnostics' && <DiagnosticsView data={selectedData} connected={connected} />}
        </div>{page!=='plan'&&<ObservationScene view={page} data={selectedData} connected={connected}/>}</div>}
      </div></main>
      <nav className="observatory-dock" aria-label="Evidence Lane workflow bar">{workflows.map(([key,label])=><button key={key} style={{'--tab-accent':studioWorld(key).accent} as CSSProperties} aria-current={page===key?'page':undefined} onClick={()=>navigate(key)}><Icon name={key} size={44}/><span>{label}</span></button>)}</nav>
    </section>
  </div></WorkspaceSelectionContext.Provider>;
}
