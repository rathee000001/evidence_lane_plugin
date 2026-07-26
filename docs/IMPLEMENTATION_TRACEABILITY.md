# Implementation traceability

| Required law | Implemented source | Verification |
| --- | --- | --- |
| Exact Git commit/tree/worktree identity | `git_adapter.py` | engine and lifecycle tests |
| Whole-source code ingestion, including Svelte | `ingest.py`, `database.py`, `schema.sql` | exact-byte, chunk, FTS, symbol tests |
| Minimal immutable PV package | `pv_package.py`, `store.py` | package validation and tamper tests |
| Authoritative MMD; rendering optional | `topology.py`, `pv_package.py` | render-warning test |
| PV1, PV2, PV3 linear numbering | `store.py`, `session.py` | full lifecycle test |
| One agent and one bounded task | `tasking.py`, `session.py` | transition/denial tests |
| Automatic visible ChatLineage | `lineage.py`, `session.py` | lifecycle and redaction tests |
| Five exact HIL outcomes | `models.py`, `session.py` | parameterized transition tests |
| Only APPROVE advances pointer | `session.py`, `store.py` | four non-approval pointer-retention tests |
| Local versus Drive persistence route | `persistence.py`, `service.py` | host-routing and fail-closed tests |
| Drive private-PV encryption | `sealing.py` | AES-GCM fake-backend test |
| Separate remote Git authority | `remote_git.py`, `git_adapter.py` | wrong-token denial test |
| Universal MCP tool contract | `mcp_server.py` | inventory, annotation, and real STDIO test |
| Persistent installation; boot context outside PV | `session.py`, hook/skill | manifest/hook/PV-content tests |

## Deliberately deferred

- public HTTPS hosting and ChatGPT developer registration;
- live Google Drive OAuth proof;
- a real non-sensitive repository HIL;
- production signing key and release signature;
- Android control surface;
- UI, 3D telemetry, and desktop application integration;
- unrelated EvidenceOS lanes;
- benchmark work.

These are not represented as completed.
