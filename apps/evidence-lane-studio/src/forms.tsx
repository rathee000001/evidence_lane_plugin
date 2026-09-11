import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { GlassShell } from './design-system/components/GlassShell';
import { GlassPill } from './design-system/components/GlassPill';
import { Icon, words } from './ui';
import { api } from './api';
import type { RecordData, Snapshot } from './types';

export type ModalKind = 'project' | 'compute' | 'plugin' | 'engine';
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null), id = useId();
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
  return createPortal(<dialog ref={ref} className="studio-dialog" aria-labelledby={id} onCancel={onClose}>
    <GlassShell surface="frosted-popup" className="studio-dialog-shell"><div className="studio-dialog-heading"><h2 id={id}>{title}</h2><button type="button" className="studio-icon-button" aria-label="Close dialog" onClick={onClose}><Icon name="close" /></button></div>{children}</GlassShell>
  </dialog>, document.body);
}
const get = (form: FormData, name: string) => String(form.get(name) ?? '').trim();
const split = (value: string) => value.split(',').map(item => item.trim()).filter(Boolean);
const expiry = (value: string, prior?: string) => value === 'keep' ? prior : value === 'none' ? 'NO_EXPIRY' : new Date(Date.now() + (value === 'week' ? 7 : 1) * 86400000).toISOString();
function Expiry({ prior }: { prior?: string }) { return <label>Grant expiry<select name="expiry_mode" defaultValue={prior ? 'keep' : 'day'}>{prior && <option value="keep">Keep current expiry</option>}<option value="day">24 hours</option><option value="week">7 days</option><option value="none">Until changed or revoked</option></select></label>; }

export function ProjectForm({ onSaved, onClose }: { onSaved: (id: string) => void; onClose: () => void }) {
  const [mode, setMode] = useState('create');
  return <SubmitForm label="Add project" onClose={onClose} onSubmit={async form => {
    const result = await api('project', {create: mode === 'create', state_root: get(form, 'state_root'), source_root: mode === 'create' ? get(form, 'source_root') : null, read_only: mode !== 'create' && form.has('read_only')});
    onSaved(result.project_id);
  }}>
    <p>Connect a source folder with a separate home for project state.</p>
    <label>Action<select name="mode" value={mode} onChange={event => setMode(event.target.value)}><option value="create">Create project state</option><option value="existing">Connect existing v4 state</option></select></label>
    {mode === 'create' && <label>Source folder<input name="source_root" placeholder="F:\MyProject" maxLength={1024} required /></label>}
    <label>State folder<input name="state_root" placeholder="F:\EvidenceLaneProjects\MyProject" maxLength={1024} required /></label>
    {mode === 'existing' && <label className="studio-check"><input type="checkbox" name="read_only" defaultChecked /> Connect read-only</label>}
  </SubmitForm>;
}

export function SubmitForm({ children, onSubmit, onClose, label }: { children: ReactNode; onSubmit: (data: FormData) => Promise<void>; onClose: () => void; label: string }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    const form = new FormData(event.currentTarget); setBusy(true); setError('');
    try { await onSubmit(form); onClose(); } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }
  return <form className="studio-form" onSubmit={event => void submit(event)}><fieldset disabled={busy}>{children}</fieldset>{error && <p className="studio-error" role="alert">{error}</p>}<div className="studio-dialog-actions"><GlassPill onClick={onClose} disabled={busy}>Cancel</GlassPill><GlassPill variant="active-cyan" type="submit" disabled={busy}>{busy ? 'Saving…' : label}</GlassPill></div></form>;
}

export function ComputeForm({ data, onSaved, onClose }: { data: Snapshot; onSaved: () => void; onClose: () => void }) {
  const setting = data.project!.accelerator, prior = setting.config;
  return <SubmitForm label="Save settings" onClose={onClose} onSubmit={async form => {
    const profile = get(form, 'requested_profile');
    const actionClasses = form.getAll('action'); if (!actionClasses.length) throw new Error('Choose at least one type of allowed work.');
    await api('accelerator', {project_id: data.project!.project_id, expected_revision: setting.revision, config: {
      requested_profile: profile, enabled_vendor_plugins: profile === prior?.requested_profile ? prior.enabled_vendor_plugins : profile === 'auto' ? ['nvidia', 'amd'] : ['nvidia', 'amd'].includes(profile) ? [profile] : [],
      purpose: get(form, 'purpose'), action_classes: actionClasses, device_id: get(form, 'device_id') || null,
      memory_budget_percent: Number(get(form, 'memory_budget_percent')), temperature_limit_c: Number(get(form, 'temperature_limit_c')),
      expires_at: expiry(get(form, 'expiry_mode'), prior?.expires_at),
    }}); onSaved();
  }}>
    <label>Preferred compute<select name="requested_profile" defaultValue={prior?.requested_profile ?? 'cpu'}><option value="cpu">CPU</option><option value="auto">Automatic</option><option value="nvidia">NVIDIA CUDA</option><option value="amd">AMD ROCm / DirectML</option></select></label>
    <label>Purpose<input name="purpose" required maxLength={500} defaultValue={prior?.purpose ?? ''} placeholder="Local retrieval and document processing" /></label>
    <fieldset className="studio-checkbox-group"><legend>Allowed work</legend>{['RETRIEVAL', 'OCR_MEDIA', 'EVALUATION'].map(action => <label className="studio-check" key={action}><input name="action" value={action} type="checkbox" defaultChecked={prior?.action_classes.includes(action) ?? false} />{words(action)}</label>)}</fieldset>
    <label>Device<select name="device_id" defaultValue={prior?.device_id ?? ''}><option value="">Select automatically when unambiguous</option>{(data.inventory.devices ?? []).map((device: RecordData) => <option value={device.device_id} key={device.device_id}>{device.name}</option>)}{prior?.device_id && !(data.inventory.devices ?? []).some((device: RecordData) => device.device_id === prior.device_id) && <option value={prior.device_id}>Previously selected device</option>}</select></label>
    <div className="studio-form-row"><label>Memory limit (%)<input type="number" name="memory_budget_percent" min={1} max={95} defaultValue={prior?.memory_budget_percent ?? 80} required /></label><label>Temperature limit (°C)<input type="number" name="temperature_limit_c" min={30} max={110} defaultValue={prior?.temperature_limit_c ?? 83} required /></label></div>
    <Expiry prior={prior?.expires_at} /><p className="studio-form-note">A preference enables eligible work. It does not start a workload.</p>
  </SubmitForm>;
}

export function PluginForm({ data, prior, onSaved, onClose }: { data: Snapshot; prior?: RecordData; onSaved: () => void; onClose: () => void }) {
  return <SubmitForm label="Save grant" onClose={onClose} onSubmit={async form => {
    const roleSchema = Object.fromEntries(split(get(form, 'role_schema')).map(value => { const pieces = value.split(':').map(item => item.trim()); if (pieces.length !== 2) throw new Error('Use field:type for each output field.'); return pieces; }));
    await api('plugin', {project_id: data.project!.project_id, expected_version: prior?.version ?? null, registration: {
      ...Object.fromEntries(['plugin_id', 'name', 'plugin_kind', 'backend_runtime', 'description', 'purpose', 'role'].map(key => [key, get(form, key)])),
      role_schema: roleSchema, capabilities: split(get(form, 'capabilities')), config_env_keys: split(get(form, 'config_env_keys')),
      allowed_lanes: form.getAll('lane'), allowed_actions: form.getAll('action'), host_profiles: form.getAll('host'),
      write_roots: get(form, 'write_roots').split('\n').map(value => value.trim()).filter(Boolean), expires_at: expiry(get(form, 'expiry_mode'), prior?.expires_at),
    }}); onSaved();
  }}>
    <p>Allow an additional connector or toolchain to operate within this project’s explicit scope.</p>
    <div className="studio-form-row"><label>Identifier<input name="plugin_id" required pattern="[a-z][a-z0-9-]{2,63}" maxLength={64} readOnly={!!prior} defaultValue={prior?.plugin_id ?? ''} placeholder="local-reader" /></label><label>Name<input name="name" maxLength={128} required defaultValue={prior?.name ?? ''} /></label></div>
    <div className="studio-form-row"><label>Kind<select name="plugin_kind" defaultValue={prior?.plugin_kind ?? 'connector'}><option value="connector">Connector</option><option value="toolchain">Additional toolchain</option></select></label><label>Runtime<select name="backend_runtime" defaultValue={prior?.backend_runtime ?? 'python'}>{['python', 'external_mcp', 'java', 'kotlin', 'go', 'rust', 'cpp'].map(runtime => <option key={runtime}>{runtime}</option>)}</select></label></div>
    {['description', 'purpose'].map(field => <label key={field}>{field === 'description' ? 'Description' : 'Purpose'}<input name={field} required maxLength={1000} defaultValue={prior?.[field] ?? ''} /></label>)}
    <label>Capabilities, separated by commas<input name="capabilities" required defaultValue={prior?.capabilities.join(', ') ?? 'read'} /></label>
    <div className="studio-form-row">{(['lane', 'action', 'host'] as const).map(field => { const key = {lane: 'lanes', action: 'actions', host: 'hosts'}[field] as 'lanes' | 'actions' | 'hosts'; const selected = prior?.[{lane: 'allowed_lanes', action: 'allowed_actions', host: 'host_profiles'}[field]] ?? []; return <label key={field}>{words(field)}<select name={field} multiple size={4} required defaultValue={selected}>{data.plugin_scopes[key].map(value => <option value={value} key={value}>{words(value)}</option>)}</select></label>; })}</div>
    <label>Role<input name="role" required pattern="[a-z][a-z0-9_-]{2,63}" defaultValue={prior?.role ?? 'reader'} /></label>
    <label>Output fields<input name="role_schema" required placeholder="text:text, source:blob_hash" defaultValue={prior ? Object.entries(prior.role_schema).map(([key, value]) => `${key}:${value}`).join(', ') : ''} /></label>
    <details><summary>Folders and environment</summary><label>Write folders, one per line<textarea name="write_roots" rows={2} defaultValue={prior?.write_roots.join('\n') ?? ''} /></label><label>Environment variable names<input name="config_env_keys" placeholder="SERVICE_API_KEY" defaultValue={prior?.config_env_keys.join(', ') ?? ''} /></label><p className="studio-form-note">Variable names only. Credentials remain in the configured environment.</p></details>
    <Expiry prior={prior?.expires_at} /><p className="studio-form-note">This grant records access. It does not install or run the backend.</p>
  </SubmitForm>;
}
