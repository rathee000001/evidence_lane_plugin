from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_installed_story_is_plain_language_and_current() -> None:
    manifest = _json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    interface = manifest["interface"]

    assert manifest["description"].startswith(
        "Evidence Lane keeps long Codex projects grounded"
    )
    assert interface["shortDescription"] == (
        "Keep long Codex projects grounded, resumable, and human-approved."
    )
    assert "Evidence Lane helps people carry substantial Codex projects" in (
        interface["longDescription"]
    )
    assert "Evidence Lane helps Codex stay oriented through a long project" in readme

    current_facts = (
        "91 native actions",
        "30 read",
        "61 write",
        "26 governed skills",
        "no separate command layer",
        "11 lifecycle event",
        "44 ordered handler",
        "18 project-sector lanes",
    )
    combined = f'{interface["longDescription"]}\n{readme}'
    for fact in current_facts:
        assert fact in combined

    for stale in (
        "test candidate",
        "non-executing tombstone",
        "83 native actions",
        "88 native actions",
        "94-requirement",
        "17 packaged skills",
        "20 governed skills",
    ):
        assert stale.casefold() not in combined.casefold()


def test_installed_prompts_explain_current_work_and_human_decisions() -> None:
    manifest = _json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    prompts = manifest["interface"]["defaultPrompt"]
    assert len(prompts) == 3
    assert all(prompt == prompt.strip() and prompt for prompt in prompts)
    assert any("current Plan row" in prompt for prompt in prompts)
    assert any("governed HIL" in prompt for prompt in prompts)
    assert any("next human decision" in prompt for prompt in prompts)


def test_github_app_story_is_the_delivery_arm_of_the_same_product() -> None:
    contract = _json(PLUGIN_ROOT / "sdk" / "host" / "github-app-connection.v1.json")
    description = contract["description"]
    assert description.startswith("Evidence Lane keeps long Codex projects connected")
    assert "exact reviewed branch" in description
    assert "person decides whether it should merge" in description
    assert "test candidate" not in description.casefold()
    assert "tombstone" not in description.casefold()
