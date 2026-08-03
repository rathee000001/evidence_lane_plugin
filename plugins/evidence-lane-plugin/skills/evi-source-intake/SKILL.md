---
name: evi-source-intake
description: Evidence Lane generalized ordered source intake with optional Git history, auto-detection, exact overrides, 18 lanes, Project Engulf, and Chat Lineage.
---

# Evidence Lane Source Intake

Call `source_intake_classify` for the user's ordered sources. Auto-detect Git,
local code, SQLite/PV brains, Chat Lineage, discussion, analysis, plan, Mode,
docs, data/Excel, PPT, PDF/OCR, images/OCR, artifacts, custom, research,
Project Engulf, and SQLite Brain; accept exact per-source overrides. Always
include Chat Lineage. Classification alone copies no source, creates no
candidate, and moves no pointer.

The Git history arm is optional for source intake. `AUTO` enriches a Git
worktree with history and otherwise falls back to deterministic content
indexing; `REQUIRED` fails closed without a readable Git worktree and HEAD;
`DISABLED` skips Git history explicitly. None of these modes authorizes a
remote Git write. Use existing internal enrollment and route tools only after
the visible classification is accepted.

For a Git worktree, enumerate current sources from tracked index entries only.
Before reading exact bytes into SQLite, FTS, CAS, history, topology, or a PV
package, apply the shared source policy and exclude `.env` variants,
`.runtime`/cache/build artifacts, credential or private-key paths, configured
secret values, and recognized credential-shaped content. Never place secret
bytes or secret environment-variable names in an exclusion receipt. Non-Git
sources use the same deterministic path/content policy but do not claim a
tracked-only boundary.
