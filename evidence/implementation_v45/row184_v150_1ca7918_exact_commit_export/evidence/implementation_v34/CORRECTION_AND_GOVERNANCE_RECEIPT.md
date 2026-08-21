# Universal brain HIL correction and governance receipt

This v34 receipt is additive to the earlier evidence directories. It records
the candidate-bound correction authorized by exact `APPROVE_WITH_DELTA`; it
does not treat that token as `APPROVE`, Fuse any candidate, move the accepted
pointer, merge `main`, or perform State Travel.

## Binding and authority

- project: `test-codex-evidence-lane-plugin`
- existing governed session resumed: `session_01kyg2m4qeqy3cbkmj278zp96h`
- destination host: `019fbb12-bb01-7503-89b5-233a523cba5a`
- corrected candidate: `PV1_CANDIDATE__RUN_01KYS9AD69SQNW772Q5CFJGM6E`
- decision receipt: `decision_01kyxhw7tcn99jakzvt61xs4hv`
- correction task: `task_01kyxhx7teef80ydg5jmnkqve6`
- branch authorization receipt: `branchauth_ef3513904b7c2ff77f58e3bbb74431e7`
- exact base: `2edef6fe8693382eb7c0280f34ebb9b7e9a31fd6`
- exact branch: `agent/evi-universal-brain-hil-v0.6.0`
- commit binding: the governed implementation commit containing this receipt

The decision receipt was checked against the live pending candidate before
implementation. No later decision receipt superseded it. The correction is
bounded to the 36-Delta plan represented in `LINEAR_DELTA_BACKLOG.json`.

## Boot boundary

The fresh host task ran the atomic `/evi-01-boot` law without State Travel:
runtime doctor, locked `ENV15_UOP15_PUBLIC_LOCKED_20260710` Flash verification,
and `session_resume` of the existing session rather than a duplicate boot.
At boot and throughout implementation, accepted PV remained null and pointer
generation remained zero.

## Implemented release surface

Version 0.6.0 exposes exactly six public controls after root `/evi`: Boot,
Rollback, Build, Refresh, Mode, and Source Intake. State Travel remains a
conditional pre-control recovery event. Exact case-sensitive `APPROVE` remains
the only Fuse authority.

The implementation adds host-aware atomic Boot and fail-closed storage
routing; generalized 18-lane plus Project Engulf intake; custom/intersection
Mode; visible secret-redacted ChatLineage; candidate-only project overlays;
full reachable Git-history, content and chunk CAS, FTS and incremental refresh;
lane SQLite/MMD/DOT artifacts; bounded connector governance; and a thin
ChatGPT remote MCP adapter that cannot become the general router.

No hidden chain-of-thought or private model reasoning is stored. Candidate
project-sector overlays do not enter accepted truth before Fuse.

## Preserved boundary and candid exception

All 36 tasks remain `QUEUED`, and their task IDs and sequence are intact.
However, the append operation reserialized the live JSON document and
backfilled derived fields on the first 21 tasks. Therefore literal byte-for-byte
preservation of the old file representation is **not proven and must not be
claimed**, even though the original 21 task IDs, order, status, contracts, and
hash-chained event semantics remain present. This is a release/HIL
nonconformance requiring an explicit human disposition.

The legacy State Travel expected hash
`CC2B4C6F2DAAD4C7D0F3A722AB0AB240A7F4B922A17F585CF446A8E571CA591B`
also differs from the immutable original package's observed manifest seal
`DDC32EE0DA82DD9B015778633D5B8A1DE4299E09FCF7361094B3DD9AD27EB6C4`.
The successor addendum bridges both facts; the original package is never
rewritten.

## HIL boundary

This receipt authorizes implementation, validation, one non-force branch push,
exact-SHA host installation attempts, an eligible ChatGPT-adapter-only preview,
and construction of one fresh **UNACCEPTED** candidate. It does not authorize
Fuse, pointer movement, main merge, or State Travel. The workflow must stop at
the six-way HIL.
