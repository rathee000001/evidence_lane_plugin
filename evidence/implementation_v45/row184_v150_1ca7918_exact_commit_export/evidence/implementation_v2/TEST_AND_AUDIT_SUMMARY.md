# Evidence Lane Plugin 0.2.0 — test and audit summary

## Outcome

The private Evidence Lane candidate is implemented, pushed, and installed in
Codex Desktop. It is not installed in ChatGPT: the live Pro account has
Developer Mode enabled but no Secure MCP Tunnel, and Pro read/fetch-only MCP
access does not satisfy this plugin's bounded write-capable HIL.

No HIL success is inferred. No live PV was created. State Travel has not begun.

## Final reference checkpoint

The Claude/Fable reference lane was reviewed through exactly six commits and is
closed at `76e77945aee28e55e8718f767eda5f423303e784`. There is no seventh commit
and no further scan is part of this HIL. The attached lineage was read
completely and has SHA-256
`051FAF161EEAEA50975893985F416D0E82E5F8A3B72101C4FC28BAC31274074E`.

Code-backed deltas retained include bounded rejection/failure recovery,
correction/research continuation, entry-state versus live-source truth,
progressive accepted-PV retrieval, exact provenance, authority labels, and
version parity. Narrative-only claims and weaker flash, persistence, or seal
behavior were not copied.

## Locked session flash

The plugin bundles and verifies a 16-member ENV15/UOP15 session authority
outside every PV. The exact anchors are:

- manifest:
  `49B150FAC223C2198C3D60F273330D1EEE27FA961BFCD4CC47198ED31821266A`;
- prompt:
  `E5751173A1419137DF57ED4811D82D0ADED227C6D064A0DF603EF7813F539D5F`;
- authority digest:
  `64976104F181BF215B40315FCB33F9EC1730C51EF24B778B7F85698AF5F93972`.

The supplied parent packet is not called intact. It declares 134 files, only
129 exist, six declared `codex/` files are missing, and one undeclared research
file is present. Only the independently verified ENV/UOP subset is accepted;
every boot retains `SOURCE_PACKET_PARTIAL_INTEGRITY`.

## Validation

- `pytest`: 31 passed, 0 failed.
- Ruff lint: pass.
- Ruff formatting: 57 files pass.
- Mypy: 28 source files pass.
- Bandit: 0 findings.
- Locked dependency audit: 0 known vulnerabilities.
- Plugin schema: pass.
- Skill schema: pass.
- Mermaid render: pass.
- Wheel build and hash-required fresh install: pass.
- Source, fresh-wheel, and installed-Codex runtime doctors: pass.
- Codex plugin list: installed and enabled as
  `0.2.0+codex.20260726051820`.
- Forbidden predecessor-name matches: 0.
- Detected real secrets: 0.

The release wheel is 2,022,188 bytes with SHA-256
`AA9A8A8E8985D7FDBA93B2CF24C37C4BB67CE792C5D65E257BAE45CD47565124`.

## Host verdict

Codex Desktop is a valid installed HIL candidate. ChatGPT host parity remains
blocked until an eligible write-capable workspace and associated Secure MCP
Tunnel exist and the live app discovers the same canonical tools. Creating a
reduced read-only Pro app would change the accepted product boundary and was
not done.

Gate:
`V020_IMPLEMENTED_CODEX_INSTALLED_CHATGPT_FULL_MCP_BLOCKED_AWAITING_GENERAL_HIL`.

The next action is a human decision:
`APPROVE`, `APPROVE_WITH_DELTA: <correction>`,
`MORE_RESEARCH: <question>`, `REJECT`, or `FAIL: <gate>`.
State Travel starts only after that decision.
