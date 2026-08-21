# GitHub App pre-HIL contract

This contract implements the local, provider-neutral proof boundary for the
GitHub App and signed tester-artifact flow. It does not register an app, create
or receive credentials, install against a repository, invite a tester, publish
an artifact, or mutate GitHub.

## Pre-HIL implementation

- `github-app-manifest.schema.json` and `GitHubAppManifest` permit only an
  explicitly selected repository set, an allowlisted event set, and a bounded
  read/check permission model. Source and workflow write are rejected.
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

Every receipt excludes raw secrets, bearer values, and artifact bytes. The
negative suite covers forged signature, delivery replay conflict, stale token,
overbroad permission, cross-repository scope, missing or revoked entitlement,
artifact substitution, and check-success authority escalation.

## Post-HIL user-controlled boundary

The host-managed GitHub App may supply installation and Actions evidence to the
checkpoint seam, but the seam never receives its private key, webhook secret,
or installation token. App registration, credential provisioning, repository
or organization installation, external tester distribution, public listing,
main promotion, publication, and production deployment remain separately
authorized operations.
