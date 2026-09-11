# Git enrollment, synchronization and branch publication

For selected-branch work, read `git_branch_authority` and use `git_sync_selected`
under the exact Code task. Supply the starting and target commits, selected
branch, named remote and expected current authority digest. Explicit local
authority selection preserves staged, unstaged and untracked bytes and does
not fetch. Fast-forward mode requires a clean checkout and grants for every
changed path; it journals fetch and source mutation separately. A prepared
effect left after interruption requires reconciliation. Sync supports explicit
local sources, HTTPS with optional host Git Credential Manager, and explicit SSH agent transport.
It does not materialize submodules/LFS, merge divergent histories or force-update branches.
Changed fast-forward and enrollment also record an addressed per-file byte
fingerprint in Sources and recheck it during acceptance. This includes exact
changed/deleted paths or the enrolled tree, respectively; it does not substitute
Git status for fresh file hashes or claim that source indexes were refreshed.
Refresh Sources/Code after a changed checkout. Recorded selection is not a
native task identity or permission to push or merge main.
For repository enrollment, first register/select the external project state and
its explicitly chosen empty source folder, then declare an `enroll_project`
Code task with the selected branch, full expected commit and file path scope.
Use its exact local source, credential-free HTTPS URL or literal SSH remote; network sources need
live network grants. The engine journals cloning before Git runs, verifies the
commit and file scope before checkout, and records branch authority in Sources.
An existing nonempty folder is preserved. Adopt an existing local checkout with
`project_register` and exact branch selection. Partial clones remain at the
selected root with their effect evidence; inspect and reconcile them without
automatic replay. Refresh Sources/Code after enrollment. Authentication uses
the same explicit provider boundary described below; success does not attest
an authenticated account, downloaded LFS assets or initialized submodules.
For an authorized branch push, use `remote_git_prepare_push`, inspect its exact
committed SHA/tree, branch authority and destination, then use
`remote_git_execute_push` with that preparation digest and the exact remote URL.
Both actions run in declared Code tasks under the same Plan revision and client,
with live publish/path grants and a network grant for HTTPS or SSH. A local destination
must be a separately granted bare repository. Preparation does not push. Execute
consumes the action before transport, preserves source index/dirty bytes and
confirms only a fresh exact remote-ref readback. Query `remote_git_action_read`
for the recorded outcome. A consumed action with an uncertain effect requires
reconciliation; do not replay it. Git still rejects non-fast-forward updates.
The protected/main/release branches and merge acceptance are outside this route.
Authentication `none` installs no credential helper and does not enable SSH. An explicitly selected
`host_git_credential_manager` binds the existing executable and runs it without
interactive prompts; availability is not proof of authentication. For SSH, select
`host_openssh_agent`: it binds the existing host executable, agent endpoint and
public host-key database, uses agent keys without reading private-key files,
and rejects unknown/changed host keys without adding them. Literal
`user@host:owner/repository.git` and `ssh://user@host:port/path` are supported;
SSH configuration aliases, proxies, forwarding and custom commands are excluded.
Provider or host-key database changes invalidate a prepared push. Never place
tokens or passwords in URLs or action arguments. The engine owns Git and helper
processes through completion or timeout. For this plugin's own website ledger,
the committed v4 Plan binding must match the current Plan definition; unrelated
projects do not inherit that website rule.
