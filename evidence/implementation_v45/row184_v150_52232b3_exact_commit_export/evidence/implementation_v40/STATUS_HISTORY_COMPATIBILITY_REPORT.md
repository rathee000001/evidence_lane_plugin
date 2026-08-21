# Delta 065 — Accepted-history status compatibility

## Corrected defect boundary

Accepted PV4 is not defective. Direct strict validation of accepted PV4 passes all
18 lane topologies, project topology, MMD/DOT identity parity, package seals, and
1,328 topology claims with zero failures.

`EvidenceLaneService.status()` failed because it applied the current promotability
contract to every accepted historical PV. Enumeration reached immutable PV3 before
PV4; PV3's bytes and seals are valid, but its topology predates the current v3
promotability contract. That strict historical requalification raised
`PV_LANE_BUNDLE_INVALID` and prevented status from reporting the valid current PV4.

## Corrective contract

- The current accepted pointer is validated with `require_promotable=true`.
- Every noncurrent accepted PV is validated with `require_promotable=false`.
- Historical mode still validates package checksums, manifests, receipts, SQLite
  integrity, metadata, and secret-safety boundaries.
- Status identifies `CURRENT_PROMOTABLE` versus `HISTORICAL_EVIDENCE`, reports
  whether promotability was required, and exposes topology status without silently
  relabeling historical topology as current-valid.
- A tampered historical package still fails closed.
- A topology-invalid current accepted package still fails closed.

## Immutable authority

- Accepted PV: `PV4`
- Pointer generation: `4`
- Accepted manifest: `CC7B84B231CB0A54E16F08BBC403B5F07041F974623BB4D0386396AFBDA622E9`
- Accepted package: `1943BE56AC9C1F5A04230C8FF8CDA38CD19B05F058B4B30B2BC73D5D9988330C`
- Accepted source commit: `c86fbc5f39d3e7caaaf8ccdcbae72b7800cb29ce`
- Accepted source tree: `c144953a63537617190d8d90a3a79d2d0a647008`
- Historical PV3 manifest: `826DCBDD78B7CD82DE6A1AE76B87B9329104E678B2F374304CAB7CA93505ADC1`
- Historical PV3 package: `73585E6A3090C0F8D29431B6A405843C5DAA021287EEAA7A2E3BDA7502860730`

## Prior POC and forensic evidence

- POC root: `F:\test codex POC\evidence-lane-v1.2-delta063-c86fbc5`
- Actual Word POC: `F:\test codex POC\evidence-lane-v1.2-delta063-c86fbc5\Evidence_Lane_v1.2_Delta063_POC_Report.docx`
- Initial forensic audit: `F:\test codex POC\evidence-lane-v1.2-delta063-c86fbc5\FORENSIC_INITIAL`
- Refresh forensic audit: `F:\test codex POC\evidence-lane-v1.2-delta063-c86fbc5\FORENSIC_REFRESH`

Those artifacts remain exact accepted-PV4 evidence and are not rewritten by this
corrective candidate.

## Candidate boundary

Delta 065 may change only the accepted-history status semantics, focused regression
tests, and this v40 evidence. It must seal a new unaccepted PV5 and stop at the
six-way HIL while retaining PV4 generation 4. It must not merge `main`, deploy
Vercel, install Codex or ChatGPT, or consume State Travel before that HIL.

## Preseal verification

Verification results are recorded in `PRESEAL_VERIFICATION.json`. Candidate identity
and postseal validation remain external immutable receipts because they cannot be
embedded in the source commit that they validate.

The real project was also exercised through the repository source runtime. Status
returned `PASS`, retained PV4 generation 4, reported PV1/PV2 as valid historical
evidence, reported PV3 as intact but nonpromotable historical evidence, and applied
the strict current contract only to PV4.

## Read-only ChatGPT 404 finding

The supplied ChatGPT transcript's `MCP SSE probe returned 404` is not evidence of a
Git or plugin defect. Local logs show that the configured tunnel successfully
forwarded commands before its child and daemon stopped during the prior Windows
shutdown. After reboot, no tunnel daemon or readiness files existed. The phrase
"SSE probe" is generic host wording for the unavailable tunnel route; adding an SSE
implementation would be the wrong correction. Restart/rescan verification remains
post-HIL and outside Delta 065.
