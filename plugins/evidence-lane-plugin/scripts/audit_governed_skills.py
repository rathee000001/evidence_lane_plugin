#!/usr/bin/env python3
"""Audit governed Evidence Lane skill packages and generated registry identity."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PLUGIN_ROOT / "skills"
SHARED_BOUNDARY = (
    "../evidence-lane-code-lifecycle/references/shared-boundaries.md"
)
FORBIDDEN_PATTERNS = {
    "full-lifecycle-eager-load": r"evidence-lane-code-lifecycle/SKILL\.md",
    "retired-command-layer": r"(?:command[- ]layer|MCP/SDK/skill/command)",
    "stale-action-count": r"twenty[- ]seven reads",
    "fixed-primary-ordinal": r"(?:seventh primary|ninth MCP|sixth MCP)",
    "engulf-double-count": r"(?:eighteen|18).*plus Project Engulf",
    "mixed-host-memory": r"ChatGPT/Codex",
}


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        value = raw.strip()
        if value.startswith('"') and value.endswith('"'):
            value = str(json.loads(value))
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        values[key.strip()] = value
    return values


def _openai_metadata(text: str) -> tuple[str, str]:
    short = re.search(r'^\s*short_description:\s*["\']?([^\r\n"\']+)', text, re.MULTILINE)
    prompt = re.search(r'^\s*default_prompt:\s*["\']?([^\r\n]+)', text, re.MULTILINE)
    return (
        short.group(1).strip() if short else "",
        prompt.group(1).strip().strip('"\'') if prompt else "",
    )


def _local_references(skill_dir: Path, text: str) -> list[tuple[str, Path]]:
    refs: list[tuple[str, Path]] = []
    for match in re.finditer(r'`((?:\.\./|references/|scripts/|assets/)[^`]+)`', text):
        relative = match.group(1)
        if any(token in relative for token in ("<", ">", "*", "?", "|")):
            continue
        refs.append((relative, (skill_dir / Path(relative)).resolve()))
    return refs


def audit(plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any]:
    skills_root = plugin_root / "skills"
    registry_path = plugin_root / "schemas" / "skills" / "skill-registry.v1.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_names = {str(item["name"]) for item in registry.get("skills", [])}
    directories = sorted(
        path for path in skills_root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()
    )
    directory_names = {path.name for path in directories}
    issues: list[dict[str, str]] = []

    for missing in sorted(registry_names - directory_names):
        issues.append({"skill": missing, "code": "missing-directory", "detail": missing})
    for extra in sorted(directory_names - registry_names):
        issues.append({"skill": extra, "code": "unregistered-directory", "detail": extra})

    for skill_dir in directories:
        name = skill_dir.name
        skill_path = skill_dir / "SKILL.md"
        agent_path = skill_dir / "agents" / "openai.yaml"
        text = skill_path.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        if meta.get("name") != name:
            issues.append({"skill": name, "code": "name-mismatch", "detail": meta.get("name", "")})
        description = meta.get("description", "")
        if not description or len(description) > 1024:
            issues.append({"skill": name, "code": "bad-description", "detail": str(len(description))})
        if not agent_path.is_file():
            issues.append({"skill": name, "code": "missing-openai-yaml", "detail": agent_path.as_posix()})
        else:
            short, prompt = _openai_metadata(agent_path.read_text(encoding="utf-8"))
            if not 25 <= len(short) <= 64:
                issues.append({"skill": name, "code": "bad-short-description", "detail": str(len(short))})
            if f"${name}" not in prompt:
                issues.append({"skill": name, "code": "default-prompt-missing-skill", "detail": prompt})
        if name != "evidence-lane-code-lifecycle" and SHARED_BOUNDARY not in text:
            issues.append({"skill": name, "code": "missing-shared-boundary", "detail": SHARED_BOUNDARY})
        for code, pattern in FORBIDDEN_PATTERNS.items():
            if re.search(pattern, text, re.IGNORECASE):
                issues.append({"skill": name, "code": code, "detail": pattern})
        for relative, resolved in _local_references(skill_dir, text):
            if not resolved.exists():
                issues.append({"skill": name, "code": "missing-reference", "detail": relative})

    return {
        "schema": "evidence-lane.governed-skill-audit.v1",
        "status": "PASS" if not issues else "FAIL",
        "skill_count": len(directories),
        "registry_skill_count": len(registry_names),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    args = parser.parse_args()
    result = audit(args.plugin_root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
