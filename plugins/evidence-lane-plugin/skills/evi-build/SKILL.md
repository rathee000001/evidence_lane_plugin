---
name: evi-build
description: Evidence Lane unaccepted candidate build and exact six-way HIL gate; only APPROVE may invoke Fuse.
---

# Evidence Lane Build and HIL

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

From an initial entry call `pv_build_initial`. From a completed bounded task,
use `/evi-refresh`. At pending HIL, show exactly `APPROVE`,
`APPROVE_WITH_DELTA`, `MORE_RESEARCH`, `ROLLBACK`, `REJECT`, and `FAIL`.
Only an exact user-supplied case-sensitive `APPROVE` calls `pv_fuse`; all five
other outcomes call `hil_decide` with their required bounded payload. Never
replay a decision against another candidate and never infer approval from the
user continuing work. After a candidate build, render the complete Exit Slip
and stop.

Project/runtime panels are not a generic HIL or candidate-build step. Under
`PROJECT_RUNTIME_RENDER_THREE_TRIGGER_LAW`, call `render_runtime_panel` and
`render_project_panel` once per tool only when presenting the physically final
PV HIL. Intermediate HILs, candidate creation, HIL classification, Fuse, and
return-to-accepted paths do not call them. The two other permitted triggers are
the separate exact-once State Travel presentation and an explicit user render
request. Never retry, substitute, or invoke a renderer for ordinary status
proof.

For a natural continuation such as "pursue same HIL", a typo, or a non-exact
acceptance phrase, call `hil_intent_classify` and return its classification plus
`suggested_next_prompt` instead of a hard parser error. That classifier never
decides HIL or promotes anything. A `/evi-build` command whose first argument is
exactly `APPROVE` may call `pv_fuse`; trailing words are follow-on instructions
and must never be replayed as another decision.

Every new candidate must validate the source-policy receipt and reconcile each
lane's SQLite authority independently to Mermaid and DOT, including structural
floors, exact MMD/DOT identity parity, and emitted count claims. Rendering alone
is not proof. Previously sealed pre-v1.1 lane bundles may remain readable only
through the explicitly reported compatibility path; do not describe their raw
topology as reconciled and do not use that path for a new candidate.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-build`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.
