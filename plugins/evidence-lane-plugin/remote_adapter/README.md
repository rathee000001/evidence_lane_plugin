# Evidence Lane Codex documentation site

This Next.js application is the public documentation and product-proof surface
for Evidence Lane 2.2. It does not expose an MCP endpoint, proxy a lifecycle
server, store accepted state, or participate in candidate, pointer, Fuse, or HIL
transitions.

Every public route renders the exact Git-tracked Markdown authority from which
its business-language story is derived. The canonical mapping is
`docs/PUBLIC_SITE_SOURCE_MAP.md`; the website remains a read-only projection.

The installed Codex plugin is built separately from
`plugins/evidence-lane-plugin/`; package rehearsal excludes this site directory.
The site may explain source-backed behavior, but it cannot substitute for an
installed-package receipt, native catalog proof, tests, CI, or post-restart HIL.

## Local verification

```powershell
pnpm install --frozen-lockfile
pnpm run test:execution-panel
pnpm run test:studio-query
pnpm run build
```

The legacy directory name `remote_adapter` is retained only to avoid an
unnecessary path migration in the active v2 correction. No remote lifecycle
adapter is shipped from this directory.
