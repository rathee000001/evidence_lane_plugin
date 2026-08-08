<p align="center">
  <img src="docs/assets/evidence-lane-full-logo.png" alt="Evidence Lane" width="900" />
</p>

<p align="center">
  <strong>Governed project evidence that stays inspectable across AI tasks while acceptance remains human-controlled.</strong>
</p>

<p align="center">
  <a href="https://evidencelane.org">Website</a>
  &nbsp;·&nbsp;
  <a href="docs/ARCHITECTURE.md">Architecture</a>
  &nbsp;·&nbsp;
  <a href="docs/HOST_CAPABILITY_MATRIX.md">Host capabilities</a>
  &nbsp;·&nbsp;
  <a href="docs/REMOTE_DEPLOYMENT.md">ChatGPT deployment</a>
  &nbsp;·&nbsp;
  <a href="docs/IMPLEMENTATION_TRACEABILITY.md">Implementation traceability</a>
  &nbsp;·&nbsp;
  <a href="docs/VERSIONING.md">Versioning</a>
  &nbsp;·&nbsp;
  <a href="SECURITY.md">Security</a>
</p>

<p align="center">
  <img src="plugins/evidence-lane-plugin/assets/evidence-lane-icon.png" alt="Evidence Lane plugin icon" width="104" />
</p>

# Evidence Lane 1.4.0

The single active product release is **1.4.0** across the root package, plugin
package, engine, Codex manifest, remote adapter, current documentation, and
current test/PoC tooling. Historical accepted PVs, sealed receipts, compatibility
labels such as `pre-v1.1`, dependency versions, and historical Delta reports keep
their original versions. See [`docs/VERSIONING.md`](docs/VERSIONING.md).

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
toolchains. Without a durable project boundary, the user repeatedly explains
the same project while the model rereads and reparses unchanged sources. That
reconstruction tax also creates context drift: a polished prose summary cannot
prove the exact source SHA, accepted pointer, changed sections, open Deltas,
tool outputs, actor identity, or unresolved human decision.

Evidence Lane makes the first complete PV deliberately evidence-heavy, then
lets later tasks resume from the accepted pointer, PV, Exit Slip, Chat Lineage,
pending candidate, and exact HIL. Instead of rebuilding the whole narrative,
the model can query relevant SQLite lane facts and reuse content-addressed
chunks; Refresh reprocesses changed sections and records the Delta. Visible
actor lineage separates user direction from AI output, and only the six-way HIL
can turn a candidate into accepted truth.

This is a mechanism claim, not a universal token, speed, or cost-savings claim.
New or changed files still require parsing, and outcomes depend on the corpus,
host, and task. What the repository can currently prove is exact reusable
evidence, delta receipts, actor lineage, and human-controlled promotion.

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
secret values; dropped registrations remain in append-only history. Its
structured settings surface exposes eight slots for independent `CODEX` and
`CHATGPT` profiles. Each registration binds one visible purpose/reason, role,
role-field schema, allowed actions/lanes/write scope, expiry, and an optional
declared backend runtime. The runtime declaration never authorizes code
execution by itself.
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
control. It runs only after an explicit user request or genuine host-context
exhaustion. Acceptance is not a prerequisite: an active task, pending
correction, or unaccepted candidate is sealed with its pointer base, live-source
identity, Plan Lane, additive Deltas, exact resume row, and host execution
profile. A fresh destination verifies those bytes and resumes the same row.
`ACCEPTED_ENTRY` remains available when explicitly requested. Evidence Lane
cannot change host-owned model selectors, so Codex destination profile mismatch
fails before host rebinding. A prepared handoff never invokes itself.

`/evi-plan` is a Codex-only Planning sidecar. If native Plan mode is not active,
it writes nothing and reminds the user to type `/pl`. After planning it persists
the canonical Plan Lane and returns a short prompt the user copies into the
host-owned Codex Goal. Linked steers append to an existing row; unrelated
steers append a new numbered row; the default boundary is before the next HIL.
The full task panel persists with exactly one active row until that HIL is
actually presented. A required user token pauses only its dependent row;
independent work continues and the host Goal is never reported complete merely
because a token, credential, or external confirmation is pending. Goal usage is
reported in readable `K`/`M` notation while retaining the exact raw count in
the evidence receipt.

## Atomic Boot and host routing

Boot performs one fail-closed flow: runtime doctor, exact ENV15/UOP15 Flash
verification, host/capability detection, durable-storage selection, then either
one new governed boot or resume of the existing session. Codex desktop and CLI
prefer user-owned local SQLite. Remote or ephemeral hosts require a configured
transactional durable connector. Google Drive is an optional verified mirror or
fallback, never the primary authority when durable local storage exists.

Codex and ChatGPT are separate host universes over the same governance law.
Codex installs from exact Git source, runs the complete lifecycle, and may
project Plan Lane into native Plan, Goal, and task-panel surfaces. ChatGPT
installs the same full Evidence Lane plugin package so its governed skills,
including Boot/ENV-UOP Flash and accepted-PV Entry/Exit workflows, remain
available. The registered ChatGPT Pro connection uses the
`CHATGPT_PRO_READ` profile: exactly 21 annotated read-only MCP tools for
accepted-PV status, ENV/UOP Flash, Entry/Exit slips, lanes, search, diffs,
backlog, and governed panels. A skill that requires a lifecycle write must
report that capability unavailable and stop; it may not simulate or claim the
mutation. ChatGPT's native ENV/UOP package and Project Mutation sector may
continue under host law, but the MCP does not perform or claim that mutation.

The Vercel project in this repository is only a thin HTTPS adapter for the
ChatGPT read MCP and the public website. It verifies release identity and
proxies to a separately configured durable read origin. Vercel is not used to
install Codex, is not the general Evidence Lane router, and stores no accepted
pointer or runtime SQLite authority. The contributor Windows bootstrap is a
separate outbound OpenAI tunnel that asks for one Tunnel ID and one masked
Runtime key, links ChatGPT once, starts after Windows sign-in, and provides
status, repair, and fail-closed removal.

The release cost boundary is fail closed. It does not configure or invoke the
usage-based GitHub Sandbox product. `sandbox` in the Code-mode formulas means a
bounded local project work directory and process, not GitHub Sandbox. GitHub
Actions and Copilot use are limited to the allowances already included in the
selected GitHub Team and personal Copilot Pro plans; paid overages and
additional usage remain disabled unless the user separately authorizes them.
The selected Vercel account plan does not change Evidence Lane authority. A
separately purchased domain may expose an exact, still-unaccepted
feature-branch candidate for HIL review; that public review surface does not
merge `main`, move an accepted pointer, Fuse the candidate, or make it
production truth.

The reviewed public website preview is
[`https://evidencelane.org`](https://evidencelane.org). The public ChatGPT MCP
endpoint is [`https://mcp.evidencelane.org/mcp`](https://mcp.evidencelane.org/mcp).
This website URL is published in both the Codex plugin manifest and ChatGPT MCP
server metadata; website availability remains separate from MCP readiness.

## Universal 18-lane brain

Source Intake accepts ordered sources, auto-detects their lanes, supports exact
overrides, and always includes Chat Lineage. Its optional Git arm accepts
`AUTO`, `REQUIRED`, or `DISABLED`: AUTO uses readable history when available and
otherwise falls back to deterministic content indexing; REQUIRED fails closed;
DISABLED skips history without authorizing remote writes. Project Engulf can
intake a whole bounded project through the same registry.

Schema-derived Source Intake pills are append-only governed extensions. Use
`/evi-source-intake ADD "<pill name>" --purpose "<need>" --schema <definition>`
to create version 1, or
`/evi-source-intake MODIFY "<pill name>" --schema <next-version-definition> --previous-sha256 <exact-sha256>`
to append the next version. MODIFY requires the exact prior hash; neither
operation mutates the canonical 18-lane registry or bypasses classification,
source policy, candidate isolation, or HIL.

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

Git worktrees use `git ls-files` as the default source boundary: ignored and
untracked operational files never enter the candidate inventory. Before any
exact bytes reach SQLite, FTS, content-addressed chunks, Git history, topology,
or a PV package, a shared fail-closed policy excludes `.env` variants,
`.runtime` state, credentials/private keys, configured secret values, and
recognized credential-shaped content. Non-Git inputs use the same deterministic
path/content policy. Exclusion receipts contain only safe path and reason codes,
never secret bytes or secret environment-variable names.

The MMD and DOT files are semantic projections of the lane SQLite authority,
not flat file inventories. Every emitted lane shows source intake, its
lane-specific schema and materialized fact kinds, retrieval/CAS/FTS, refresh
and pointer evidence, and the inspectable output contract. Code lanes
additionally show symbols, imports, routes, dependencies, reachable Git
commits/refs, file changes, blob/chunk CAS, occurrences, and history FTS. A
lane that is neither loaded nor detected emits no PV folder or placeholder;
only the canonical registry remains schema-ready for a later intake.
The v1.4 reconciliation gate parses both formats, requires meaningful structural
floors, rejects dangling endpoints, compares exact subgraph/node/edge identities,
and checks every emitted table, fact-kind, and root count against read-only
SQLite. A syntactically valid six-line graph, understated count, or MMD/DOT
divergence fails the candidate instead of passing as a decorative diagram.
Optional Mermaid SVG/PNG rendering uses an explicitly configured browser or a
locally installed Chrome/Edge executable; the plugin never downloads a browser
at build time, and the `.mmd` source remains authoritative.

The deterministic row-46 one-shot proof is executable with:

```powershell
.\.venv\Scripts\python.exe plugins\evidence-lane-plugin\scripts\build_one_shot_dummy_poc.py <output-directory>
```

It loads all 18 dummy lanes in one PV, gives the GitHub Code lane a real
three-commit synthetic repository and parent chain, validates the initial
package plus an unchanged Refresh, and writes independent forensic reports.
The checked correction evidence is under
`evidence/implementation_v42/ONE_SHOT_DUMMY_POC`; its receipt SHA-256 is
`EAE51C5F3CAC3A6DCEDA9E9EFB83624BC67C536D0D830712EEDF3670A1739F9F`.
The separate post-acceptance real-Git test remains bound to the full reachable
history of the main Evidence Lane repository.

Build and Refresh use one bounded in-process worker pool to compute independent
lane packages concurrently from one hash-frozen source snapshot. A barrier then
rechecks the repository snapshot, verifies every lane database against its
routed source hashes, and assembles reports in canonical lane order. This is
compute parallelism inside one writer and one linear task; Chat Lineage append,
HIL, Fuse, accepted-pointer movement, rollback, and State Travel remain serial
authorities. A failed worker or changed source snapshot produces no candidate.
Current v1.4 lane bundles use the universal-lane v2 contract plus sealed source-policy and
parallel-execution receipts, and they require topology reconciliation. Accepted
v1 bundles remain readable through their existing narrow compatibility path.
Sealed pre-v1.1 v2 bundles that contain the original parallel receipt but no
source-policy receipt remain readable through a separately reported
compatibility path; their historical MMD/DOT is never relabeled as reconciled.
A damaged or incomplete current v2 bundle still fails closed.

If the host or MCP client disconnects while a long build is in
`EXIT_BUILDING`, the same Refresh may resume only when no candidate was sealed.
The retry appends an interrupted-exit recovery receipt to visible ChatLineage,
keeps the accepted pointer unchanged, and still stops at the unaccepted HIL.
The MCP process initializes its optional NumPy/OpenCV/ONNX OCR engine before the
event loop starts, then reuses that process-local engine while independent lane
builders remain parallel. The bundled long-tool timeout is still one hour for
genuinely large repositories; timeout inflation is not the lifecycle fix.

Python remains the orchestration, schema, SQLite, AI/OCR, and lifecycle layer.
Connector grants may declare Java/Kotlin for enterprise adapters, Go for
network/queue services, Rust or C++ for benchmark-proven native parsing,
hashing, or compression, and `external_mcp` for a host-managed service. These
are governed interoperability slots, not a rewrite plan: a non-Python backend
must beat the Python path on a reproducible workload and must return the same
hash-bound receipts before it can be selected.

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

## v1.4 release and compatibility invariants

An accepted PV remains immutable entry authority even when a later engine adds
stricter topology or promotability rules. Boot, Resume, status, direct
continuation, State Travel, and rollback revalidate its exact bytes, pointer,
manifest/package hashes, lane checksums, and SQLite integrity without
retroactively treating it as a new candidate. The compatibility state remains
visible. Every successor candidate must pass every current rule.

Mode selection binds the chosen lane's locked ENV/UOP governance without
moving the lifecycle. The exact Code-mode law is:

`Mode=code | ENV formula: plan -> sandbox build -> test -> hash -> package | Loop: entry -> preflight -> sandbox -> patch -> test -> exit | CI/CD: CONTROLLED_REQUIRED | Operators: PCM + MBA + SUPPLY | Receipt=5183AB1AD17D570DA860858B7B45D90F67E996273EA3A642B5E2C2511BA553A6`

Other modes retain their own lane gates and HIL effects while preserving the
same six exact decision tokens.

The current visible Plan Lane is separate from the immutable 80-row historical
Delta ledger. Its last execution row retains the complete carried POC:
full-ledger reconciliation, forensic audit, real-Git history proof,
GitHub-agent proof, lane-absence checks, and security review. After fresh HIL
acceptance, the same accepted release evidence must propagate through the
GitHub README and relevant Markdown, every relevant website page and footer,
the website Delta table, the complete Vercel production build from `main`, and
only the existing Devpost project `1348634/evidence_os`. Runtime website
footers obtain the deployed source SHA from `VERCEL_GIT_COMMIT_SHA`, avoiding a
circular hard-coded self-commit claim.

Every historical top-level v1.3 Delta receipt that declares `receipt_sha256` is self-sealed
as SHA-256 over canonical JSON after removing only that top-level field.
`tests/test_v130_evidence_receipt_seals.py` scans the complete historical v1.3 evidence
directory and fails on a stale seal. A tracked-source change after a remote Git
action or exact-SHA preview is prepared supersedes that action or preview; its
old token must remain unused, and a new tested commit, action, token, and
preview are required.

## Build and validate locally

Requires Python 3.11+ and Git 2.30+. The runtime pins MCP `1.28.1`, Pydantic
`2.13.4`, and the complete transitive dependency set in
`plugins/evidence-lane-plugin/requirements.lock.txt`; `pip check` must report no
broken requirements.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy plugins/evidence-lane-plugin/src
```

Build the durable MCP container with:

```text
docker build --pull --tag evidence-lane-plugin:1.4.0 .
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

For private CodeQL, the personal canonical repository retains SARIF as a
private Actions artifact. A separately configured private `Evidence-Lane`
organization mirror can upload the exact reviewed SHA to GitHub Code Security
through the owner-gated hosted workflow. The mirror does not replace the
canonical plugin remote or become project authority.

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

Human review and evaluation contributions:

- [Naveen Rathee](https://www.linkedin.com/in/naveen-rathee/): Strategic
  Challenger and Cross-Project Human Review Gate.
- [Kapil Dhawan](https://www.linkedin.com/in/kdhawan23/): Enterprise
  Engineering and Product Communication Reviewer.
- [Steven Tock](https://www.linkedin.com/in/steventock/): Senior Strategic
  Reviewer and Controlled-AI Advisor.
- [Sumit Hooda](https://www.linkedin.com/in/sumit-hooda-378884192/): External
  Software Engineering Evaluator for bare-metal code review, adversarial plugin
  testing, AI-drift analysis, and implementation loophole discovery.

These roles record attributable review; they do not imply source authorship,
ownership transfer, candidate acceptance, or release authority. Accepted source
contributions must remain attributable, reviewed, licensed, and entered through
the governed Git and HIL process. See the
[credits and contribution policy](docs/CREDITS_AND_CONTRIBUTIONS.md),
[upstream reference provenance ledger](docs/UPSTREAM_REFERENCE_PROVENANCE.md),
[copyright notice](COPYRIGHT.md), and [proprietary license](LICENSE.md).

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
