from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "evidence-lane-plugin"
SCAN_ROOTS = (
    PLUGIN_ROOT / "src",
    PLUGIN_ROOT / "scripts",
    PLUGIN_ROOT / "hooks",
    ROOT / "tests",
    PLUGIN_ROOT / "tests",
)
LOCAL_TOP_LEVEL_MODULES = {
    "behavior_handoff",
    "build_one_shot_dummy_poc",
    "build_real_git_poc",
    "build_release_candidate_rehearsal",
    "event_isolation",
    "evidence_lane_plugin",
    "generate_public_schema_catalog",
    "install_codex_stable",
    "optional_event_observer",
    "prepare_github_pages",
    "runtime_contract",
    "scripts",
    "subhook_pipeline",
}
DECLARATION_ALIASES = {
    "bs4": {"beautifulsoup4"},
    "cv2": {"opencv-python-headless"},
    "docling": {"docling"},
    "docx": {"python-docx"},
    "fastmcp": {"fastmcp"},
    "fitz": {"pymupdf"},
    "git": {"gitpython"},
    "github": {"pygithub"},
    "jwt": {"pyjwt"},
    "pil": {"pillow"},
    "pptx": {"python-pptx"},
    "sentence_transformers": {"sentence-transformers"},
    "sklearn": {"scikit-learn"},
    "yaml": {"pyyaml"},
}
GUARDED_OPTIONAL_IMPORTS = {
    # lane_engine checks module availability first and uses declared rapidocr as
    # the current primary. This legacy provider is never a required route.
    "rapidocr_onnxruntime",
}


def _normalized_requirement(value: str) -> str:
    return re.split(r"[<>=!~;\[]", value, maxsplit=1)[0].strip().casefold().replace(
        "_", "-"
    )


def _declared_distributions() -> set[str]:
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text("utf-8"))[
        "project"
    ]
    requirements = list(project["dependencies"])
    requirements.extend(project.get("optional-dependencies", {}).get("dev", []))
    return {_normalized_requirement(str(value)) for value in requirements}


def _absolute_imports() -> dict[str, set[str]]:
    imports: dict[str, set[str]] = {}
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".", 1)[0] for alias in node.names]
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module
                ):
                    names = [node.module.split(".", 1)[0]]
                for name in names:
                    imports.setdefault(name, set()).add(
                        path.relative_to(ROOT).as_posix()
                    )
    return imports


def test_every_direct_third_party_import_has_an_explicit_declaration() -> None:
    declared = _declared_distributions()
    package_map = metadata.packages_distributions()
    missing: dict[str, dict[str, object]] = {}
    unresolved: dict[str, list[str]] = {}
    for module, paths in sorted(_absolute_imports().items()):
        if (
            module in sys.stdlib_module_names
            or module in LOCAL_TOP_LEVEL_MODULES
            or module in GUARDED_OPTIONAL_IMPORTS
        ):
            continue
        distributions = {
            value.casefold().replace("_", "-")
            for value in package_map.get(module, [])
        }
        distributions.update(DECLARATION_ALIASES.get(module.casefold(), set()))
        if not distributions:
            unresolved[module] = sorted(paths)
        elif not distributions & declared:
            missing[module] = {
                "eligible_distributions": sorted(distributions),
                "imported_by": sorted(paths),
            }
    assert not unresolved, f"Unresolved absolute imports: {unresolved}"
    assert not missing, f"Direct imports relying on transitive packages: {missing}"
