# Contributing to Evidence Lane

Work against the current `codex/v4.0` source and keep each change reviewable.
Preserve existing dirty bytes, project data and Git history. Do not commit
credentials, runtime state, project databases, dependency caches or generated
test workspaces.

Before changing a component, identify its current registry owner, authority or
sector lane, affected operation contracts and direct tests. Generated schemas,
SDK bindings, MCP bindings, skills and package manifests must come from their
current generator rather than a hand-maintained parallel route.

Run the smallest current CI or focused test profile that covers the change.
After changing a shared registry, generator, schema or runtime boundary,
regenerate the dependent projections and verify there are no stale members.
Report what changed, why, the exact checks that ran and any remaining limit.

Security issues follow [SECURITY.md](SECURITY.md). Publication, plugin
installation, remote writes, merge to `main` and release remain separate steps;
ordinary source contribution does not authorize them.
