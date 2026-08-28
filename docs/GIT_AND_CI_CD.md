<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Git and CI/CD boundary

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
