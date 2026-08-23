<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Plan and change display

The native Plan Lane is the sole authority for task order, dependencies,
status, completed history, queued HIL rows, and the physically final HIL. The
Codex Step Task List and Changes panel are bounded host projections of that
authority; they are not a second Plan database.

## One active row

Exactly one executable row may be `in_progress`. Rows advance only after the
current row's exact acceptance evidence passes. A transition changes Plan state
only; it does not create a candidate, infer HIL, Fuse a PV, or move the accepted
pointer.

Linked steers append immutable corrections to their existing task. Unrelated
work receives one complete new Delta row at the governed insertion point.
Completed rows move into Plan history and are never deleted or silently
reconstructed.

## Host 1+9 projection

The persistent host list shows:

1. one compact progress/header tracker; and
2. the active Delta plus its next eight executable rows.

Each Delta uses at most three compact lines: stable task ID and status;
class/group/dependency/graph pointer; and a human-readable outcome with its FTS
locator. Git appears only on the row where Git actually runs.

Native Codex marks a row complete while the same batch remains visible. The
list rehydrates only when the current 1+9 membership changes, the tenth visible
step finishes, or host UI loss is detected. It must not shift one row after
every completion. If a new steer changes the current queue, the Plan and Goal
order update atomically before implementation resumes.

## Bounded retrieval

The full Plan stays in durable SQLite outside model context. Reads use exact
task IDs or bounded FTS5/BM25 windows. Writes return compact receipts containing
counts, hashes, the active row, and host-window effect—not the full backlog.

The change display may show active task/Delta identity, source and installed
package identity, activation/Refresh state, tool/skill/hook counts, and exact
receipts. It does not own Codex's native file-change UI or invent host placement.

## Plan acceptance and HIL

The native “Implement this plan” action accepts only the proposed Codex Plan
projection. It is not Evidence Lane HIL and cannot approve a candidate. Goal
activation resumes the exact accepted Plan row; it must not reorder back to a
previous task.
