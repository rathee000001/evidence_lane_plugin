---
description: Intake or synchronize one exact Git branch
argument-hint: <project_id> <credential-free-https-url-or-local-git-path> <branch>
---

# /evi-02-git

Require successful `/evi-01-boot`, which atomically verifies Boot and locked
ENV/UOP Flash. For a new project, call `pv_enroll_project` with one exact
source and branch. For an existing project with one classified task, call
`git_sync_selected`. Only a clean, path-bounded fast-forward is valid.
Never broaden branch authority, merge, approve, or push.
