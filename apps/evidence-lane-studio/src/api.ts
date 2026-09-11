import type { Snapshot } from './types';

let csrf: string | null = null;
let initialization: Promise<void> | undefined;
const messages: Record<string, string> = {
  STUDIO_AUTHENTICATION_REQUIRED: 'Open Studio from its launcher to reconnect.',
  STUDIO_TICKET_EXPIRED: 'This launch link expired. Open Studio again from its launcher.',
  STUDIO_CSRF_REQUIRED: 'Refresh Studio before making this change.',
  READ_ONLY_PROJECT: 'This project is connected read-only.',
  PROJECT_WRITER_BUSY: 'This project must reach a safe checkpoint before this change.',
  RUNTIME_IN_USE: 'The project is busy with another operation.',
  ACCELERATOR_REVISION_CONFLICT: 'Compute settings changed. Refresh and review the current settings.',
  PLUGIN_VERSION_CONFLICT: 'This grant changed. Refresh and review the current version.',
  INVALID_MESSAGE: 'Check the form fields and select values supported by this build.',
  PROJECT_LINK_REQUIRED: 'One of the selected projects has no active link from this project. Review the selection or turn off the link requirement.',
  QUERY_PLAN_CHANGED: 'A selected Plan changed during this read. Read the projects again for current results.',
  QUERY_OUTPUT_BUDGET: 'These results exceed the page budget. Select fewer projects or narrow your search.',
  QUERY_SCHEMA_INCOMPATIBLE: 'A selected project uses an incompatible schema. Its data was left unchanged.',
  SCHEMA_NEWER_THAN_ENGINE: 'A selected project needs a newer compatible engine.',
  QUERY_TIMEOUT: 'The project read exceeded its time budget. Narrow the selection and try again.',
  VIEW_SOURCE_CHANGED: 'This lane changed after the preview. Preview its current records before exporting.',
  VIEW_GENERATION_CONFLICT: 'A newer view was exported. Preview again to use the current generation.',
  VIEW_CONTRACT_CHANGED: 'This view contract changed. Refresh Studio and preview again.',
  VIEW_TOOL_UNAVAILABLE: 'The selected graph exporter is unavailable in this runtime.',
  VIEW_WORKER_UNAVAILABLE: 'This runtime has no registered graph-export worker.',
  VIEW_EXPORT_TIMEOUT: 'The export did not finish within its wait budget. No view was published.',
  VIEW_ARTIFACT_CHANGED: 'A saved artifact differs from its recorded content. Export a new view from current records.',
  VIEW_ARTIFACT_MISSING: 'A saved artifact is missing. Export a new view from current records.',
  RECOVERY_JOBS_NOT_QUIESCENT: 'Finish or checkpoint the project jobs before creating a backup or restoring source.',
  RECOVERY_PLAN_CHECKPOINT_REQUIRED: 'Checkpoint the active Plan task before this recovery action.',
  RECOVERY_STEER_PENDING: 'Resolve the pending project changes before this recovery action.',
  RECOVERY_IO_OR_DATA_FAILURE: 'A recovery file or record could not be validated. Inspect the recorded attempt before retrying.',
  RESTORE_PREVIEW_CHANGED: 'The source or Plan changed. Preview the current restoration before proceeding.',
  RESTORE_RECONCILIATION_REQUIRED: 'This attempt is already recorded. Inspect or reconcile its fresh workspace before starting another.',
  RESTORE_FRESH_ROOT_REQUIRED: 'Choose a new workspace folder beneath an existing parent directory.',
  RESTORE_ROOT_OVERLAP: 'Choose a folder outside all registered source, project state, and engine directories.',
  BACKUP_INCOMPLETE: 'This folder contains an incomplete backup. Preserve it and select a new destination.',
  BACKUP_FILE_CHANGED: 'A saved backup file differs from its recorded content. This backup was not verified.',
  BACKUP_MANIFEST_MISMATCH: 'The backup manifest does not match the selected project and digest.',
  SOURCE_RESTORE_PLAN_REQUIRED: 'Refresh the Plan for the restored source before continuing work.',
  DATABASE_RECOVERY_PLAN_REQUIRED: 'Refresh the Plan for the recovered project state before continuing work.',
};

export async function api<T = Record<string, any>>(route: string, payload?: unknown, read = false): Promise<T> {
  const longOperation = ['backup', 'backup-verify', 'git-restore'].includes(route) ||
    (route === 'read' && typeof payload === 'object' && payload !== null && 'action' in payload &&
      ['git_restore_preview', 'project_recovery_inspect'].includes(String(payload.action)));
  const options: RequestInit = read ? { headers: { 'X-Studio-Read': '1' } } : {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    body: JSON.stringify(payload ?? {}),
  };
  const response = await fetch('/studio/api/' + route, { ...options, credentials: 'same-origin', cache: 'no-store',
    redirect: 'error', signal: AbortSignal.timeout(longOperation ? 120000 : 15000) });
  const result = await response.json();
  if (!response.ok) throw new Error(messages[result.error] ?? `The operation was not confirmed (${String(result.error ?? response.status)}).`);
  return result as T;
}

export function initialize(): Promise<void> {
  if (!initialization) {
    const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket');
    if (ticket) history.replaceState(null, '', '/studio/#projects');
    initialization = api<{ csrf: string }>('session', ticket ? { ticket } : {}).then(session => { csrf = session.csrf; });
  }
  return initialization;
}

export async function snapshot(projectId: string): Promise<Snapshot> {
  const value = await api<Snapshot>('snapshot' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''), undefined, true);
  if (!value.engine || !Array.isArray(value.projects) || (projectId && value.project?.project_id !== projectId)) {
    throw new Error('The returned workspace does not match the selected project.');
  }
  return value;
}

export async function logout() { await api('logout', {}); csrf = null; }
