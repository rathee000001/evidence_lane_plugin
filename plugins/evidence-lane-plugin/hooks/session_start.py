"""Advisory SessionStart context; deliberately performs no state mutation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_FLASH_PROMPT_SHA256 = (
    "E5751173A1419137DF57ED4811D82D0ADED227C6D064A0DF603EF7813F539D5F"
)


def _flash_context() -> str:
    prompt_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "evidence_lane_plugin"
        / "session_flash"
        / "env15"
        / "UNIVERSAL_FLASH_PROMPT.md"
    )
    if not prompt_path.is_file():
        return (
            "SESSION_FLASH_AUTHORITY_UNAVAILABLE: the universal flash prompt is "
            "missing. Do not start Evidence Lane lifecycle work."
        )
    prompt_bytes = prompt_path.read_bytes()
    actual_hash = hashlib.sha256(prompt_bytes).hexdigest().upper()
    if actual_hash != _FLASH_PROMPT_SHA256:
        return (
            "SESSION_FLASH_AUTHORITY_MISMATCH: the universal flash prompt failed "
            "SHA-256 verification. Do not start Evidence Lane lifecycle work."
        )
    return (
        prompt_bytes.decode("utf-8")
        + "\n\nFLASH_PROMPT_SHA256="
        + actual_hash
        + "\nFLASH_CONTEXT_INSIDE_PV=false"
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    source = str(payload.get("source", "startup"))
    context = (
        _flash_context()
        + "\n\nCall runtime_doctor, session_flash_status, and session_boot before "
        "lifecycle work. Session source: " + source + "."
    )
    print(
        json.dumps(
            {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
