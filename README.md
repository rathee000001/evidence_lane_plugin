# Evidence Lane Plugin 0.6.0

Evidence Lane is a local-first, Git-backed evidence lifecycle for Codex and a
durable remote MCP for ChatGPT. It builds immutable unaccepted project-version
candidates, exposes a six-way human gate, and moves accepted truth only after
an exact case-sensitive `APPROVE` is supplied to the Fuse tool.

## Public control surface

Root `/evi` conditionally presents `/evi-state-travel` only when a sealed
accepted-PV handoff exists. Otherwise it exposes exactly these six controls in
order:

1. `/evi-boot`
2. `/evi-rollback`
3. `/evi-build`
4. `/evi-refresh`
5. `/evi-mode`
6. `/evi-source-intake`

`/evi-exit-boot` is the explicit session-deactivation command. Internal MCP
tool names remain stable for compatibility; they are not extra public controls.

Boot is atomic: runtime doctor, locked ENV15/UOP15 Flash verification, host and
storage capability detection, then boot or resume of the single governed
session. That session persists across host tasks until Exit Boot. An ephemeral
runtime without a configured transactional durable connector fails closed.

Source Intake accepts ordered sources, auto-detects their lanes, and supports
exact per-source overrides. It covers all eighteen canonical lanes and Project
Engulf and always includes Chat Lineage. Mode stays separate and accepts
ordered locked-mode intersections plus explicit custom mode schemas.

## Universal brain

The canonical lanes are GitHub Code, Local Code, Chat Lineage, Discussion,
Analysis, Plan, Mode, Docs, Data/Excel/CSV, PPT, PDF/OCR, Images/OCR, Artifacts,
Custom, SQLite PV Candidate Loader, Research, Project Engulf, and SQLite Brain.
Each emits lane-specific SQLite, Mermaid, DOT, tool identity, refresh evidence,
and a sealed manifest.

Code lanes index all reachable Git commits, refs, changes, exact blobs,
content-addressed chunks, occurrences, and FTS. Refresh reuses unchanged lane
artifacts and chunk CAS entries and records changed-section reuse. Candidate
project-sector overlays fan visible Chat Lineage into the appropriate sectors
but never write accepted sector truth before Fuse.

Visible Chat Lineage may contain full user prompts and steers, visible assistant
output, actor type, model/submodel when available, token metrics when available,
tools, commands, files, tests, builds, output links, hashes, and pointers.
Secrets are redacted. Hidden chain-of-thought and private model reasoning are
rejected and never stored.

Up to eight additional persistent connector or AI-toolchain plugins can be
registered in the connector brain. Registration stores environment-variable
names, not secret values. Drop is history-preserving and requires the exact
`DROP:<plugin-id>` token.

## Lifecycle

PV1 is the only normal full build. Later candidates use incremental Refresh or
a declared schema/tool-identity fallback. A candidate remains explicitly
unaccepted through tests, Git publication, installation, or deployment.

The six HIL choices are:

- `APPROVE`
- `APPROVE_WITH_DELTA`
- `MORE_RESEARCH`
- `ROLLBACK`
- `REJECT`
- `FAIL`

The general HIL recorder rejects `APPROVE`; only the dedicated Fuse API accepts
that exact token. Rollback moves only the accepted pointer among immutable
accepted versions. After Fuse, State Travel verifies a sealed handoff in a
genuinely fresh task or chat and stops in `WAITING_FOR_NEXT_USER_COMMAND`.

## Storage and hosts

- Durable Codex desktop/CLI uses the user-owned local SQLite store and local
  Git checkout.
- ChatGPT reaches a durable MCP origin. The included Vercel project is only a
  thin ChatGPT HTTPS adapter; it verifies exact release identity and proxies to
  that origin. It stores no Evidence Lane authority and is not a general router.
- Google Drive is an optional verified mirror/fallback. It is never primary
  when durable local storage exists and is not a transactional runtime-state
  substitute for an ephemeral server.

See [host capabilities](docs/HOST_CAPABILITY_MATRIX.md), [architecture](docs/ARCHITECTURE.md),
and [remote deployment](docs/REMOTE_DEPLOYMENT.md).

## Local build and validation

Requires Python 3.11+ and Git 2.30+.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy plugins/evidence-lane-plugin/src
```

Build the durable container with:

```text
docker build --pull --tag evidence-lane-plugin:0.6.0 .
```

It serves `/mcp` and `/healthz` on port 8080 and requires one writer plus a
durable volume at `/var/lib/evidence-lane`.

## Codex installation

Pin the reviewed Git ref or exact commit through the Git marketplace route:

```text
codex plugin marketplace add rathee000001/evidence_lane_plugin --ref REVIEWED_REF
codex plugin add evidence-lane-plugin@evidence-lane-github --json
```

The plugin cache is executable material, not source authority. Validate the
installed manifest and runtime identity, and start a genuinely fresh Codex task
after an update. Do not remove an older working install until the new exact-SHA
install has been verified.

## Security and release gate

Never place credentials in source, manifests, SQLite, PV packages, Chat
Lineage, receipts, logs, or prompts. An OpenAI key disclosed during the July 31
implementation intake is treated as compromised. It must be revoked manually
in the OpenAI Platform before release or deployment; the key is not reproduced
or saved here. See [SECURITY.md](SECURITY.md).

Publication, installation, or a Vercel preview never accepts a candidate,
moves the pointer, merges `main`, or authorizes State Travel.
