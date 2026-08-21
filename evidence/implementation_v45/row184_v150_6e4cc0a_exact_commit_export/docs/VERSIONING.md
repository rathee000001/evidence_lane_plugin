# Evidence Lane versioning

## Active product release

The active Evidence Lane product release is `1.5.0`. These surfaces must use
that same base version:

- root `pyproject.toml`;
- plugin `pyproject.toml`;
- `ENGINE_VERSION` in `constants.py`;
- `.codex-plugin/plugin.json` before its `+codex.<cachebuster>` suffix;
- the remote adapter `package.json`;
- current README, architecture, website, PoC, acceptance, and release tooling.

`tests/test_v140_version_consistency.py` is the fail-closed release check. A
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
