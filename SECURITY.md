# Security boundary

Report security issues privately to the repository owner. Do not open a public
issue containing credentials, source code, PV packages, access tokens, or
private-engine material.

The server enforces project and transition authorization. Model instructions,
tool annotations, plugin installation, candidate approval, and remote Git access
are separate authority layers.

Secrets must be supplied through the host environment or an approved secret
manager. They are redacted from normal errors and never belong in:

- `.codex-plugin/plugin.json`;
- `.mcp.json`;
- `code.sqlite`;
- PV packages or receipts;
- ChatLineage;
- Google Drive metadata;
- Git remotes embedded in receipts;
- screenshots or ordinary logs.

Remote Git push is disabled unless an accepted candidate exists and a separate,
exact action request and confirmation are both recorded.
