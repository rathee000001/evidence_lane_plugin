<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Source Intake and lanes

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


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
