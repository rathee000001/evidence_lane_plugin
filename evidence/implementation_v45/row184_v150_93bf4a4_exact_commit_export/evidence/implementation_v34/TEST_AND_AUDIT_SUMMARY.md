# v0.6.0 test and audit summary

Final source validation, run after the last code hardening edits:

- complete pytest suite: **80 passed in 487.37 seconds**;
- Ruff: pass;
- MyPy: pass, 43 source files and zero issues;
- Bandit: exit 0, zero findings; informational existing `nosec` warnings were
  emitted and are not suppressed in this receipt;
- `pip-audit -r requirements.in`: no known vulnerabilities;
- official OpenAI plugin validator: pass;
- fresh wheel and sdist build: pass;
- isolated wheel install/import: version 0.6.0, schema 3.0.0;
- changed-source secret scan: no compromised key found; one new key-shaped
  literal is a sequential-alphabet redaction test fixture; `.env.local` is not
  tracked;
- `git diff --check`: pass.

The 80-test suite includes dummy-source execution across all 18 canonical
lanes, SQLite integrity/foreign-key/FTS checks, content-addressed chunk and
incremental refresh behavior, visible ChatLineage/privacy controls, Git-history
brain indexing, connector governance/routing, candidate-only project overlays,
lifecycle/CAS failure modes, and the ChatGPT adapter's fail-closed behavior.

The built release artifacts are external validation outputs, not committed
binaries:

- `evidence_lane_plugin-0.6.0-py3-none-any.whl` —
  `7F1AF14AA767FD6D175464E634441A48AD0E80F7D8B900C5E0C014C9F50AF3BE`
- `evidence_lane_plugin-0.6.0.tar.gz` —
  `02D0458F34D5353771D3E6D2E471B8716543B5846A63F52B1D1AAD70A08EB564`

`twine check` was not run because `twine` is absent from the repository's
isolated environment. The direct install/import smoke check passed. The
developer environment's installer tooling is not part of the locked runtime
requirements and is not represented as runtime dependency evidence.
