from __future__ import annotations

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from evidence_lane_plugin.build import build_identity
from evidence_lane_plugin.engine import Empty, Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import dispatch_authenticated
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.registry import ActionContext, ActionSpec, Contract
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.upgrade import prepare_upgrade
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

HASH = WorkerOperation("hash_text", "evidence_lane_plugin.worker_tasks", "hash_text")
CONTEXT = ActionContext("test-owner", None, frozenset({"read"}))


class Completed(Contract):
    done: bool


def wait_for_file(arguments):
    deadline = time.monotonic() + 5
    while not Path(arguments["gate"]).exists():
        if time.monotonic() > deadline:
            raise TimeoutError()
        time.sleep(0.01)
    return {"done": True}


def test_drain_retains_accepted_request_and_its_later_worker_submission(tmp_path):
    entered, release = threading.Event(), threading.Event()
    pool = WorkerPool((HASH,), workers=1)
    engine = Engine(tmp_path, worker_pool=pool)

    def operation():
        # Exercise the owner admission lifetime directly. Adding a synthetic
        # public action would invalidate the exact locked Flash registry before
        # this lifecycle test could enter its intended boundary.
        with engine.admit():
            entered.set()
            assert release.wait(5)
            result = engine.workers.submit("hash_text", {"text": "accepted-before-drain"}).result(5)
            return Completed(done=result["status"] == "ok")

    engine.start()
    with ThreadPoolExecutor(1) as requests:
        future = requests.submit(operation)
        try:
            assert entered.wait(5)
            result = engine.quiesce(timeout=0)
            assert not result.quiescent and result.stage == "requests" and result.accepted_requests == 1
            rejected = dispatch_authenticated(engine, ActionRequest(action="project_status"), CONTEXT)
            assert rejected.error.code == "ENGINE_DRAINING"
            assert not engine.stop(timeout=0)
            assert engine.phase == "draining"
            health = dispatch_authenticated(engine, ActionRequest(action="engine_health"), CONTEXT)
            assert health.status == "ok" and health.result["accepted_requests"] == 1
        finally:
            release.set()
        assert future.result(5).done
    assert engine.quiesce(timeout=5).quiescent
    assert pool.status()["submitted"] == pool.status()["succeeded_operations"] == 1
    engine.stop()


def test_uncompiled_action_never_enters_a_shutdown_fixture_handler(tmp_path):
    entered = threading.Event()
    engine = Engine(tmp_path)
    def operation(context, arguments):
        entered.set()
        return Completed(done=True)
    engine.registry.register(ActionSpec('delayed', 'Synthetic uncompiled action.', Empty, Completed,
        operation, project_required=False))
    with engine:
        response = dispatch_authenticated(engine, ActionRequest(action='delayed'), CONTEXT)
        assert response.error.code == 'SESSION_FLASH_REGISTRY_CHANGED'
        assert not entered.is_set()


def test_quiescence_includes_completion_callbacks_without_holding_engine_lock(tmp_path):
    callback_entered, callback_release, callback_finished = (threading.Event() for _ in range(3))
    operation = WorkerOperation("wait_for_file", __name__, "wait_for_file")
    engine = Engine(tmp_path, worker_pool=WorkerPool((operation,), workers=1))
    engine.start()

    def callback(future):
        callback_entered.set()
        assert callback_release.wait(5)
        assert engine.health().phase == "draining"
        callback_finished.set()

    gate = tmp_path / "worker-gate"
    future = engine.workers.submit("wait_for_file", {"gate": str(gate)})
    future.add_done_callback(callback)
    gate.touch()
    try:
        assert callback_entered.wait(5)
        # The pool's internal finished callback ran first: outstanding=0 alone
        # must not claim the user completion callback has released its writer.
        assert engine.workers.status()["outstanding"] == 0
        result = engine.quiesce(timeout=0.05)
        assert not result.quiescent and result.stage == "workers"
        assert not engine.workers.status()["shutdown_complete"]
    finally:
        callback_release.set()
    assert engine.quiesce(timeout=5).quiescent and callback_finished.is_set()
    assert engine.workers.status()["shutdown_complete"]
    engine.stop()


def test_replacement_requires_closed_old_pool_and_fresh_ready_processes(tmp_path):
    old = WorkerPool((HASH,), workers=1)
    replacement = WorkerPool((HASH,), workers=2)
    with Engine(tmp_path, worker_pool=old) as engine:
        old_generation = old.generation
        old.submit("hash_text", {"text": "before"}).result(5)
        receipt = engine.replace_workers(replacement, timeout=5)
        assert receipt.quiescent and receipt.worker_generation == old_generation
        assert old.status()["shutdown_complete"] and old.status()["outstanding"] == 0
        assert engine.phase == "running" and engine.workers is replacement
        assert replacement.generation != old_generation
        assert len(replacement.status()["initialized_workers"]) == 2
        assert replacement.submit("hash_text", {"text": "after"}).result(5)["status"] == "ok"
        with pytest.raises(LaneError):
            old.start()


def test_provider_drain_timeout_preserves_ownership_and_defers_replacement(tmp_path):
    entered, release = threading.Event(), threading.Event()
    old, replacement = WorkerPool((HASH,), workers=1), WorkerPool((HASH,), workers=1)
    with Engine(tmp_path, worker_pool=old) as engine:
        providers = engine.provider_workers
        def preparation():
            entered.set()
            release.wait(10)
        thread = threading.Thread(target=preparation)
        providers._entries[('fixture', 'device')] = {
            'state': 'prewarming', 'group': None, 'process': None, 'threads': [thread]}
        thread.start()
        try:
            assert entered.wait(2)
            assert old.submit('hash_text', {'text': 'accepted'}).result(5)['status'] == 'ok'
            # Finish the lane executor first so the short wait targets provider teardown.
            assert old.close(timeout=5)
            pending = engine.replace_workers(replacement, timeout=0)
            assert not pending.quiescent and pending.stage == 'provider_workers'
            assert pending.worker_generation == old.generation
            assert engine.phase == 'draining' and engine.provider_workers is providers
            assert engine.workers is old and replacement.status()['state'] == 'created'
            competing = Engine(tmp_path)
            with pytest.raises(LaneError):
                competing.start()
        finally:
            release.set()
            thread.join(5)
        done = engine.replace_workers(replacement, timeout=5)
        assert done.quiescent and engine.workers is replacement
        assert engine.provider_workers is not providers
        assert engine.capabilities.provider_workers is engine.provider_workers
        assert providers._entries[('fixture', 'device')]['state'] == 'stopped'
        assert replacement.submit('hash_text', {'text': 'after'}).result(5)['status'] == 'ok'


def test_failed_replacement_stays_draining_without_automatic_retry(tmp_path, monkeypatch):
    old, new = WorkerPool((HASH,), workers=1), WorkerPool((HASH,), workers=1)
    attempts = []

    def fail(**kwargs):
        attempts.append(1)
        raise LaneError("WORKER_START_TIMEOUT", "No startup acknowledgement.")

    monkeypatch.setattr(new, "start", fail)
    with Engine(tmp_path, worker_pool=old) as engine:
        with pytest.raises(LaneError) as error:
            engine.replace_workers(new, timeout=2)
        assert error.value.code == "WORKER_START_TIMEOUT"
        assert engine.phase == "draining" and engine.workers is old
        assert old.status()["shutdown_complete"] and attempts == [1]


def test_incompatible_replacement_leaves_running_pool_untouched(tmp_path):
    with Engine(tmp_path, worker_pool=WorkerPool((HASH,), workers=1)) as engine:
        incompatible = WorkerPool((), workers=1)
        with pytest.raises(LaneError) as error:
            engine.replace_workers(incompatible)
        assert error.value.code == "INCOMPATIBLE_WORKER_REPLACEMENT"
        assert engine.phase == "running" and engine.workers.status()["accepting"]


def test_owner_controls_cannot_wait_from_inside_an_admitted_action(tmp_path):
    with Engine(tmp_path) as engine:
        with engine.admit(), pytest.raises(LaneError) as error:
            engine.stop()
        assert error.value.code == "LIFECYCLE_FROM_ACTION"
        assert engine.phase == "running" and engine.health().accepted_requests == 0


def test_local_connection_and_studio_commands_are_rejected_during_drain(tmp_path):
    with Engine(tmp_path / "runtime") as engine, LocalEndpoint(engine) as endpoint:
        native = LocalTransport(engine.root)
        token = endpoint.studio.issue_ticket()
        _, session = endpoint.studio.exchange(token)
        engine.begin_drain()
        with pytest.raises(LaneError):
            LocalTransport(engine.root)
        with pytest.raises(LaneError) as error:
            endpoint.studio.command("project", {"state_root": str(tmp_path / "never-created"), "create": True}, session)
        assert error.value.code == "ENGINE_DRAINING" and not (tmp_path / "never-created").exists()
        assert endpoint.studio.snapshot()["engine"]["phase"] == "draining"
        assert native.send(ActionRequest(action="engine_health")).status == "ok"
        native.close()


def committed_package(tmp_path):
    source = tmp_path / "package"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "evidence-lane-plugin", "version": "4.0.2"}))
    (source / "src").mkdir()
    (source / "src/main.py").write_text("x = 1\n")
    for arguments in (("init", "--quiet"), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                       "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")):
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    return source


def test_upgrade_requires_committed_exact_target_then_stops_and_expires_sessions(tmp_path):
    source = committed_package(tmp_path)
    identity = build_identity(source)
    engine = Engine(tmp_path / "runtime")
    engine.start()
    from evidence_lane_plugin.connections import ConnectRequest
    token, _session = engine.clients.connect(ConnectRequest())
    with pytest.raises(LaneError) as error:
        prepare_upgrade(engine, source, expected_build_id="0" * 64)
    assert error.value.code == "UPGRADE_TARGET_CHANGED" and engine.phase == "running"
    receipt = prepare_upgrade(engine, source, expected_build_id=identity["build_id"])
    assert receipt["status"] == "engine_stopped" and not receipt["installation_performed"]
    assert engine.wait(0) and engine.phase == "stopped"
    assert json.loads((engine.root / "upgrade-preparation.json").read_text()) == receipt
    with Engine(engine.root) as successor, pytest.raises(LaneError):
        successor.clients.authenticate(token)


def test_dirty_upgrade_target_rejected_before_drain(tmp_path):
    source = committed_package(tmp_path)
    identity = build_identity(source)
    (source / "src/main.py").write_text("x = 2\n")
    with Engine(tmp_path / "runtime") as engine, pytest.raises(LaneError) as error:
        prepare_upgrade(engine, source, expected_build_id=identity["build_id"])
    assert error.value.code == "UNCOMMITTED_PACKAGE"


def test_unconfirmed_stop_never_records_completed_upgrade_preparation(tmp_path, monkeypatch):
    source = committed_package(tmp_path)
    identity = build_identity(source)
    with Engine(tmp_path / 'runtime') as engine:
        with monkeypatch.context() as patch:
            patch.setattr(engine, 'stop', lambda **kwargs: False)
            result = prepare_upgrade(engine, source, expected_build_id=identity['build_id'])
        assert result['status'] == 'deferred' and not result['engine_stop_confirmed']
        assert engine.phase == 'draining' and not engine.wait(0)
        assert not result['installation_performed']
        assert json.loads((engine.root / 'upgrade-preparation.json').read_text()) == result
