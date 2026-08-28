"""Native per-event Evidence Lane hook control; never edits Codex config directly."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from install_codex_stable import (
    EXPECTED_CODEX_HOST_HOOK_EVENTS,
    LOCAL_TESTING_MARKETPLACE_NAME,
    PLUGIN_NAME,
    _resolve_codex_cli_executable,
    _set_native_hook_event_states,
)

CONFIRMATION = "EXPLICIT_NATIVE_EVIDENCE_LANE_HOOK_EVENT_CAS"


def _assignment(value: str, *, boolean: bool) -> tuple[str, object]:
    name, separator, raw = str(value or "").partition("=")
    if not separator or name not in EXPECTED_CODEX_HOST_HOOK_EVENTS:
        raise argparse.ArgumentTypeError("Hook assignment requires EventName=value")
    if boolean:
        normalized = raw.strip().lower()
        if normalized not in {"true", "false"}:
            raise argparse.ArgumentTypeError("Hook state must be true or false")
        return name, normalized == "true"
    exact = raw.strip().upper()
    if len(exact) != 64 or any(
        character not in "0123456789ABCDEF" for character in exact
    ):
        raise argparse.ArgumentTypeError("Hook proof must be one SHA-256")
    return name, exact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-state", action="append", default=[])
    parser.add_argument("--event-proof", action="append", default=[])
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--changed-by", required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--hook-cwd", type=Path, required=True)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument(
        "--plugin-selector",
        default=f"{PLUGIN_NAME}@{LOCAL_TESTING_MARKETPLACE_NAME}",
    )
    args = parser.parse_args()
    if args.confirmation != CONFIRMATION:
        raise SystemExit("Native hook control requires the exact confirmation token")
    states = dict(_assignment(value, boolean=True) for value in list(args.event_state))
    proofs = dict(_assignment(value, boolean=False) for value in list(args.event_proof))
    result = _set_native_hook_event_states(
        executable=_resolve_codex_cli_executable(
            args.codex_executable,
            verify_version=True,
        ),
        codex_home=args.codex_home,
        data_root=args.data_root,
        hook_cwd=args.hook_cwd,
        plugin_selector=args.plugin_selector,
        desired_event_states=states,
        verified_event_receipts=proofs,
        changed_by=args.changed_by,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
