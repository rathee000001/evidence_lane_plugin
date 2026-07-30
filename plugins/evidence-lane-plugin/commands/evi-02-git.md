---
description: Intake or synchronize one exact Git branch
argument-hint: <project_id> <credential-free-https-url-or-local-git-path> <branch>
---

# /evi-02-git

Require successful `/evi-01-boot`, which atomically verifies Boot and locked
ENV/UOP Flash. For a new project, call `pv_enroll_project` with one exact
source and branch. For an existing project with one classified task, call
`git_sync_selected`. The registered branch remains authoritative by default.
When the user explicitly selects a different branch and the clean governed
checkout is already on that exact branch, pass
`replace_registered_branch=true`; the tool replaces the prior branch set with
that one branch and writes a project-scoped receipt. It never switches
branches or broadens authority. Only a clean, path-bounded fast-forward is
valid. Never merge, approve, or push.
