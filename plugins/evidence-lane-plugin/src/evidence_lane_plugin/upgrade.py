"""Owner-only preparation for a committed package upgrade; no installer effects."""

from __future__ import annotations

from pathlib import Path

from .build import build_identity
from .engine import Engine
from .errors import LaneError
from .projects import atomic_json
from .storage import now, reject_links


def prepare_upgrade(engine: Engine, source: Path, *, expected_build_id: str, timeout: float = 30) -> dict:
    """Bind a real committed target, finish accepted work, and stop this engine.

    The installer must subsequently acquire the runtime OS lock and verify the
    target again before changing installed files. This receipt is evidence of
    this engine's shutdown, not continuing ownership or installation proof.
    """
    with engine.lifecycle_control():
        target = build_identity(source)
        if target["build_id"] != expected_build_id:
            raise LaneError("UPGRADE_TARGET_CHANGED", "The committed upgrade target no longer matches its selected build.")
        drained = engine.quiesce(timeout=timeout)
        result = {"schema_version": 1, "engine_instance": engine.instance_id,
                  "target_build_id": target["build_id"], "target_commit": target["git_head"],
                  "quiescence": drained.model_dump(), "observed_at": now(),
                  "status": "deferred", "engine_stop_confirmed": False, "installation_performed": False,
                  "installer_must_reacquire_runtime_lock": True, "installer_must_reverify_target": True}
        if drained.quiescent:
            if build_identity(source)["build_id"] != target["build_id"]:
                raise LaneError("UPGRADE_TARGET_CHANGED", "The upgrade source changed while work was draining.")
            result['engine_stop_confirmed'] = engine.stop(timeout=0)
            if result['engine_stop_confirmed']:
                result["status"] = "engine_stopped"
        path = engine.root / "upgrade-preparation.json"
        reject_links(path, engine.root)
        atomic_json(path, result)
        return result
