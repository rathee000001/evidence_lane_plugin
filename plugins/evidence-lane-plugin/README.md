# Evidence Lane plugin 2.0.0

Evidence Lane 2.0.0 is the stable Codex release. Its package carries the six
primary controls -- Boot, Rollback, Build, Refresh, Mode, and Source Intake --
plus 15 governed skills and exactly one package-local native MCP server. The
native catalog is fixed at 62 tools: 21 read-only and 41 write-capable.

Candidate creation, installation, remote Git push, pointer movement, and Fuse
remain separate governed actions. Natural-language approval never moves
accepted truth; Fuse requires the exact pending six-way HIL contract.

## Host, storage, and tunnel matrix

Storage durability, interaction profile, VM lifetime, account tier, and API
billing are independent axes.

| Execution profile | PV storage | Tunnel |
| --- | --- | --- |
| Headless API or direct CLI/API on a local or persistent host | Durable local SQLite when available | Not required at the API layer |
| Headless API or direct CLI/API on an ephemeral VM | Durable mount when present; otherwise an explicitly configured transactional connector | Not required at the API layer |
| Interactive Codex app on a local or persistent host | Durable local SQLite | One setup per persistent host and release |
| Interactive Codex app on an ephemeral VM | Durable mount or explicitly configured transactional connector | One setup per VM; key and tunnel remain only for that VM lifetime |

Pro, Plus, Business, Edu, Enterprise, and API billing labels do not choose the
storage route and do not create or remove a tunnel requirement. A replacement
ephemeral VM must perform a fresh interactive tunnel setup. A headless API
invocation instead verifies locked ENV/UOP Flash on entry, loads the exact
project Entry/Exit state from durable PV storage, and emits the copyable
`PV_EXIT_SUGGESTED_NEXT_PROMPT` without inferring a HIL choice.

ChatGPT transport is a separate registered remote MCP channel. It is not
packaged as the Codex lifecycle server and never becomes local PV authority.

## Stable Codex package map

- `.codex-plugin/plugin.json` -- stable product and host metadata
- `.mcp.json` -- the sole package-local native MCP registration
- `hooks/` -- SessionStart, UserPromptSubmit, and Stop turn-control hooks
- `skills/` -- the 15 governed skill contracts
- `src/evidence_lane_plugin/` -- runtime, storage, lifecycle, and MCP source
- `scripts/codex-release-channel.json` -- v2 release and Git policy
- `scripts/codex_release/` -- supported installer, controlled restart helper,
  and installed-package acceptance checker

The stable Codex archive excludes `.app.json`, generated/app namespaces,
ChatGPT connection metadata, the remote adapter, and evidence directories.

## Install, activate, and verify

1. Build the deterministic non-lifecycle rehearsal archive from the exact
   source commit/tree and verify its package and source manifests.
2. Run `scripts/codex_release/install_codex_stable.py` without activation to
   stage the local marketplace, then with activation to use Codex's supported
   marketplace/plugin commands. The installer never writes the generated cache
   directly and never deletes the historical v1.5 cache.
3. Run `scripts/codex_release/accept_codex_stable.py` before restart to prove
   marketplace/cache parity, the enabled selector, hooks, skills, catalog, Code
   mode, and controlled CI/CD law.
4. When the host has frozen the old MCP/skill snapshot, use
   `scripts/codex_release/Restart-EvidenceLaneCodex.ps1` only with its explicit
   restart switch. It stops one verified root Codex process, preserves the same
   task and project state, and makes no lifecycle-resume or State Travel call.
5. Reopen the same task and run the acceptance checker with a fresh native-route
   receipt. Only that post-restart result is eligible for installed-host HIL.

The persistent Plan/Delta change notice is emitted as a supported Codex hook
`systemMessage` on startup, prompt submission, and response stop. It is
non-authoritative and never exposes raw Delta text, private research questions,
secrets, or private reasoning. Exact placement above the prompt bar remains a
host-owned UI observation and must be proven at installed-host HIL.

## Release and Git boundary

Version 2.0.0 is the stable Codex slot. Historical 1.5.0 Git, packages,
tunnels, caches, PVs, and receipts remain immutable archive evidence. The exact
sole registered non-protected test branch may push automatically only through
the governed v2 remote-Git route using host-managed credentials. The plugin
does not request or store a PAT. Main push, merge, pull-request acceptance,
force push, publication, deployment, candidate acceptance, pointer movement,
and HIL inference remain forbidden.

Package rights and dependency obligations are recorded in
[LICENSE.md](LICENSE.md), [COPYRIGHT.md](COPYRIGHT.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Local tests prove deterministic packaging, source boundaries, metadata,
database integrity, and fail-closed behavior. They do not prove installed-host
pickup, external publication, customer value, or HIL acceptance.
