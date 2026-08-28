<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Git and CI/CD boundary

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


Repository writes use the selected Evidence Lane GitHub App route and exact
branch policy. Implementation occurs only on a governed feature branch; `main`
is never a live working branch. The App creates the exact commit through the
Git Database API as `evidence-lane[bot]`, verifies every blob/tree/parent/ref,
and fast-forwards only that feature-branch ref with `force=false`. A local
human-authored commit followed by an App-authenticated push is not equivalent.
The receipt binds project, task, branch, parent, tree, commit, changed paths,
App route, request IDs, and post-update ref. Feature commits use
`github_app_exact_commit_push_v1`; a green feature head reaches `main` only via
`github_app_main_fast_forward_v3`, which proves strict ancestry and exact-head
gates before one `force=false` App ref update, then verifies the exact feature
commit/tree and final `main` ref without checking out or working on `main`
locally.

That grant does not authorize:

- writing, rewriting, or directly implementing on a protected/default branch;
- force-push;
- Project candidate creation or acceptance;
- accepted-pointer movement or Fuse; or
- production publication or deployment.

## Commit batches

CI runs once per dependency-coherent integration batch, not once per file or
every Delta. Each included Delta retains independent acceptance evidence and
lifecycle state. A cross-Delta matrix maps tasks to changed surfaces, tests,
remote checks, installed-host checks, outcomes, and exact failure ownership.
Any included-row failure fails the batch closed. After the exact branch is
green, the separately governed current App merge route may bring the branch to
`main`; no working-tree implementation occurs on `main` before or after that
merge, and the obsolete full-tree/blob-replay merge path cannot execute.

The behavior-bearing commit updates source, schemas, tests, public contracts,
and the bounded public Plan/Delta projection together. Generated projections
must match the same passing native authority before push.

## Clean-checkout evidence

Configured workflows cover governed Python tests, source/MCP contracts,
lifecycle and lane tests, package/preview compilation, and CodeQL. A local or
dirty-worktree rehearsal is useful evidence but cannot replace exact-commit
clean CI or install a Stable/Git release selector.

GitHub Pages and a feature-branch preview are documentation projections. They
do not install Codex, persist project truth, create a candidate, or move a
pointer. Production Vercel publication remains a separately governed later
action.

Evidence Lane does not invoke GitHub Sandbox or imply paid coding-agent usage.
Local agent work stays in its bounded workspace; GitHub Actions supplies the
clean-checkout execution environment.
