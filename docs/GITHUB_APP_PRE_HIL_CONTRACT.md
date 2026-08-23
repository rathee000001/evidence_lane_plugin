# GitHub App pre-HIL contract

This contract implements the provider-neutral proof boundary and the current
maintainer branch-write route for the GitHub App and signed tester-artifact
flow. It does not register an app, persist credentials, invite a tester,
publish an artifact, change App permissions, write `main`, or promote any
Evidence Lane authority.

## Pre-HIL implementation

- `github-app-manifest.schema.json` and `GitHubAppManifest` permit only an
  explicitly selected repository set, an allowlisted event set, and a bounded
  permission model. The maintainer exact-commit route requires metadata read,
  contents write, and workflows write only when workflow files are changed.
- `WebhookVerifier` verifies HMAC bytes before JSON parsing, bounds delivery
  age, treats an identical delivery as idempotent, and rejects a reused
  delivery identity with changed bytes.
- `InstallationTokenBroker` binds project, task, installation, repository,
  permission subset, request time, expiry, and idempotency. The provider token
  remains in caller-owned memory; only its hash can appear in a receipt.
- `map_check_run_receipt` maps GitHub check state while explicitly denying PV,
  Learning, Canon, Fuse, HIL, and pointer effects.
- `ArtifactEntitlementStore` models tester request, human approval, terms hash,
  exact artifact hash, short-lived signed authorization, download verification,
  caller-reported installation result, privacy-minimized feedback, expiry, and
  revocation. It grants no development-repository credential and performs no
  installation itself.
- `DeterministicMockGitHubProvider` is a disposable test adapter. It performs no
  network call and holds no production identity.
- `ProductionDeliveryIdentity` and `GitHubAppProductionDeliveryRoute` form the
  credential-free checkpoint SDK seam. They bind one installation capability,
  exact repository/branch/commit/tree, successful Actions head, exact package,
  installed branch-commit recovery slot, mutable local slot, and unchanged
  main-merge fallback. A changed replay, non-green run, package substitution,
  slot alias, or fallback mutation fails closed.
- `scripts/codex_release/seal_github_app_production_delivery.py` is the public
  executable adapter for that seam. It consumes exact non-secret identities,
  writes one immutable receipt, and performs no Git, network, installation,
  candidate, HIL, or pointer action.
- `GitHubAppExactCommitPushRoute` and
  `scripts/codex_release/push_github_app_exact_commit.py` implement the current
  branch-write route. The local object is only a deterministic preview and is
  accepted only when both its author and committer are the canonical
  `evidence-lane[bot]`. The App recreates exact blobs, the tree, ordered commit
  parents, and the commit through the Git Database API, then fast-forwards one
  named feature branch with `force=false`. A human-authored commit, stale
  remote parent, different object identity, protected/default branch, or
  credential fallback fails before ref mutation.

Every receipt excludes raw secrets, bearer values, and artifact bytes. The
negative suite covers forged signature, delivery replay conflict, stale token,
overbroad permission, cross-repository scope, non-bot commit identity, wrong
parent/tree/commit objects, missing or revoked entitlement, artifact
substitution, and check-success authority escalation.

## Post-HIL user-controlled boundary

The host-managed GitHub App supplies short-lived installation authority to the
exact branch-write route; secrets remain in process memory and never enter a
receipt. The credential-free checkpoint sealer remains a separate route and
never receives those values. App registration, credential provisioning,
repository or organization installation, permission changes, external tester
distribution, public listing, main promotion, publication, and production
deployment remain separately authorized operations.
