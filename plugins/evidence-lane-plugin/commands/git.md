---
description: Intake or synchronize one explicit Git repository and branch
argument-hint: <project_id> <https-url-or-local-git-source> <owner> <name> <branch> [expected-commit]
---

# Evidence Lane Git intake

Require successful `/EV` verification in this host task.

- For a new project, call `pv_enroll_project` with the exact credential-free
  HTTPS URL and exact selected branch. Enrollment clones only that branch,
  registers it, and performs no remote write. Then return to `/EV` to boot.
- For an existing registration, call `git_sync_selected`. It may fetch the
  exact selected HTTPS or local Git source and apply only a clean fast-forward
  on an already-authorized branch. If a governed session is active, pass its
  session ID; the tool must require one classified task and must preview all
  changed paths against that task before mutation.
- Never switch branches, merge divergent history, broaden the branch allowlist,
  accept embedded credentials, create a merge commit, approve a PV, or push.

After intake, use the canonical `github_code` lane for accepted/candidate reads
and label live-source evidence separately.
