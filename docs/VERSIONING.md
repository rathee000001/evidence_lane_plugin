# Evidence Lane versioning

## Active product release

The current pre-HIL Evidence Lane Codex source release is `2.2.0`. These
source and package-build surfaces must use that same base version:

- root `pyproject.toml`;
- plugin `pyproject.toml`;
- `ENGINE_VERSION` in `constants.py`;
- `.codex-plugin/plugin.json` before its `+codex.<cachebuster>` suffix;
- the public documentation site `package.json` when it is versioned with the Codex source;
- current Codex README, architecture, acceptance, and release tooling.

Accepted Project Truth remains PV12/generation 12 on the `2.1.0` base. The
disabled fallback host surface was directly observed at historical PV11/main
`2.0.0`; it must not be relabeled as PV12/2.1 proof, and this source-only row
does not repair or activate it. No external chat app or remote MCP adapter is
part of the active `2.2.0` Codex source release. Historical delivery artifacts
remain available only through immutable Git and receipt history; they are not
current aliases.

`tests/test_v140_version_consistency.py` is the fail-closed source-release
check. A
release bump is incomplete until that test and the exact active surfaces agree.

## Versions that must not be rewritten

Historical versions remain evidence, not active release aliases. Do not replace:

- immutable accepted-PV or candidate bytes;
- sealed historical receipts and historical Delta traceability;
- compatibility boundaries such as `pre-v1.1` or schema migration labels;
- third-party dependency versions in lock files;
- independent database, lane-contract, ENV, UOP, or manifest schema versions.

This separation prevents a current product-version cleanup from falsifying old
evidence or conflating product, package-cache, dependency, and schema identities.
