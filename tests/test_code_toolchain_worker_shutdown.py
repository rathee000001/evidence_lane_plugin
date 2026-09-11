from __future__ import annotations

from typing import Any

from evidence_lane_plugin import code_toolchain


class _FakeProcess:
    def __init__(self, *, survives_terminate: bool = False) -> None:
        self.alive = True
        self.survives_terminate = survives_terminate
        self.join_timeouts: list[float] = []
        self.terminate_count = 0
        self.kill_count = 0

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(float(timeout or 0.0))

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminate_count += 1
        if not self.survives_terminate:
            self.alive = False

    def kill(self) -> None:
        self.kill_count += 1
        self.alive = False


class _FakeConnection:
    def __init__(self, *, poll_result: bool = False) -> None:
        self.sent: list[Any] = []
        self.closed = False
        self.poll_result = poll_result

    def send(self, value: Any) -> None:
        self.sent.append(value)

    def close(self) -> None:
        self.closed = True

    def poll(self, timeout: float | None = None) -> bool:
        del timeout
        return self.poll_result


def test_shutdown_terminates_native_worker_without_unbounded_wait(monkeypatch) -> None:
    process = _FakeProcess()
    request_connection = _FakeConnection()
    response_connection = _FakeConnection()
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_WORKER_PROCESS", process)
    monkeypatch.setattr(
        code_toolchain, "_TREE_SITTER_REQUEST_CONNECTION", request_connection
    )
    monkeypatch.setattr(
        code_toolchain, "_TREE_SITTER_RESPONSE_CONNECTION", response_connection
    )
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_SHUTDOWN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_KILL_GRACE_SECONDS", 0.0)

    code_toolchain.shutdown_tree_sitter_runtime()

    assert code_toolchain._TREE_SITTER_WORKER_PROCESS is None
    assert code_toolchain._TREE_SITTER_REQUEST_CONNECTION is None
    assert code_toolchain._TREE_SITTER_RESPONSE_CONNECTION is None
    assert request_connection.sent == [None]
    assert request_connection.closed is True
    assert response_connection.closed is True
    assert process.terminate_count == 1
    assert process.kill_count == 0
    assert process.is_alive() is False


def test_shutdown_kills_worker_that_ignores_terminate(monkeypatch) -> None:
    process = _FakeProcess(survives_terminate=True)
    request_connection = _FakeConnection()
    response_connection = _FakeConnection()
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_WORKER_PROCESS", process)
    monkeypatch.setattr(
        code_toolchain, "_TREE_SITTER_REQUEST_CONNECTION", request_connection
    )
    monkeypatch.setattr(
        code_toolchain, "_TREE_SITTER_RESPONSE_CONNECTION", response_connection
    )
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_SHUTDOWN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_KILL_GRACE_SECONDS", 0.0)

    code_toolchain.shutdown_tree_sitter_runtime()

    assert process.terminate_count == 1
    assert process.kill_count == 1
    assert process.is_alive() is False


def test_parser_timeout_discards_worker_without_waiting_on_pool_thread(
    monkeypatch,
) -> None:
    process = _FakeProcess()
    request_connection = _FakeConnection()
    response_connection = _FakeConnection(poll_result=False)
    monkeypatch.setattr(code_toolchain, "tree_sitter_available", lambda: True)
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_WORKER_PROCESS", process)
    monkeypatch.setattr(
        code_toolchain, "_TREE_SITTER_REQUEST_CONNECTION", request_connection
    )
    monkeypatch.setattr(
        code_toolchain, "_TREE_SITTER_RESPONSE_CONNECTION", response_connection
    )
    monkeypatch.setattr(
        code_toolchain,
        "_tree_sitter_runtime",
        lambda: (process, request_connection, response_connection),
    )
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_WORKER_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_SHUTDOWN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(code_toolchain, "_TREE_SITTER_KILL_GRACE_SECONDS", 0.0)

    receipt = code_toolchain.extract_tree_sitter_facts("app.py", "print('ok')")

    assert receipt.status == "NATIVE_PARSER_TIMEOUT_ISOLATED"
    assert request_connection.sent[0][1:] == ("app.py", "print('ok')")
    assert request_connection.sent[-1] is None
    assert process.terminate_count == 1
    assert process.is_alive() is False
