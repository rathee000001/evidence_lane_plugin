# Security policy

Report security issues privately to the repository owner. Do not put
credentials, private project data, unpublished source material or exploit
details in a public issue.

Evidence Lane keeps project authorization, external effects, release identity,
installation and human decisions separate. A successful test, local process,
catalog entry, plugin load, CI run or deployment does not grant another
authority.

Secrets must come from the host environment or an approved secret manager.
They are excluded from source intake, release assets, project databases,
ChatLineage, receipts and ordinary logs. External connectors keep credential
names and bounded configuration metadata; credential values are never copied
into the project or plugin package.

Source intake uses bounded paths and bytes, rejects links and credential-shaped
content, preserves exact source identities and records exclusions without
returning secret values. Archive processing rejects traversal, alternate data
streams, reserved device names, links, collisions and budget overruns.

First detection accepts only exact sparse-root source and SHA-256-bound assets
from the same immutable repository release. It uses one installation lock,
stages before promotion, allows only fixed offline materialization operations,
requires wheel and license evidence, writes a complete immutable-file manifest
and validates that manifest before reusing an existing release.

Studio sessions are read-only at both UI and backend boundaries. Native MCP and
optional remote clients remain authenticated, project-scoped and permission
checked. External writes require the registered operation, exact resource,
current grant and effect receipt.

Security reports should include the affected revision and path, prerequisites,
observed behavior, expected boundary and a minimal reproduction that contains
no secret or private user data.
