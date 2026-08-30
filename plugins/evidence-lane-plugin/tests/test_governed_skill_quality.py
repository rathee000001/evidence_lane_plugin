from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = PLUGIN_ROOT / "scripts" / "audit_governed_skills.py"
GENERATOR_PATH = PLUGIN_ROOT / "scripts" / "generate_package_surface_projections.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_governed_skill_packages_pass_native_quality_contract() -> None:
    audit_module = _load(AUDIT_PATH, "audit_governed_skills")
    result = audit_module.audit(PLUGIN_ROOT)
    assert result["status"] == "PASS", result["issues"]
    assert result["skill_count"] == result["registry_skill_count"]


def test_quoted_skill_descriptions_are_normalized_for_generated_json() -> None:
    generator_module = _load(GENERATOR_PATH, "generate_package_surface_projections")
    rows = {row["name"]: row for row in generator_module._skill_rows()}
    assert rows["evi-learning"]["description"].startswith("Govern the project-isolated")
    assert not rows["evi-learning"]["description"].startswith('"')
    assert not rows["evi-refresh"]["description"].startswith('"')


def test_architecture_outputs_use_the_atomic_retry_writer() -> None:
    source = GENERATOR_PATH.read_text(encoding="utf-8")
    for filename in (
        "UNIVERSAL_PLUGIN_ARCHITECTURE.mmd",
        "UNIVERSAL_PLUGIN_ARCHITECTURE.dot",
        "MEMORY_AUTHORITY_ARCHITECTURE.mmd",
        "MEMORY_AUTHORITY_ARCHITECTURE.dot",
    ):
        assert f'(toolchains_root / "{filename}").write_text' not in source
        assert f'toolchains_root / "{filename}",' in source
