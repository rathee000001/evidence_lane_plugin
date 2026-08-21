# Clean Git-authoritative Codex cache

The versioned Codex cache contained all 95 Git source files byte-for-byte with
zero missing files and zero hash mismatches. It also contained 18 legitimate
Codex-generated command-to-skill wrappers. A live bootstrap inspection found 60
additional disposable setuptools build and egg-info files.

The bootstrap now removes only two fixed plugin-root targets:
`build` and `src/evidence_lane_plugin.egg-info`. It verifies that each target
resolves inside the plugin root, refuses symlinks, and runs cleanup before and
after the non-editable package installation. A regression test proves that
these two generated targets are removed while the source package remains
unchanged.

Validation:

- cleanup regression on Python 3.11: 9 passed in 24.33 seconds;
- cleanup regression on Python 3.14: 9 passed in 24.55 seconds;
- real source bootstrap: pass, with neither generated directory remaining;
- exact final full suite: 58 passed in 338.93 seconds;
- Ruff format and lint: pass across 107 files;
- mypy: 36 source files, zero issues;
- Bandit on the changed bootstrap: zero findings.

The bootstrap retained the 0.4.0 installation receipt and reused the existing
installation-scoped flash receipt. It created no PV, moved no pointer, and
inferred no HIL approval.

The governed session remained `CORRECTION_TASK_PENDING`; the original PV1
candidate remained preserved and unaccepted, and the accepted pointer remained
`null` at generation zero.

The two separately named Deltas remain preserved and unimplemented:

- `EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001`
- `EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002`
