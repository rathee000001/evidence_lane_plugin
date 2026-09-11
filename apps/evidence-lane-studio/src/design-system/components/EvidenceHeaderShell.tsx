import type { ReactNode } from 'react';
import { EvidenceOSLogo } from './EvidenceOSLogo';
import { HeaderShell } from './HeaderShell';

export type EvidenceHeaderShellProps = {
  projectLabel: string;
  sourceRoot?: string;
  enginePhase: string;
  connectionCount: number;
  connected: boolean;
  controls: ReactNode;
};

export function EvidenceHeaderShell({ projectLabel, sourceRoot, enginePhase, connectionCount, connected, controls }: EvidenceHeaderShellProps) {
  return <HeaderShell className="evidence-header-shell studio-header" label="Evidence Lane project context">
    <div className="evidence-header-shell__logo"><div className="evidence-header-shell__logo-pane"><EvidenceOSLogo animated /></div><span className="studio-brand-caption">EVIDENCE LANE · STUDIO</span></div>
    <div className="studio-header-context"><span className="studio-eyebrow">{sourceRoot ? 'SELECTED PROJECT' : 'LOCAL WORKSPACE'}</span><h2 title={projectLabel}>{projectLabel}</h2><p title={sourceRoot}>{sourceRoot ?? 'Your projects, connected to one persistent engine'}</p><div className="studio-context-meta"><span><i className={`studio-live-dot ${connected ? '' : 'offline'}`} />{connected ? `Engine ${enginePhase}` : 'Disconnected'}</span><span>{connectionCount} client{connectionCount === 1 ? '' : 's'}</span></div></div>
    <div className="studio-header-controls">{controls}</div>
  </HeaderShell>;
}
