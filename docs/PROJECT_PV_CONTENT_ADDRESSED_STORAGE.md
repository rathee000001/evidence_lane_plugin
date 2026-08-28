<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Project PV content-addressed storage

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


The registered external project root is the sole live working authority. It is
also the current working candidate overlay: Evidence Lane records a bounded
identity receipt over those bytes and does not copy them into a second
`candidates/` tree.

## Authority layout

- `active_pointer.json` remains compare-and-swap lifecycle metadata outside the
  accepted artifact.
- `accepted/` contains only the one governed root-PV snapshot ZIP selected by
  the current retention law; it is never queried as live authority.
- Transient sessions, locks, candidate-overlay receipts, capture-route receipts,
  and accepted-swap journals are not Project Truth and are excluded from the
  accepted archive identity.
- The live project root is never rewritten merely to query, validate, or stage a
  candidate.

## Future exact-HIL transition

Only the existing exact Project HIL promotion route may invoke the transition.
It must:

1. Validate the candidate receipt, parent accepted pointer, proposed PV, and
   post-seal approval receipt.
2. Build one deterministic ZIP outside `accepted/` while proving that the live
   working identity did not change during the build.
3. Store each unique SHA-256 object once and map every project-relative member
   through `PROJECT_PV_MANIFEST.json`.
4. Validate the exact ZIP member set, every object hash, the canonical manifest
   hash, and the candidate identity before any swap.
5. Swap `accepted/` under the project store lock, then compare-and-swap the
   pointer. If the pointer write fails, restore the prior accepted directory.
6. Verify that exactly one accepted artifact matches the new pointer before
   purging the prior artifact under the user's single-retention policy.

Plan acceptance, package installation, Git activity, tests, and storage status
queries never invoke this transition and never move the pointer.

## Retention and rollback semantics

The single retained archive carries the prior accepted PV and manifest identity
as lineage. It does not claim that the prior accepted bytes remain embedded or
that exact prior-byte rollback is available after the approved purge. Restoring
older bytes therefore requires separately retained external evidence or a
future, separately approved retention policy.

Temporary accepted views, when explicitly required by a hard-restore route, are
materialized outside the project authority and removed after use. Ordinary
status, query, Plan, and Delta work never opens the accepted ZIP.
