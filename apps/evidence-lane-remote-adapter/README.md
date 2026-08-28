# Evidence Lane Codex documentation site

This Next.js application is the public documentation and product-proof surface
for Evidence Lane 2.2. It does not expose an MCP endpoint, proxy a lifecycle
server, store accepted state, or participate in candidate, pointer, Fuse, or HIL
transitions.

Every public route renders the exact Git-tracked Markdown authority from which
its business-language story is derived. The canonical mapping is
`docs/REPOSITORY_MAP.md`; the website remains a read-only projection.

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

`remote_adapter` is the read-only website and transport projection. It owns no
project lifecycle, pointer, Plan, Goal, candidate, or HIL authority and is not
shipped as a remote lifecycle adapter.
