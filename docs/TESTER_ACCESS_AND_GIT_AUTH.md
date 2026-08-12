# Tester access, license, and Git authentication

The existing repository and plugin `LICENSE.md` files are authoritative. They
permit no redistribution, derivative release, publication, sublicensing, or
commercial use merely because a person can clone, install, or test the source.
Private testing beyond those terms requires the owner's written permission.
This document explains the access path; it does not create new legal rights.

## Tester path

1. The tester requests repository access under their own GitHub identity and
   reviews `LICENSE.md`, `COPYRIGHT.md`, `THIRD_PARTY_NOTICES.md`, and the test
   scope before installation.
2. The repository owner grants only the required repository role and test
   branch scope. Production secrets, owner credentials, accepted project data,
   and the administrator tunnel remain excluded.
3. The tester authenticates Git through their own host-managed OAuth session,
   credential manager, SSH key, or PAT according to GitHub policy. Evidence
   Lane never asks the tester to paste a Git token into a prompt, MCP tool,
   task, PV, receipt, SQLite database, or plugin configuration.
4. Each tester uses an isolated Evidence Lane data root and, when an interactive
   tunnel is required, a separately issued Tunnel ID and Runtime key. A tester
   tunnel never implies project, production, owner, HIL, merge, or release
   authority.
5. A contribution push is limited to the tester's authorized non-protected
   branch. Pull-request review and merge remain separate repository-owner
   actions.

Git cannot prove that someone read a license merely from a clone or pull. If
auditable assent is required, collect it through a separate written access
agreement or repository access workflow before granting access. The plugin must
not pretend a PAT-generation screen is a license-acceptance mechanism.

## Administrator path

The owner's existing machine/account is not sent through tester onboarding.
It may use its already configured host credential provider, authorized project
store, and tunnel profile. The plugin does not request, rotate, reveal, or copy
the owner's GitHub PAT, OAuth token, OpenAI credential, or tunnel Runtime key.

## Automatic test-branch push

Version 2 removes the v1.5 per-push confirmation token. A prepared action can
execute automatically only when all of these remain exact:

- one accepted PV and unchanged pointer identity;
- one sole registered project branch;
- matching local and remote branch names;
- a non-protected branch beginning with `agent/`, `test/`, `tests/`,
  `feature/`, `fix/`, or `chore/`;
- the prepared commit and tree;
- host-managed Git credentials and the separate remote-Git authorization scope.

`main`, `master`, development/production/stable/release branches, `release/`
and `hotfix/` branches, force push, merge, PR acceptance, wildcard refs, and
credential intake remain blocked. Automatic test-branch push is not automatic
acceptance, publication, deployment, or merge.
