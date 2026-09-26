import type {Snapshot} from '../types';
import {Icon} from '../ui';
import {useObserver} from './ObserverContext';
import {useWorkspaceSelection} from './WorkspaceSelection';
import {subject} from './subjects';

export function SnapshotSections({data}: {data: Snapshot}) {
  const {inspectSubject} = useObserver();const workspace=useWorkspaceSelection();
  const sections = [
    {id:'engine',title: 'Engine observation', icon: 'brain', value: data.engine, detail: 'Version, phase and recorded shutdown'},
    {id:'connections',title: 'Client observations', icon: 'connections', value: data.connections, detail: `${data.connections.length} returned client sessions`},
    {id:'workers',title: 'Worker pool', icon: 'workers', value: data.workers, detail: 'Processes and aggregate operation counts'},
    {id:'tool_catalog',title: 'Tool catalogue', icon: 'tools', value: data.tool_catalog, detail: 'Declared roles and measured readiness'},
    {id:'inventory',title: 'Compute inventory', icon: 'accelerators', value: data.inventory, detail: 'Devices and provider environments'},
    {id:'active_executions',title: 'Active executions', icon: 'jobs', value: data.active_executions, detail: `${data.active_executions.length} returned execution records`},
  ];
  return <section aria-label="Snapshot sections" className="observer-snapshot-sections">{sections.map(section => <button key={section.title} aria-haspopup="dialog" data-subject-key={`diagnostic-section:${section.id}`} onClick={() => {const value=subject('diagnostic-section',section.value,section.title,section.id,section.id);workspace?workspace.open(value):inspectSubject(value)}}><Icon name={section.icon} size={40}/><span><strong>{section.title}</strong><small>{section.detail}</small></span><span aria-hidden="true">↗</span></button>)}</section>;
}
