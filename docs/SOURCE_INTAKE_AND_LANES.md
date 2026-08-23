<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Source Intake and lanes

This is the Git-tracked authority for the GitHub Pages Lanes story and the
current Vercel Lanes projection. Both must describe the same 3.0.0 contract:
ordered authorized intake, eighteen bounded lane contracts, selective lane
firing, content-addressed reuse, and explicit failures. Neither public surface
is an execution route or an authority substitute.

Source Intake accepts one or more ordered, user-authorized sources, classifies
their project role, and routes them into only the lanes that actually fire.
ChatLineage is included for visible project continuity. Unloaded lanes do not
receive empty folders or placeholder databases.

The registry supports eighteen canonical lane contracts plus governed project
overlays for code, Git history, documents, PDFs, images/OCR, spreadsheets,
research, discussions, plans, SQLite brains, custom schemas, and other declared
source types.

## Four-file lane surface

Each fired lane emits its governed machine and human surfaces:

- SQLite authority with schema and FTS5/BM25 indexes;
- Mermaid MMD topology;
- Graphviz DOT topology; and
- tools/route JSON with exact capability and version state.

Project sectors sit beneath one project folder. Canon, AI Learning, Memory, and
host-entry continuity retain their own first-class authorities outside ordinary
sector merging. ENV/UOP remains runtime governance and is not copied into the
project package.

## Query contract

Models receive stable paths and routes for exact task, lane, cross-lane,
parallel-lane, and authorized cross-project queries. Results return bounded
locators, hashes, relationships, and exclusions. Raw Markdown, full PV packages,
full Plan backlogs, and whole SQLite databases are not loaded into context.

For governed work, Plan authority is queried first. Changed applicable
authority lanes are refreshed at accepted Delta boundaries; unchanged bytes
reuse their content-addressed records and are not rewritten to manufacture a
new timestamp. A stale aggregate may be resolved by a bounded query to the
exact sector-lane SQLite authority, never by silently selecting an older route.

Every lane retains source identity, parser/tool capability state, hashes,
provenance edges, and fail-visible unsupported states. No-hit and missing-tool
results fail visibly rather than selecting an unrelated parser or connector.

## Schema evolution

The initial schema is a governed starting contract. Project requirements may
add rows, columns, tables, sheets, registries, or relationships through the
lane schema-evolution route. Existing accepted records and receipts remain
immutable; destructive, unknown-version, or hash-mismatched migrations fail
closed.
