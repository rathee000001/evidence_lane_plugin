import json
from pathlib import Path

from scripts.audit_final_workspace_purge import audit

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"


def test_final_workspace_has_one_current_owner_for_every_executable_surface():
    result = audit()
    assert result["status"] == "PASS", result["violations"]
    actions = json.loads(
        (PLUGIN / "schemas/public-action-schemas.v4.json").read_bytes()
    )["actions"]
    skills = json.loads(
        (PLUGIN / "skills/skill-surface-registry.v4.json").read_bytes()
    )["skills"]
    authorities = json.loads(
        (PLUGIN / "authorities/authority-surface-registry.v4.json").read_bytes()
    )["authorities"]
    sectors = json.loads(
        (PLUGIN / "authorities/project_sectors/sector-runtime-registry.v4.json").read_bytes()
    )["implemented_sectors"]
    tools = json.loads(
        (PLUGIN / "toolchains/tool-definitions.v4.json").read_bytes()
    )["entries"]
    licenses = json.loads(
        (PLUGIN / "toolchains/licenses/retained-install-license-index.v4.json").read_bytes()
    )["records"]
    assert result["counts"] == {
        "actions": len(actions),
        "skills": len(skills),
        "authorities": len(authorities),
        "sectors": len(sectors),
        "tools": len(tools),
        "license_records": len(licenses),
    }
    assert result["project_data_changed"] is False
    assert result["installed_execution_claimed"] is False
