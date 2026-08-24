# Remote Git write contract

PV approval and remote Git write are independent decisions.

## Candidate distribution branch

Publishing this plugin repository's explicitly named feature branch for a
cross-host installation HIL is separate from pushing a governed user's project
source. It may occur before PV acceptance only when the user explicitly
supersedes the HIL boundary and names branch publication as required evidence.
That bounded action must:

- create one deterministic local preview commit on the current feature branch
  with canonical `evidence-lane[bot]` author and committer identity;
- use `github_app_exact_commit_push_v1` to recreate the exact blobs, tree,
  ordered parents, and commit through the selected Evidence Lane GitHub App;
- prove the current remote feature ref equals the exact parent or is its strict
  ancestor, then fast-forward only that exact feature-branch ref without force;
- leave `main` unchanged locally and remotely;
- record the exact commit and remote branch;
- install and test that commit without inferring PV approval;
- stop at the universal HIL before merge, pointer movement, or public release.

## Governed main promotion

After the exact feature head passes every required clean-checkout workflow, the
only maintainer promotion route is `github_app_main_fast_forward_v3`, owned by
`scripts/codex_release/fast_forward_github_app_feature_to_main.py`. It reads the
exact source ref, source tree, target ref, strict target-to-source ancestry,
App-bot feature identity, and latest required exact-head workflow runs before
one GitHub `force=false` main-ref update. It then verifies that `main` equals the
exact feature commit/tree.

This route performs no local `main` checkout, merge commit, implementation, force
push, blob replay, candidate action, HIL inference, or pointer movement. A
moved ref, divergence, missing/failed workflow, mismatched tree, wrong actor, stale
App attachment, or ambiguous route fails closed before another mutation. The
older repository-merge implementation is not a public fallback.

The `remote_git_prepare_push` and `remote_git_execute_push` tools below govern
project-source publication after accepted-PV authority. They do not govern
this repository's separately authorized maintainer branch and must never be
used as its fallback.

## Required sequence

1. A validated accepted PV must exist.
2. The user explicitly requests a remote branch push.
3. `remote_git_prepare_push` records the accepted PV, manifest hash, pointer
   generation, remote, local ref, remote branch, and requester.
4. The controller verifies that local and remote names match the sole registered
   project branch and that the branch is a named non-protected test branch.
5. The prepared receipt records automatic authorization, host-managed
   credentials, and explicit denial of main push, merge, and PR acceptance. It
   creates no per-push confirmation token.
6. `remote_git_execute_push` consumes only that exact prepared action.
7. The controller rechecks branch authority and the accepted pointer using all three recorded
   authority fields.
8. Only then may one non-force branch push occur.

## Exact-source invalidation

A prepared action is bound to its exact local ref plus the accepted PV,
manifest hash, and pointer generation. If any tracked source changes before
execution, the action is superseded even when the accepted pointer is
unchanged. Do not execute or repurpose the stale action. Finish the new source
gate, create the new commit, then prepare a new exact action.

The same rule applies when a pre-push audit finds invalid release evidence: the
finding is corrected before publication, and the obsolete action remains
unconsumed as historical control evidence.

## Forbidden behavior

- no implicit push merely because a build or HIL passed; automatic execution is
  limited to the separately requested and prepared exact test-branch action;
- no force push;
- no wildcard ref;
- no shell-composed Git command;
- no second use of a consumed action;
- no execution after pointer drift;
- no credential in source, PV, lineage, or normal logs;
- no generated one-use push token.

Branch creation, pull request creation, and deletion are not implemented by the
first private HIL. Maintainer main promotion exists only through the separately
governed current route above after exact-head CI is green.
