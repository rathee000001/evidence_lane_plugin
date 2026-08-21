# Exact Git provenance for copied Codex plugin installs

This additive correction follows implementation v32. It does not replace a
prior evidence layer, transition a linear Delta, accept a candidate, Fuse a PV,
move the accepted pointer, merge `main`, deploy Vercel, or infer HIL success.

## Defect and correction

Codex deliberately copies an installed plugin out of its configured Git
marketplace checkout. The 0.5.3 cache matched all 132 tracked plugin files and
the marketplace was pinned to exact commit
`f26d8a5d0c06a1cc8b96671134ea60d7e0d3b810`, but the runtime doctor still
reported `engine.commit: UNCOMMITTED` because the copied cache has no `.git`
directory.

Version 0.5.4 adds a fail-closed installed-source identity path. The runtime
reports a marketplace `HEAD` only when:

- the cache path maps to the corresponding Codex marketplace checkout;
- Git returns one valid 40-character commit;
- the tracked plugin subtree is clean relative to that commit;
- every tracked source file exists in the installed cache; and
- every tracked source/cache byte hash matches.

A missing checkout, invalid commit, dirty tracked subtree, missing file, path
escape, symlink escape, or byte mismatch returns `UNCOMMITTED`. Untracked
runtime material such as the plugin-local `.venv` is not confused with source
provenance.

The probe was exercised against the real 0.5.3 Codex cache and returned the
exact pinned commit above. Focused tests prove both installed-cache tampering
and a dirty marketplace fail closed.

## Validation

- complete suite: 72 passed in 391.82 seconds;
- focused Drive-doctor and installed-provenance suite: 2 passed;
- official OpenAI plugin validator: pass;
- Ruff lint and format: pass across 57 Python files;
- mypy: pass across 42 source and hook files;
- Bandit: pass;
- `pip check`: no broken requirements;
- pip-audit: no known dependency vulnerabilities;
- root and plugin wheel/sdist builds: pass;
- isolated 0.5.4 wheel install and version/schema/locked-Flash resource
  readback: pass;
- changed-line secret scan: zero findings and `.env.local` remains untracked;
- `git diff --check`: pass.

The staged implementation before this evidence directory is tree
`f7ebde42a5b795ce8a5a7476155c1b2d1890a46d`.

## Preserved HIL boundary

The accepted pointer remains null at generation 0. Both earlier PV1 candidates
remain preserved and unaccepted. All 21 linear Deltas remain `QUEUED`; the
derived Plan projection remains `PASS` at 21/21 events with seven plans and one
Planning-mode event.

The next authorized work is one non-force feature-branch commit/push, exact
0.5.4 Git reinstall, refresh of the existing private ChatGPT Secure MCP Tunnel,
branch-authority pickup, and one fresh unaccepted candidate. The workflow must
then stop at the six-way `/evi-80-hil`; exact case-sensitive `APPROVE` remains
the only Fuse token.
