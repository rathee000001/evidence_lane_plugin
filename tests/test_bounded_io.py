from __future__ import annotations

import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.bounded_io import (
    IOBudget,
    bounded_file_identity,
    run_bounded_process,
    run_bounded_process_digest,
)
from evidence_lane_plugin.errors import EvidenceLaneError


def test_bounded_file_identity_streams_with_stable_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"bounded-content")
    budget = IOBudget(max_file_bytes=64, max_file_count=1, max_aggregate_bytes=64)

    identity = bounded_file_identity(source, budget=budget, root=tmp_path)

    assert identity["size_bytes"] == len(b"bounded-content")
    assert identity["streamed"] is True
    assert identity["descriptor_identity_stable"] is True
    assert identity["symlink_followed"] is False
    assert budget.receipt()["consumed_file_count"] == 1


def test_bounded_file_identity_rejects_preallocation_over_budget(
    tmp_path: Path,
) -> None:
    source = tmp_path / "too-large.bin"
    source.write_bytes(b"12345")
    budget = IOBudget(max_file_bytes=4, max_file_count=1, max_aggregate_bytes=4)

    with pytest.raises(EvidenceLaneError) as blocked:
        bounded_file_identity(source, budget=budget, root=tmp_path)

    assert blocked.value.code == "BOUNDED_IO_FILE_BYTES_EXCEEDED"


def test_bounded_process_caps_each_output_stream(tmp_path: Path) -> None:
    passed = run_bounded_process(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        max_stdout_bytes=16,
        max_stderr_bytes=16,
    )
    assert passed.returncode == 0
    assert passed.stdout.strip() == b"ok"

    with pytest.raises(EvidenceLaneError) as blocked:
        run_bounded_process(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 32)"],
            cwd=tmp_path,
            max_stdout_bytes=8,
            max_stderr_bytes=16,
        )
    assert blocked.value.code == "BOUNDED_PROCESS_OUTPUT_EXCEEDED"


def test_bounded_process_digest_does_not_retain_stdout(tmp_path: Path) -> None:
    result = run_bounded_process_digest(
        [sys.executable, "-c", "import sys; sys.stdout.write('z' * 10000)"],
        cwd=tmp_path,
        max_stdout_bytes=10000,
        max_stderr_bytes=16,
    )

    assert result.returncode == 0
    assert result.stdout_bytes == 10000
    assert len(result.stdout_sha256) == 64
