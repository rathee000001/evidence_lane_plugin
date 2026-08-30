from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "evidence-lane-plugin" / "skills"

EXPECTED = {
    "evi-formula": ("formula_engine_run", "not Mode"),
    "evi-brain-scaling": ("brain_scaling_select", "never trains"),
    "evi-project-recipe": ("project_recipe_compile", "State Travel resumes"),
    "evi-toolchain": ("ai_toolchain_route", "never run every"),
    "evi-bigger-universe": ("bigger_universe_register", "hash-only"),
}


def test_first_class_skills_are_distinct_thin_workflow_owners() -> None:
    all_skills = sorted(SKILLS.glob("*/SKILL.md"))
    registry = json.loads(
        (SKILLS / "skill-surface-registry.v1.json").read_text(encoding="utf-8")
    )
    assert len(all_skills) == registry["skill_count"]
    assert {path.parent.name for path in all_skills} == {
        row["name"] for row in registry["skills"]
    }
    for name, required in EXPECTED.items():
        path = SKILLS / name / "SKILL.md"
        agent = SKILLS / name / "agents" / "openai.yaml"
        assert path.is_file()
        assert agent.is_file()
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert "TODO" not in text
        for marker in required:
            assert marker in text
        metadata = agent.read_text(encoding="utf-8")
        assert f"${name}" in metadata
        assert "allow_implicit_invocation: true" in metadata


def test_obsolete_competing_skill_is_purged() -> None:
    assert not (SKILLS / "evi-change-storage-connector").exists()
    assert not (SKILLS / "evi-prepare-state-travel").exists()
    assert not (SKILLS / "evi-resume-state-travel").exists()
