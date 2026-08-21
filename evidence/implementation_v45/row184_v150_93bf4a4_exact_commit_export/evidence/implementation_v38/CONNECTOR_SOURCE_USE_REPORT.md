# Selected source, brain, and V5.9 generator audit

Recorded: 2026-08-03T23:09:10.628Z

## Outcome

The user clarification is correct on the version axis: **V5.9 refers to the
SQLite Brain Builder application/runtime, not to Graphify**. The application
source also contains an internal stable runtime named
`V5.3_CLEAN_COMPACT_REAL_LANES`. Those names coexist; one does not disprove the
other.

The earlier audit collapsed those axes and is superseded by this report.

The ten selected inputs replay successfully through the corrected intake
contract:

- four ZIP files route to `brain_loader` and receive full-file SHA-256
  identities;
- six extracted repository folders route to `project_engulf` and receive
  path-and-size-only identities;
- none of the extracted folders contains `.git`, so none is represented as Git
  history;
- none of the four packages binds a generator version plus executable SHA-256,
  so none independently proves which desktop binary generated it.

No selected source byte was modified or copied into the candidate. The
accepted PV3 pointer remains unchanged.

## V5.9 application evidence

The selected desktop shortcut resolves to:

`C:/Users/rathe/AppData/Local/Programs/SQLite Brain Builder V5.9/SQLiteBrainBuilderV5_9.exe`

Verified executable identity:

- SHA-256:
  `3E7B6AC4CFE59D2FE9E8CF95B66934549A1AE75BFF373F29176EA29DE86C3CAE`
- bytes: `367035613`
- executable creation UTC: `2026-07-09T10:37:17Z`
- executable last-write UTC: `2026-07-09T10:28:54Z`
- shortcut creation UTC: `2026-07-09T10:37:35Z`
- Windows file/product version metadata: absent

The user-provided full-app source contains explicit V5.9 runtime evidence:

- `gui/main_window.py:132-144` wires the V5.9 functional runtime;
- `runtime/chatlineage_state_travel_v57.py:15` declares the V5.9 State Travel
  patch;
- `runtime/flash_prompt_v57c.py:39,99` declares the V5.9 boot and write-scope
  law;
- `runtime/stable_runtime_v53.py:319` separately declares the V5.3 stable
  runtime constant written into older internal receipts.

This proves the source contains a V5.9 application layer over internal V5.3
runtime/schema lineage. It does **not** yet prove that the selected executable
was built from those exact source bytes, or that it generated any particular
ZIP. The current selected executable is dated July 9. A prior build or reinstall
could explain the user's pre-July chronology, but no earlier matching executable
was found in the bounded F-drive and Downloads searches.

## Four brain packages

### GitHub Agentic Coding brain

- ZIP SHA-256:
  `0CC50621A5355A4D77626601553605179B167B95DB485EE1F96F335D9A7187D4`
- package format: `UEPC_SECTOR_PACKAGE`
- indexed local-code SQLite SHA-256:
  `63DBC28277F4E7F0C2484551A8DA86A3A271E42B1A42A4398ECD4C17EA89F92C`
- SQLite integrity: `ok`; foreign-key errors: `0`
- source files: `6626`; code files: `3623`; chunks: `12435`
- Git commits, branches, remotes, and file changes: `0`

It is a useful folder-derived index of `gh-aw`, but not Git-history or generator
binary evidence.

### Evidence Lane v0.9.0 brain

- ZIP SHA-256:
  `C51378BB05BB689031C84BC0ED7677D5C71C414C11DD2E564E68ABADBD9CCE61`
- package format: `UEPC_SECTOR_PACKAGE`
- selected SQLite SHA-256:
  `0B4D0057785294EB45A9F92157D83160C76780D6BAD59D04E598A175CA0679A9`
- SQLite integrity: `ok`; foreign-key errors: `0`
- indexed repository identity:
  `evidence_lane_plugin-agent-evi-connector-mcp-hil-v0.9.0`

It remains historical/negative-delta evidence. Its outer filename cannot make
it newer than the current v1.2 candidate.

### RIL and Gold mini brains

Both are `EvidenceOS_V2_FULL_VERTICAL_project_mini_brain` packages created on
June 16, 2026. They are a different package family from the UEPC sector
packages.

- RIL: ZIP
  `53B2777588A995E0444DA21B378EE6F81732B2118B0F1FE8C72189506DB6D0AE`,
  SQLite
  `9A15E0D0E23763E10E0B7929EDA2EA617C4859C624BB49BBFDBC6B8B2179ECA6`,
  `47274` nodes, `101260` edges, `127` source files, `0` read errors.
- Gold: ZIP
  `D42EF938FC6F69F3446EC6A079075B26C8C003BC340D0C843E7BDCE0D1177E8B`,
  SQLite
  `1F47F9DAF14687D6EB53F7698A9A489B8B89D95C9B3CD3529CE3C5D23F868EEE`,
  `143998` nodes, `296484` edges, `444` source files, `0` read errors.

They preserve historical RIL/Gold product, motion, pill, and AI-studio evidence.
They do not independently bind the V5.9 executable and they are not substitutes
for current RIL/Gold repository authority.

## Six extracted official sources

The intake receipt now says exactly what it knows: path and size identity, not
content identity and not Git history. Separate read-only ZIP-to-tree checks
provide the stronger provenance:

| Source | Files | Paired commit | Tree result |
|---|---:|---|---|
| `github/gh-aw` | 6626 | `61bd1aa20cb68d77d2e0d4b4974b4480cb1b305c` | 6626/6626 exact |
| `Graphify-Labs/graphify` | 776 | `00efd6e7969837ae4a9f11d8d504dcd3b20b09df` | 776/776 exact |
| `github/codeql` | 58970 | `74c8994c9fa3ca4551c01879ef9f74e3e09e791a` | paths/sizes exact; 1024 CRC sample exact; sampled Git blob needs CRLF normalization |
| `github/github-mcp-server` | 536 | `3778a41476e31a072430cfee7c5d31c5f72def60` | 536/536 exact |
| `github/branch-deploy` | 208 | `7ad5ec6a7e19e3e341846e4d33c4ed779b3e8036` | 208/208 exact |
| `github/local-action` | 224 | `b9351d8a8f1e6eed27646f4d892b49a3847ba180` | 224/224 exact |

CodeQL was used only as a negative-test/static-analysis reference. The CodeQL
CLI was not installed or run, so this candidate makes no CodeQL scan claim.

## Evidence-justified implementation deltas

Two bounded deltas are justified:

1. Connector routing now evaluates the ordered guards `ACTIVE`, `GRANT_LIVE`,
   `CAPABILITY_AND_ACTION_ALLOWED`, `LANE_ALLOWED`, and `HOST_ALLOWED`, records
   the first failure, honors one exact preferred connector, and fails closed on
   ambiguity.
2. Source intake now separates full archive bytes, extracted path/size identity,
   Git commit/tree/worktree state, package format, internal runtime/schema
   version, and desktop generator identity. A filename cannot establish a
   generator.

The other source-derived patterns are used as conformance and negative-test
guidance: GitHub MCP read-only/toolset upper bounds, Branch Deploy no-op and
commit-safety semantics, Local Action secret suppression, Graphify
extracted-versus-inferred provenance, and CodeQL-style negative cases. Their
runtimes and source bytes were not transplanted.

## Verification

- corrected ten-source replay: **PASS 10/10**
- Ruff: **PASS**
- mypy: **PASS**
- focused source-intake/Git/universal tests: **23 passed in 29.68s**
- source bytes modified: **false**
- accepted pointer moved: **false**
- candidate created: **false**

## Verdict

**FIX-THEN-PURSUE — confidence 99%.**

The source audit and the two narrow implementation deltas are now sound enough
to continue toward the single PV4 candidate. Generator attribution remains a
declared user observation rather than cryptographic provenance. This verdict
would change with a package receipt binding the exact V5.9 executable SHA-256,
an earlier/reproducible V5.9 build receipt that proves the pre-July chronology,
or a failing conformance test that contradicts the implemented contracts.
