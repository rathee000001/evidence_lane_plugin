"""Current cross-surface skill, SDK, hook, and website contracts."""

from __future__ import annotations

import json
import re
from itertools import pairwise
from pathlib import Path

from evidence_lane_plugin.constants import GOVERNED_SKILL_COUNT

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
ADAPTER = ROOT / "apps" / "evidence-lane-app"

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


def _reduced_motion_section(styles: str, selector: str) -> str:
    return next(
        section
        for section in styles.split("@media (prefers-reduced-motion: reduce)")[1:]
        if selector in section
    )


def _css_rule_with(styles: str, selector: str, required: str) -> str:
    return next(
        section.split("}", 1)[0]
        for section in styles.split(selector)[1:]
        if required in section.split("}", 1)[0]
    )


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


def test_current_codex_package_has_no_active_chatgpt_host_surface() -> None:
    manifest = json.loads(_read(PLUGIN / ".codex-plugin" / "plugin.json"))
    skill_files = sorted((PLUGIN / "skills").glob("*/SKILL.md"))

    assert manifest["interface"]["displayName"] == "Evidence Lane"
    assert manifest["version"] == "3.0.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "apps" not in manifest
    assert not (PLUGIN / ".app.json").exists()
    assert len(skill_files) == GOVERNED_SKILL_COUNT
    assert not (PLUGIN / "chatgpt-app-connection.json").exists()
    assert not (PLUGIN / "chatgpt-app-submission.json").exists()
    assert not (ROOT / "apps" / "evidence-lane-app" / "api" / "index.py").exists()
    assert not (ROOT / "apps" / "evidence-lane-app" / "requirements.txt").exists()
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
    reduced_motion = _reduced_motion_section(styles, ".studioBuilderInput")
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
    assert (
        'bottomTag(String(currentProductContract.canonicalLaneCount), "lane proofs")'
        in proof_block
    )
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

    final_orb_rule = _css_rule_with(
        styles, ".glass-icon-orb {", "overflow: hidden;"
    )
    final_content_rule = _css_rule_with(
        styles, ".glass-icon-orb__content {", "left: 50%;"
    )
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

    reduced_motion = _reduced_motion_section(styles, ".orbitHeroFrame::before")
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


def test_current_execution_ledger_uses_generated_unabridged_plan_authority() -> None:
    current = _read(ADAPTER / "app" / "_data" / "website-current-execution.ts")
    projection = json.loads(
        _read(ADAPTER / "app" / "_data" / "website-plan-projection.json")
    )
    ledger = _read(ADAPTER / "app" / "_data" / "delta-ledger.ts")
    explorer = _read(ADAPTER / "app" / "_components" / "delta-ledger-explorer.tsx")
    home_page = _read(ADAPTER / "app" / "page.tsx")

    rows = projection["rows"]
    assert projection["canonical_authority"] == "PLAN_LANE"
    assert projection["row_start"] == 81
    assert projection["task_count"] == len(rows)
    assert projection["row_end"] == projection["row_start"] + len(rows) - 1
    assert [row["row"] for row in rows] == list(
        range(projection["row_start"], projection["row_end"] + 1)
    )
    assert [row["task_position"] for row in rows] == list(range(1, len(rows) + 1))
    assert sum(projection["status_counts"].values()) == len(rows)
    assert projection["status_counts"]["in_progress"] == 1
    active = [row for row in rows if row["status"] == "IN_PROGRESS"]
    assert [(row["row"], row["task_id"]) for row in active] == [
        (projection["active_row"], projection["active_task_id"])
    ]
    assert rows[-1]["row"] == projection["row_end"]
    assert rows[-1]["task_id"] == (
        "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR"
    )
    assert rows[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert projection["persistent_until"] == "NEXT_GOVERNED_HIL_PRESENTED"

    assert 'import planProjection from "./website-plan-projection.json"' in current
    assert "const rows = planProjection.rows" in current
    assert "rows.map((row)" in current
    assert "linkedDeltaIds: row.linked_delta_ids" in current
    assert "exactlyOneActiveRow" in current
    assert "physicallyFinalHilIsLast" in current
    assert "ROW_184" not in current
    assert "foundationIds" in ledger
    assert '{ order: 80, id: "EL-V130-ACCEPTED-AUTHORITY-SUCCESSOR-AND-RELEASE-GATE-DELTA-080"' in ledger
    assert "websiteCurrentExecution.map" in ledger
    assert "entry.nested" not in explorer
    assert "deltaNestedGroup" not in explorer
    assert "deltaLedgerBoundary.rowStart" in explorer
    assert "deltaLedgerBoundary.rowEnd" in explorer
    assert "deltaLedgerBoundary.websitePlanSnapshotSha256" in explorer
    assert "deltaLedgerBoundary.rowStart" in home_page
    assert "deltaLedgerBoundary.rowEnd" in home_page
    assert "deltaLedgerBoundary.activeTaskId" in home_page
