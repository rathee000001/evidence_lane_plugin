<p align="center">
  <img src="docs/assets/evidence-os-full-logo.png" alt="Evidence OS" width="900" />
</p>

<p align="center">
  <strong>Governed project evidence that stays inspectable across AI tasks while acceptance remains human-controlled.</strong>
</p>

<p align="center">
  <a href="docs/ARCHITECTURE.md">Architecture</a>
  &nbsp;·&nbsp;
  <a href="docs/HOST_CAPABILITY_MATRIX.md">Host capabilities</a>
  &nbsp;·&nbsp;
  <a href="docs/REMOTE_DEPLOYMENT.md">ChatGPT deployment</a>
  &nbsp;·&nbsp;
  <a href="docs/DELTA_001_043_TRACEABILITY.md">Delta traceability</a>
  &nbsp;·&nbsp;
  <a href="SECURITY.md">Security</a>
</p>

<p align="center">
  <img src="plugins/evidence-lane-plugin/assets/evidence-lane-icon.png" alt="Evidence Lane plugin icon" width="104" />
</p>

# Evidence Lane Plugin 0.8.3

Evidence Lane is a local-first, Git-backed evidence lifecycle for Codex, with a
durable remote MCP boundary for ChatGPT. It turns visible project sources and
AI-task lineage into immutable, content-sealed candidate packages. A candidate
can be built, tested, pushed, installed, or deployed and still remains
unaccepted. Accepted truth moves only when a human supplies the exact
case-sensitive token `APPROVE` to the dedicated Fuse operation.

This repository is independently developed Evidence Lane R&D. It does not use
a third-party product or comparison report as source authority, does not copy
another implementation, and does not treat outside questions as its test
method. Claims below are bounded to source, automated tests, sealed artifacts,
and explicitly labeled owner attestations.

## The problem

Long AI-assisted work crosses task windows, models, hosts, repositories, and
toolchains. A prose summary may be helpful, but it cannot prove the exact source
SHA, accepted pointer, changed sections, open Deltas, tool outputs, or unresolved
human decision. Evidence Lane treats that missing evidence packet and lifecycle
boundary as the core problem.

## Public control surface

Root `/evi` exposes exactly six primary controls, in this order:

1. `/evi-boot`
2. `/evi-rollback`
3. `/evi-build`
4. `/evi-refresh`
5. `/evi-mode`
6. `/evi-source-intake`

`/evi-plugin` is an administrative sidecar outside those six controls. It can
list, register, route, or separately drop up to eight persistent connector or
AI-toolchain plugins. It stores configuration environment-variable names, not
secret values; dropped registrations remain in append-only history.
Compatibility names `/evi-additional-plugin` and
`/evi-drop-additional-plugin` use the same grant ledger. `/evi-storage` and
`/evi-change-storage-connector` inspect or select project storage; none becomes
a seventh primary control.

`/evi-exit-boot` closes the governed session and fully detaches ENV/UOP Flash
context plus visible prompt/response capture. It does not uninstall the plugin
or delete Flash verification, SQLite evidence, lineage, backlog, candidates,
accepted PVs, or pointer history. A later `/evi-boot` re-verifies the locked
authority and explicitly reattaches the runtime.

`/evi-state-travel` is a conditional continuity event, not a normal seventh
control. A sealed accepted-PV handoff makes it eligible, but it runs only after
an explicit user request or genuine host-context exhaustion. It never runs just
because a task is long, a handoff exists, or the user asks to continue the same
HIL in the current task.

## Atomic Boot and host routing

Boot performs one fail-closed flow: runtime doctor, exact ENV15/UOP15 Flash
verification, host/capability detection, durable-storage selection, then either
one new governed boot or resume of the existing session. Codex desktop and CLI
prefer user-owned local SQLite. Remote or ephemeral hosts require a configured
transactional durable connector. Google Drive is an optional verified mirror or
fallback, never the primary authority when durable local storage exists.

The Vercel project in this repository is only a thin HTTPS adapter for the
ChatGPT remote MCP. It verifies release identity and proxies to a separately
configured durable MCP origin. Vercel is not used to install Codex, is not the
general Evidence Lane router, and stores no accepted pointer or runtime SQLite
authority.

## Universal 18-lane brain

Source Intake accepts ordered sources, auto-detects their lanes, supports exact
overrides, and always includes Chat Lineage. Its optional Git arm accepts
`AUTO`, `REQUIRED`, or `DISABLED`: AUTO uses readable history when available and
otherwise falls back to deterministic content indexing; REQUIRED fails closed;
DISABLED skips history without authorizing remote writes. Project Engulf can
intake a whole bounded project through the same registry.

The canonical lanes are GitHub Code, Local Code, Chat Lineage, Discussion,
Analysis, Plan, Mode, Docs, Data/Excel/CSV, PPT, PDF/OCR, Images/OCR, Artifacts,
Custom, SQLite PV Candidate Loader, Research, Project Engulf, and SQLite Brain.
Every lane package contains and verifies:

- a lane-specific SQLite brain with integrity, foreign-key, schema, and FTS
  evidence;
- authoritative Mermaid (`.mmd`) and Graphviz (`.dot`) topology;
- `lane_pointer.json`, `refresh_receipt.json`, tool identity, and a sealed lane
  manifest;
- content hashes that bind every required member.

Build and Refresh use one bounded in-process worker pool to compute independent
lane packages concurrently from one hash-frozen source snapshot. A barrier then
rechecks the repository snapshot, verifies every lane database against its
routed source hashes, and assembles reports in canonical lane order. This is
compute parallelism inside one writer and one linear task; Chat Lineage append,
HIL, Fuse, accepted-pointer movement, rollback, and State Travel remain serial
authorities. A failed worker or changed source snapshot produces no candidate.
New lane bundles use schema v2 and require the sealed parallel-execution
receipt. Accepted v1 bundles remain readable through a narrow compatibility
path that applies only when all v2 parallel metadata is absent; a damaged or
incomplete v2 bundle still fails closed.

If the host or MCP client disconnects while a long build is in
`EXIT_BUILDING`, the same Refresh may resume only when no candidate was sealed.
The retry appends an interrupted-exit recovery receipt to visible ChatLineage,
keeps the accepted pointer unchanged, and still stops at the unaccepted HIL.
The bundled MCP long-tool timeout is one hour so full repository and OCR lanes
are not cut off by the former five-minute transport default.

Git code lanes index reachable commits, refs, changes, exact blobs,
content-addressed chunks, occurrences, and history FTS. Incremental Refresh
reuses a single accepted index, reindexes changed sections only, records chunk
reuse, tombstones removals, and falls back visibly when schema or tool identity
requires a full rebuild.

## Chat Lineage and candidate overlays

Visible user prompts and detectable mid-turn steers append as separate ordered,
idempotent Chat Lineage events. Visible assistant output may include actor type,
model/submodel when supplied by the host, available token metrics, tools,
commands, files, tests, builds, output links, hashes, and pointers. Secrets are
redacted. Hidden chain-of-thought, private model reasoning, and reasoning-content
fields are never stored.

Project-sector overlays fan this visible lineage into deterministic candidate
sectors. Tool, command, file, test, build, and Git activity routes to the active
code sector and Artifacts; source classifications route to their declared
sectors; Chat Lineage remains visible in every candidate. No overlay becomes
accepted sector truth before Fuse.

## Human gate and lifecycle

PV1 is the only normal full build. Later candidates use incremental Refresh or
a declared compatibility fallback. The six HIL outcomes are:

- `APPROVE`
- `APPROVE_WITH_DELTA`
- `MORE_RESEARCH`
- `ROLLBACK`
- `REJECT`
- `FAIL`

Only exact `APPROVE` reaches Fuse. Natural language such as "pursue same HIL,"
common typos, install requests, test results, or continued work are classified
and appended to Chat Lineage, but never interpreted as approval. Rollback moves
only the accepted pointer among immutable accepted versions. Publication,
installation, Vercel preview, and ChatGPT connection are release evidence, not
candidate acceptance.

## Build and validate locally

Requires Python 3.11+ and Git 2.30+.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy plugins/evidence-lane-plugin/src
```

Build the durable MCP container with:

```text
docker build --pull --tag evidence-lane-plugin:0.8.3 .
```

The container exposes `/mcp` and `/healthz` on port 8080 and requires one writer
plus durable storage at `/var/lib/evidence-lane`. The ChatGPT adapter remains
fail-closed until its exact-SHA durable HTTPS origin and authentication are
configured.

## Codex installation

Codex installs directly from the reviewed Git branch or exact commit through the
Git marketplace route; Vercel is not involved:

```text
codex plugin marketplace add rathee000001/evidence_lane_plugin --ref REVIEWED_REF
codex plugin add evidence-lane-plugin@evidence-lane-github --json
```

Validate the plugin archive and installed runtime identity, then use a genuinely
fresh Codex task for native pickup. Keep an older working install until the new
exact-SHA cache is verified; remove the redundant install only afterward.

## Security and release blocker

Credentials never belong in Git, plugin manifests, SQLite brains, candidate
packages, Chat Lineage, receipts, screenshots, or ordinary logs. An OpenAI key
disclosed during the July 31 intake is treated as compromised. This repository
does not contain the key or a derived identifier. Manual revocation in the
OpenAI Platform is a hard release blocker unless a connected key-management
capability can prove revocation. See [SECURITY.md](SECURITY.md).

## Ownership, credits, and contributions

Evidence Lane is conceived, directed, funded, and owned by Praveen Rathee.
Copyright © 2026 Praveen Rathee. All rights reserved. Model and tool assistance
does not transfer project authorship, acceptance authority, or intellectual
property ownership.

The development record credits the AI/toolchain roles actually used:

- OpenAI ChatGPT supported adversarial analysis, research, red/blue-team
  critique, and product reasoning; Codex and GPT-5.6 supported implementation,
  debugging, test execution, and evidence review under human direction.
- Google Gemini supported reasoning and exploratory discussion.
- Anthropic Claude supported a separate Fable-plugin experiment and comparative
  context; no Claude/Fable source is copied into this repository.
- GitHub and Git provide source history and distribution; SQLite, Python, MCP,
  Vercel, and the declared dependencies in `pyproject.toml` and
  `requirements.in` provide the implementation toolchain. Each third-party
  project remains governed by its own license and trademarks.

No external human contributor list is published yet while contribution records
are being refined. Questions, feedback, and direction do not automatically
create code authorship. Future accepted contributions must be attributable,
reviewed, licensed, and entered through the governed Git and HIL process. See
[credits and contribution policy](docs/CREDITS_AND_CONTRIBUTIONS.md) and
[LICENSE.md](LICENSE.md).

## Repository access and rights

This is proprietary source. Access for evaluation or collaboration does not
grant permission to redistribute, publish, sublicense, commercialize, or create
derivative releases. Third-party components retain their original licenses.

## Current claim boundary

Evidence Lane is stronger today as a governed continuity, provenance,
acceptance, and rollback architecture than as a proven performance product.
The repository does not yet claim universal provider neutrality, production
readiness, measured token or speed savings, clean-machine deployment across all
hosts, or unaided external-user success. Those require separate real-world
evidence rather than more internal reasoning.
