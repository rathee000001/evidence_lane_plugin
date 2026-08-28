<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Lifecycle and human gates

Evidence Lane separates source registration, Plan execution, candidate
construction, human disposition, accepted pointer movement, and Goal
completion. Success at one stage never implies authority for the next.

```text
Boot + locked ENV/UOP Flash
        |
        v
Source Intake -> verified entry pointer
        |
        v
Plan / task / Delta execution
        |
        v
Build or Refresh -> immutable unaccepted candidate
        |
        v
Exact six-way Project HIL
        |
        +-- APPROVE + exact Fuse -> accepted PV and pointer movement
        +-- APPROVE_WITH_DELTA   -> correction remains explicit
        +-- MORE_RESEARCH        -> research remains explicit
        +-- ROLLBACK             -> pointer-only governed rollback
        +-- REJECT / FAIL        -> no promotion
```

Only exact, case-sensitive authority at the candidate-bound pending HIL may be
recorded. Natural language, a Plan click, tests, CI, install, a preview, an
agent report, continued conversation, or Goal status is not approval.

## Independent HIL surfaces

- **Project HIL:** six-way candidate disposition; only exact approval followed
  by Fuse may move the Project pointer.
- **Canon Input HIL:** receiver-owned `ACCEPT`, `REJECT`, or `MORE_RESEARCH`;
  admits bounded input only.
- **AI Learning HIL:** separate six-way Learning decision; may move only the
  Learning pointer.

One user message may contain distinct labelled decisions, but each token is
validated against its own pending candidate and receipt. No decision propagates
into another authority.

## State Travel

State Travel resumes exact unfinished work in a fresh Codex task after native
identity, source, worktree, pointer, Plan, plugin, execution-profile, and task
bindings pass. It does not restart the app, replay HIL, move the pointer, create
a Goal competitor, or reconstruct the Plan from chat.

## Goal completion

Goal completion is human-owned and independent from Project, Canon, or Learning
HIL. Only the explicit Goal-completion command may close it. Pausing, stalling,
State Travel, tests, a candidate, or a completed Plan row cannot do so.
