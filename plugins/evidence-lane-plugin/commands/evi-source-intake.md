---
description: Auto-detect or override ordered sources with an optional Git arm
argument-hint: [sources] [source=lane overrides] [git=AUTO|REQUIRED|DISABLED]
---

# /evi-source-intake

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
