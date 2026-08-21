# Portable multi-project routing

Evidence Lane treats an MCP server or Secure MCP Tunnel as a transport boundary,
not as a project binding. The runtime-global tools are limited to doctor, Flash,
activation, transition-law, lane-catalog, and runtime-panel reads. Every other
MCP tool requires an explicit `project_id` in its input schema. The native route
receipt fails closed if a project-scoped tool is ever registered without it.

## Store-root selection

The durable store root is selected in this order:

1. an explicit `EvidenceLaneService(data_root=...)` configuration;
2. `EVIDENCE_LANE_DATA_ROOT`;
3. legacy `PLUGIN_DATA`, retained only for migration compatibility;
4. the platform per-user default, `~/EvidenceLanePV`.

An explicitly configured empty root, a file in place of a directory, or an
unreadable/unwritable root is rejected. `runtime_doctor` exposes the resolved
root, its selection source, accessibility, project count, and the fact that
Google Drive cannot be the primary runtime. `storage_connector_inspect` and
`pv_status` expose the exact project route and storage selection without storing
credentials.

## Exact project isolation

Every project resolves only to:

```text
<resolved-store-root>/projects/<exact-project-id>
```

Project IDs are ASCII-exact and may contain only letters, numbers, dot,
underscore, and hyphen. Empty, hidden, traversal, absolute-path, separator, and
Unicode injection forms are rejected. NFKC plus case-folding is used only as a
collision detector: it never rewrites the authoritative ID. A second ID that
would alias an existing path by case or normalization is rejected, as is binding
the same resolved repository path to another project ID.

Pointers, candidates, sessions, backlogs, lineage, receipts, storage selection,
and locks remain beneath that project directory. There is no implicit default
project and no cross-project fallback. New runtime-continuity receipts seal the
resolved store root, relative project route, exact project ID, host storage
profile, and the no-fallback rule. Older accepted receipts remain immutable and
validation-compatible.

## Surface placement

Codex loads only the native installed Evidence Lane full-lifecycle MCP. Normal
ChatGPT may load the separately named ChatGPT connector or a private development
tunnel. The ChatGPT route is never installed into the Codex plugin catalog and
cannot substitute for the native server. A private tunnel carries the MCP
process and the configured store root, but it carries no fixed project ID; each
project-scoped call must still supply one.

Google Drive is never selected as live transactional storage by this routing
contract. Changing a project's storage selection still requires the exact
`SELECT_STORAGE:...` confirmation handled by `/evi-storage`.
