from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
ADAPTER = PLUGIN / "remote_adapter"

CONTROL_CONTRACT = (
    ("evi-boot", "Boot"),
    ("evi-rollback", "Rollback"),
    ("evi-build", "Build"),
    ("evi-refresh", "Refresh"),
    ("evi-mode", "Mode"),
    ("evi-source-intake", "Source Intake"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_exact_six_control_order_matches_skills_and_website() -> None:
    expected_commands = tuple(f"/{skill}" for skill, _ in CONTROL_CONTRACT)
    expected_names = tuple(label for _, label in CONTROL_CONTRACT)

    root_skill = _read(PLUGIN / "skills" / "evi" / "SKILL.md")
    lifecycle_skill = _read(
        PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md"
    )
    for skill_text in (root_skill, lifecycle_skill):
        commands = tuple(
            re.findall(r"^\d+\. `(/evi-[^`]+)`$", skill_text, flags=re.MULTILINE)
        )
        assert commands[:6] == expected_commands

    site_source = _read(ADAPTER / "app" / "_data" / "site.ts")
    controls_block = site_source.split("export const controls = [", 1)[1].split(
        "export const laneToolchains", 1
    )[0]
    assert tuple(re.findall(r'name: "([^"]+)"', controls_block)) == expected_names

    surface_source = _read(ADAPTER / "app" / "_data" / "plugin-surfaces.ts")
    surface_blocks = re.findall(
        r"^  \{\n(?P<body>.*?)^  \},$",
        surface_source,
        flags=re.DOTALL | re.MULTILINE,
    )
    primary_pairs = tuple(
        (
            re.search(r'^    id: "([^"]+)",$', block, flags=re.MULTILINE).group(1),
            re.search(r'^    label: "([^"]+)",$', block, flags=re.MULTILINE).group(1),
        )
        for block in surface_blocks
        if "    primaryControl: true," in block
    )
    assert len(primary_pairs) == 6
    assert set(primary_pairs) == set(CONTROL_CONTRACT)

    for skill, _ in CONTROL_CONTRACT:
        assert (PLUGIN / "skills" / skill / "SKILL.md").is_file()


def test_chatgpt_full_plugin_mapping_keeps_skills_and_read_fail_closed_law() -> None:
    manifest = json.loads(
        _read(PLUGIN / ".codex-plugin" / "plugin.json")
    )
    app_manifest = json.loads(_read(PLUGIN / ".app.json"))
    skill_files = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    packaged_skill_names = {path.parent.name for path in skill_files}

    assert manifest["interface"]["displayName"] == "Evidence Lane"
    assert "Evidence Lane 1.4" not in manifest["interface"]["displayName"]
    assert manifest["version"].startswith("1.4.0+")
    assert manifest["skills"] == "./skills/"
    assert len(skill_files) == 15
    assert packaged_skill_names == {
        "evi",
        "evi-additional-plugin",
        "evi-boot",
        "evi-build",
        "evi-change-storage-connector",
        "evi-drop-additional-plugin",
        "evi-exit-boot",
        "evi-mode",
        "evi-plugin",
        "evi-refresh",
        "evi-rollback",
        "evi-source-intake",
        "evi-state-travel",
        "evi-storage",
        "evidence-lane-code-lifecycle",
    }
    assert app_manifest["apps"]["evidence-lane"]["id"] == (
        "asdk_app_6a7743d238e48191be8b69c87fb71d7f"
    )

    root_skill = _read(PLUGIN / "skills" / "evi" / "SKILL.md")
    boot_skill = _read(PLUGIN / "skills" / "evi-boot" / "SKILL.md")
    exit_skill = _read(PLUGIN / "skills" / "evi-exit-boot" / "SKILL.md")
    mcp_source = _read(PLUGIN / "src" / "evidence_lane_plugin" / "mcp_server.py")
    assert "UNAVAILABLE_ON_CHATGPT_PRO_READ" in root_skill
    assert "CHATGPT_PRO_READ_ATTACH" in boot_skill
    assert "CHATGPT_PRO_READ_EXIT_OBSERVED" in exit_skill
    assert "Do not hide, rename, simulate, or claim those controls" in root_skill
    assert "Packaged skills remain available" in mcp_source
    assert "cannot create or resume a runtime session" in mcp_source

    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    assert public_metadata["skill_surface"] == {
        "packaged_count": 15,
        "visibility": "ALL_PACKAGED_SKILL_ENTRIES_VISIBLE_ON_CHATGPT_AND_CODEX",
        "read_safe_workflows": [
            "Root routing and exact six-control presentation",
            "Boot ENV/UOP Flash, runtime and accepted-PV observation",
            "Accepted-PV Entry and Exit Slip observation",
            "Lane, search, diff, backlog and governed-panel guidance",
            "Mode, storage and lifecycle-law explanation without mutation",
        ],
        "chatgpt_pro_write_behavior": (
            "UNAVAILABLE_ACTION_REPORTED_AND_FAIL_CLOSED_WITHOUT_CLAIMING_MUTATION"
        ),
    }


def test_threejs_site_remains_and_meshy_is_not_a_runtime_dependency() -> None:
    package = json.loads(_read(ADAPTER / "package.json"))
    combined_dependencies = {
        **package.get("dependencies", {}),
        **package.get("devDependencies", {}),
    }
    assert "three" in combined_dependencies
    assert "@types/three" in combined_dependencies
    assert not any("meshy" in name.lower() for name in combined_dependencies)
    ambient_source = _read(
        ADAPTER / "app" / "_components" / "ambient-evidence-field.tsx"
    )
    assert 'import * as THREE from "three"' in ambient_source
    assert "WebGLRenderer" in ambient_source
