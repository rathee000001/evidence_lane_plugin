# Remote Git write contract

PV approval and remote Git write are independent decisions.

## Candidate distribution branch

Publishing this plugin repository's explicitly named feature branch for a
cross-host installation HIL is separate from pushing a governed user's project
source. It may occur before PV acceptance only when the user explicitly
supersedes the HIL boundary and names branch publication as required evidence.
That bounded action must:

- create one local commit on the current feature branch;
- push only that exact branch ref without force;
- leave `main` unchanged locally and remotely;
- record the exact commit and remote branch;
- install and test that commit without inferring PV approval;
- stop at the universal HIL before merge, pointer movement, or public release.

The `remote_git_prepare_push` and `remote_git_execute_push` tools below govern
project-source publication after accepted-PV authority. They do not govern
this repository's separately authorized candidate-distribution branch.

## Required sequence

1. A validated accepted PV must exist.
2. The user explicitly requests a remote branch push.
3. `remote_git_prepare_push` records the accepted PV, manifest hash, pointer
   generation, remote, local ref, remote branch, and requester.
4. The tool returns a high-entropy, one-use confirmation token.
5. The exact action is shown to the user.
6. `remote_git_execute_push` accepts only the exact token.
7. The controller rechecks the accepted pointer using all three recorded
   authority fields.
8. Only then may one non-force branch push occur.

## Exact-source invalidation

A prepared action is bound to its exact local ref plus the accepted PV,
manifest hash, and pointer generation. If any tracked source changes before
execution, the action is superseded even when the accepted pointer is
unchanged. Do not execute or repurpose its token. Finish the new source gate,
create the new commit, then prepare and display a new one-use action/token.

The same rule applies when a pre-push audit finds invalid release evidence: the
finding is corrected before publication, and the obsolete action remains
unconsumed as historical control evidence.

## Forbidden behavior

- no automatic push after a build or HIL approval;
- no force push;
- no wildcard ref;
- no shell-composed Git command;
- no second use of a consumed action;
- no execution after pointer drift;
- no token in source, PV, lineage, or normal logs.

Branch creation, pull request creation, merge, and deletion are not implemented
by the first private HIL.
