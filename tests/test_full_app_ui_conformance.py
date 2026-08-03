from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "plugins" / "evidence-lane-plugin" / "remote_adapter"
APP = ADAPTER / "app"
COMPONENTS = APP / "_components"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _active_tsx() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in APP.rglob("*.tsx"))


def test_authoritative_logo_icon_and_static_brain_have_exact_roles() -> None:
    brain = ADAPTER / "public" / "assets" / "evidence-static-brain.png"
    full_logo = ADAPTER / "public" / "evidence-lane-full-logo.png"
    app_icon = ADAPTER / "public" / "evidence-lane-icon.png"
    header = (COMPONENTS / "site-header.tsx").read_text(encoding="utf-8")
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")
    assets = (COMPONENTS / "evidence-assets.tsx").read_text(encoding="utf-8")

    assert brain.stat().st_size == 1_019_717
    assert _sha256(brain) == "70FBC1E11C029DDA98AA695F80E96891312E028464476B7E54BA0B6B259A6529"
    assert _sha256(full_logo) == "FB7356284760A9AF436B08B75158B9407E582F8107077FC585E4C8F716530933"
    assert _sha256(app_icon) == "17B8B60FF41388237302CA1D56BD37A9F9E08D7484D67C9A82E59637B0BE2DEA"
    assert 'src="/evidence-lane-full-logo.png"' in header
    assert 'icons: { icon: "/evidence-lane-icon.png"' in layout
    assert 'src="/assets/evidence-static-brain.png"' in assets


def test_full_width_ril_gold_story_replaces_the_desktop_app_shell() -> None:
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")
    header = (COMPONENTS / "site-header.tsx").read_text(encoding="utf-8")
    css = (APP / "globals.css").read_text(encoding="utf-8")
    active_tsx = _active_tsx()

    assert "SiteHeader" in layout and "SiteFooter" in layout and "SiteAtmosphere" in layout
    assert 'className="rilFloatingNav"' in header
    assert 'className="navPillCluster"' in header
    assert 'className="floatingBrandLogo"' in header
    assert ".shell {\n  width: 100%;\n  max-width: none;" in css
    assert ".rilFloatingNav" in css and ".rilPill" in css
    assert ".homeHero" in css and ".sourceBrainPanel" in css
    assert ".section[id] { scroll-margin-top: 120px; }" in css
    assert ".section[id] { scroll-margin-top: 158px; }" in css
    for forbidden in (
        "EvidenceWorkspaceFrame",
        "evidenceWorkspaceRail",
        "promptStudioRail",
        "system task status",
        "pc cluster",
    ):
        assert forbidden.casefold() not in active_tsx.casefold()


def test_source_lab_exposes_generator_six_sources_and_four_brains() -> None:
    lab = (COMPONENTS / "source-brain-lab.tsx").read_text(encoding="utf-8")

    assert len(re.findall(r'^    shortName: "', lab, flags=re.MULTILINE)) == 11
    for label in (
        "V5.9 app",
        "Graphify",
        "GitHub AW",
        "AW brain",
        "GitHub MCP",
        "CodeQL",
        "Branch Deploy",
        "Local Action",
        "EL v0.9",
        "RIL brain",
        "Gold brain",
    ):
        assert f'shortName: "{label}"' in lab

    for exact_commit in (
        "00efd6e7969837ae4a9f11d8d504dcd3b20b09df",
        "61bd1aa20cb68d77d2e0d4b4974b4480cb1b305c",
        "3778a41476e31a072430cfee7c5d31c5f72def60",
        "74c8994c9fa3ca4551c01879ef9f74e3e09e791a",
        "7ad5ec6a7e19e3e341846e4d33c4ed779b3e8036",
        "b9351d8a8f1e6eed27646f4d892b49a3847ba180",
    ):
        assert exact_commit in lab

    assert "V5.9 is the app/runtime identity" in lab
    assert "V5.3 is an internal stable-runtime or package-schema identity" in lab
    assert "Neither is Graphify's version" in lab
    assert "the earlier build remains unproven" in lab
    assert "GENERATOR UNPROVEN" in lab
    assert "No manifest binds them to the selected V5.9 executable SHA-256" in lab
    assert "sourceTabTarget" in lab


def test_source_lab_keeps_reuse_refusal_and_agentic_route_law_visible() -> None:
    lab = (COMPONENTS / "source-brain-lab.tsx").read_text(encoding="utf-8")

    assert "active.reused" in lab and "active.refused" in lab and "active.boundary" in lab
    assert "active.entities.map" in lab
    for guard in ("ACTIVE", "GRANT LIVE", "CAPABILITY + ACTION", "LANE", "HOST"):
        assert f'"{guard}"' in lab
    assert "zero matches: fail closed" in lab
    assert "multiple matches: require exact preferred ID" in lab
    assert "not a second GitHub gateway" in lab


def test_toolchain_is_official_icons_to_one_static_brain_to_four_files() -> None:
    console = (COMPONENTS / "evidence-console.tsx").read_text(encoding="utf-8")
    assets = (COMPONENTS / "evidence-assets.tsx").read_text(encoding="utf-8")
    css = (APP / "globals.css").read_text(encoding="utf-8")

    assert "laneToolchains.map" in console
    assert "laneToolSteps(active)" in console
    assert console.count("<EvidenceBrainAsset") == 1
    assert "OfficialToolIcon" in console
    assert '`${active.id}_sector_v001.sqlite`' in console
    assert '`${active.id}.mmd`' in console
    assert '`${active.id}.dot`' in console
    assert '"refresh_receipt.json"' in console
    assert "outputFiles.map" in console
    assert "--orbit-start" in console and "--orbit-end" in console
    assert "translateX(98px)" in css and "rotate(var(--orbit-end))" in css
    assert "enter · orbit · digest · emit" in console

    for tool in (
        "database",
        "docker",
        "git",
        "media",
        "node",
        "package",
        "pulse",
        "python",
        "terminal",
    ):
        icon = ADAPTER / "public" / "assets" / "tool-icons" / f"{tool}.svg"
        assert icon.stat().st_size >= 700
        assert f'{tool}: "/assets/tool-icons/{tool}.svg"' in assets

    for forbidden in ("root-fiber", "Telemetry", "Scanner"):
        assert forbidden not in console


def test_all_eighteen_canonical_lanes_drive_the_interactive_toolchain() -> None:
    site = (APP / "_data" / "site.ts").read_text(encoding="utf-8")
    console = (COMPONENTS / "evidence-console.tsx").read_text(encoding="utf-8")
    lane_block = site.split("export const laneToolchains = [", 1)[1].split("] as const;", 1)[0]
    lane_ids = re.findall(r'^    id: "([a-z_]+)",$', lane_block, flags=re.MULTILINE)

    assert len(lane_ids) == 18
    assert len(set(lane_ids)) == 18
    assert lane_ids[-1] == "chat_lineage"
    assert 'role="tablist" aria-label="Canonical Evidence Lane toolchains"' in console
    assert "ArrowRight" in console and "ArrowLeft" in console
    assert "Replay flow" in console and "Auto cycle" in console


def test_prompt_studio_is_full_width_grounded_and_refuses_unknowns() -> None:
    studio = (COMPONENTS / "evidence-prompt-studio.tsx").read_text(encoding="utf-8")
    css = (APP / "globals.css").read_text(encoding="utf-8")

    assert "promptStudioRail" not in studio
    assert "promptStudioHeader" in studio and "promptStudioWorkspace" in studio
    assert 'data-grounding="LOCAL_SITE_KNOWLEDGE_MAP"' in studio
    assert "no external model call is implied" in studio
    assert "cannot ground that question" in studio
    assert "promptStopWords" in studio
    assert "!promptStopWords.has(token)" in studio
    assert "Published sources" in studio and "Boundary reference" in studio
    assert ".promptStudio { min-height: 760px; border: 0; background: transparent; }" in css


def test_three_and_framer_motion_are_bounded_and_accessible() -> None:
    ambient = (COMPONENTS / "ambient-evidence-field.tsx").read_text(encoding="utf-8")
    loader = (COMPONENTS / "site-atmosphere.tsx").read_text(encoding="utf-8")
    reveal = (COMPONENTS / "motion-reveal.tsx").read_text(encoding="utf-8")
    package = (ADAPTER / "package.json").read_text(encoding="utf-8")

    assert 'from "three"' in ambient
    assert "TorusGeometry" in ambient and "THREE.Points" in ambient
    assert "ResizeObserver" in ambient and "prefers-reduced-motion: reduce" in ambient
    assert "geometry.dispose()" in ambient and "renderer.forceContextLoss()" in ambient
    assert "{ ssr: false }" in loader
    assert 'from "framer-motion"' in reveal
    assert "initial={{ opacity: 0, y: 28 }}" in reveal
    assert '"framer-motion": "12.38.0"' in package
    for forbidden in ("root-fiber", "Telemetry", "Scanner"):
        assert forbidden not in ambient
