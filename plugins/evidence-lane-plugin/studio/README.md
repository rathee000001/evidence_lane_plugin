# Evidence Lane Studio linkage

This package binds the Codex-managed Evidence Lane plugin to the separately
installed Windows Studio, persistent engine and complete shared local toolchain.
`install.py` is a discoverable thin alias for `scripts/bootstrap.py`; all
provisioning, release verification and stable-root publication remain in the
canonical first-detection installer. MCP startup may start that installer in a
detached process, then returns without blocking an unrelated Codex task.

Studio is a visible read-only observer. Codex calls the engine through the
plugin MCP/SDK routes. The Studio bundle installs local dependencies once under
`C:/Apps/EvidenceLaneStudio`; external services still require explicit
configuration. The retired tunnel and Codex runtime/dedup tree have no role.

The contracts here are source bindings. Their installed flags stay false until
the exact packaged candidate is installed and read back.
