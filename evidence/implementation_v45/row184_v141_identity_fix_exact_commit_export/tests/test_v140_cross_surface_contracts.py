from __future__ import annotations

import asyncio
import json
import re
from itertools import pairwise
from pathlib import Path

from evidence_lane_plugin.mcp_server import (
    CHATGPT_PRO_GOVERNED_EXPOSURE_PROFILE,
    create_mcp_server,
)
from evidence_lane_plugin.service import EvidenceLaneService
from PIL import Image

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


def test_codex_package_keeps_skills_and_separates_remote_app_connections() -> None:
    manifest = json.loads(
        _read(PLUGIN / ".codex-plugin" / "plugin.json")
    )
    app_manifest = json.loads(_read(PLUGIN / ".app.json"))
    skill_files = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    packaged_skill_names = {path.parent.name for path in skill_files}

    assert manifest["interface"]["displayName"] == "Evidence Lane"
    assert "Evidence Lane 1.4" not in manifest["interface"]["displayName"]
    assert manifest["version"].startswith("1.4.1+")
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "apps" not in manifest
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
    assert app_manifest == {"apps": {}}

    root_skill = _read(PLUGIN / "skills" / "evi" / "SKILL.md")
    boot_skill = _read(PLUGIN / "skills" / "evi-boot" / "SKILL.md")
    mode_skill = _read(PLUGIN / "skills" / "evi-mode" / "SKILL.md")
    state_travel_skill = _read(
        PLUGIN / "skills" / "evi-state-travel" / "SKILL.md"
    )
    lifecycle_skill = _read(
        PLUGIN / "skills" / "evidence-lane-code-lifecycle" / "SKILL.md"
    )
    exit_skill = _read(PLUGIN / "skills" / "evi-exit-boot" / "SKILL.md")
    mcp_source = _read(PLUGIN / "src" / "evidence_lane_plugin" / "mcp_server.py")
    assert "UNAVAILABLE_ON_CHATGPT_PRO" in root_skill
    assert "CHATGPT_PRO_READ_ATTACH" in boot_skill
    assert "CHATGPT_PRO_READ_EXIT_OBSERVED" in exit_skill
    assert "Do not hide, rename, simulate, or claim those controls" in root_skill
    for skill in (root_skill, boot_skill, state_travel_skill, lifecycle_skill):
        normalized_skill = " ".join(skill.split())
        assert "exactly one" in normalized_skill
        assert "physically final" in normalized_skill
        assert "decision-dependent" in normalized_skill
    assert (
        "At the destination, re-project the exact complete task list as the first action"
        in " ".join(state_travel_skill.split())
    )
    assert "first post-verification action" in " ".join(boot_skill.split())
    assert "must precede source inspection" in " ".join(root_skill.split())
    for skill in (root_skill, state_travel_skill, lifecycle_skill):
        normalized_skill = " ".join(skill.split())
        assert "one governed project" in normalized_skill
        assert "one live writer" in normalized_skill
        assert "alternate-checkout writer" in normalized_skill
        assert "background mutation" in normalized_skill
        assert "second browser profile" in normalized_skill
    for skill in (root_skill, mode_skill, lifecycle_skill):
        normalized_skill = " ".join(skill.split())
        assert "same canonical Plan Lane" in normalized_skill
        assert "active source boundary" in normalized_skill
        assert "single-writer session" in normalized_skill
        assert "token wait" in normalized_skill
        assert "HIL wait" in normalized_skill
        assert "completed-but-still-governing rows" in normalized_skill
    assert "Packaged skills remain available" in mcp_source
    assert "cannot create or resume a runtime session" in mcp_source

    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    assert public_metadata["execution_writer_boundary"] == {
        "schema": "evidence-lane.execution-writer-boundary.v1",
        "project_policy": "ONE_GOVERNED_PROJECT",
        "writer_policy": "ONE_LIVE_WRITER",
        "execution_order": "LINEAR",
        "verification_order": "EVIDENCE_FIRST",
        "execution_profile": {
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "ultra",
            "reasoning_speed": "standard",
        },
        "execution_profile_change_authority": "EXPLICIT_USER_CHANGE_ONLY",
        "entry_recovery_agents": (
            "READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"
        ),
        "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
        "alternate_checkout_writer": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "background_mutation": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "browser_profile": (
            "ONE_USER_SELECTED_PROFILE_ONLY_UNLESS_EXPLICIT_USER_CHANGE"
        ),
    }
    assert public_metadata["goal_continuity"] == {
        "schema": "evidence-lane.goal-continuity.v1",
        "receipt_binding": "EXACT_TASK_LIST_SHA256_AND_GOVERNED_SESSION_ID",
        "plan_authority": "SAME_CANONICAL_PLAN_LANE",
        "source_boundary": "SAME_ACTIVE_SOURCE_BOUNDARY",
        "writer_session": "SAME_SINGLE_WRITER_SESSION",
        "pause_triggers": [
            "UI_CRASH",
            "TOKEN_WAIT",
            "REQUIRED_USER_INPUT",
            "HIL_WAIT",
        ],
        "pause_effect": "PAUSE_DEPENDENT_WORK_ONLY",
        "goal_completion_effect_while_waiting": "FORBIDDEN",
        "usage_reporting_task_status_effect": "NONE",
        "reconstruction_requires": [
            "ALL_COMPLETED_BUT_STILL_GOVERNING_ROWS",
            "EXACTLY_ONE_ACTIVE_ROW_WHEN_PANEL_PRESENT",
            "ALL_PENDING_ROWS",
        ],
        "completed_governing_rows_may_be_omitted": False,
        "goal_completion_allowed_when": (
            "PHYSICALLY_FINAL_SIX_WAY_HIL_DECIDED_AND_"
            "DECISION_DEPENDENT_WORK_COMPLETE"
        ),
    }
    assert public_metadata["plan_lane"] == {
        "current_public_projection": "https://evidencelane.org/#current-execution-plan",
        "sealed_historical_delta_rows": 80,
        "live_projection_rows": 111,
        "governed_receipt_rows": 119,
        "active_public_row": 182,
        "active_public_task_position": 102,
        "active_governed_receipt_position": 110,
        "last_pre_hil_public_row": 190,
        "last_pre_hil_governed_receipt_position": 118,
        "physically_final_hil_public_row": 191,
        "physically_final_hil_governed_receipt_position": 119,
        "steer_default_boundary": "BEFORE_NEXT_HIL",
        "persistent_until": (
            "PHYSICALLY_FINAL_SIX_WAY_HIL_DECIDED_AND_"
            "DECISION_DEPENDENT_WORK_COMPLETE"
        ),
        "panel_reactivation": {
            "schema": "evidence-lane.persistent-panel-reactivation.v1",
            "triggers": [
                "TOKEN_DRIVEN_CONTINUATION",
                "STALLED_GOAL",
                "CONTEXT_COMPACTION",
                "BROWSER_RESTART",
                "CODEX_RESTART",
                "SESSION_CONTINUATION",
                "SESSION_RESUME",
                "STATE_TRAVEL_DESTINATION_ENTRY",
            ],
            "first_required_action": "REPROJECT_EXACT_COMPLETE_TASK_LIST",
            "must_precede": [
                "SOURCE_INSPECTION",
                "SOURCE_MUTATION",
                "TESTING",
                "GIT_ACTIVITY",
                "LIFECYCLE_CALL",
            ],
            "exactly_one_in_progress": True,
            "preserve_order_and_row_count": True,
            "preserve_completed_and_pending_descriptions_unabridged": True,
            "visible_through_pause_and_hil": True,
            "drop_allowed_when": (
                "PHYSICALLY_FINAL_SIX_WAY_HIL_DECIDED_AND_"
                "DECISION_DEPENDENT_WORK_COMPLETE"
            ),
        },
    }
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
            "VISIBLE_ACTION_INTERCEPTED_BEFORE_SERVICE_INVOCATION_AND_REPORTED_UNAVAILABLE_WITHOUT_MUTATION"
        ),
    }
    chatgpt_profile = public_metadata["exposure_profiles"]["chatgpt_pro"]
    assert chatgpt_profile["name"] == "CHATGPT_PRO_GOVERNED"
    assert chatgpt_profile["tool_count"] == 62
    assert chatgpt_profile["active_read_tool_count"] == 21
    assert chatgpt_profile["visible_fail_closed_write_tool_count"] == 41
    assert chatgpt_profile["all_lifecycle_mutations_blocked"] is True


def test_chatgpt_submission_matches_the_live_governed_catalog(tmp_path: Path) -> None:
    submission = json.loads(_read(PLUGIN / "chatgpt-app-submission.json"))
    manifest = json.loads(_read(PLUGIN / ".codex-plugin" / "plugin.json"))
    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store"),
        exposure_profile=CHATGPT_PRO_GOVERNED_EXPOSURE_PROFILE,
    )
    live_tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert submission["$schema"] == (
        "https://developers.openai.com/apps-sdk/schemas/"
        "chatgpt-app-submission.v1.json"
    )
    assert submission["schema_version"] == 1
    assert submission["app_info"] == {
        "display_name": "Evidence Lane",
        "subtitle": "Governed project evidence",
        "description": (
            "Evidence Lane helps users inspect accepted project evidence, "
            "verified runtime and ENV/UOP Flash status, source lanes, Chat "
            "Lineage, diffs, backlog, and human-controlled lifecycle boundaries "
            "through ChatGPT."
        ),
        "category": "DEVELOPER_TOOLS",
    }
    assert len(submission["app_info"]["subtitle"]) <= 30
    assert set(submission["tools"]) == set(live_tools)
    assert len(live_tools) == 62
    assert sum(tool.annotations.readOnlyHint is True for tool in live_tools.values()) == 21
    assert sum(tool.annotations.readOnlyHint is False for tool in live_tools.values()) == 41

    justification_keys = {
        "read_only_justification",
        "open_world_justification",
        "destructive_justification",
    }
    for name, live_tool in live_tools.items():
        declared = submission["tools"][name]
        assert declared["annotations"] == {
            "readOnlyHint": live_tool.annotations.readOnlyHint,
            "openWorldHint": live_tool.annotations.openWorldHint,
            "destructiveHint": live_tool.annotations.destructiveHint,
        }
        assert set(declared["justifications"]) == justification_keys
        assert all(
            isinstance(value, str) and value.endswith(".") and "\n" not in value
            for value in declared["justifications"].values()
        )
        assert live_tool.outputSchema is not None
        assert live_tool.outputSchema["type"] == "object"

    assert len(submission["test_cases"]) == 5
    assert len(submission["negative_test_cases"]) == 3
    assert all(
        case["tools_triggered"] in live_tools
        and case["file_attachment_urls"] is None
        and case["expected_output_url"] is None
        for case in submission["test_cases"]
    )
    assert all(
        case["tools_triggered"] is None
        and case["file_attachment_urls"] is None
        and case["expected_output_url"] is None
        for case in submission["negative_test_cases"]
    )

    interface = manifest["interface"]
    assert interface["displayName"] == submission["app_info"]["display_name"]
    assert interface["websiteURL"] == public_metadata["homepage"]
    assert interface["privacyPolicyURL"] == public_metadata["privacy"]
    assert interface["termsOfServiceURL"] == public_metadata["terms"]
    assert public_metadata["version"] == "1.4.1"
    with Image.open(PLUGIN / "assets" / "evidence-lane-icon.png") as icon:
        assert icon.format == "PNG"
        assert icon.size == (256, 256)


def test_threejs_site_remains_and_meshy_is_not_a_runtime_dependency() -> None:
    package = json.loads(_read(ADAPTER / "package.json"))
    combined_dependencies = {
        **package.get("dependencies", {}),
        **package.get("devDependencies", {}),
    }
    assert "three" in combined_dependencies
    assert "@types/three" in combined_dependencies
    assert "framer-motion" in combined_dependencies
    assert not any("meshy" in name.lower() for name in combined_dependencies)
    assert not any(
        path.suffix.casefold() in {".glb", ".gltf"}
        for path in ADAPTER.rglob("*")
        if path.is_file()
    )
    ambient_source = _read(
        ADAPTER / "app" / "_components" / "ambient-evidence-field.tsx"
    )
    assert 'import * as THREE from "three"' in ambient_source
    assert "WebGLRenderer" in ambient_source
    studio_source = _read(ADAPTER / "app" / "studio" / "page.tsx").casefold()
    assert "adobe.com" not in studio_source
    assert "meshy.ai" not in studio_source


def test_route_specific_hero_centers_are_semantic_circles_inside_their_rings() -> None:
    hero_source = _read(ADAPTER / "app" / "_components" / "hero-orbit.tsx")
    styles = _read(ADAPTER / "app" / "globals.css")

    brain_block = hero_source.split("function BrainCenter", 1)[1].split(
        "function OperatorCenter", 1
    )[0]
    operator_block = hero_source.split("function OperatorCenter", 1)[1].split(
        "function StudioCenter", 1
    )[0]
    center_router = hero_source.split("function HeroCenter", 1)[1].split(
        "export function HeroOrbit", 1
    )[0]

    assert 'preset: "home" | "architecture"' in brain_block
    assert "PulsatingBrain" in brain_block
    assert "modeOperatorGuide.mode_count" in operator_block
    assert "PulsatingBrain" not in operator_block
    assert (
        'preset === "home" || preset === "architecture"'
        in center_router
    )
    assert 'heroOrbitCenter--${preset}' in brain_block
    assert 'data-hero-center={`${preset}-brain`}' in brain_block
    for preset, identity in (
        ("operators", "operator-authority"),
        ("studio", "studio-home-brain"),
        ("proof", "proof-lanes"),
        ("provenance", "provenance-r-and-d"),
        ("connect", "host-connection"),
        ("hil", "human-gate"),
    ):
        assert f'heroOrbitCenter--{preset}' in hero_source
        assert f'data-hero-center="{identity}"' in hero_source

    circular_selector = styles.split(
        ".heroOrbit--operators .operatorAuthorityCard,", 1
    )[1].split(".heroOrbit--operators .heroOrbitCenter", 1)[0]
    for selector in (
        ".heroOrbit--provenance .provenanceStamp",
        ".heroOrbit--connect .connectHeroSphere",
        ".heroOrbit--hil .hilHeroGate",
        ".heroOrbit--proof .heroOrbitLaneCore",
    ):
        assert selector in circular_selector
    assert "aspect-ratio: 1;" in circular_selector
    assert "border-radius: 50%;" in circular_selector

    center_rule = styles.split(".heroOrbitCenter {", 1)[1].split("}", 1)[0]
    ring_rule = styles.split(".heroOrbitRing {", 1)[1].split("}", 1)[0]
    assert "z-index: 4;" in center_rule
    assert "left: 50%;" in center_rule and "top: 50%;" in center_rule
    assert "z-index: 2;" in ring_rule
    assert "left: 50%;" in ring_rule and "top: 50%;" in ring_rule
    assert "const ringOpeningStart = 0.85" in hero_source
    assert (
        "animation: hero-center-arrive .75s cubic-bezier(.18,.82,.24,1) .08s both;"
        in styles
    )
    assert 0.08 + 0.75 < 0.85

    assert ".heroOrbitCenter--home { width: 44%; height: 44%; }" in styles
    assert ".heroOrbitCenter--architecture { width: 40%; height: 40%; }" in styles
    assert ".heroOrbit--operators .heroOrbitCenter { width: 55%; height: 55%; }" in styles
    assert ".heroOrbit--proof .heroOrbitCenter { width: 45%; height: 45%; }" in styles
    assert (
        ".heroOrbit--hil .heroOrbitCenter { width: 47%; height: 47%; }"
        in styles
    )

    # Architecture is the tightest route. At the 981px desktop boundary its
    # 48% inner ring still clears the 40% center and a 30px-diameter node.
    architecture_orbit = 981 * 0.46
    architecture_clearance = architecture_orbit * (0.48 - 0.40) / 2 - 15
    assert architecture_clearance > 3


def test_hero_orbits_preserve_distinct_routes_and_independent_safe_motion() -> None:
    hero_source = _read(ADAPTER / "app" / "_components" / "hero-orbit.tsx")
    styles = _read(ADAPTER / "app" / "globals.css")
    lanes_page = _read(ADAPTER / "app" / "lanes" / "page.tsx")
    home_page = _read(ADAPTER / "app" / "page.tsx")
    studio_page = _read(ADAPTER / "app" / "studio" / "page.tsx")

    assert hero_source.startswith('"use client";')
    assert '<HeroOrbit preset="home" />' in home_page
    assert '<HeroOrbit preset="studio" />' in studio_page
    assert "LaneOrbitAside" in lanes_page
    assert "HeroOrbit" not in lanes_page

    home_block = hero_source.split(
        "const homeGovernanceRings: readonly HeroOrbitRing[] = [", 1
    )[1].split("const presetRings", 1)[0]
    home_sizes = [int(value) for value in re.findall(r"size: (\d+)", home_block)]
    assert home_sizes == [54, 68, 82, 96]
    assert 'label: "Exact six-way HIL"' in home_block
    assert 'nodes: hilDecisionNodes' in home_block
    assert 'bottomTag("6", "exact HIL decisions")' in home_block
    assert "governed controls" in home_block
    assert "plugin surfaces" in home_block
    assert "source lanes" in home_block
    assert "home: homeGovernanceRings" in hero_source
    assert "studio: homeGovernanceRings" in hero_source
    assert 'side: "right"' not in hero_source
    assert "topTag" in home_block
    assert "leftTag" in home_block
    assert "bottomTag" in home_block

    hil_block = hero_source.split(
        "const hilDecisionNodes: readonly HeroOrbitNode[] = [", 1
    )[1].split("];", 1)[0]
    hil_labels = re.findall(r'label: "([A-Z ]+)"', hil_block)
    hil_icons = re.findall(r'tool: "([a-z]+)"', hil_block)
    assert hil_labels == [
        "APPROVE",
        "APPROVE WITH DELTA",
        "MORE RESEARCH",
        "ROLLBACK",
        "REJECT",
        "FAIL",
    ]
    assert len(hil_icons) == len(set(hil_icons)) == 6

    assert ".heroOrbitCenter--home { width: 44%; height: 44%; }" in styles
    assert ".heroOrbit--studio .heroOrbitCenter { z-index: 5; width: 100%; height: 100%; }" in styles
    assert 'data-hero-center="studio-home-brain"' in hero_source
    assert 'width: 44%;\n  height: 44%;' in styles
    assert "const ringOpeningStart = 0.85" in hero_source
    assert "studioSequenceStart = openingCompleteAt + 0.25" in hero_source
    assert '"--studio-sequence-start": `${studioSequenceStart}s`' in hero_source
    assert "calc(var(--studio-sequence-start) + var(--studio-flight-delay))" in styles
    assert "studio-input-ingest" in styles
    assert "studio-output-emit" in styles
    assert "studio-brain-absorb" in styles
    assert "scale(1.16)" in styles
    assert "studio-orb-engulf" not in styles
    assert "studio-brain-engulf" not in styles
    assert 'className="heroOrbitLayer"' in hero_source
    assert 'if (preset === "studio") return undefined;' in hero_source
    for origin in (
        "Exact six-way HIL",
        "Governed controls",
        "Plugin surfaces",
        "Source lanes",
    ):
        assert f'originRing: "{origin}"' in hero_source
    assert "studioInputOrigin(input)" in hero_source
    assert 'data-origin-orb={input.label}' in hero_source
    assert 'data-origin-ring={input.originRing}' in hero_source
    for label, extension in (
        ("SQLite", ".sqlite"),
        ("MMD", ".mmd"),
        ("DOT", ".dot"),
        ("JSON", ".json"),
    ):
        assert f'label: "{label}"' in hero_source
        assert f'extension: "{extension}"' in hero_source
    for x, y in ((24, 40), (76, 40), (24, 60), (76, 60)):
        assert f"x: {x}, y: {y}" in hero_source
    reduced_motion = styles.rsplit("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".studioBuilderInput" in reduced_motion
    assert ".heroOrbit--studio .studioBuilderBrain" in reduced_motion
    assert "opacity: .34;" in reduced_motion
    assert ".heroOrbit--studio .studioBuilderOutput" in reduced_motion
    assert "opacity: 1;" in reduced_motion
    assert "<EvidencePromptStudio" in studio_page

    # At the desktop maximum, adjacent Home-ring centers remain farther apart
    # than the maximum 40px orb diameter, even when two nodes align radially.
    minimum_ring_step = min(
        outer - inner for inner, outer in pairwise(home_sizes)
    )
    radial_gap = 780 * minimum_ring_step / 200
    assert round(radial_gap, 1) == 54.6
    assert "width: clamp(30px,2.55vw,40px) !important" in styles
    assert ".heroOrbitRing .heroOrbitGlassOrb { width: 20px !important" in styles
    inner_circumference_per_orb = 3.14159 * 780 * 0.54 / 6
    assert inner_circumference_per_orb > 200

    assert "ringRestAngles = [5, -9, 14, -20]" in hero_source
    assert "ringRotations" in hero_source
    assert "ringIndexFromTarget(event.target)" in hero_source
    assert "index === ringIndex" in hero_source
    assert 'data-ring-index={ringIndex}' in hero_source
    assert "suppressClick.current" in hero_source
    assert 'cursor: grab' in styles
    assert '.heroOrbit[data-orbit-interactive="true"] .heroOrbitNode' in styles
    assert re.search(r"\.heroOrbitRing \{.*?pointer-events: none;", styles, re.DOTALL)
    assert re.search(r"\.heroOrbitNode \{.*?pointer-events: none;", styles, re.DOTALL)
    assert re.search(
        r'\.heroOrbit\[data-orbit-interactive="true"\] \.heroOrbitGlassOrb \{.*?pointer-events: auto;',
        styles,
        re.DOTALL,
    )
    assert "heroOrbitNodeLabel" in hero_source
    assert "hover or focus an orb for its name" in hero_source
    assert "heroOrbitMarkerLine" in hero_source
    assert "markerTarget(ring)" in hero_source
    assert "hero-marker-line-draw" in styles
    assert "hero-marker-dot-reveal" in styles


def test_proof_hero_uses_lane_grammar_and_selected_lane_exact_artifacts() -> None:
    hero_source = _read(ADAPTER / "app" / "_components" / "hero-orbit.tsx")
    styles = _read(ADAPTER / "app" / "globals.css")
    proof_page = _read(ADAPTER / "app" / "proof" / "page.tsx")
    explorer = _read(ADAPTER / "app" / "_components" / "lane-proof-explorer.tsx")
    index = json.loads(
        _read(ADAPTER / "app" / "_data" / "dummy-lane-artifacts.json")
    )

    proof_block = hero_source.split("  proof: [", 1)[1].split(
        "  provenance: [", 1
    )[0]
    proof_sizes = [int(value) for value in re.findall(r"size: (\d+)", proof_block)]
    assert proof_sizes == [66, 84]
    assert 'label: "Lane proofs"' in proof_block
    assert "nodes: laneNodes" in proof_block
    assert 'bottomTag("18", "lane proofs")' in proof_block
    assert 'label: "Governed files"' in proof_block
    assert 'topTag("4", "governed files")' in proof_block
    for label in ("SQLite", "MMD", "DOT", "JSON / receipt"):
        assert f'label: "{label}"' in proof_block

    proof_center = hero_source.split("function ProofCenter", 1)[1].split(
        "function ProvenanceCenter", 1
    )[0]
    assert "PulsatingBrain" not in proof_center
    assert 'data-hero-center="proof-lanes"' in proof_center
    assert "numberAside heroOrbitLaneCore" in proof_center
    assert ".heroOrbit--proof .heroOrbitCenter { width: 45%; height: 45%; }" in styles
    assert ".heroOrbit--proof .heroOrbitLaneCore { display: flex; }" in styles
    assert "heroOrbitRingTag" in hero_source
    assert "heroOrbitMarkerLine" in hero_source
    assert "markerTarget(ring)" in hero_source

    assert 'aside={<HeroOrbit preset="proof" />}' in proof_page
    assert "<LaneProofExplorer />" in proof_page
    assert 'role="tablist"' in explorer and 'role="tabpanel"' in explorer
    assert "lane.canonical_artifacts.map" in explorer
    assert "download={artifact.filename}" in explorer
    assert "Download 8K PNG" in explorer and "Download SVG" in explorer
    assert "Open lossless full view" in explorer
    assert "vectorSrc" in explorer and "source_mmd_sha256" in explorer

    assert index["lane_count"] == 18
    assert index["canonical_file_count_per_lane"] == 4
    assert index["derived_render_count_per_lane"] == 2
    assert len(index["lanes"]) == 18
    for lane in index["lanes"]:
        assert [artifact["kind"] for artifact in lane["canonical_artifacts"]] == [
            "sqlite",
            "mmd",
            "dot",
            "receipt",
        ]
        assert lane["render"]["kind"] == "mmd_8k_png"
        assert lane["vector_render"]["kind"] == "mmd_vector_svg"
        assert (
            lane["render"]["source_mmd_sha256"]
            == lane["vector_render"]["source_mmd_sha256"]
        )


def test_glass_orbs_and_ring_markers_keep_centered_responsive_schema() -> None:
    asset_source = _read(ADAPTER / "app" / "_components" / "evidence-assets.tsx")
    hero_source = _read(ADAPTER / "app" / "_components" / "hero-orbit.tsx")
    styles = _read(ADAPTER / "app" / "globals.css")

    assert 'data-glass-orb-host="true"' in asset_source
    assert 'data-universal-orb-schema="T023_UNIVERSAL_GLASS_ORB_V001"' in asset_source
    assert (
        '<span className="glass-icon-orb__content">\n'
        '        <span className="glass-icon-orb__payload">{children}</span>'
        in asset_source
    )

    final_orb_rule = styles.rsplit(".glass-icon-orb {", 1)[1].split("}", 1)[0]
    final_content_rule = styles.rsplit(".glass-icon-orb__content {", 1)[1].split(
        "}", 1
    )[0]
    assert "overflow: hidden;" in final_orb_rule
    assert "left: 50%;" in final_content_rule
    assert "top: 50%;" in final_content_rule
    assert re.search(r"transform:\s*translate\(-50%,\s*-50%\);", final_content_rule)
    assert re.search(
        r"\.glass-icon-orb__content,\s*\.glass-icon-orb__payload \{"
        r".*?display: grid;.*?overflow: hidden;.*?place-items: center;",
        styles,
        re.DOTALL,
    )
    assert re.search(
        r"\.glass-icon-orb__payload > :is\(svg,img\).*?position: static;"
        r".*?margin: auto;.*?transform: none;",
        styles,
        re.DOTALL,
    )

    assert "rings.some((ring) => ring.tag.side === \"left\")" in hero_source
    assert "heroOrbit--has-left-ring-tag" in hero_source
    assert "heroOrbitRingTag" in hero_source
    assert "heroOrbitMarkerLine" in hero_source
    assert "markerTarget(ring)" in hero_source
    assert "display: inline-flex;" in styles
    assert "white-space: nowrap;" in styles
    mobile_rules = styles.split("@media (max-width: 620px)", 1)[1].split(
        "@media (prefers-reduced-motion: reduce)", 1
    )[0]
    assert ".heroOrbit--has-left-ring-tag" in mobile_rules
    assert "width: min(500px, 62vw);" in mobile_rules
    assert "transform: translateX(14vw);" in mobile_rules


def test_floating_studio_minimizes_without_resetting_governed_state() -> None:
    floating = _read(
        ADAPTER / "app" / "_components" / "floating-evidence-studio.tsx"
    )
    layout = _read(ADAPTER / "app" / "layout.tsx")

    assert layout.count("<FloatingEvidenceStudio />") == 1
    assert 'const [input, setInput] = useState("")' in floating
    assert "const [messages, setMessages]" in floating
    assert "const hasRetainedState = input.length > 0 || messages.length > 1 || busy" in floating
    assert 'data-retained-state={hasRetainedState ? "true" : "false"}' in floating
    assert 'hasRetainedState ? "Restore Evidence AI Studio"' in floating

    assert "panelRef.current?.contains(target)" in floating
    assert 'document.addEventListener("pointerdown", onOutsidePointer, true)' in floating
    assert 'document.removeEventListener("pointerdown", onOutsidePointer, true)' in floating
    assert 'window.addEventListener("keydown", onEscape)' in floating
    assert 'if (event.key === "Escape") setOpen(false)' in floating
    assert 'aria-label="Minimize Evidence AI Studio"' in floating

    outside_effect = floating.split("const onOutsidePointer", 1)[1].split(
        "}, [open]);", 1
    )[0]
    assert "setOpen(false)" in outside_effect
    assert "setMessages" not in outside_effect
    assert "setInput" not in outside_effect
    assert "setSequence" not in outside_effect


def test_hero_frame_motion_is_perimeter_only_without_diagonal_rotation() -> None:
    styles = _read(ADAPTER / "app" / "globals.css")

    frame_rule = styles.split(".orbitHeroFrame::before {", 1)[1].split("}", 1)[0]
    assert "linear-gradient(90deg" in frame_rule
    assert "background-size: 300% 100%;" in frame_rule
    assert "mask-composite: exclude;" in frame_rule
    assert "animation: hero-frame-trace-once" in frame_rule
    assert "transform:" not in frame_rule
    assert "rotate(" not in frame_rule
    assert ".orbitHeroFrame::after" not in styles

    trace_keyframes = styles.split("@keyframes hero-frame-trace-once", 1)[1].split(
        "@keyframes hero-center-arrive", 1
    )[0]
    assert "background-position:" in trace_keyframes
    assert "transform:" not in trace_keyframes
    assert "rotate(" not in trace_keyframes

    reduced_motion = styles.rsplit("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".orbitHeroFrame::before" in reduced_motion
    assert "animation: none !important;" in reduced_motion
    assert "hero-ring-clockwise-once" in styles


def test_each_primary_route_owns_exactly_one_distinct_hero_map() -> None:
    route_presets = {
        "page.tsx": "home",
        "architecture/page.tsx": "architecture",
        "operators/page.tsx": "operators",
        "studio/page.tsx": "studio",
        "proof/page.tsx": "proof",
        "provenance/page.tsx": "provenance",
        "connect/page.tsx": "connect",
        "hil/page.tsx": "hil",
    }
    for relative_path, preset in route_presets.items():
        source = _read(ADAPTER / "app" / relative_path)
        assert source.count("<HeroOrbit preset=") == 1
        assert f'<HeroOrbit preset="{preset}" />' in source
        for other in set(route_presets.values()) - {preset}:
            assert f'<HeroOrbit preset="{other}" />' not in source
        assert "ConcentricGovernanceMap" not in source
        assert "EvidenceOrbit" not in source

    architecture = _read(ADAPTER / "app" / "architecture" / "page.tsx")
    assert architecture.count("<PageHero") == 1
    assert 'className="parallelDiagram"' in architecture
    assert "heroOrbitRing" not in architecture
    assert "heroOrbitRingTag" not in architecture
    assert "operatorAuthorityCard" not in architecture

    retired_definitions = {
        ADAPTER / "app" / "_components" / "concentric-governance-map.tsx",
        ADAPTER / "app" / "_components" / "evidence-orbit.tsx",
    }
    live_sources = "\n".join(
        _read(path)
        for path in (ADAPTER / "app").rglob("*.tsx")
        if path not in retired_definitions
    )
    assert "ConcentricGovernanceMap" not in live_sources
    assert "EvidenceOrbit" not in live_sources


def test_current_execution_ledger_is_flat_unabridged_081_through_191() -> None:
    current = _read(ADAPTER / "app" / "_data" / "website-current-execution.ts")
    ledger = _read(ADAPTER / "app" / "_data" / "delta-ledger.ts")
    explorer = _read(ADAPTER / "app" / "_components" / "delta-ledger-explorer.tsx")
    home_page = _read(ADAPTER / "app" / "page.tsx")

    orders = [
        int(value)
        for value in re.findall(r"^    order: (\d+),$", current, re.MULTILINE)
    ]
    ids = re.findall(r'^    id: "(ROW_\d+)",$', current, re.MULTILINE)
    statuses = re.findall(
        r'^    status: "([A-Z ]+)",$', current, re.MULTILINE
    )
    assert orders == list(range(81, 192))
    assert ids == [f"ROW_{order}" for order in range(81, 192)]
    assert statuses.count("COMPLETED") == 100
    assert statuses.count("IN PROGRESS") == 1
    assert statuses.count("PENDING") == 10
    assert 'order: 155,\n    id: "ROW_155",\n    status: "PENDING"' in current
    assert 'order: 166,\n    id: "ROW_166",\n    status: "COMPLETED"' in current
    assert 'order: 167,\n    id: "ROW_167",\n    status: "COMPLETED"' in current
    assert 'order: 169,\n    id: "ROW_169",\n    status: "COMPLETED"' in current
    assert 'order: 170,\n    id: "ROW_170",\n    status: "COMPLETED"' in current
    assert 'order: 171,\n    id: "ROW_171",\n    status: "COMPLETED"' in current
    assert 'order: 180,\n    id: "ROW_180",\n    status: "COMPLETED"' in current
    assert 'order: 181,\n    id: "ROW_181",\n    status: "COMPLETED"' in current
    assert 'order: 182,\n    id: "ROW_182",\n    status: "IN PROGRESS"' in current
    assert 'order: 190,\n    id: "ROW_190"' in current
    assert 'order: 191,\n    id: "ROW_191"' in current
    assert "Latest superseding Home-and-Studio HIL-orbit Delta" in current
    assert "do not alter accepted historical rows 001–080" in current
    assert "activeTaskPosition: 102" in current
    assert "activeReceiptPosition: 110" in current
    assert "governedReceiptRows: 119" in current
    assert "panelReactivation" in current
    assert "executionWriterBoundary" in current
    assert "goalContinuity" in current
    assert "sealedPublicTaskListSha256: \"B3E3C620F95DAA59B9E0206C69EBBF42AE4E1E2B142F3B60E81BBFB4E83B31D0\"" in current

    assert "foundationIds" in ledger
    assert '{ order: 80, id: "EL-V130-ACCEPTED-AUTHORITY-SUCCESSOR-AND-RELEASE-GATE-DELTA-080"' in ledger
    assert "entry.nested" not in explorer
    assert "deltaNestedGroup" not in explorer
    assert "Delta SHA-256" not in explorer
    assert "Recorded by" not in explorer
    assert "81&ndash;191" in explorer
    assert "from public row 081 through 191" in home_page
