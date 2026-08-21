from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "evidence-lane-plugin" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_one_shot_dummy_poc import build_one_shot_dummy_poc


def test_one_shot_dummy_poc_proves_all_lanes_and_real_git_chain(
    tmp_path: Path,
) -> None:
    output = tmp_path / "one-shot"
    result = build_one_shot_dummy_poc(output, "test one-shot")
    receipt = json.loads((output / "POC_RECEIPT.json").read_text(encoding="utf-8"))

    assert result["status"] == "PASS"
    assert receipt["status"] == "PASS"
    assert receipt["lane_policy"]["emission"] == "LOADED_OR_DETECTED_ONLY"
    assert receipt["lane_policy"]["loaded_lane_count"] == 18
    assert len(receipt["lane_policy"]["emitted_lane_ids"]) == 18
    assert receipt["lane_policy"]["omitted_lane_ids"] == []
    assert len(receipt["lane_policy"]["actual_lane_directory_ids"]) == 18
    assert receipt["synthetic_git"]["commit_count"] == 3
    assert receipt["synthetic_git"]["parent_count"] == 2
    assert receipt["synthetic_git"]["file_change_count"] >= 5
    assert receipt["synthetic_git"]["chain_valid"] is True
    assert receipt["refresh"]["all_eighteen_lanes_byte_reused"] is True
    assert (output / "FORENSIC_INITIAL" / "FORENSIC_AUDIT.json").is_file()
    assert (output / "FORENSIC_REFRESH" / "FORENSIC_AUDIT.json").is_file()
    assert (output / "PACKAGE_MANIFEST.json").is_file()
