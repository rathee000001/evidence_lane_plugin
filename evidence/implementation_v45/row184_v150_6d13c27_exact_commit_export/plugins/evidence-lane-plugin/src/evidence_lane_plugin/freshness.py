"""Compare immutable PV source identity with the live governed source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .git_adapter import identity_json, inspect_repository
from .store import ProjectStore


def evaluate_freshness(
    store: ProjectStore,
    project_id: str,
    package: str | Path,
) -> dict[str, Any]:
    """Return an explicit current-source state without changing either source."""

    package_root = Path(package).resolve()
    bound = json.loads(
        (package_root / "project_identity.json").read_text(encoding="utf-8")
    )["repository"]
    try:
        config = store.config(project_id)
        live_identity = inspect_repository(config.repository_path)
        live = identity_json(live_identity, config.repository_path)
    # Freshness is an advisory read boundary: an unexpected live-source adapter
    # failure must downgrade truth instead of hiding immutable PV evidence.
    except Exception as exc:  # noqa: BLE001
        return {
            "state": "UNVERIFIED",
            "reason": "The live source could not be verified.",
            "error_type": type(exc).__name__,
            "bound_commit": bound.get("commit_sha"),
            "bound_tree": bound.get("tree_sha"),
        }

    identity_fields = ("owner", "name", "repository_url")
    mismatch = {
        field: {"bound": bound.get(field), "live": live.get(field)}
        for field in identity_fields
        if bound.get(field) != live.get(field)
    }
    if mismatch:
        state = "MISMATCH"
        reason = "The live source is not the repository bound to this PV."
    elif bound.get("commit_sha") != live.get("commit_sha") or bound.get(
        "tree_sha"
    ) != live.get("tree_sha"):
        state = "STALE"
        reason = "The live source has moved past or away from this PV."
    elif bound.get("worktree_sha256") != live.get("worktree_sha256"):
        state = "DIRTY_WORKING_TREE"
        reason = "HEAD matches the PV, but live working-tree bytes differ."
    else:
        state = "FRESH"
        reason = "The live source exactly matches the PV source identity."
    return {
        "state": state,
        "reason": reason,
        "bound_commit": bound.get("commit_sha"),
        "bound_tree": bound.get("tree_sha"),
        "bound_worktree_sha256": bound.get("worktree_sha256"),
        "live_commit": live.get("commit_sha"),
        "live_tree": live.get("tree_sha"),
        "live_worktree_sha256": live.get("worktree_sha256"),
        "live_clean": live.get("is_clean"),
        "identity_mismatch": mismatch,
    }


def result_status(base_status: str, freshness: dict[str, Any]) -> str:
    """Downgrade model-visible read status when live truth is not exact."""

    state = freshness.get("state")
    if state == "FRESH":
        return base_status
    if state in {"STALE", "DIRTY_WORKING_TREE", "UNVERIFIED"}:
        return "STALE"
    if state == "MISMATCH":
        return "MISMATCH"
    return base_status
