# Installation activation correction

The Git-installed 0.4.0 runtime imported from its own non-editable cache, but
the durable installation receipt still reported 0.3.0. The cause was bounded:
the cold bootstrap installed the engine and ran the read-only doctor without
first activating the installed engine version.

The correction adds an explicit `activate-installation` CLI step and invokes it
from the hash-locked bootstrap before the doctor. It updates the installation
receipt to `ENGINE_VERSION`, preserves the original `installed_at`, leaves the
session-flash receipt installation-scoped, creates no PV, moves no pointer, and
infers no HIL approval.

Validation:

- focused security/persistence suite: 8 passed in 23.58 seconds;
- full suite: 56 passed in 365.04 seconds;
- Ruff format: 105 files already formatted;
- Ruff lint: pass;
- mypy: 36 source files, zero issues;
- Bandit on the changed runtime paths: zero findings.

At recording time the governed session remained
`CORRECTION_TASK_PENDING`; the original PV1 candidate remained preserved and
unaccepted, and the accepted pointer remained `null` at generation zero.

The two separately named Deltas remain preserved and unimplemented:

- `EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001`
- `EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002`
