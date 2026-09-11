from __future__ import annotations

import time
from concurrent.futures import CancelledError
from concurrent.futures.process import BrokenProcessPool

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

HASH = WorkerOperation("hash_text", "evidence_lane_plugin.worker_tasks", "hash_text", ("hashlib",))


def slow_operation(arguments):
    time.sleep(arguments["seconds"])
    return {"done": True}


def test_every_process_is_prewarmed_and_reused():
    with WorkerPool((HASH,), workers=2) as pool:
        ready = pool.status()["initialized_workers"]
        assert len({item["pid"] for item in ready}) == 2
        result = pool.submit("hash_text", {"text": "hello"}).result(timeout=5)
        assert result["status"] == "ok"
        assert result["result"]["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result["worker_pid"] in {item["pid"] for item in ready}
        assert pool.status()["submitted"] == 1


def test_queue_is_bounded_and_drain_rejects_new_work():
    operation = WorkerOperation("slow", __name__, "slow_operation")
    with WorkerPool((operation,), workers=1, queue_capacity=0) as pool:
        future = pool.submit("slow", {"seconds": 0.4})
        with pytest.raises(LaneError) as error:
            pool.submit("slow", {"seconds": 0.1})
        assert error.value.code == "WORKER_QUEUE_FULL"
        assert future.result(timeout=5)["status"] == "ok"
        pool.begin_drain()
        with pytest.raises(LaneError) as error:
            pool.submit("slow", {"seconds": 0.1})
        assert error.value.code == "WORKERS_DRAINING"


def test_missing_dependency_never_submits_an_operation():
    missing = WorkerOperation("missing", "evidence_lane_plugin.worker_tasks", "hash_text",
                              ("evidence_lane_unavailable_package_xyz",))
    with WorkerPool((missing,), workers=1) as pool, pytest.raises(LaneError) as error:
        pool.submit("missing", {"text": "unused"})
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    assert pool.status()["submitted"] == 0


def test_broken_worker_is_reported_without_replaying_the_job():
    operation = WorkerOperation("slow", __name__, "slow_operation")
    pool = WorkerPool((operation,), workers=1)
    pool.start()
    future = pool.submit("slow", {"seconds": 10})
    pool.close(force=True)
    # A queued operation can be cancelled before execution; a running one loses its worker.
    with pytest.raises((BrokenProcessPool, RuntimeError, CancelledError)):
        future.result(timeout=5)
    assert pool.status()["submitted"] == 1


def test_worker_errors_do_not_echo_private_inputs():
    with WorkerPool((HASH,), workers=1) as pool:
        response = pool.submit("hash_text", {"private-secret": "not valid input"}).result(timeout=5)
    assert response == {"status": "error", "code": "WORKER_OPERATION_FAILED"}


def declared_error(arguments):
    raise LaneError(arguments['code'], 'Private user content must not be returned')


def test_only_declared_codes_cross_the_worker_boundary_and_messages_stay_private():
    operation = WorkerOperation('declared_error', __name__, 'declared_error', error_codes=('FIXTURE_BUDGET',))
    with WorkerPool((operation,), workers=1) as pool:
        known = pool.submit('declared_error', {'code': 'FIXTURE_BUDGET'}).result(timeout=5)
        unknown = pool.submit('declared_error', {'code': 'PRIVATE_VALUE'}).result(timeout=5)
    assert known == {'status': 'error', 'code': 'FIXTURE_BUDGET'}
    assert unknown == {'status': 'error', 'code': 'WORKER_OPERATION_FAILED'}


def test_larger_input_budget_is_explicit_per_operation_and_default_stays_bounded():
    large = WorkerOperation('large_hash', 'evidence_lane_plugin.worker_tasks', 'hash_text',
                            max_input_bytes=2_097_152)
    value = {'text': 'a' * 1_100_000}
    with WorkerPool((HASH, large), workers=1) as pool:
        with pytest.raises(LaneError) as error:
            pool.submit('hash_text', value)
        assert error.value.code == 'WORKER_INPUT_TOO_LARGE' and pool.status()['submitted'] == 0
        response = pool.submit('large_hash', value).result(timeout=10)
        assert response['status'] == 'ok' and response['result']['bytes'] == 1_100_000
        with pytest.raises(LaneError) as error:
            pool.submit('large_hash', {'text': 'b' * 2_097_152})
        assert error.value.code == 'WORKER_INPUT_TOO_LARGE' and pool.status()['submitted'] == 1


def test_worker_contract_caps_remain_finite():
    with pytest.raises(LaneError) as error:
        WorkerPool((WorkerOperation('oversized', __name__, 'slow_operation', max_input_bytes=33_554_433),))
    assert error.value.code == 'INVALID_WORKER_INPUT_BUDGET'
    with pytest.raises(LaneError) as error:
        WorkerPool((WorkerOperation('oversized', __name__, 'slow_operation', max_output_bytes=67_108_865),))
    assert error.value.code == 'INVALID_WORKER_OUTPUT_BUDGET'
