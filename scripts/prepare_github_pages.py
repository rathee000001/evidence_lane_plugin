from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "github-pages"
REPOSITORY = "rathee000001/evidence_lane_plugin"

PAGES = (
    ("index", "README", "README.md"),
    ("architecture", "Architecture", "ARCHITECTURE.md"),
    ("canon", "Canon", "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md"),
    ("ai-learning", "AI Learning", "docs/AI_LEARNING.md"),
    ("memory", "Memory", "docs/MEMORY.md"),
    ("host-matrix", "Host Matrix", "docs/HOST_AND_STORAGE_MATRIX.md"),
    ("skills", "Skills", "docs/SKILLS.md"),
    ("mcp", "MCP", "docs/MCP.md"),
    ("tools", "Tools", "docs/TOOLS.md"),
    ("commands", "Commands", "docs/COMMANDS.md"),
    ("hooks", "Hooks", "docs/HOOKS.md"),
    ("plan", "Plan and Changes", "docs/PLAN_AND_CHANGE_DISPLAY.md"),
    ("lanes", "Source Intake and Lanes", "docs/SOURCE_INTAKE_AND_LANES.md"),
    ("helper", "Helper install", "docs/USER_HELPER_GUIDE.md"),
    ("tunnel", "Tunnel guide", "docs/USER_TUNNEL_GUIDE.md"),
    ("installation", "Installation", "docs/INSTALLATION_AND_RECOVERY.md"),
    ("git-ci", "Git and CI", "docs/GIT_AND_CI_CD.md"),
    ("lifecycle", "Lifecycle and HIL", "docs/LIFECYCLE_AND_HIL.md"),
    ("release", "Release", "docs/RELEASE_AND_COMPATIBILITY.md"),
    (
        "provenance",
        "Provenance",
        "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    ),
    (
        "contributors",
        "Contributors",
        "docs/CREDITS_AND_CONTRIBUTIONS.md",
    ),
    ("license", "License", "LICENSE.md"),
    ("copyright", "Copyright", "docs/COPYRIGHT.md"),
    ("third-party", "Third-party licenses", "docs/THIRD_PARTY_LICENSES.md"),
    ("terms", "Terms", "docs/TERMS_AND_CONDITIONS.md"),
    ("security", "Security", "SECURITY.md"),
    ("repository-map", "Repository Map", "docs/REPOSITORY_MAP.md"),
)


def _exact_revision() -> str:
    revision = os.environ.get("GITHUB_SHA", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        return revision.lower()
    return "agent/evi-v220-systemwide-release-hil-v2.2.0"


def _rewrite_relative_markdown_links(
    text: str,
    *,
    source_path: Path,
    revision: str,
) -> str:
    pattern = re.compile(r"\]\((?!https?://|mailto:|#|/)([^)]+\.md(?:#[^)]*)?)\)")

    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        path_text, separator, fragment = target.partition("#")
        resolved = (source_path.parent / path_text).resolve()
        try:
            relative = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            return match.group(0)
        encoded_revision = quote(revision, safe="")
        url = (
            f"https://github.com/{REPOSITORY}/blob/"
            f"{encoded_revision}/{quote(relative, safe='/')}"
        )
        if separator:
            url = f"{url}#{fragment}"
        return f"]({url})"

    rewritten = pattern.sub(replace, text)
    return rewritten.replace(
        "docs/assets/evidence-lane-full-logo.png",
        "/evidence_lane_plugin/assets/evidence-lane-full-logo.png",
    ).replace(
        "plugins/evidence-lane-plugin/assets/evidence-lane-icon.png",
        "/evidence_lane_plugin/assets/evidence-lane-icon.png",
    )


def build(output: Path) -> dict[str, object]:
    output = output.resolve()
    if output == ROOT or ROOT in output.parents and output.name != ".github-pages-build":
        raise ValueError("Refusing to replace a non-generated repository path.")
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(TEMPLATE, output)

    revision = _exact_revision()
    generated: list[dict[str, str]] = []
    for slug, title, relative_source in PAGES:
        source = ROOT / relative_source
        if not source.is_file():
            raise FileNotFoundError(relative_source)
        body = _rewrite_relative_markdown_links(
            source.read_text(encoding="utf-8"),
            source_path=source,
            revision=revision,
        )
        source_url = (
            f"https://github.com/{REPOSITORY}/blob/"
            f"{quote(revision, safe='')}/{quote(relative_source, safe='/')}"
        )
        permalink = "/" if slug == "index" else f"/{slug}/"
        page = (
            "---\n"
            "layout: default\n"
            f"title: \"{title}\"\n"
            f"permalink: {permalink}\n"
            f"source_path: \"{relative_source}\"\n"
            f"source_url: \"{source_url}\"\n"
            "---\n\n"
            "{% raw %}\n"
            f"{body.rstrip()}\n"
            "{% endraw %}\n"
        )
        (output / f"{slug}.md").write_text(page, encoding="utf-8")
        generated.append(
            {"slug": slug, "title": title, "source": relative_source}
        )

    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    shutil.copy2(
        ROOT / "docs/assets/evidence-lane-full-logo.png",
        assets / "evidence-lane-full-logo.png",
    )
    shutil.copy2(
        ROOT / "plugins/evidence-lane-plugin/assets/evidence-lane-icon.png",
        assets / "evidence-lane-icon.png",
    )
    receipt = {
        "schema": "evidence-lane.github-pages-source-projection.v1",
        "status": "PASS",
        "repository": REPOSITORY,
        "revision": revision,
        "page_count": len(generated),
        "pages": generated,
    }
    (output / "projection-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".github-pages-build",
    )
    args = parser.parse_args()
    print(json.dumps(build(args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
