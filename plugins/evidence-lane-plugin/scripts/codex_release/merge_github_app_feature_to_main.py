"""Non-executing tombstone for the superseded repository-merge route."""

from __future__ import annotations

import json


def main() -> int:
    print(
        json.dumps(
            {
                "status": "OBSOLETE_ROUTE",
                "obsolete_route": "github_app_repository_merge_v2",
                "required_current_route": "github_app_main_fast_forward_v3",
                "required_command": "fast_forward_github_app_feature_to_main.py",
                "legacy_execution_attempted": False,
            },
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
