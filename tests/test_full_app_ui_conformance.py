from __future__ import annotations

import hashlib
import json
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
    assert app_icon.stat().st_size <= 10 * 1024
    assert app_icon.read_bytes() == (
        ROOT / "plugins" / "evidence-lane-plugin" / "assets" / "evidence-lane-icon.png"
    ).read_bytes()
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
    assert '<div className="brand" aria-hidden="true">' in header
    assert '<Link className="brand"' not in header
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


def test_primary_navigation_has_one_typed_visual_identity_per_route() -> None:
    header = (COMPONENTS / "site-header.tsx").read_text(encoding="utf-8")
    site = (APP / "_data" / "site.ts").read_text(encoding="utf-8")
    navigation = site.split("export const primaryNavigation = [", 1)[1].split(
        "] as const;", 1
    )[0]
    hrefs = re.findall(r'href: "([^"]+)"', navigation)

    assert hrefs == [
        "/",
        "/architecture",
        "/lanes",
        "/operators",
        "/memory",
        "/canon",
        "/ai-learning",
        "/git-ci",
        "/skills",
        "/mcp",
        "/hooks",
        "/commands",
        "/proof",
        "/provenance",
        "/connect",
        "/studio",
        "/hil",
    ]
    assert len(hrefs) == 17
    assert len(set(hrefs)) == len(hrefs)
    assert "satisfies Readonly<" in header
    assert "Record<PrimaryNavigationHref" in header
    for href in hrefs:
        assert f'"{href}": {{ color:' in header


def test_primary_navigation_uses_two_desktop_rows_without_horizontal_scroll() -> None:
    css = (APP / "globals.css").read_text(encoding="utf-8")
    nav_law = css.split("/* Current v3.0 primary-navigation law", 1)[1].split(
        "/* Full-depth business pages", 1
    )[0]
    final_nav_rule = nav_law.split(".rilFloatingNav .navLinks {", 1)[1].split("}", 1)[0]

    assert "display: grid" in final_nav_rule
    assert "grid-template-columns: repeat(9, max-content)" in final_nav_rule
    assert "overflow: visible" in final_nav_rule
    assert "overflow-x: auto" not in final_nav_rule
    assert "Current v3.0 primary-navigation law" in css


def test_current_v300_depth_pages_use_interactive_governed_popups() -> None:
    explorer = (COMPONENTS / "governed-story-explorer.tsx").read_text(encoding="utf-8")
    popup = (COMPONENTS / "governed-popup.tsx").read_text(encoding="utf-8")
    routes = ("memory", "canon", "ai-learning", "git-ci", "plan", "release")

    assert 'role="tablist"' in explorer
    assert "ArrowRight" in explorer and "ArrowLeft" in explorer
    assert "GovernedPopup" in explorer
    assert "previousFocusRef.current?.focus()" in popup
    assert "keepFocusInside" in popup and 'event.key !== "Tab"' in popup
    for route in routes:
        page = (APP / route / "page.tsx").read_text(encoding="utf-8")
        assert "GovernedStoryExplorer" in page
        assert "depthContractGrid" in page or "deliveryChecklist" in page


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


def test_toolchain_uses_exact_app_brain_tools_and_schema_while_proof_owns_files() -> None:
    console = (COMPONENTS / "evidence-console.tsx").read_text(encoding="utf-8")
    proof = (COMPONENTS / "lane-proof-explorer.tsx").read_text(encoding="utf-8")
    assets = (COMPONENTS / "evidence-assets.tsx").read_text(encoding="utf-8")
    popup = (COMPONENTS / "governed-popup.tsx").read_text(encoding="utf-8")
    assert "laneRuntimeContracts" in console
    assert "universalLaneSchema" in console
    assert "runtime.tools.map" in console
    assert "runtime.schemaAdditions.map" in console
    assert console.count("<PulsatingBrain") == 1
    assert "OfficialToolIcon" in console
    assert "outputFiles.map" not in console
    assert "laneOutputFiles" not in console
    assert "dummy-lane-artifacts.json" in proof
    assert "canonical_artifacts.map" in proof
    assert "Download 8K PNG" in proof and "Download SVG" in proof
    assert "proofLightboxCanvas" in proof
    assert "?sha256=${lane.vector_render.sha256}" in proof
    assert proof.count("unoptimized") == 2
    assert 'event.key === "Escape"' in proof
    assert "MAX_ZOOM = 128" in proof and "multiplyZoom" in proof and "startDrag" in proof
    assert "onDoubleClick" in proof and "lossless deep zoom" in proof
    assert "--orbit-start" not in console and "--orbit-end" not in console
    assert "T023_UNIVERSAL_POPUP_FADE_V001" in popup
    assert "T023_UNIVERSAL_FROSTED_POPUP_V001" in popup
    assert "Universal schema contract" in console and "{active.name} additions" in console

    css = (APP / "globals.css").read_text(encoding="utf-8")
    assert ".laneProofPanel { width: 100%; max-width: 100%; min-width: 0;" in css
    assert "grid-template-columns: minmax(0,.85fr) minmax(0,1.15fr)" in css
    assert ".proofLightboxCanvas img" in css and "image-rendering: auto" in css

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
    landing = (APP / "page.tsx").read_text(encoding="utf-8")
    lanes_page = (APP / "lanes" / "page.tsx").read_text(encoding="utf-8")
    lane_block = site.split("export const laneToolchains = [", 1)[1].split("] as const;", 1)[0]
    lane_ids = re.findall(r'^    id: "([a-z_]+)",$', lane_block, flags=re.MULTILINE)

    assert len(lane_ids) == 18
    assert len(set(lane_ids)) == 18
    assert lane_ids[-1] == "chat_lineage"
    assert 'role="tablist"' in console
    assert 'aria-label="Canonical Evidence Lane toolchains"' in console
    assert "ArrowRight" in console and "ArrowLeft" in console
    assert "LaneToolchainExplorer" in lanes_page
    assert "LaneToolchainExplorer" not in landing
    assert "Replay flow" not in console and "Auto cycle" not in console

    css = (APP / "globals.css").read_text(encoding="utf-8")
    lane_picker_rule = re.search(r"\.laneToolchainPage \.lanePicker \{([^}]+)\}", css)
    assert lane_picker_rule is not None
    assert "display: grid" in lane_picker_rule.group(1)
    assert "grid-template-columns: repeat(9,minmax(0,1fr))" in lane_picker_rule.group(1)
    assert "overflow-x: auto" not in lane_picker_rule.group(1)


def test_v140_home_uses_concentric_delta_story_plugin_catalog_and_universal_glass_pills() -> None:
    landing = (APP / "page.tsx").read_text(encoding="utf-8")
    header = (COMPONENTS / "site-header.tsx").read_text(encoding="utf-8")
    catalog = (COMPONENTS / "plugin-surface-catalog.tsx").read_text(encoding="utf-8")
    popup = (COMPONENTS / "governed-popup.tsx").read_text(encoding="utf-8")
    surfaces = (APP / "_data" / "plugin-surfaces.ts").read_text(encoding="utf-8")
    ledger = (APP / "_data" / "delta-ledger.ts").read_text(encoding="utf-8")
    css = (APP / "globals.css").read_text(encoding="utf-8")
    site_data = (APP / "_data" / "site.ts").read_text(encoding="utf-8")
    operators = (COMPONENTS / "mode-operator-explorer.tsx").read_text(encoding="utf-8")
    architecture = (APP / "architecture" / "page.tsx").read_text(encoding="utf-8")
    orbit = (COMPONENTS / "evidence-orbit.tsx").read_text(encoding="utf-8")

    assert "DeltaLedgerExplorer" in landing
    assert "PluginSurfaceCatalog" in landing
    assert "HeroOrbit" in landing
    assert "sourceLanes" in orbit and "pluginSurfaces" in orbit
    assert 'aria-label="18 source lanes"' in orbit
    assert 'aria-label="17 plugin surfaces"' in orbit
    assert "Human HIL" in orbit
    for retired in ("SourceBrainLab", "UniversalCommandDeck", "LaneToolchainExplorer"):
        assert retired not in landing

    assert surfaces.count("primaryControl: true") == 6
    assert len(re.findall(r'^    id: "[a-z0-9-]+",$', surfaces, flags=re.MULTILINE)) == 17
    assert "T023_UNIVERSAL_GLASS_PILL_V001" in header
    assert "GlassIconOrb" in header and "GlassIconOrb" in catalog and "GlassIconOrb" in popup
    assert "T023_UNIVERSAL_POPUP_FADE_V001" in popup
    assert 'role="dialog"' in popup and 'aria-modal="true"' in popup
    assert "order: index + 1" in ledger and "order: 80," in ledger
    assert "ACCEPTED IN PV7" in ledger
    assert ".rilFloatingNav .brand" in css
    assert "background: transparent" in css
    assert "--universal-popup-fade-duration: 140ms" in css
    assert '@keyframes universal-popup-fade' in css
    assert "position: fixed" in css and "place-items: center" in css
    assert landing.index('className="section shell releaseHome"') < landing.index('id="delta-ledger"')
    assert site_data.index('{ href: "/", label: "Home" }') < site_data.index('{ href: "/architecture"')
    assert "data.modes.map" in operators
    operator_tabs_rule = re.search(r"\.operatorModeTabs \{([^}]+)\}", css)
    assert operator_tabs_rule is not None
    assert "grid-template-columns: repeat(8,minmax(0,1fr))" in operator_tabs_rule.group(1)
    assert "overflow-x: auto" not in operator_tabs_rule.group(1)
    assert "parallelSources.map" in architecture
    assert 'className="sourceIntakeDepthPill"' in architecture
    assert "SourceLaneIcon" in architecture
    assert 'className="source-lane-orb"' in architecture
    assert architecture.count('className="compactDepthPill"') == 3
    assert "HostCapabilityMatrix" in architecture
    assert architecture.count("<GlassIconOrb") >= 4
    assert ".parallelDiagram::before" in css
    assert ".flowDepthPill" in css and "width: max-content" in css


def test_every_legacy_route_pill_uses_the_glass_orb_schema() -> None:
    routes = [
        APP / "page.tsx",
        APP / "architecture" / "page.tsx",
        APP / "connect" / "page.tsx",
        APP / "operators" / "page.tsx",
        APP / "studio" / "page.tsx",
        APP / "not-found.tsx",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in routes)

    assert 'className="pill dark"' not in combined
    assert 'className="pill blue"' not in combined
    assert 'className="pill gold"' not in combined
    assert combined.count("T023_UNIVERSAL_GLASS_PILL_V001") >= 7
    assert combined.count('className="compactDepthPill"') >= 5
    assert combined.count("<GlassIconOrb") >= 12
    assert combined.count("<OfficialToolIcon") >= 12


def test_prompt_studio_is_full_width_grounded_and_refuses_unknowns() -> None:
    studio = (COMPONENTS / "evidence-prompt-studio.tsx").read_text(encoding="utf-8")
    retrieval = (APP / "_data" / "studio-retrieval.ts").read_text(encoding="utf-8")
    query_route = (APP / "api" / "studio-query" / "route.ts").read_text(encoding="utf-8")
    openrouter = (
        APP / "api" / "studio-query" / "openrouter-general.ts"
    ).read_text(encoding="utf-8")
    adapter_root = ROOT / "plugins" / "evidence-lane-plugin" / "remote_adapter"
    adapter_package = json.loads((adapter_root / "package.json").read_text(encoding="utf-8"))
    adapter_readme = (adapter_root / "README.md").read_text(encoding="utf-8")
    openrouter_test = (
        adapter_root / "scripts" / "test-openrouter-boundary.mjs"
    ).read_text(encoding="utf-8")
    floating = (COMPONENTS / "floating-evidence-studio.tsx").read_text(encoding="utf-8")
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")
    css = (APP / "globals.css").read_text(encoding="utf-8")

    assert "promptStudioRail" not in studio
    assert "promptStudioHeader" in studio and "promptStudioWorkspace" in studio
    assert 'data-grounding="LOCAL_BM25_TFIDF_RRF_PROJECTION_OF_SQLITE_FTS5_CORPUS"' in studio
    assert 'studio-rag-index.json' in retrieval
    assert "rankEvidence" in retrieval and "answerFromEvidence" in retrieval
    assert "bm25" in retrieval and "tfidf" in retrieval and "rrf" in retrieval
    assert "promptStopWords" in retrieval
    assert "!promptStopWords.has(token)" in retrieval
    assert "Supporting business guidance sources" in studio and "Boundary / provider reference" in studio
    assert "promptRetrievalReceipt" in studio and ".promptRetrievalReceipt" in css
    assert ".promptStudio { min-height: 760px; border: 0; background: transparent; }" in css
    assert 'className="promptSuggestionCard"' in studio
    assert ".promptSuggestionCluster .promptSuggestionCard" in css
    assert "FloatingEvidenceStudio" in layout and "floatingStudioPanel" in floating
    assert 'fetch("/api/studio-query"' in floating
    assert 'OPENROUTER_FREE_MODEL = "openrouter/free"' in openrouter
    assert "NO_EXTERNAL_PROJECT_CLAIMS_NO_PAID_MODEL_FALLBACK" in query_route
    assert "EVIDENCE_LANE_GENERAL_AI_ENABLED" in openrouter
    assert "OPENROUTER_API_KEY" in openrouter
    assert not (adapter_root / ".env.example").exists()
    assert "NEXT_PUBLIC_OPENROUTER" not in query_route + openrouter
    assert "does not expose an MCP endpoint" in adapter_readme
    assert "real_provider_calls: 0" in openrouter_test
    assert adapter_package["scripts"]["test:studio-query"] == (
        "node --experimental-strip-types scripts/test-openrouter-boundary.mjs"
    )
    post_body = query_route.split("export async function POST", 1)[1]
    assert post_body.index("answerFromEvidence(question, { pagePath, history })") < post_body.index(
        "isEvidenceLaneQuestion(question)"
    ) < post_body.index("requestFreeGeneralAnswer")
    assert "verifyStudioRetrievalConfidence" in retrieval
    assert 'id: "general-no-hit"' in retrieval
    assert 'id: "project-nonsense-no-hit"' in retrieval
    assert 'id: "active-plan-hit"' in retrieval
    assert "RETRIEVAL_CONFIDENCE_GATE_FAILED" in query_route

    index_builder = (
        ROOT / "plugins" / "evidence-lane-plugin" / "scripts" / "build_prompt_studio_index.py"
    ).read_text(encoding="utf-8")
    assert (
        '"plugins/evidence-lane-plugin/remote_adapter/app/_data/studio-retrieval.ts"'
        in index_builder
    )

    rag_index = json.loads((APP / "_data" / "studio-rag-index.json").read_text(encoding="utf-8"))
    indexed_paths = {source["path"] for source in rag_index["sources"]}
    assert (
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/studio-retrieval.ts"
        not in indexed_paths
    )


def test_evidence_ai_studio_is_a_business_guide_for_the_whole_plugin() -> None:
    studio_page = (APP / "studio" / "page.tsx").read_text(encoding="utf-8")
    studio = (COMPONENTS / "evidence-prompt-studio.tsx").read_text(encoding="utf-8")
    floating = (COMPONENTS / "floating-evidence-studio.tsx").read_text(encoding="utf-8")
    retrieval = (APP / "_data" / "studio-retrieval.ts").read_text(encoding="utf-8")
    guide = (APP / "_data" / "business-guidance.ts").read_text(encoding="utf-8")

    assert "business guide to the whole Evidence Lane plugin" in studio_page
    assert "without turning the main conversation into code discussion" in studio_page
    assert "Business guide to the whole plugin" in studio
    assert "Business answer first" in floating
    assert "businessGuideFor" in retrieval
    assert "raw source extracts" in retrieval
    assert "selected.map((result) => excerpt" not in retrieval
    assert studio.count("<details") >= 1
    assert floating.count("<details") >= 1
    assert len(re.findall(r'^    id: "[a-z-]+",$', guide, flags=re.MULTILINE)) >= 18
    for topic in (
        "Why Evidence Lane exists",
        "Eighteen source lanes",
        "Seventeen clear plugin surfaces",
        "HIL keeps the decision with the human",
        "State Travel resumes unfinished work exactly",
        "Codex source, accepted truth, installed stable, and fallback stay separate",
        "Release and publication happen after acceptance",
    ):
        assert topic in guide


def test_studio_candidate_is_route_aware_bounded_and_artifact_inspectable() -> None:
    route = (APP / "api" / "studio-query" / "route.ts").read_text(encoding="utf-8")
    retrieval = (APP / "_data" / "studio-retrieval.ts").read_text(encoding="utf-8")
    route_context = (APP / "_data" / "studio-route-context.ts").read_text(encoding="utf-8")
    artifacts = (APP / "_data" / "studio-artifact-catalog.ts").read_text(encoding="utf-8")
    full_studio = (COMPONENTS / "evidence-prompt-studio.tsx").read_text(encoding="utf-8")
    floating = (COMPONENTS / "floating-evidence-studio.tsx").read_text(encoding="utf-8")
    artifact_lab = (COMPONENTS / "studio-artifact-lab.tsx").read_text(encoding="utf-8")
    guide = (APP / "_data" / "business-guidance.ts").read_text(encoding="utf-8")

    assert "MAX_HISTORY_TURNS = 8" in route
    assert "boundedPagePath" in route and "boundedHistory" in route
    assert "studioRouteContextFor(options.pagePath)" in retrieval
    assert "historyTurns" in retrieval and "artifactIds" in retrieval
    assert "pagePath: \"/studio\", history" in full_studio
    assert "pagePath: pathname, history" in floating
    assert "result.suggestions" in full_studio and "result.suggestions" in floating
    assert len(re.findall(r'^    id: "[a-z-]+",$', route_context, flags=re.MULTILINE)) >= 9
    assert route_context.count("suggestions: [") >= 9
    for artifact_format in ("SQLite", "Markdown", "JSON", "CSV", "Chart", "Table", "MMD", "DOT"):
        assert f'"{artifact_format}"' in artifacts
        assert f'"{artifact_format}"' in artifact_lab
    assert "WIRED_NOT_CONFIGURED_READ_ONLY_ONLY" in artifacts
    assert "WIRED_NOT_CONFIGURED_OPTIONAL" in artifacts
    assert "Executable action chart" in artifact_lab
    assert "Host capability table" in artifact_lab
    assert "whole project" in guide
    assert "current 3.0.0 pre-HIL source" in guide


def test_native_threejs_motion_remains_without_retired_3d_or_adobe_links() -> None:
    studio_page = (APP / "studio" / "page.tsx").read_text(encoding="utf-8")
    active_site_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in APP.rglob("*")
        if path.is_file()
        and path.name != "studio-rag-index.json"
        and path.suffix in {".ts", ".tsx", ".css"}
    ).casefold()

    assert "https://www.adobe.com/express/" not in studio_page
    assert "Open official Adobe Express" not in studio_page
    assert "adobe.com" not in active_site_text
    assert 'from "meshy' not in active_site_text
    assert "@meshy" not in active_site_text
    assert ".glb" not in active_site_text
    assert "meshy.ai" not in active_site_text


def test_home_story_collapsed_delta_and_canonical_legal_footer_are_explicit() -> None:
    landing = (APP / "page.tsx").read_text(encoding="utf-8")
    ledger = (COMPONENTS / "delta-ledger-explorer.tsx").read_text(encoding="utf-8")
    ledger_data = (APP / "_data" / "delta-ledger.ts").read_text(encoding="utf-8")
    current_plan_data = (APP / "_data" / "current-execution-plan.ts").read_text(
        encoding="utf-8"
    )
    website_current_plan_data = (
        APP / "_data" / "website-current-execution.ts"
    ).read_text(encoding="utf-8")
    website_plan_snapshot = json.loads(
        (APP / "_data" / "website-plan-projection.json").read_text(encoding="utf-8")
    )
    footer = (COMPONENTS / "site-footer.tsx").read_text(encoding="utf-8")
    release_identity = (APP / "_data" / "release-identity.ts").read_text(encoding="utf-8")
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")
    site = (APP / "_data" / "site.ts").read_text(encoding="utf-8")
    contributors = (APP / "_data" / "contributors.ts").read_text(encoding="utf-8")

    assert "Resume from verified project truth - not another re-explanation." in landing
    assert "&mdash;" not in landing
    assert "ReleaseStatus" not in landing
    assert "homeHostTruth" not in landing
    assert "<HostCapabilityMatrix compact />" in landing
    assert "ChatGPT MCP edge fail-closed" not in landing
    assert "No re-explanation tax" in landing
    assert "Parse once, query again" in landing
    assert "Human / AI boundary" in landing
    assert landing.index('className="section shell releaseHome"') < landing.index('id="delta-ledger"')
    assert 'id="current-execution-plan"' not in landing
    assert "One additive ledger. {deltaLedgerBoundary.totalRows} governed public rows." in landing
    assert "websiteCurrentExecution.map" in current_plan_data
    assert "activeRow: websiteCurrentExecutionBoundary.activePublicOrder" in current_plan_data
    assert "activeTaskPosition: websiteCurrentExecutionBoundary.activeTaskPosition" in current_plan_data
    assert "persistentUntil: websiteCurrentExecutionBoundary.persistentUntil" in current_plan_data
    assert (
        'exactlyOneActiveRow: currentExecutionPlan.filter((row) => row.status === "IN_PROGRESS").length === 1'
        in current_plan_data
    )
    assert "physicallyLastStep: websiteCurrentExecutionBoundary.finalHilPublicOrder" in current_plan_data
    assert 'authority: websiteCurrentExecutionBoundary.canonicalAuthority' in current_plan_data
    assert 'import planProjection from "./website-plan-projection.json"' in website_current_plan_data
    assert website_plan_snapshot["canonical_authority"] == "PLAN_LANE"
    assert website_plan_snapshot["task_count"] == len(website_plan_snapshot["rows"])
    assert website_plan_snapshot["row_start"] == 81
    assert website_plan_snapshot["row_end"] == (
        website_plan_snapshot["row_start"]
        + website_plan_snapshot["task_count"]
        - 1
    )
    assert website_plan_snapshot["physically_final_hil_row"] == website_plan_snapshot["row_end"]
    assert [row["row"] for row in website_plan_snapshot["rows"]] == list(
        range(website_plan_snapshot["row_start"], website_plan_snapshot["row_end"] + 1)
    )
    active_rows = [
        row["row"]
        for row in website_plan_snapshot["rows"]
        if row["status"] == "IN_PROGRESS"
    ]
    assert active_rows == [website_plan_snapshot["active_row"]]
    assert website_plan_snapshot["rows"][-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert 'phase: "Current execution"' in ledger_data
    assert "websiteCurrentExecution.map" in ledger_data
    assert "...currentExecution" in ledger_data
    assert "sealedHistoricalDeltaRows" in ledger_data
    assert "liveExecutionRows" in ledger_data
    assert "deltaLedgerBoundary.rowStart" in ledger
    assert "deltaLedgerBoundary.rowEnd" in ledger
    assert "Linked Deltas" in ledger
    assert "websitePlanSnapshotSha256" in ledger
    assert "80 sealed historical Delta rows" in (
        COMPONENTS / "current-execution-plan.tsx"
    ).read_text(encoding="utf-8")
    assert "useState(false)" in ledger
    assert 'aria-expanded={expanded}' in ledger
    assert 'expanded ? "Collapse Delta ledger" : "Open Delta ledger"' in ledger
    assert "{expanded ? (" in ledger

    for heading in ("Policies", "Repository", "Contributors", "Praveen Rathee"):
        assert f"<strong>{heading}</strong>" in footer
    assert "data-release-version={releaseIdentity.version}" in footer
    assert "data-release-commit={releaseIdentity.commit" in footer
    assert "Release <strong>{releaseIdentity.version}</strong>" in footer
    assert 'releaseVersion = "3.0.0"' in release_identity
    assert "VERCEL_GIT_COMMIT_SHA" in release_identity
    assert "NEXT_PUBLIC_EVIDENCE_LANE_RELEASE_SHA" in release_identity
    assert "GITHUB_MARKDOWN_TO_SITE_FOOTERS_DELTA_TABLE_VERCEL_AND_EXISTING_DEVPOST" in release_identity
    assert '"evidence-lane:release-version"' in layout
    assert '"evidence-lane:release-commit"' in layout
    for route in (
        "/license",
        "/copyright",
        "/credits",
        "/commands",
        "/third-party",
        "/helper",
        "/tunnel",
    ):
        assert (APP / route.removeprefix("/") / "page.tsx").is_file()
    for label in (
        "README",
        "Architecture",
        "Skills",
        "Native MCP",
        "Hooks",
        "Commands",
        "License",
        "Copyright",
        "Terms and conditions",
        "Third-party licenses and rights",
        "Security",
        "Human contributors",
        "User Helper Guide",
        "User Tunnel Guide",
    ):
        assert f">{label}</Link>" in footer
    assert footer.count('href="/credits"') == 1
    assert "Naveen Rathee" not in contributors
    for person in ("Kapil Dhawan", "Steven Tock", "Sumit Hooda"):
        assert person in contributors
        assert person not in footer
    for label, url in (
        ("LinkedIn", "https://www.linkedin.com/in/praveen-rathee-8b028030b/"),
        ("Devpost", "https://devpost.com/software/evidence_os"),
        ("YouTube", "https://www.youtube.com/@praveenrathee8675"),
    ):
        assert f'label: "{label}"' in site
        assert url in site
    assert "salary" not in contributors.casefold()
    assert "h1b" not in contributors.casefold()
    assert site.startswith('import { currentProductContract } from "./current-product-contract.ts";')
    assert 'export const publicSiteUrl = "https://evidencelane.org";' in site
    assert "publicMcpUrl" not in site


def test_lanes_hero_orbits_all_eighteen_glass_icons_once_then_stops() -> None:
    lanes_page = (APP / "lanes" / "page.tsx").read_text(encoding="utf-8")
    orbit = (COMPONENTS / "lane-orbit-aside.tsx").read_text(encoding="utf-8")
    css = (APP / "globals.css").read_text(encoding="utf-8")

    assert "LaneOrbitAside" in lanes_page
    assert len(re.findall(r'^  \["[a-z_]+", ".+"\],$', orbit, flags=re.MULTILINE)) == 18
    assert "GlassIconOrb" in orbit and "SourceLaneIcon" in orbit
    assert "laneOrbitWheel" in orbit and "laneOrbitGlyph" in orbit
    assert "lane-orbit-clockwise-once" in css and "rotate(360deg)" in css
    assert "lane-orbit-counter-once" in css and "rotate(-360deg)" in css
    assert ".laneOrbitWheel,.laneOrbitGlyph { animation: none !important; }" in css


def test_public_plugin_metadata_and_third_party_rights_are_canonical() -> None:
    metadata = json.loads(
        (ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json").read_text(
            encoding="utf-8"
        )
    )
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")
    connect = (APP / "connect" / "page.tsx").read_text(encoding="utf-8")
    license_page = (APP / "license" / "page.tsx").read_text(encoding="utf-8")
    copyright_page = (APP / "copyright" / "page.tsx").read_text(encoding="utf-8")
    repository_license = (ROOT / "LICENSE.md").read_text(encoding="utf-8")
    repository_copyright = (ROOT / "docs" / "COPYRIGHT.md").read_text(
        encoding="utf-8"
    )

    assert metadata["homepage"] == "https://evidencelane.org"
    assert "mcp_endpoint" not in metadata
    assert metadata["interactive_ui"]["mime_type"] == "text/html;profile=mcp-app"
    assert metadata["interactive_ui"]["render_tools"] == [
        "render_runtime_panel",
        "render_project_panel",
    ]
    assert "siteName: \"Evidence Lane\"" in layout
    assert "local native MCP server" in connect
    assert "ChatGPT" in connect and "Deferred" in connect
    boundary = (
        "Third-party software, services, models, assets, and trademarks remain "
        "governed"
    )
    for surface in (
        license_page,
        copyright_page,
        repository_license,
        repository_copyright,
    ):
        assert boundary in surface
        assert "Evidence Lane grants no rights over them" in surface


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


def test_connect_endpoint_cards_are_linked_readable_and_truthful() -> None:
    connect = (APP / "connect" / "page.tsx").read_text(encoding="utf-8")
    styles = (APP / "globals.css").read_text(encoding="utf-8")

    assert '<a href={publicSiteUrl} className="endpointCard endpointCardReady">' in connect
    assert (
        '<a href={repositoryUrl} className="endpointCard endpointCardProtocol">'
        in connect
    )
    assert "website explains the product; it does not execute the lifecycle" in connect
    assert "prewarmed separately" not in connect
    assert "a support tunnel is eligible only for a measured host-tool gap" in connect
    assert ".endpointCard strong { color: #ffffff;" in styles
    assert ".endpointCard small { color: #d6e7f3;" in styles
    for selector in (
        ".endpointCardReady",
        ".endpointCardProtocol",
    ):
        assert selector in styles
