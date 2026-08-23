from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.conversation_memory import (
    ConversationMemoryManager,
    resolve_conversation_memory,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.internal_sdk import SDKBinding

TASK_ID = "01a02759-9842-74c1-860e-0c9a64f8258d"
PROFILE = {
    "model": "gpt-5.6-sol",
    "submodel": "sol",
    "reasoning_effort": "xhigh",
    "reasoning_speed": "standard",
}


def _resolve(home: Path, root: Path, cwd: Path, **overrides: object):
    values: dict[str, object] = {
        "codex_home": home,
        "project_root": root,
        "cwd": cwd,
        "project_id": "test-codex-evidence-lane-plugin",
        "governed_session_id": "session_01kz48pm60mt58v5fyzqq6yq1g",
        "host_task_id": TASK_ID,
        "host_task_deep_link": f"codex://threads/{TASK_ID}",
        "host_session_id": TASK_ID,
        "workspace_id": str(root),
        "active_plan_task_id": "EL-CODEX-R264",
        "execution_profile": PROFILE,
    }
    values.update(overrides)
    return resolve_conversation_memory(**values)


def test_memory_discovers_global_and_project_chain_with_nested_precedence(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex-home"
    root = tmp_path / "project"
    nested = root / "src" / "feature"
    home.mkdir()
    nested.mkdir(parents=True)
    (home / "MEMORY.md").write_text("global standard", encoding="utf-8")
    (home / "MEMORY.override.md").write_text("global override", encoding="utf-8")
    (root / "MEMORY.md").write_text("project root", encoding="utf-8")
    (root / "src" / "MEMORY.md").write_text("", encoding="utf-8")
    (root / "src" / "feature" / "MEMORY.override.md").write_text(
        "near guidance",
        encoding="utf-8",
    )

    resolved = _resolve(home, root, nested)

    assert resolved.guidance == "global override\n\nproject root\n\nnear guidance"
    receipt = resolved.receipt
    assert receipt["status"] == "PASS"
    assert receipt["source_count"] == 3
    assert [source["locator"] for source in receipt["source_chain"]["sources"]] == [
        "CODEX_HOME/MEMORY.override.md",
        "PROJECT_ROOT/MEMORY.md",
        "PROJECT_ROOT/src/feature/MEMORY.override.md",
    ]
    assert receipt["raw_guidance_text_returned"] is False
    assert receipt["absolute_source_paths_returned"] is False
    assert str(tmp_path) not in str(receipt)
    assert receipt["user_observed_host_evidence"]["causal_attribution"] == (
        "UNVERIFIED_PENDING_INSTALLED_HOST_AB"
    )
    assert receipt["authority_effects"]["host_compaction_disabled"] is False
    assert receipt["authority_effects"]["project_memory_replaced"] is False


def test_memory_supports_bounded_configured_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    home.mkdir()
    root.mkdir()
    (home / "config.toml").write_text(
        'conversation_memory_doc_fallback_filenames = ["CONTINUATION.md"]\n'
        "conversation_memory_doc_max_bytes = 128\n",
        encoding="utf-8",
    )
    (root / "MEMORY.md").write_text("  \n", encoding="utf-8")
    (root / "CONTINUATION.md").write_text("bounded route", encoding="utf-8")

    resolved = _resolve(home, root, root)

    source = resolved.receipt["source_chain"]["sources"][0]
    assert source["filename"] == "CONTINUATION.md"
    assert source["precedence_kind"] == "CONFIGURED_FALLBACK"
    assert resolved.receipt["memory_doc_max_bytes"] == 128


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"bad\x00memory", "CONVERSATION_MEMORY_SOURCE_MALFORMED"),
        (b"x" * 65, "CONVERSATION_MEMORY_SOURCE_OVERSIZED"),
    ],
)
def test_memory_fails_closed_on_invalid_content(
    tmp_path: Path,
    payload: bytes,
    code: str,
) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    home.mkdir()
    root.mkdir()
    (home / "config.toml").write_text(
        "conversation_memory_doc_max_bytes = 64\n",
        encoding="utf-8",
    )
    (root / "MEMORY.md").write_bytes(payload)

    with pytest.raises(EvidenceLaneError) as exc:
        _resolve(home, root, root)
    assert exc.value.code == code


def test_memory_rejects_wrong_task_and_wrong_project_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    outside = tmp_path / "other"
    home.mkdir()
    root.mkdir()
    outside.mkdir()

    with pytest.raises(EvidenceLaneError) as wrong_task:
        _resolve(
            home, root, root, host_session_id="01a0036f-32fa-79b2-8846-9c716d4fe777"
        )
    assert wrong_task.value.code == "CONVERSATION_MEMORY_TASK_BINDING_MISMATCH"

    with pytest.raises(EvidenceLaneError) as wrong_cwd:
        _resolve(home, root, outside)
    assert wrong_cwd.value.code == "CONVERSATION_MEMORY_WORKTREE_MISMATCH"


def test_memory_resolves_once_per_manager_and_rebuilds_after_restart(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    root = tmp_path / "project"
    home.mkdir()
    root.mkdir()
    source = root / "MEMORY.md"
    source.write_text("first", encoding="utf-8")
    kwargs = {
        "codex_home": home,
        "project_root": root,
        "cwd": root,
        "project_id": "test-codex-evidence-lane-plugin",
        "governed_session_id": "session_01kz48pm60mt58v5fyzqq6yq1g",
        "host_task_id": TASK_ID,
        "host_task_deep_link": f"codex://threads/{TASK_ID}",
        "host_session_id": TASK_ID,
        "workspace_id": str(root),
        "active_plan_task_id": "EL-CODEX-R264",
        "execution_profile": PROFILE,
    }
    manager = ConversationMemoryManager()
    first = manager.resolve(**kwargs)
    source.write_text("second", encoding="utf-8")
    cached = manager.resolve(**kwargs)
    restarted = ConversationMemoryManager().resolve(**kwargs)

    assert cached.guidance == first.guidance == "first"
    assert restarted.guidance == "second"
    assert (
        restarted.receipt["source_chain_sha256"] != first.receipt["source_chain_sha256"]
    )
    assert restarted.receipt["binding"] == first.receipt["binding"]


def test_sdk_memory_hashes_are_paired_and_do_not_grant_authority() -> None:
    values = {
        "project_id": "project",
        "session_id": "session_abc",
        "task_id": "task-1",
        "accepted_pv": "PV12",
        "pointer_generation": 12,
        "accepted_manifest_sha256": "A" * 64,
        "lineage_head_sha256": "B" * 64,
        "env_authority_sha256": "C" * 64,
        "uop_authority_sha256": "D" * 64,
        "derived_projection_sha256": "E" * 64,
        "flash_receipt_sha256": "F" * 64,
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "xhigh",
        "reasoning_speed": "standard",
        "host_kind": "CODEX",
        "host_session_id": TASK_ID,
        "conversation_memory_authority_sha256": "1" * 64,
        "conversation_memory_source_chain_sha256": "2" * 64,
        "write_scope": [],
    }
    binding = SDKBinding.from_dict(values)
    assert binding.as_dict()["conversation_memory_authority_sha256"] == "1" * 64

    incomplete = dict(values)
    incomplete.pop("conversation_memory_source_chain_sha256")
    with pytest.raises(EvidenceLaneError) as exc:
        SDKBinding.from_dict(incomplete)
    assert exc.value.code == "SDK_BINDING_SHAPE_INVALID"
