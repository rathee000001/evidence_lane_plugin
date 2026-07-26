"""Advisory SessionStart context; deliberately performs no state mutation."""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    source = str(payload.get("source", "startup"))
    context = (
        "Evidence Lane Plugin is installed. If the user selects this workflow, "
        "use one project, one accepted PV, one agent, and one bounded task. Call "
        "doctor and session_boot before lifecycle work. Never infer HIL approval, "
        "move a pointer outside hil_decide, or push Git remotely without the "
        "separate exact confirmation. Session boot context stays outside PV files. "
        f"Session source: {source}."
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
