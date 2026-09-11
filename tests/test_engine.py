from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.sdk import ActionRequest, dispatch


def test_singleton_lock_is_exclusive_and_released(tmp_path):
    with Engine(tmp_path) as engine:
        with pytest.raises(LaneError) as error:
            Engine(tmp_path).start()
        assert error.value.code == "RUNTIME_IN_USE"
        assert engine.health().phase == "running"
        assert engine.health().previous_shutdown == "first_start"
    with Engine(tmp_path) as successor:
        assert successor.health().previous_shutdown == "clean"
        assert successor.instance_id != engine.instance_id


def test_crashed_process_releases_os_lock_and_is_reported_unclean(tmp_path):
    source = Path(__file__).parents[1] / "plugins/evidence-lane-plugin/src"
    environment = dict(os.environ, PYTHONPATH=str(source))
    code = (
        "import os,sys; from pathlib import Path; "
        "from evidence_lane_plugin.engine import Engine; "
        "e=Engine(Path(sys.argv[1])); e.start(); os._exit(17)"
    )
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=environment,
                            check=False, timeout=15)
    assert result.returncode == 17
    with Engine(tmp_path) as engine:
        assert engine.previous_shutdown == "unclean"


def test_bad_lifecycle_record_is_not_overwritten(tmp_path):
    state = tmp_path / "engine-state.json"
    state.write_text('{"format_version":999,"phase":"running"}', encoding="utf-8")
    before = state.read_bytes()
    with pytest.raises(LaneError) as error:
        Engine(tmp_path).start()
    assert error.value.code == "RUNTIME_STATE_INVALID"
    assert state.read_bytes() == before
    state.unlink()
    with Engine(tmp_path):
        pass


def test_lifecycle_health_does_not_attest_native_task_identity(tmp_path):
    with Engine(tmp_path) as engine:
        response = dispatch(engine.registry, ActionRequest(action="engine_health"),
                            ActionContext("connection", None, frozenset({"read"})))
        assert response.result["native_task_attestation"] == "not_provided"
        engine.begin_drain()
        assert engine.health().phase == "draining"
    assert engine.wait(0)
    state = json.loads((tmp_path / "engine-state.json").read_text())
    assert state["phase"] == "stopped"
    with pytest.raises(LaneError):
        engine.start()
