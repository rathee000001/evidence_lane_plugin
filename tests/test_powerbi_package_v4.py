"""Independent Power BI package, local stdio, immutable index and view checks."""

import asyncio
import base64
import hashlib
import importlib.util
import json
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.powerbi_parsers import bundle, package_members, parse_powerbi
from evidence_lane_plugin.sector_support import sector_package_contract

from .powerbi_fixtures import model_document, tmdl_documents
from .test_native_workflow_bindings import native
from .test_powerbi_parsers_v4 import SAMPLES
from .test_powerbi_parsers_v4 import powerbi_assets as powerbi_assets  # noqa: PLC0414
from .test_powerbi_profile_v4 import call, execute, plan
from .test_powerbi_profile_v4 import powerbi_system as powerbi_system  # noqa: PLC0414


def test_package_owns_powerbi_schema_and_registered_actions(powerbi_system):
    engine, store, _ = powerbi_system
    path = Path("plugins/evidence-lane-plugin/authorities/project_sectors/power_bi/runtime.py")
    spec = importlib.util.spec_from_file_location("fixture_powerbi_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LANE_ID == "power_bi" and {item.owner for item in module.migrations()} == {
        "powerbi", "powerbiselector"
    }
    assert not module.inspect(store)["initialized"]
    contract = sector_package_contract("power_bi", engine.registry)
    assert contract["database"] == "power_bi/power_bi_sector_v001.sqlite"
    assert contract["runtime_module"] == "evidence_lane_plugin.powerbi_profile"
    assert {item["name"] for item in contract["actions"]} == {
        "powerbi_index",
        "powerbi_refresh",
        "powerbi_current",
        "powerbi_read",
        "powerbi_query",
        "powerbi_schema",
        "powerbi_generate",
        "powerbi_edit",
        "powerbi_export", "source_snapshot_state", "source_snapshot_retire",
    }


def test_graph_files_preserve_locators_without_rendering_claim(powerbi_system):
    plan(powerbi_system, ["powerbi_index"])
    indexed = execute(
        powerbi_system, "powerbi_index", {"filename": "abc.pbix", "max_rows_per_table": 1}
    )
    arguments = {"view_id": "power_bi.structure", "scope": {"query": indexed["powerbi_id"]}}
    response = call(powerbi_system, "lane_view_preview", arguments)
    assert response.status == "ok", response.error
    preview = response.result
    columns = [node for node in preview["graph"]["nodes"] if node["kind"] == "powerbi_column"]
    assert len(columns) == 6
    assert all(node["locator"]["locator_basis"] == "pbixray_response" for node in columns)
    published = call(
        powerbi_system,
        "lane_view_refresh",
        {
            **arguments,
            "scope": preview["scope"],
            "expected_generation": preview["generation"],
            "contract_digest": preview["contract_digest"],
            "source_digest": preview["source_digest"],
            "formats": ["mmd", "dot"],
            "include_pointer": True,
        },
    )
    assert published.status == "ok", published.error
    assert {Path(row["path"]).name for row in published.result["files"]} == {
        "powerbi.mmd",
        "powerbi.dot",
        "powerbi.pointer.json",
    }
    assert all(
        "/power_bi/" in row["path"].replace("\\", "/") for row in published.result["files"]
    )
    read = call(
        powerbi_system,
        "lane_view_read",
        {
            "view_id": arguments["view_id"],
            "snapshot_digest": published.result["snapshot_digest"],
            "include_content": True,
        },
    )
    assert read.status == "ok", read.error
    pointer = json.loads(read.result["contents"]["pointer"])
    assert not pointer["report_rendering_verified"] and not pointer["dax_execution"]
    assert {row["snapshot_id"] for row in pointer["locators"].values()} == {indexed["snapshot_id"]}


@pytest.mark.parametrize("tamper", ["typed", "fts"])
def test_queries_reject_corrupted_index_and_chunk_registration(powerbi_system, tamper):
    engine, store, _ = powerbi_system
    plan(powerbi_system, ["powerbi_index"])
    indexed = execute(
        powerbi_system, "powerbi_index", {"filename": "abc.pbix", "max_rows_per_table": 0}
    )
    with (
        engine.project_work.mutation(store) as lease,
        lease.coordinated_transaction(["power_bi"]),
        store.lane("power_bi").transaction() as connection,
    ):
        if tamper == "typed":
            connection.execute(
                "UPDATE powerbi_column SET payload_json=json_set(payload_json,'$.name','forged')"
            )
        else:
            connection.execute("UPDATE powerbi_chunk_fts SET text_content='counterfeit'")
    selection = (
        {"collection": "column"}
        if tamper == "typed"
        else {"collection": "text", "query": "counterfeit" if tamper == "fts" else "ABC"}
    )
    response = call(
        powerbi_system, "powerbi_query", {"snapshot_id": indexed["snapshot_id"], **selection}
    )
    assert response.error.code == "POWERBI_QUERY_INTEGRITY"


def test_corrupted_chunk_registration_cannot_be_published(powerbi_system):
    engine, store, _ = powerbi_system
    plan(powerbi_system, ["powerbi_index"])
    indexed = execute(
        powerbi_system, "powerbi_index", {"filename": "abc.pbix", "max_rows_per_table": 0}
    )
    before = call(powerbi_system, "powerbi_current").result
    with (
        pytest.raises(LaneError) as rejected,
        engine.project_work.mutation(store) as lease,
        lease.coordinated_transaction(["power_bi"]),
        store.lane("power_bi").transaction() as connection,
    ):
        connection.execute(
            "UPDATE objects SET size_bytes=size_bytes+1 WHERE digest IN (SELECT text_object FROM powerbi_chunk)"
        )
    assert rejected.value.code == "OBJECT_INTEGRITY_FAILED"
    assert call(powerbi_system, "powerbi_current").result == before
    response = call(
        powerbi_system,
        "powerbi_query",
        {"snapshot_id": indexed["snapshot_id"], "collection": "text", "query": "ABC"},
    )
    assert response.status == "ok" and response.result["result"]["rows"], response.error


@pytest.mark.parametrize("entrypoint", ["adapter", "cli", "package"])
def test_stdio_intake_and_readonly_access(powerbi_system, entrypoint):
    engine, store, _ = powerbi_system
    plan(powerbi_system, ["powerbi_index"])
    before_source = (store.source_root / "abc.pbix").read_bytes()

    async def run():
        async with native(
            engine.root,
            store.project_id,
            permissions=("read", "write", "tools"),
            entrypoint=entrypoint,
        ) as session:
            catalog = await session.list_tools()
            assert {row.name for row in catalog.tools if row.name.startswith("powerbi_")} == {
                "powerbi_index",
                "powerbi_refresh",
                "powerbi_current",
                "powerbi_query",
                "powerbi_read",
                "powerbi_generate",
                "powerbi_edit",
                "powerbi_export",
                "powerbi_schema",
            }
            task = PlanStore(store).task("powerbi-0", expected_revision=1)
            admitted = await session.call_tool(
                "delta_enter",
                {
                    "project_id": store.project_id,
                    "expected_revision": 1,
                    "arguments": {
                        "task_id": task.definition.task_id,
                        "plan_revision": 1,
                        "contract_digest": task.contract_digest,
                        "action": "powerbi_index",
                        "arguments": {"filename": "abc.pbix", "max_rows_per_table": 2},
                    },
                },
            )
            body = admitted.structuredContent
            assert body["status"] == "queued", body
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    with store.lane("plan").connection(read_only=True) as connection:
                        row = dict(
                            connection.execute(
                                "SELECT * FROM delta_runs WHERE job_id=?", (body["job_id"],)
                            ).fetchone()
                        )
                except LaneError as error:
                    if error.code != 'PROJECT_RECOVERY_REQUIRED':
                        raise
                    await asyncio.sleep(.03)
                    continue
                if row["state"] in {"verified", "blocked"}:
                    break
                await asyncio.sleep(0.03)
            assert row["state"] == "verified", row
            indexed = json.loads(store.lane("plan").read_object(row["result_object"]))["result"][
                "result"
            ]
        database_bytes = {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}
        async with native(engine.root, store.project_id, entrypoint=entrypoint) as session:
            read = await session.call_tool(
                "powerbi_query",
                {
                    "project_id": store.project_id,
                    "arguments": {"snapshot_id": indexed["snapshot_id"], "collection": "model_row"},
                },
            )
            result = read.structuredContent
            assert result["status"] == "ok", result
            values = [
                row["data"]["values"]
                for row in result["result"]["result"]["rows"]
                if row["table_name"] == "ABC"
            ]
            assert values == [[1, 5], [2, 6]]
            context = await session.call_tool("client_context", {"arguments": {}})
            assert context.structuredContent["result"]["native_task_attestation"] == "not_provided"
            denied = await session.call_tool(
                "powerbi_export",
                {
                    "project_id": store.project_id,
                    "arguments": {
                        "snapshot_id": indexed["snapshot_id"],
                        "filename": "forbidden.pbix",
                    },
                },
            )
            assert denied.structuredContent["status"] != "ok"
            assert not (store.source_root / "forbidden.pbix").exists()
        assert database_bytes == {
            p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()
        }
        assert (store.source_root / "abc.pbix").read_bytes() == before_source

    with LocalEndpoint(engine):
        asyncio.run(run())


def test_exact_vendor_member_reads_reassemble_binary_model(powerbi_system):
    plan(powerbi_system, ["powerbi_index"])
    indexed = execute(
        powerbi_system, "powerbi_index", {"filename": "abc.pbix", "max_rows_per_table": 0}
    )
    members = package_members((SAMPLES / "abc.pbix").read_bytes())
    for name in ("DataModel", "Report/Layout"):
        collected, offset = bytearray(), 0
        while True:
            response = call(
                powerbi_system,
                "powerbi_read",
                {
                    "snapshot_id": indexed["snapshot_id"],
                    "representation": "member",
                    "member": name,
                    "offset": offset,
                    "max_bytes": 1024,
                },
            )
            assert response.status == "ok", response.error
            body = response.result["result"]
            collected.extend(base64.b64decode(body["content_base64"]))
            offset = body["next_offset"]
            if offset is None:
                break
        assert bytes(collected) == members[name]


@pytest.mark.parametrize(
    "filename",
    ["empty-schema-calc-only.pbix", "old-schema17-DataTable.pbix", "ols-sample-report.pbix"],
)
def test_additional_native_model_shapes_preserve_real_metadata_and_values(powerbi_assets, filename):
    raw = (SAMPLES / filename).read_bytes()
    facts = parse_powerbi(filename, raw, max_rows_per_table=2)
    assert facts["features"]["pbix_model_metadata_read"] and facts["counts"]["model_row"] > 0
    assert not facts["fidelity"]["dax_evaluated"] and not facts["fidelity"]["live_data_read"]
    assert all(row["data"]["row_index"] < 2 for row in facts["items"] if row["kind"] == "model_row")
    if filename == "ols-sample-report.pbix":
        roles = [row for row in facts["items"] if row["kind"] == "role"]
        assert roles and {row["data"]["source_collection"] for row in roles} >= {"rls", "ols"}
    proof = json.loads((SAMPLES / "provenance.json").read_bytes())
    expected = next(row for row in proof["files"] if row["file"] == filename)
    assert hashlib.sha256(raw).hexdigest() == expected["sha256"]


def test_pbit_semantic_metadata_is_deserialized_without_stored_row_claim(powerbi_assets):
    raw = bundle(
        {
            "DataModelSchema": json.dumps(model_document()).encode(),
            "Report/Layout": json.dumps({"sections": []}).encode("utf-16le"),
        }
    )
    facts = parse_powerbi("fixture.pbit", raw)
    assert facts["counts"]["table"] == 1 and facts["counts"]["measure"] == 1
    assert facts["fidelity"]["model_metadata_deserialized"]
    assert (
        not facts["fidelity"]["compressed_model_data_read"]
        and not facts["fidelity"]["layout_verified"]
    )


def test_missing_shared_native_assets_fail_before_worker_or_lane_creation(
    powerbi_system, monkeypatch, tmp_path
):
    engine, store, _ = powerbi_system
    plan(powerbi_system, ["powerbi_index"])
    monkeypatch.setenv("EVIDENCE_LANE_STUDIO_ROOT", str(tmp_path / "unprovisioned"))
    task = PlanStore(store).task("powerbi-0", expected_revision=1)
    submitted = engine.workers.status()["submitted"]
    response = call(
        powerbi_system,
        "delta_enter",
        {
            "task_id": task.definition.task_id,
            "plan_revision": 1,
            "contract_digest": task.contract_digest,
            "action": "powerbi_index",
            "arguments": {"filename": "abc.pbix"},
        },
        expected_revision=1,
    )
    assert response.error.code == "TOOL_ROUTE_UNAVAILABLE"
    assert engine.workers.status()["submitted"] == submitted
    assert not call(powerbi_system, "powerbi_current").result["result"]["initialized"]


def test_refresh_records_new_originals_and_retains_earlier_snapshot(powerbi_system):
    plan(powerbi_system, ["powerbi_index", "powerbi_refresh"])
    store = powerbi_system[1]
    original = (store.source_root / "model.bim").read_bytes()
    first = execute(powerbi_system, "powerbi_index", {"filename": "model.bim"})
    changed = model_document()
    changed["model"]["tables"][0]["measures"][0]["expression"] = "2 * SUM(Facts[Value])"
    updated = json.dumps(changed).encode()
    (store.source_root / "model.bim").write_bytes(updated)
    second = execute(
        powerbi_system,
        "powerbi_refresh",
        {"filename": "model.bim", "expected_snapshot": first["snapshot_id"]},
        index=1,
    )
    assert second["generation"] == 2 and second["previous_snapshot"] == first["snapshot_id"]
    for snapshot, expected in [(first, original), (second, updated)]:
        response = call(
            powerbi_system,
            "powerbi_read",
            {"snapshot_id": snapshot["snapshot_id"], "representation": "original_source"},
        )
        assert response.status == "ok", response.error
        assert base64.b64decode(response.result["result"]["content_base64"]) == expected
    current = call(powerbi_system, "powerbi_current").result["result"]["powerbis"]
    assert [row["snapshot_id"] for row in current] == [second["snapshot_id"]]


def test_native_tmdl_locator_preserves_actual_member_case(powerbi_assets):
    documents = tmdl_documents()
    documents["MODEL.TMDL"] = documents.pop("model.tmdl")
    facts = parse_powerbi(
        "model.zip", bundle({name: text.encode() for name, text in documents.items()})
    )
    assert facts["native_evidence"][0]["part"] == "MODEL.TMDL"
    assert {
        row["part"]
        for row in facts["items"]
        if row["kind"] in {"model", "table", "column", "measure"}
    } == {"MODEL.TMDL"}
