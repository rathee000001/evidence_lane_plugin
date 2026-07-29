# Evidence Lane Git and remote-runtime audit v6

## Outcome

The 0.4.0 candidate is locally ready for an exact Git commit and pinned Codex
marketplace installation. It is not yet a demonstrated ChatGPT connection.

The complete suite passed 55/55 checks in 387.21 seconds. Ruff format and lint,
mypy over 36 source files, Bandit, and the 70-dependency locked audit passed
with no known vulnerability.

## Proof that the Git snapshot is executable

The prior installation could accidentally inherit the authority checkout's
virtual environment and repository-level package metadata. A plugin-only copy
was created without `.venv`, then bootstrapped from its own `pyproject.toml` and
hashed lock. It installed non-editably, imported only from that isolated
snapshot, returned `runtime_doctor=PASS`, and exposed all 35 MCP tools.

This proves self-contained cache execution. It does not make the cache source
authority; the exact Git commit remains distribution authority.

## Cross-platform container correction

The first Linux build failed correctly because the Windows-generated lock made
`pywin32` unconditional. The dependency was made explicitly Windows-only at the
input and both package manifests, then the hashed lock was regenerated.

After correction:

- Windows hash-locked bootstrap passed;
- Linux hash-locked image build passed;
- the image runs as the non-root `evidence-lane` user;
- `/healthz` returned 200;
- an authenticated Streamable HTTP client discovered all 35 tools;
- OAuth protected-resource metadata named the configured external issuer;
- unauthenticated `/mcp` returned the expected 401 discovery challenge;
- an exact test file retained the same SHA-256 across replacement of the
  container while reusing the durable volume.

The exact smoke containers and test volume were removed after validation.

## Honest external boundary

A private Git repository is distributable source, not a ChatGPT Server URL.
ChatGPT still needs a long-lived public HTTPS container, durable mounted volume,
and a compatible OAuth issuer. No authorized hosting or identity-provider
account is available in this lane, and a serverless Vercel deployment would not
satisfy the current stateful SQLite/one-writer contract.

The final ChatGPT risk acknowledgement and Create action remain human-owned.
No public service, Drive connection, or ChatGPT connection was created.

The live governed project remains at `CORRECTION_TASK_PENDING`, pointer
generation 0, with no accepted PV. The initial candidate is preserved. Neither
passing tests nor installation is HIL approval.

The two named pre-existing Deltas remain preserved without being marked
implemented:

- `EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001`
- `EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002`

## Verdict

Codex Git distribution: `PURSUE` after exact commit, push, and pinned-cache
installation.

ChatGPT remote connection: `FIX-THEN-PURSUE`.

Confidence: `0.99`.

The ChatGPT verdict changes only with a live durable HTTPS endpoint, compatible
OAuth round trip, complete tool discovery, and new-chat persistent-state
readback.
