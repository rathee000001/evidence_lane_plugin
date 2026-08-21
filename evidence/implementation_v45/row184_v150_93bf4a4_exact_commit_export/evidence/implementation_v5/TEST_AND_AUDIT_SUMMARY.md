# Evidence Lane objective audit v5

## Outcome

The local objective proof is stronger than the prior 42-test completion audit.
The current 67-member candidate totals 698,951 bytes and has exact manifest
SHA-256
`AE9ADB035B5E61D12282D249B7487CE98E1EE0F405A0702A70B2E213FEBBE857`.

The parent and remote `main` remain unchanged at
`4dd109c53af461eed2a69e2f3a0d411c5cb2564e`. The candidate remains
uncommitted and unpushed.

## Newly proved

The previous evidence recreated a prompt in the same Python process and ran
SessionStart only against an empty store. The v5 audit replaces that weak proof
with:

- a separate Python process loading the same accepted PV1 directly;
- `entry_action=CLASSIFY_ONE_TASK`, not a PV1 rebuild;
- next candidate `PV2` and accepted history `[PV1]`;
- recursive PV1 bytes unchanged by the process restart;
- two different staged cachebuster roots reading the same external store;
- identical complete store hashes before and after both SessionStart hooks;
- the same accepted PV, pointer generation, seal, history, and next ordinal
  visible after cache replacement;
- recursive PV1 and PV2 byte equality before and after rollback;
- dirty live-source reads correctly downgraded to `STALE` while immutable
  accepted truth remains available.

## Current validation

- F-drive V1/V3/current-app inputs: `10/10` hashes still match.
- Pytest: `44/44` in `273.83` seconds.
- Ruff formatting: `98` files pass.
- Ruff lint: pass.
- Mypy: `35` source files, zero issues.
- Bandit: zero findings.
- Locked dependency audit: `68` dependencies, zero known vulnerabilities.
- Plugin and skill validators: pass.
- Installed source hashes: `8/8`.
- Installed surface: `28` commands, `31` MCP tools, `18` lanes.
- Installed runtime doctor, lane catalog, and single transition law: pass.

The runtime wheel and installed plugin source did not change in this audit;
only tests and additive evidence changed. The installed cachebuster remains
`0.3.0+codex.20260726103131`.

## Honest remaining boundary

This task itself still loads the older cached skill/server. That is direct
evidence that a genuinely fresh Codex task is required to observe the current
native command and MCP surface. Installed files and standalone STDIO evidence
cannot prove native prompt-bar discovery.

The Claude reference repository has advanced beyond the authorized commit 8.
The new remote head was observed but not inspected or imported.

No real session/PV/pointer, external host connection, Drive action, commit, or
push occurred. HIL remains pending.

Verdict: `FIX-THEN-PURSUE`

Confidence: `0.99`

The verdict changes to `PURSUE` for the Codex product when a fresh task exposes
the installed 28 commands and 31 current tools. Full cross-host parity also
requires the separately unproven ChatGPT write boundary if the user retains it.
