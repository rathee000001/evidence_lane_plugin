# Evidence Lane Plugin

`evidence_lane_plugin` is a private, single-user MCP plugin for Codex and
ChatGPT Work. It turns one explicit Git or local-code source into immutable,
human-approved Evidence Lane project versions. The installable identifier is
`evidence-lane-plugin`.

The user-facing lifecycle is:

```text
/ev
  -> verify installed source + persistent ENV15/UOP15 flash
  -> resume one governed session or reveal /git and /local intake
  -> one agent / one active task
  -> /pv-refresh creates the next unaccepted candidate
  -> /pv-fuse ... APPROVE promotes and directly enters it
  -> or APPROVE_WITH_DELTA | MORE_RESEARCH
     | ROLLBACK [PVn | PROMPT n | TURN id] | REJECT | FAIL
```

`/ev` is first. The older separate boot, flash, enroll, and exit prompt
commands are intentionally absent. Flash persists until plugin removal.
`entry_slip.json` and `exit_slip.json` are generated automatically inside each
sealed candidate; they are evidence artifacts, not user commands.

Only the exact case-sensitive token `APPROVE` can Fuse a candidate.
Promotion is byte-preserving and immediately hands the same session into the
newly accepted PV—there is no remake. `ROLLBACK` is separate pointer-only
state travel. It preserves accepted history, candidates, source bytes, and the
monotonic next-PV ordinal.

## Prompt-bar command surface

- `/ev [project_id]`: verify install/flash/Drive dependency, then boot or resume;
- `/git ...`: clone or fast-forward one explicit repository and authorized
  branch without remote write;
- `/local ...`: adopt or read one explicit local-code Git path;
- `/pv-refresh <project_id> <session_id>`: seal final source as an unaccepted
  candidate;
- `/pv-fuse <project_id> <session_id> APPROVE`: exact approval plus direct
  accepted-PV handoff;
- `/pv-hil ...`: bounded Delta, research, rollback, reject, or fail;
- `/pv-rollback ...`: target `PVn`, `PROMPT <index>`, `TURN <id>`, or omit the
  target for the current prompt/session entry PV.

Codex command files are statically installed, so a host cannot literally hide
and reveal menu entries at runtime. The plugin enforces the intended behavior
in its workflow: source intake and lane work require `/ev` verification in the
current host task.

## Implemented engine

- one immutable registry for 18 sectors:
  `github_code`, `local_code`, `chat_lineage`, `discussion`, `analysis`,
  `plan`, `mode`, `docs`, `data_excel`, `ppt`, `pdf_ocr`, `images_ocr`,
  `artifacts`, `custom`, `brain_loader`, `research`, `project_engulf`, and
  `sqlite_brain`;
- per-sector SQLite, authoritative MMD and DOT, tool identity, pointer evidence,
  Refresh receipt, manifest, exact source bytes, structured facts, FTS5/BM25,
  and materialized TF-IDF;
- hardened DOCX/XLSX/PPTX parsing; workbook/sheet/range/cell/formula/
  dependency/table/chart facts; CSV, JSON/JSONL, and Parquet facts; native PDF
  parsing plus local RapidOCR/ONNX and Tesseract fallback evidence; image OCR;
  slide shape/text/table/image/relationship facts; read-only SQLite inspection;
  code facts; and exact fail-visible unsupported states;
- PV1-only full sector construction; PV2+ byte-reuses unchanged
  SQLite/MMD/DOT/tool artifacts, rebuilds only changed/new sources, and
  tombstones removals;
- one-candidate named lane-route grants with accepted-route inheritance;
- exact executable `cmd:` acceptance checks; prose remains
  `PENDING_HUMAN_REVIEW`, and checks that mutate source block candidate sealing;
- recursive package checksums, SQLite integrity/foreign-key checks, immutable
  candidate storage, byte-preserving promotion, and compare-and-swap pointers;
- one executable lifecycle transition table shared by boot, build, task,
  Refresh, six-way HIL, rollback, recovery, and next-turn handoff;
- persistent accepted-PV/session hints on startup, resume, clear, and compact;
  `/ev`, `session_resume`, and `pv_status` perform the verifying handoff;
- a privacy-minimized `UserPromptSubmit` hook that indexes entry PV, turn ID,
  and SHA-256 without storing raw prompt text or private model reasoning;
- local-path adoption or credential-free HTTPS enrollment without remote write;
- explicit local/HTTPS branch synchronization that accepts only a clean
  fast-forward and checks changed paths before mutating an active task source;
- an ordered multi-task backlog with one active task maximum;
- separately gated remote Git preparation/execution; PV approval alone never
  authorizes a push.

## Durable boundary

The default authority is the user-owned `~/EvidenceLanePV` directory, or the
exact `EVIDENCE_LANE_DATA_ROOT`/`PLUGIN_DATA` override. Plugin cache location is
never state authority. ENV15/UOP15 flash data remains installation-scoped and
outside every PV.

Installation declares Google Drive connector
`connector_5f3c8c41a1e54ad7a76272c89e2554fa` as required, so the host uses its
normal Google OAuth connection flow. The host never gives that connector token
to the Python MCP:

- a durable local MCP server keeps the local store authoritative and may use
  the connector for a separately verified mirror;
- an explicitly ephemeral server fails closed unless the separate direct
  server-side Drive backend is configured;
- `runtime_doctor` reports these two connection classes separately.

Host brand does not decide durability. A local ChatGPT desktop MCP can have a
durable filesystem; a remote Codex or ChatGPT runner may not.

## Install and validate

For source development, build the hash-locked plugin-local runtime from the
repository root.

Windows PowerShell:

```powershell
.\plugins\evidence-lane-plugin\scripts\bootstrap.ps1
```

Portable Python:

```text
python plugins/evidence-lane-plugin/scripts/bootstrap.py
```

The bootstrap installs the hash-locked dependency set and a non-editable copy
of the engine into the plugin-local `.venv`. The plugin subdirectory has its
own `pyproject.toml`, so a versioned Git marketplace snapshot is self-contained
and never reaches back into the F-drive authority checkout. On a fresh Git
install, the first MCP start performs the same hash-locked bootstrap inside the
versioned cache.

For normal Codex use, add the private GitHub marketplace at one exact reviewed
ref, then install or update the plugin:

```text
codex plugin marketplace add rathee000001/evidence_lane_plugin --ref REVIEWED_REF
codex plugin add evidence-lane-plugin@evidence-lane-github --json
```

When prompted, connect the required Google Drive dependency through the normal
host OAuth screen. Review and trust the two bundled hooks. Start a new Codex
task or ChatGPT Work chat before expecting the updated commands or MCP tools;
an already-running task retains its original plugin snapshot.

```text
plugins/evidence-lane-plugin/.venv/Scripts/python.exe -m pytest
plugins/evidence-lane-plugin/.venv/Scripts/python.exe -m evidence_lane_plugin.cli doctor
```

STDIO:

```text
plugins/evidence-lane-plugin/.venv/Scripts/python.exe \
  plugins/evidence-lane-plugin/scripts/run_mcp.py --transport stdio
```

Loopback Streamable HTTP:

```text
plugins/evidence-lane-plugin/.venv/Scripts/python.exe \
  plugins/evidence-lane-plugin/scripts/run_mcp.py --transport streamable-http \
  --host 127.0.0.1 --port 8765
```

The endpoint is `/mcp`. Non-loopback HTTP fails closed without an HTTPS base
URL and either static bearer authentication or standards-based OAuth. Static
bearer is a Codex-only private-client fallback. ChatGPT requires a public HTTPS
deployment with compatible OAuth; a Git repository URL is not an MCP endpoint.

The bundled STDIO server is usable by supported local plugin surfaces. ChatGPT
requires a registered remote MCP connection; no connection ID is invented or
embedded here. The included container contract runs one persistent remote
service but still requires an authorized container host and OAuth issuer. After
any plugin update, reinstall the new version and start a new task/chat.

## Authority and current HIL boundary

Implementation success, passing tests, a valid package, installation, and a
connected Drive dependency do not accept a real project PV. The live State
Travel HIL remains authoritative. This repository never infers approval,
deployment, publication, or remote Git write from local success.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/STATE_MACHINE.mmd`](docs/STATE_MACHINE.mmd),
[`docs/HOST_CAPABILITY_MATRIX.md`](docs/HOST_CAPABILITY_MATRIX.md), and
[`docs/CHATGPT_CONNECTION.md`](docs/CHATGPT_CONNECTION.md). Remote distribution,
container, OAuth, and final ChatGPT fields are in
[`docs/REMOTE_DEPLOYMENT.md`](docs/REMOTE_DEPLOYMENT.md).
