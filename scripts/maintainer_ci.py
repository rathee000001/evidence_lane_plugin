"""Repository-only, change-selected CI. It does not configure user projects."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
POLICY = ".github/maintainer-ci.v4.json"
SHA = re.compile(r"[a-fA-F0-9]{40}")
EXCLUDED = {".git", ".work", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_policy(root=ROOT):
    policy = json.loads((root / POLICY).read_bytes())
    if policy.get("schema") != "evidence-lane.maintainer-ci.v4" or policy.get("project_defaults") is not False:
        raise ValueError("Invalid repository CI policy")
    if not policy["profiles"] or "ci" not in policy["profiles"]:
        raise ValueError("CI must include its own selector and runner tests")
    seen = set()
    for profile, values in policy["profiles"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", profile) or not values["tests"]:
            raise ValueError("Profiles must have safe names and explicit tests")
        for selector in values["tests"]:
            if not re.fullmatch(r"tests/test_[a-z0-9_]+\.py", selector):
                raise ValueError("Only literal repository test files are accepted")
            if selector in seen or not (root / selector).is_file():
                raise ValueError(f"Duplicate or missing CI test: {selector}")
            seen.add(selector)
    for rule in policy["rules"]:
        if not set(rule["profiles"]) <= set(policy["profiles"]):
            raise ValueError("Change rule refers to an unknown CI profile")
    with (root / "pyproject.toml").open("rb") as stream:
        defaults = tomllib.load(stream)["tool"]["pytest"]["ini_options"]["testpaths"]
    if set(defaults) != seen or len(defaults) != len(seen):
        raise ValueError("Default pytest collection must match the explicit CI test set")
    return policy


def select_profiles(policy, changed_paths, *, full=False):
    profiles = set(policy["profiles"]) if full else {"ci"}
    reasons = []
    for value in changed_paths:
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
                or any(ord(char) < 32 for char in value)):
            raise ValueError("Changed paths must be safe repository-relative paths")
        matches = [rule for rule in policy["rules"] if any(path.full_match(pattern) for pattern in rule["paths"])]
        selected = {profile for rule in matches for profile in rule["profiles"]}
        if not matches:
            selected = set(policy["profiles"])
        profiles.update(selected)
        reasons.append({"path": value, "profiles": sorted(selected),
                        "basis": "declared_rule" if matches else "conservative_fallback"})
    return {"profiles": sorted(profiles), "path_decisions": reasons,
            "full_regression_selected": full, "complete_product_qualification": False}


def git(root, *arguments):
    # Every ref below is validated as a complete commit id before this call.
    completed = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *arguments],
        check=True, capture_output=True, timeout=30)
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise ValueError("Git comparison exceeds the bounded CI change list")
    return completed.stdout


def event_changes(root, event_name, event, expected_head=None):
    head = git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    if not SHA.fullmatch(head) or (expected_head is not None and expected_head != head):
        raise ValueError("The checkout does not match the expected CI commit")
    base = None
    compare_head = head
    if event_name == "pull_request":
        base = event.get("pull_request", {}).get("base", {}).get("sha")
        compare_head = event.get("pull_request", {}).get("head", {}).get("sha")
    elif event_name == "push":
        base = event.get("before")
    if event_name == "workflow_dispatch":
        return head, [], True, "manual_full_regression"
    if not isinstance(base, str) or not SHA.fullmatch(base) or set(base) == {"0"}:
        return head, [], True, "comparison_unavailable_full_regression"
    if not isinstance(compare_head, str) or not SHA.fullmatch(compare_head):
        raise ValueError("Invalid comparison commit")
    try:
        if event_name == "pull_request":
            base = git(root, "merge-base", base, compare_head).decode("ascii").strip()
            if not SHA.fullmatch(base):
                raise ValueError("Invalid merge base")
        raw = git(root, "diff", "--name-only", "--no-renames", "-z", base, compare_head, "--")
        paths = [value.decode("utf-8", errors="strict") for value in raw.split(b"\0") if value]
    except (subprocess.SubprocessError, UnicodeError):
        return head, [], True, "comparison_unavailable_full_regression"
    return head, sorted(set(paths)), False, "git_committed_diff"


def source_snapshot(root):
    """Hash declared executable/test/config trees; this is not an atomic filesystem claim."""
    paths = set()
    for directory in ("plugins/evidence-lane-plugin", "tests", "scripts", ".github", "docs", "contracts"):
        for parent, dirs, files in os.walk(root / directory, followlinks=False):
            dirs[:] = [name for name in dirs if name not in EXCLUDED]
            for name in files:
                if not name.endswith((".pyc", ".pyo")):
                    paths.add(Path(parent) / name)
    paths.update(root / name for name in ("pyproject.toml", "requirements.in", "requirements-dev.in",
        "requirements.lock.txt", "requirements-dev.lock.txt"))
    if len(paths) > 12000:
        raise ValueError("CI source snapshot exceeds its file bound")
    result, total = {}, 0
    for path in sorted(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("CI source snapshot encountered an external or symbolic source")
        size = path.stat().st_size
        total += size
        if size > 128 * 1024 * 1024 or total > 2 * 1024 * 1024 * 1024:
            raise ValueError("CI source snapshot exceeds its byte bound")
        result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def summarize_junit(path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Missing or oversized CI test result")
    document = ElementTree.parse(path).getroot()
    suites = [document] if document.tag == "testsuite" else document.findall("testsuite")
    return {key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run_profile(root, policy, profile):
    if profile not in policy["profiles"]:
        raise ValueError("Unknown CI profile; legacy PV/formula profiles are retired")
    if sys.version_info[:2] != (3, 14) or sys.platform != "win32":
        raise ValueError("This locked CI runtime requires Windows and CPython 3.14")
    sys.path.insert(0, str(root / "plugins/evidence-lane-plugin/src"))
    from evidence_lane_plugin.bounded_io import run_owned_bounded_process
    from evidence_lane_plugin.errors import EvidenceLaneError
    from evidence_lane_plugin.redaction import redact_text

    output = root / ".work/ci" / profile
    if output.exists():
        raise ValueError("CI output already exists; use a fresh checkout or preserve and relocate the previous run")
    output.mkdir(parents=True)
    before = source_snapshot(root)
    receipt = {"schema": "evidence-lane.maintainer-ci-result.v4", "profile": profile,
        "status": "running", "started_at": datetime.now(UTC).isoformat(),
        "tests": policy["profiles"][profile]["tests"], "source_hashes": before,
        "source_observation_atomic": False, "policy_sha256": digest((root / POLICY).read_bytes()),
        "python_executable_sha256": digest(Path(sys.executable).read_bytes()),
        "python_version": sys.version, "project_defaults": False,
        "installed_native_verified": False, "complete_product_qualification": False,
        "coverage": policy["coverage"]}
    write_json(output / "receipt.json", receipt)
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
        "COMSPEC", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}}
    environment.update(PYTHONPATH=str(root / "plugins/evidence-lane-plugin/src"),
        PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CI="true",
        GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", PYTHONHASHSEED="0")
    command = [sys.executable, "-B", "-m", "pytest", *receipt["tests"], "-q", "--tb=short",
        "-p", "no:cacheprovider", f"--basetemp={output / 'tmp'}", f"--junitxml={output / 'junit.xml'}"]
    started = time.monotonic()
    try:
        result = run_owned_bounded_process(command, cwd=root, env=environment,
            timeout_seconds=policy["profiles"][profile]["timeout_seconds"],
            max_stdout_bytes=2 * 1024 * 1024, max_stderr_bytes=2 * 1024 * 1024)
        for name, data in (("stdout", result.stdout), ("stderr", result.stderr)):
            (output / f"{name}.txt").write_text(redact_text(data.decode("utf-8", errors="replace")), encoding="utf-8")
        counts = summarize_junit(output / "junit.xml")
        receipt.update(returncode=result.returncode, counts=counts, descendants_joined=True)
        clean = source_snapshot(root) == before
        receipt.update(source_hashes_unchanged=clean)
        receipt["status"] = "passed" if (result.returncode == 0 and counts["tests"] > 0
            and not any(counts[key] for key in ("errors", "failures", "skipped")) and clean) else "failed"
    except (EvidenceLaneError, OSError, ValueError, ElementTree.ParseError) as error:
        receipt.update(status="blocked", error_type=type(error).__name__,
            error_code=getattr(error, "code", "CI_EXECUTION_ERROR"))
    receipt.update(elapsed_seconds=round(time.monotonic() - started, 6), finished_at=datetime.now(UTC).isoformat())
    write_json(output / "receipt.json", receipt)
    print(json.dumps({key: receipt.get(key) for key in ("profile", "status", "counts", "error_code", "source_hashes_unchanged")}))
    return 0 if receipt["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--all", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("--profile", required=True)
    args = parser.parse_args()
    policy = read_policy()
    if args.command == "run":
        return run_profile(ROOT, policy, args.profile)
    if args.all:
        head, paths, full, basis = None, [], True, "explicit_full_regression"
    else:
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        if event_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Oversized GitHub event")
        head, paths, full, basis = event_changes(ROOT, os.environ["GITHUB_EVENT_NAME"],
            json.loads(event_path.read_bytes()), os.environ.get("GITHUB_SHA"))
    selection = select_profiles(policy, paths, full=full)
    selection.update(commit=head, basis=basis, policy_sha256=digest((ROOT / POLICY).read_bytes()),
        coverage=policy["coverage"], project_defaults=False)
    write_json(ROOT / ".work/ci/selection.json", selection)
    matrix = json.dumps({"profile": selection["profiles"]}, separators=(",", ":"))
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={matrix}\n")
    print(json.dumps({"matrix": json.loads(matrix), "basis": basis, "commit": head,
        "complete_product_qualification": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
