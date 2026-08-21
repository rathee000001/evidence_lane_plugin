# Project Universe

Project Universe is a project-isolated, read-optimized graph derived from the
live 18-lane authority, canonical Plan SQLite, and accepted-pointer identity.
It does not own Project Truth, Plan lifecycle, lane bytes, candidates, HIL, or
the accepted pointer.

The real bundle is materialized only by `project_universe_refresh`. It contains
one SQLite graph plus bounded JSON, Mermaid, DOT, tool, pointer, refresh, and
manifest projections. Counts are queried from live databases; no capability,
source, task, lane, or hook count is hard-coded.

Stable nodes cover the project, all canonical lanes, registered sources, Plan
tasks, and the accepted pointer. Edges express lane containment, source
indexing, Plan ownership, task dependencies, and accepted-PV baselines. A
source fingerprint binds every refresh to the exact sector SQLite hashes, Plan
hash, and pointer identity.

Queries accept a node kind, text filter, and a limit from 1 through 100. They
return a bounded hit window and graph identity, never the full graph. Refresh
validates every source SQLite and stages the complete bundle before an atomic
derived-projection swap. It creates no Project candidate, infers no HIL, and
moves no accepted pointer.
