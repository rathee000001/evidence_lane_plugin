export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type RecordData = Record<string, any>;
export type Project = { project_id: string; source_root: string; state_root: string; read_only: boolean };
export type Task = { position: number; state: string; contract_digest: string; definition: {
  task_id: string; title: string; requested_outcome: string; profile: string; dependencies: string[];
  permitted_paths: string[]; permitted_tools: string[]; allowed_actions: string[]; acceptance_checks: string[];
  budget: RecordData; stop_condition: string;
} };
export type Snapshot = {
  observed_at: string; engine: RecordData; projects: Project[];
  project: null | { project_id: string; jobs: { counts: Record<string, number>; recent: RecordData[] };
    evidence: { object_count: number; total_bytes: number; receipts: RecordData[] };
    plan: { state: string; title: string | null; revision: number | null; total_tasks: number; truncated: boolean; counts: Record<string, number>; tasks: Task[] };
    control: RecordData; accelerator: { revision: number; config: RecordData | null }; plugins: RecordData[];
    learning: { lessons: RecordData[]; truncated: boolean }; lineage: { events: RecordData[]; total_events: number };
    memory: { locators: RecordData[]; truncated: boolean; head: string | null };
    linked_projects: { links: RecordData[]; last_project_id: string | null; truncated: boolean };
    continuity: { offers: RecordData[]; truncated: boolean };
    recovery?: { source_restore: RecordData | null; database_recovery: RecordData | null };
    canon: { participants: RecordData[]; contracts: RecordData[]; exchanges: RecordData[]; last_sequence: number; truncated: boolean; participants_truncated: boolean; contracts_truncated: boolean };
  };
  workers: RecordData; connections: RecordData[]; inventory: RecordData; active_executions: RecordData[];
  actions: RecordData[]; plugin_scopes: { lanes: string[]; actions: string[]; hosts: string[] };
  lane_views: RecordData[];
  tool_catalog: { base_entry_count: number; counts: Record<string, number>; entries: RecordData[] };
};
