from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "github-pages"
REPOSITORY = "rathee000001/evidence_lane_plugin"
CURRENT_ROUTE_RECEIPT = "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.json"
CURRENT_ROUTE_RECEIPT_MARKDOWN = "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.md"
CURRENT_ROUTE_RECEIPT_SCHEMA = "evidence-lane.current-route-file-refresh-receipt.v2"

PAGES = (
    ("index", "README", "README.md"),
    ("architecture", "Architecture", "ARCHITECTURE.md"),
    ("canon", "Canon", "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md"),
    ("ai-learning", "AI Learning", "docs/AI_LEARNING.md"),
    ("memory", "Memory", "docs/MEMORY.md"),
    (
        "project-universe",
        "Project Universe",
        "docs/PROJECT_UNIVERSE.md",
    ),
    (
        "pv-storage",
        "PV Storage",
        "docs/PROJECT_PV_CONTENT_ADDRESSED_STORAGE.md",
    ),
    (
        "host-matrix",
        "Host Matrix",
        "docs/HOST_STORAGE_ENV_MODE_CONTINUITY.md",
    ),
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

PUBLIC_DOC_REFRESH_MARKER = "evidence-lane-public-docs-full-refresh: 3.0.0"
STALE_PUBLIC_DOC_PATTERNS = (
    re.compile(
        r"\b(?:package defines|registers) eight (?:hook )?events\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bhidden eighth event\b", re.IGNORECASE),
    re.compile(r"\bseven visible controls\b", re.IGNORECASE),
    re.compile(r"\b(?:83|87) canonical (?:actions|tools)\b", re.IGNORECASE),
    re.compile(r"\bBoot or Resume\b"),
    re.compile(r"\bpv_state_travel_(?:prepare|resume)\b"),
)


def _assert_current_public_document(path: str, text: str) -> None:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if PUBLIC_DOC_REFRESH_MARKER not in first_line:
        raise RuntimeError(f"Current public-document refresh marker missing: {path}")
    for pattern in STALE_PUBLIC_DOC_PATTERNS:
        if pattern.search(text):
            raise RuntimeError(
                f"Stale public documentation route/contract remains in {path}: "
                f"{pattern.pattern}"
            )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _current_commit_refresh_paths() -> set[str]:
    revision = os.environ.get("GITHUB_SHA", "").strip()
    command = (
        [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            revision,
        ]
        if re.fullmatch(r"[0-9a-fA-F]{40}", revision)
        else ["git", "diff", "--cached", "--name-only", "HEAD", "--"]
    )
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    }


def _git_text(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _revision_tracked_paths(revision: str) -> set[str]:
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    command = (
        ["git", "ls-tree", "-r", "-z", "--name-only", revision]
        if re.fullmatch(r"[0-9a-fA-F]{40}", github_sha)
        else ["git", "ls-files", "-z"]
    )
    raw = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return {
        value.decode("utf-8").replace("\\", "/") for value in raw.split(b"\0") if value
    }


def _verify_current_route_refresh_receipt(
    *,
    revision: str,
    changed_paths: set[str],
    page_sources: set[str],
) -> set[str]:
    required_receipts = {CURRENT_ROUTE_RECEIPT, CURRENT_ROUTE_RECEIPT_MARKDOWN}
    if not required_receipts.issubset(changed_paths):
        missing = sorted(required_receipts - changed_paths)
        raise RuntimeError(
            "The current commit did not refresh both tracked-tree receipt files: "
            + ", ".join(missing)
        )
    receipt = json.loads((ROOT / CURRENT_ROUTE_RECEIPT).read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != CURRENT_ROUTE_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
    ):
        raise RuntimeError(
            "The current-route tracked-tree receipt is not authoritative."
        )
    entries = receipt.get("entries")
    if not isinstance(entries, list) or not all(
        isinstance(row, dict) for row in entries
    ):
        raise RuntimeError(
            "The current-route tracked-tree receipt entries are malformed."
        )
    rows = {str(row.get("path") or ""): row for row in entries}
    if "" in rows or len(rows) != len(entries):
        raise RuntimeError(
            "The current-route tracked-tree receipt has duplicate paths."
        )
    tracked_paths = _revision_tracked_paths(revision)
    if set(rows) != tracked_paths:
        raise RuntimeError(
            "The current-route receipt path set does not equal the exact Git tree."
        )
    expected_path_set_sha256 = (
        hashlib.sha256("\n".join(sorted(tracked_paths)).encode("utf-8"))
        .hexdigest()
        .upper()
    )
    summary = receipt.get("summary")
    if (
        not isinstance(summary, dict)
        or summary.get("path_count") != len(tracked_paths)
        or summary.get("entry_path_set_sha256") != expected_path_set_sha256
        or summary.get("tracked_path_set_equality") is not True
    ):
        raise RuntimeError("The current-route tracked-tree summary does not match Git.")
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    expected_base = (
        _git_text("rev-parse", f"{revision}^")
        if re.fullmatch(r"[0-9a-fA-F]{40}", github_sha)
        else _git_text("rev-parse", "HEAD")
    )
    if str(receipt.get("base_commit") or "").lower() != expected_base.lower():
        raise RuntimeError(
            "The current-route receipt is bound to the wrong base commit."
        )
    for path in required_receipts:
        if rows[path].get("disposition") != "RECEIPT_SELF_BOUND_BY_FINAL_GIT_TREE":
            raise RuntimeError(f"Receipt self-reference disposition is invalid: {path}")
    for path in page_sources:
        row = rows[path]
        if (
            row.get("route_refresh_verified") is not True
            or row.get("disposition") not in {"CHANGED", "UNCHANGED_VERIFIED"}
            or row.get("sha256") != _sha256(ROOT / path)
        ):
            raise RuntimeError(f"Current-route document fingerprint mismatch: {path}")
    return set(page_sources)


def _exact_revision() -> str:
    revision = os.environ.get("GITHUB_SHA", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        return revision.lower()
    return "agent/evi-v300-systemwide-release-hil-v3.0.0"


def _rewrite_relative_markdown_links(
    text: str,
    *,
    source_path: Path,
    revision: str,
) -> str:
    def repository_url(target: str) -> str | None:
        path_text, separator, fragment = target.partition("#")
        resolved = (source_path.parent / path_text).resolve()
        try:
            relative = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            return None
        encoded_revision = quote(revision, safe="")
        url = (
            f"https://github.com/{REPOSITORY}/blob/"
            f"{encoded_revision}/{quote(relative, safe='/')}"
        )
        if separator:
            url = f"{url}#{fragment}"
        return url

    markdown_pattern = re.compile(
        r"\]\((?!https?://|mailto:|#|/)([^)]+\.md(?:#[^)]*)?)\)"
    )

    def replace_markdown(match: re.Match[str]) -> str:
        url = repository_url(match.group(1))
        if url is None:
            return match.group(0)
        return f"]({url})"

    html_pattern = re.compile(
        r"(?P<prefix>(?:href|src)\s*=\s*[\"'])"
        r"(?P<target>(?!https?://|mailto:|#|/)[^\"']+\.md(?:#[^\"']*)?)"
        r"(?P<suffix>[\"'])",
        re.IGNORECASE,
    )

    def replace_html(match: re.Match[str]) -> str:
        url = repository_url(match.group("target"))
        if url is None:
            return match.group(0)
        return f"{match.group('prefix')}{url}{match.group('suffix')}"

    rewritten = markdown_pattern.sub(replace_markdown, text)
    rewritten = html_pattern.sub(replace_html, rewritten)
    return rewritten.replace(
        "docs/assets/evidence-lane-full-logo.png",
        "/evidence_lane_plugin/assets/evidence-lane-full-logo.png",
    ).replace(
        "plugins/evidence-lane-plugin/assets/evidence-lane-icon.png",
        "/evidence_lane_plugin/assets/evidence-lane-icon.png",
    )


def build(
    output: Path,
    *,
    require_current_commit_refresh: bool = False,
) -> dict[str, object]:
    output = output.resolve()
    if (
        output == ROOT
        or ROOT in output.parents
        and output.name != ".github-pages-build"
    ):
        raise ValueError("Refusing to replace a non-generated repository path.")
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(TEMPLATE, output)

    revision = _exact_revision()
    page_sources = {source for _, _, source in PAGES}
    changed_paths = (
        _current_commit_refresh_paths() if require_current_commit_refresh else set()
    )
    refreshed_paths = set(changed_paths)
    if require_current_commit_refresh:
        refreshed_paths.update(
            _verify_current_route_refresh_receipt(
                revision=revision,
                changed_paths=changed_paths,
                page_sources=page_sources,
            )
        )
    missing_refresh = (
        sorted(page_sources - refreshed_paths) if require_current_commit_refresh else []
    )
    if missing_refresh:
        raise RuntimeError(
            "Every Git commit must refresh all GitHub documentation/Page sources; "
            f"missing: {', '.join(missing_refresh)}"
        )
    generated: list[dict[str, str]] = []
    for slug, title, relative_source in PAGES:
        source = ROOT / relative_source
        if not source.is_file():
            raise FileNotFoundError(relative_source)
        source_text = source.read_text(encoding="utf-8")
        _assert_current_public_document(relative_source, source_text)
        body = _rewrite_relative_markdown_links(
            source_text,
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
            f'title: "{title}"\n'
            f"permalink: {permalink}\n"
            f'source_path: "{relative_source}"\n'
            f'source_url: "{source_url}"\n'
            "---\n\n"
            "{% raw %}\n"
            f"{body.rstrip()}\n"
            "{% endraw %}\n"
        )
        (output / f"{slug}.md").write_text(page, encoding="utf-8")
        generated.append(
            {
                "slug": slug,
                "title": title,
                "source": relative_source,
                "source_sha256": _sha256(source),
            }
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
        "documentation_refresh": {
            "status": "PASS",
            "current_release": "3.0.0",
            "scope": "ALL_GITHUB_DOCUMENTS_AND_ALL_GITHUB_PAGES_EVERY_COMMIT",
            "page_count": len(generated),
            "current_commit_refresh_required": require_current_commit_refresh,
            "current_commit_refresh_verified": (
                require_current_commit_refresh and not missing_refresh
            ),
            "tracked_tree_receipt": CURRENT_ROUTE_RECEIPT,
            "tracked_tree_receipt_schema": CURRENT_ROUTE_RECEIPT_SCHEMA,
            "unchanged_sources_verified_by_fingerprint": True,
            "source_paths": sorted(page_sources),
            "source_set_sha256": hashlib.sha256(
                json.dumps(
                    generated,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            .hexdigest()
            .upper(),
        },
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
    parser.add_argument(
        "--require-current-commit-refresh",
        action="store_true",
        help=(
            "Fail unless the current commit refreshes the complete tracked-tree "
            "fingerprint receipt and every GitHub documentation/Page source is "
            "content-address verified."
        ),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.output,
                require_current_commit_refresh=args.require_current_commit_refresh,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
