from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lanes import AUTHORITY_LANE_IDS
from evidence_lane_plugin.recovery_snapshot import inventory, relative_file
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.storage import project_snapshot


def test_project_register_intakes_code_then_initializes_direct_authorities(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = b"def answer():\n    return 42\n"
    (source / "pyproject.toml").write_text(
        '[project]\nname="flat-pv-fixture"\nversion="0.0.1"\n', encoding="utf-8"
    )
    (source / "main.py").write_bytes(original)
    state = tmp_path / "Evidence_Lane_PV"
    engine = Engine(tmp_path / "runtime")
    context = ActionContext(
        client_id="project-owner",
        project_id=None,
        permissions=frozenset({"project_admin"}),
    )

    result = engine.registry.execute(
        "project_register",
        {
            "state_root": str(state),
            "source_root": str(source),
            "create": True,
            "read_only": False,
            "display_name": "Flat PV fixture",
            "sensitivity": "PRIVATE",
            "capture_route": "GOVERNED_PROJECT_FULL",
        },
        context,
    )

    store = engine.directory.open(result["project_id"], write=True)
    expected_order = ["local_code", *AUTHORITY_LANE_IDS]
    assert [row["lane_id"] for row in store.lane_catalog()] == expected_order
    assert result["initial_source_intake"]["selected_lane_ids"] == ["local_code"]
    assert result["initial_source_intake"]["authority_lane_order"] == list(AUTHORITY_LANE_IDS)
    assert result["initial_source_intake"]["code_lane_state"] == (
        "SCHEMA_READY_SOURCE_REGISTERED_INDEX_PENDING_PLAN"
    )
    assert not (state / "authorities").exists()
    assert not (state / "sectors").exists()
    assert not any(
        (state / lane_id).exists()
        for lane_id in (
            "docs",
            "data_excel",
            "ppt",
            "pdf_ocr",
            "images_ocr",
            "research",
            "artifacts",
            "custom",
            "tableau",
            "power_bi",
        )
    )
    for lane_id in expected_order:
        lane = store.lane(lane_id)
        assert lane.database.is_file()
        assert lane.schema_history.is_file()
        assert {
            "authority.ref.json",
            "lane_manifest.json",
            "lane_pointer.json",
            "tools.json",
            "schema-history.v4.json",
            "refresh_receipt.json",
            f"{lane_id}.mmd",
            f"{lane_id}.dot",
        } <= {path.name for path in lane.folder.iterdir() if path.is_file()}
        assert not (lane.folder / "files").exists()
        assert not (lane.folder / "schema-history").exists()
        authority_ref = json.loads((lane.folder / "authority.ref.json").read_text(encoding="utf-8"))
        lane_manifest = json.loads((lane.folder / "lane_manifest.json").read_text(encoding="utf-8"))
        lane_pointer = json.loads((lane.folder / "lane_pointer.json").read_text(encoding="utf-8"))
        for projection in (authority_ref, lane_manifest, lane_pointer):
            assert projection["root_pointer"] == "../active_pointer.json"
            assert "root_pv_head" not in projection and "root_pv_revision" not in projection
    with store.lane("local_code").connection(read_only=True) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='code_snapshot'"
        ).fetchone()
        assert connection.execute("SELECT count(*) FROM code_snapshot").fetchone()[0] == 0
    with store.lane("sources").connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM intake_batch").fetchone()[0] == 1
    assert not store.lane("local_code").files.exists()
    assert store.lane("sources").files.is_dir()
    assert (source / "main.py").read_bytes() == original

    project = json.loads((state / "project.json").read_text(encoding="utf-8"))
    authority = json.loads((state / "project_authority.json").read_text(encoding="utf-8"))
    assert project["initial_source_lane_ids"] == ["local_code"]
    assert authority["ordered_lane_ids"] == expected_order
    assert authority["wrapper_directories"] == []
    assert authority["initial_source_intake"]["source_batch_id"]

    local_pointer = (state / "local_code" / "lane_pointer.json").read_bytes()
    before_revision = store.pv_head()["revision"]
    with engine.project_work.mutation(store) as lease, lease.transaction("receipts") as connection:
        store.append_receipt("unrelated_lane_fixture", {"purpose": "advance only Receipts"}, connection=connection)
    assert store.pv_head()["revision"] > before_revision
    assert (state / "local_code" / "lane_pointer.json").read_bytes() == local_pointer
    active_pointer = json.loads((state / "active_pointer.json").read_text(encoding="utf-8"))
    assert {key: active_pointer[key] for key in ("revision", "head_digest", "commit_id", "singleton")} == store.pv_head()

    with project_snapshot(store.root):
        backed_up = inventory(store, require_quiescent=False)
    paths = {row["path"] for row in backed_up["files"]}
    assert "project.json" in paths
    assert "local_code/schema-history.v4.json" in paths
    assert "sessions/schema-history.v4.json" in paths
    assert not any(path.startswith(("authorities/", "sectors/")) for path in paths)


def test_recovery_scope_accepts_direct_members_and_rejects_old_wrappers():
    assert relative_file("plan/schema-history.v4.json") == Path("plan/schema-history.v4.json")
    assert relative_file("sessions/sessions_authority_v001.sqlite") == Path(
        "sessions/sessions_authority_v001.sqlite"
    )
    with pytest.raises(LaneError) as error:
        relative_file("authorities/plan/plan_authority_v001.sqlite")
    assert error.value.code == "RECOVERY_FILE_SCOPE"
