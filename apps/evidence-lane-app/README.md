# Evidence Lane website

The Next.js public frontend for Evidence Lane. It is separate from the
executable plugin and the Windows read-only Studio application. Website
illustrations do not connect to a user's engine or execute project actions.

## Development

Use Node.js and pnpm with the checked-in `pnpm-lock.yaml` and workspace policy.

```sh
pnpm install --frozen-lockfile
pnpm dev --hostname 127.0.0.1 --port 3100
```

Production build:

```sh
pnpm build
pnpm start
```

## Pages and assets

The application contains Home, Product, Workflows, Studio, How it works,
Integrations, Docs and Download, plus practical guides, 24 individual workflow
guides and information/license routes. Source is in `app`; local fonts,
branding and scene assets are in `public`. Universes and interactive scenes
are rendered in code with motion controls and compact/reduced-motion fallbacks.

Public plugin/release labels use the 4.0.7 source candidate snapshot. Catalogue
support, public release availability, installation and native execution are
distinct states. Source snapshot scripts inspect the owning plugin contracts;
they are maintainer tools and are not required to serve the website.

## Deployment boundary

Vercel's project root for this app is `apps/evidence-lane-app`. The checked-in
`vercel.json` enables Git deployments, and page metadata permits indexing. The
first production deployment and `evidencelane.org` domain cutover still occur
only at the explicit D142 release gate after the old Vercel project is removed.

Ship source, configuration, required public assets and dependency metadata.
Exclude dependency/build caches, preview logs, local Windows launchers and
internal review/progress artifacts. Never include project databases or secrets.
