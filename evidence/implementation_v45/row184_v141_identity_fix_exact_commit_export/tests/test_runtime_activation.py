from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .conftest import boot_local


def _run_session_start(root: Path, store: Path, host_session_id: str) -> str:
    hook = (
        root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "session_start.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(store)
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {"source": "startup", "session_id": host_session_id}
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]


def test_exit_boot_detaches_flash_context_and_later_boot_reattaches(service) -> None:
    root = Path(__file__).resolve().parents[1]
    first = boot_local(service)
    session_id = first["session"]["session_id"]
    active_context = _run_session_start(
        root,
        service.store.root,
        "host-session-test",
    )
    assert "# Evidence Lane universal session flash" in active_context
    assert '"state":"ACTIVE"' in active_context

    closed = service.sessions.close(
        "book-faires",
        session_id,
        reason="USER_REQUESTED_EVI_EXIT_BOOT",
    )
    assert closed["runtime_activation"]["state"] == "DETACHED"
    detached_context = _run_session_start(
        root,
        service.store.root,
        "host-session-after-exit",
    )
    assert "EVIDENCE_LANE_RUNTIME=DETACHED" in detached_context
    assert "# Evidence Lane universal session flash" not in detached_context
    assert '"state":"DETACHED"' in detached_context

    second = boot_local(service)
    assert second["session"]["session_id"] != session_id
    assert second["session_flash"]["flash_action"] == "REUSED"
    assert second["runtime_activation"]["state"] == "ACTIVE"
    reattached_context = _run_session_start(
        root,
        service.store.root,
        "host-session-test",
    )
    assert "# Evidence Lane universal session flash" in reattached_context


def test_prompt_hook_fails_closed_when_bound_runtime_is_detached(
    service,
    source_repository: Path,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.runtime_activation.detach(
        project_id="book-faires",
        session_id=session_id,
        reason="TEST_ONLY_DETACH_WITH_SESSION_RECORD_RETAINED",
    )
    root = Path(__file__).resolve().parents[1]
    hook = (
        root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "prompt_submit.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "host-session-test",
                "turn_id": "turn-detached",
                "cwd": str(source_repository),
                "prompt": "This must not be captured while detached.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    payload = json.loads(completed.stdout)
    indexed = json.loads(
        payload["hookSpecificOutput"]["additionalContext"].removeprefix(
            "EVIDENCE_LANE_PROMPT_ENTRY="
        )
    )
    assert indexed == {
        "state": "NOT_INDEXED",
        "reason": "EVIDENCE_LANE_RUNTIME_DETACHED",
        "raw_prompt_stored": False,
    }
    assert not (service.store.root / "prompt-index").exists()
