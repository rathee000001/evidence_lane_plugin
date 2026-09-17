# Evidence Lane Studio linkage

This package binds the Codex-managed Evidence Lane plugin to the separately
installed Windows Studio, persistent engine and complete shared local toolchain.
`install.py` is a discoverable thin alias for `scripts/bootstrap.py`; all
provisioning, release verification and stable-root publication remain in the
canonical first-detection installer. MCP startup may start that installer in a
detached process, then returns without blocking an unrelated Codex task.

Background engine discovery does not open Studio. The installed launcher opens
one exact private-profile window; a later open request restores a healthy
authenticated window. If its session expired or its engine changed, the owner
launcher closes only that private profile and opens one freshly ticketed window.
Failed profile closure prevents a new window from being launched. The twelve-hour
authentication lifetime remains unchanged.
Before a flat-root upgrade, the installer closes only that exact Studio profile,
requests owner-authenticated engine shutdown and confirms lock release before
preserving the previous release.
Valid engine-owned project locator references survive the upgrade independently
of immutable runtime files. Project databases, credentials, client/session bindings
and active selections are never copied into the new runtime.

Studio is a visible read-only observer. Codex calls the engine through the
plugin MCP/SDK routes. The Studio bundle installs local dependencies once under
`C:/Apps/EvidenceLaneStudio`; external services still require explicit
configuration. The retired tunnel and Codex runtime/dedup tree have no role.

The contracts here are source bindings. Their installed flags stay false until
the exact packaged candidate is installed and read back.
