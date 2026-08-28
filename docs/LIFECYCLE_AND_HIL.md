<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Lifecycle and human gates

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


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
