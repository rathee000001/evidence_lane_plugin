from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_github_docs.py"
TOOL_MATRIX = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "toolchains"
    / "tool-requirement-matrix.v1.json"
)


def _module():
    spec = importlib.util.spec_from_file_location("github_docs_generation", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_readme_requires_current_linked_docs_and_is_reviewed_after_commit(
    tmp_path: Path,
) -> None:
    output = tmp_path / "README.md"
    receipt = tmp_path / "README.review.json"
    review = _module().generate_page(
        page="README.md",
        output=output,
        review_receipt=receipt,
    )
    parsed = json.loads(receipt.read_text(encoding="utf-8"))
    assert parsed == review
    assert parsed["status"] == "SOURCE_VERIFIED_REVIEW_AFTER_GIT_COMMIT"
    assert parsed["linked_docs_current"] is True
    assert parsed["linked_page_count"] == 20
    assert parsed["user_review_timing"] == "AFTER_GITHUB_COMMIT"
    assert parsed["staging_authorized"] is False
    assert parsed["commit_authorized"] is False
    text = output.read_text(encoding="utf-8")
    assert "91" in text and "30 read-only" in text and "61 write-capable" in text
    tool_count = len(json.loads(TOOL_MATRIX.read_text(encoding="utf-8"))["requirements"])
    assert "26" in text and str(tool_count) in text
    assert "25 " + "current skills" not in text
    assert "six controls" not in text.casefold()
    assert "No separate user helper ships" not in text
    assert "Five product failures Evidence Lane addresses" in text
    assert "fragmented project" in text
    assert "Hardware accelerators are a third inventory" in text
    assert "ChromaDB" not in text


def test_technical_pages_are_source_derived_for_post_commit_review(
    tmp_path: Path,
) -> None:
    for page in ("ARCHITECTURE.md", "docs/MCP.md", "docs/TOOLS.md"):
        output = tmp_path / page
        receipt = tmp_path / (page.replace("/", "__") + ".json")
        review = _module().generate_page(
            page=page,
            output=output,
            review_receipt=receipt,
        )
        assert review["status"] == "SOURCE_VERIFIED_REVIEW_AFTER_GIT_COMMIT"
        assert review["user_review_timing"] == "AFTER_GITHUB_COMMIT"
        assert review["staging_authorized"] is False
        assert output.stat().st_size > 500


def test_repository_markdown_matches_current_renderers() -> None:
    module = _module()
    values = module._source_snapshot()
    for page in sorted(module.TECHNICAL_PAGES):
        expected = module.render_technical_page(
            page,
            values=values,
            plugin_root=module.PLUGIN_ROOT,
        )
        assert (ROOT / page).read_text(encoding="utf-8") == expected
    readme, review = module.render_root_readme()
    assert (ROOT / "README.md").read_text(encoding="utf-8") == readme
    assert review["linked_docs_current"] is True


def test_every_generated_technical_page_has_source_bound_depth_and_workflow() -> None:
    module = _module()
    values = module._source_snapshot()
    for page in sorted(module.TECHNICAL_PAGES):
        text = module.render_technical_page(
            page,
            values=values,
            plugin_root=module.PLUGIN_ROOT,
        )
        assert "```mermaid" in text, page
        assert "fail closed" in text.casefold(), page
        assert len(text.encode("utf-8")) > 2_000, page
        if page != "docs/UPSTREAM_REFERENCE_PROVENANCE.md":
            assert "## Contract and readback" in text, page
            assert "## Canonical source owners" in text, page


def test_maintained_markdown_local_links_resolve() -> None:
    module = _module()
    pages = [ROOT / "README.md", *(ROOT / page for page in module.TECHNICAL_PAGES)]
    failures: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
            clean = target.strip().strip("<>").split("#", 1)[0]
            if not clean or clean.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / clean).resolve().exists():
                failures.append(f"{page.relative_to(ROOT).as_posix()} -> {target}")
    assert failures == []


def test_pages_workflow_fetches_receipt_feature_and_baseline_commits() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "evidence-lane-github-pages.yml"
    ).read_text(encoding="utf-8")
    assert "fetch-depth: 3" in workflow
    assert "Receipt HEAD -> feature commit -> baseline commit" in workflow
    governed = (
        ROOT / ".github" / "workflows" / "evidence-lane-ci.yml"
    ).read_text(encoding="utf-8")
    assert "fetch-depth: 3" in governed
    assert "Receipt HEAD -> feature commit -> baseline commit" in governed
