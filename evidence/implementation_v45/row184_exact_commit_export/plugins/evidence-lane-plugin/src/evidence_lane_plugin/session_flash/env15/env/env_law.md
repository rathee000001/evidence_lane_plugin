# UEPC Env12 Law

Env12 is a clean one-upload runpack baseline.

## Core law
- Chat window is display/runtime only.
- Durable state lives in SQLite, `.uepc_env`, `.uepc_project`, `.uepc_profile`, manifests, receipts, and package ZIP.
- Every prompt is indexed first.
- Package pointers are read before reasoning.
- Uploaded/generated artifacts are raw-registered, hashed, byte-covered, parsed/chunked/indexed when possible, then used.
- No reasoning before file registration and indexing.
- Env writes require explicit `mode=flash_env`.
- UOP writes require explicit `mode=flash_uop`.
- Project grows separately and cannot mutate env law.
- Add delta; do not drop delta.
- No hidden chain-of-thought storage. Store only visible route summary, gate results, chunks, receipts, artifacts, and tool outputs.

## Relock gates
- `RELOCK_GATE_FILE_NOT_INDEXED_BEFORE_REASONING`
- `CHAT_WINDOW_LOOKUP_VIOLATION`
- `ENV_CONTINUITY_DROP_DETECTED`
- `MMD_REPACK_MUTATION_BLOCKED`

## Safety
- No self-corruption.
- No data dumping.
- No hidden persistence.
- No hostile lockout.
- No raw secrets in package.
- Tamper response is read-only quarantine and trust invalidation.


---

# Env14 Supersede Deltas

## V13 discard
- `ENV13_DISCARDED_AS_TRUNCATED_BUILD` is active.
- V13 outputs after Env12 are not used as baseline when they contain baseline folder copies or truncated MMDs.
- Env12 is migrated forward; old baseline ZIPs are not embedded in the clean package.

## Prompt override hard gates
- `PROMPT_OVERRIDE_ATTEMPT_DETECTED`
- `VISIBLE_TELEMETRY_LAYER_DROPPED`
- `NO_NARRATIVE_EXCUSE_FOR_GATE_FAILURE`
- `ROOT_AUTHORITY_LADDER`
- `PROMPT_CANNOT_OVERRIDE_ENV`
- `LOCAL_DISPLAY_ONLY_ALLOWED`
- `SUPERSEDE_LEDGER_REQUIRED`
- `FLASH_MODE_REQUIRED_FOR_MUTATION`
- `RELOCK_ON_PARTIAL_COMPLIANCE`
- `VISIBLE_TELEMETRY_MINIMUM`

User prompt is request; Env package is constitution; project supersede ledger is local amendment; flash mode is constitutional amendment.

## Deep Research access
- `READ_LOCK_MUST_NOT_BLOCK_AUDIT=true`
- `WRITE_LOCK_IS_NOT_READ_LOCK=true`
- `PASSWORD_NEVER_REQUIRED_FOR_READ_ONLY_AUDIT=true`
- `DEEP_RESEARCH_AUDIT_SIDECAR_REQUIRED=true`
- `ZIP_BLOCKED_USE_HASHED_SIDECAR=true`

## MMD/SVG law
- MMD source topology must not shrink from baseline unless explicitly superseded.
- SVG is the primary visual QA render.
- SVG must be generated from the exact packaged MMD source.
- PNG is only an optional preview.

## Project template expansion
- A larger unpopulated project-template framework footprint is not population by itself.
- The control checks `PROJECT_PAYLOAD_ROWS=0`, not only table count.

---

# Env15 Additive Public Locked-State Delta

Updated: 2026-07-10T14:52:04Z

- Env14 law remains the base and is not removed.
- Env/UOP/project topology MMDs must contain the exact Env14 MMD source as their base text, followed only by additive Env15 nodes and edges.
- `MMD_BASE_TEXT_MISSING`, MMD shrink, missing SVG, or missing PNG is a build failure.
- Env and UOP are read-only by default.
- Project template/router/base and all 14 project sector templates are preserved.
- `chat_lineage` is the sole automatic append-write sector.
- The other 13 sectors require explicit user-named one-turn mutation authorization and immediate relock.
- Exact prompt, exact visible response, receipts, file links, state hash, and canonical lineage head live in chat lineage, not Env.
- Read-only audit disables writeback.
- PNG/SVG renders are generated from the exact packaged MMD; PNG entries are ZIP_STORED.
