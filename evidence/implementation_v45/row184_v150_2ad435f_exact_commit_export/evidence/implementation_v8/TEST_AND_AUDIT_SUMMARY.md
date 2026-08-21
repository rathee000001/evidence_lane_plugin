# Python floor and deterministic SQLite closure

Two additional live-install findings were corrected before the governed source
was mutated.

First, the package declared Python 3.11 support while a lock regenerated under
Python 3.14 pinned NumPy 2.5.1, which requires Python 3.12 or newer. The direct
lock input now pins NumPy 2.4.6 and the hash lock is regenerated under Python
3.11. A genuine Python 3.11 cold bootstrap installed the plugin non-editably,
recorded engine version 0.4.0, created no project or PV, and exposed all 35 MCP
tools with a passing runtime doctor.

Second, the Python 3.11 suite exposed a transient SQLite `-shm` file in the
governed build workspace. `database.connect` now guarantees that every
connection closes when its context exits. Regression checks prove the handle is
closed, WAL sidecars are absent, and the build workspace is empty before the
read-only cachebuster hook comparison.

Validation:

- focused resource/persistence set: 13 passed in 56.84 seconds;
- full Python 3.11 suite: 57 passed in 394.37 seconds;
- Ruff format and lint: pass across 106 files;
- mypy: 36 source files, zero issues;
- Bandit: zero findings;
- dependency audit: no known vulnerabilities;
- Python 3.14 container build: pass at image digest
  `sha256:080a65880494ae465b62580d3de8100767a86ebfc0eab3f07262d2b4dd496d5b`;
- container health: HTTP 200;
- authenticated Streamable HTTP MCP: 35 tools and doctor pass;
- unauthenticated MCP: HTTP 401 with protected-resource challenge;
- test container and test volume: removed.

The governed session remained `CORRECTION_TASK_PENDING`; the original PV1
candidate remained preserved and unaccepted, and the accepted pointer remained
`null` at generation zero.

The two separately named Deltas remain preserved and unimplemented:

- `EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001`
- `EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002`
