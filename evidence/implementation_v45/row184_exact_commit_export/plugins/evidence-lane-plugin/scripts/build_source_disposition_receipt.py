from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.source_disposition import (
    build_all_source_disposition_receipt,
)


def main() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    output = (
        repository_root
        / "evidence"
        / "implementation_v42"
        / "ALL_SOURCE_DISPOSITION_RECEIPT.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = build_all_source_disposition_receipt(repository_root)
    output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(output.as_posix())
    print(receipt["receipt_sha256"])


if __name__ == "__main__":
    main()
