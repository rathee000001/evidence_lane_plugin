"""Power BI through actual engine workers, Delta, own-lane storage and SDK calls."""

from __future__ import annotations

import base64
import json
import time

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from evidence_lane_plugin.powerbi_parsers import digest, package_members
from evidence_lane_plugin.powerbi_workers import powerbi_worker_operations
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from .powerbi_fixtures import model_document, project_documents
from .test_powerbi_parsers_v4 import SAMPLES
from .test_powerbi_parsers_v4 import powerbi_assets as powerbi_assets  # noqa: PLC0414


@pytest.fixture
def powerbi_system(tmp_path, powerbi_assets):
    source = tmp_path / "source"
    source.mkdir()
    for name, content in project_documents().items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (source / "model.bim").write_text(json.dumps(model_document()), encoding="utf-8")
    (source / "abc.pbix").write_bytes((SAMPLES / "abc.pbix").read_bytes())
    operations = (
        *powerbi_worker_operations(),
        WorkerOperation(
            "render_lane_view",
            "evidence_lane_plugin.artifact_contract",
            "render_lane_view_worker",
            dependencies=("langgraph", "langchain_core", "graphviz"),
        ),
    )
    with Engine(tmp_path / "runtime", worker_pool=WorkerPool(operations, workers=1)) as engine:
        entry = engine.directory.register(
            tmp_path / "state", source_root=source, create=True, read_only=False
        )
        store = engine.directory.open(entry["project_id"], write=True)
        _, session = engine.clients.connect(
            ConnectRequest(
                projects=[
                    ProjectSelection(
                        project_id=store.project_id, permissions=["read", "write", "tools", "admin"]
                    )
                ]
            )
        )
        yield engine, store, session


def call(system, action, arguments=None, *, transport=None, **kwargs):
    engine, store, session = system
    request = ActionRequest(
        action=action, project_id=store.project_id, arguments=arguments or {}, **kwargs
    )
    return (
        transport.send(request)
        if transport
        else PublicActionSDKDispatcher(engine).execute(request, session)
    )


def plan(system, actions, *, permitted_paths=(".",)):
    engine, store, _ = system
    tasks = [
        TaskDefinition(
            task_id="powerbi-" + str(index),
            title=action,
            requested_outcome="Verify the exact Power BI operation",
            profile="power_bi",
            allowed_actions=[action],
            permitted_tools=["Python", "SQLite_FTS5_BM25", "PowerBI_TOM", "PBIXRay", "JSONSchema",
                "LangGraph_Mermaid_engine", "Python_Graphviz_DOT_engine"],
            permitted_paths=list(permitted_paths),
            acceptance_checks=list(engine.registry.get(action).verification_checks),
            budget=TaskBudget(
                max_input_bytes=33_554_432, max_output_bytes=67_108_864, max_seconds=240
            ),
        )
        for index, action in enumerate(actions)
    ]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(
            PlanCreate(title="Power BI fixture", tasks=tasks), lease, actor_id="fixture"
        )


def execute(system, action, arguments, *, index=0, failure=None, transport=None):
    store = system[1]
    task = PlanStore(store).task("powerbi-" + str(index), expected_revision=1)
    admitted = call(
        system,
        "delta_enter",
        {
            "task_id": task.definition.task_id,
            "plan_revision": 1,
            "contract_digest": task.contract_digest,
            "action": action,
            "arguments": arguments,
        },
        expected_revision=1,
        transport=transport,
    )
    assert admitted.status == "queued", admitted.error
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with store.lane("plan").connection(read_only=True) as connection:
                row = dict(
                    connection.execute(
                        "SELECT * FROM delta_runs WHERE job_id=?", (admitted.job_id,)
                    ).fetchone()
                )
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.03)
            continue
        if row["state"] in {"verified", "blocked"}:
            break
        time.sleep(0.03)
    if failure:
        assert row["state"] == "blocked" and row["error_code"] == failure, row
        return row
    assert row["state"] == "verified", {
        key: row[key] for key in ("state", "error_code", "result_object")
    }
    while time.monotonic() < deadline and any(
        item["project_id"] == store.project_id for item in system[0].project_work.status()
    ):
        time.sleep(0.01)
    assert not any(item["project_id"] == store.project_id for item in system[0].project_work.status())
    return json.loads(store.lane("plan").read_object(row["result_object"]))["result"]["result"]


def test_pbix_rows_and_read_only_queries_keep_source_and_databases_unchanged(powerbi_system):
    plan(powerbi_system, ["powerbi_index"])
    store = powerbi_system[1]
    raw = (store.source_root / "abc.pbix").read_bytes()
    indexed = execute(
        powerbi_system, "powerbi_index", {"filename": "abc.pbix", "max_rows_per_table": 2}
    )
    before = {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}
    query = call(
        powerbi_system,
        "powerbi_query",
        {"snapshot_id": indexed["snapshot_id"], "collection": "model_row"},
    )
    assert query.status == "ok", query.error
    text_query = call(powerbi_system, 'powerbi_query', {'snapshot_id': indexed['snapshot_id'],
        'collection': 'text', 'query': 'ABC'})
    assert text_query.status == 'ok' and text_query.result['result']['rows'], text_query.error
    assert [
        row["data"]["values"]
        for row in query.result["result"]["rows"]
        if row["table_name"] == "ABC"
    ] == [[1, 5], [2, 6]]
    read = call(
        powerbi_system, "powerbi_read", {"snapshot_id": indexed["snapshot_id"], "max_bytes": 131072}
    )
    assert base64.b64decode(read.result["result"]["content_base64"]) == raw
    assert before == {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}
    assert (store.source_root / "abc.pbix").read_bytes() == raw
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE name GLOB 'powerbi_*'"
            ).fetchone()[0]
            == 0
        )
    assert (
        indexed["fidelity"]["compressed_model_data_read"]
        and not indexed["fidelity"]["dax_evaluated"]
    )


def test_project_edit_keeps_exact_original_members_and_exports_separately(powerbi_system):
    plan(powerbi_system, ["powerbi_index", "powerbi_edit", "powerbi_export"])
    store = powerbi_system[1]
    documents = project_documents()
    primary_raw = (store.source_root / "Example.pbip").read_bytes()
    args = {
        "filename": "Example.pbip",
        "companion_files": [name for name in documents if name != "Example.pbip"],
    }
    indexed = execute(powerbi_system, "powerbi_index", args)
    assert indexed["fidelity"]["report_schema_validated"]
    raw = store.lane("power_bi").read_object(indexed["sha256"])
    members = package_members(raw)
    part = "Example.Report/definition/pages/Main/page.json"
    changed = json.loads(members[part])
    changed["displayName"] = "Revised overview"
    edited = execute(
        powerbi_system,
        "powerbi_edit",
        {
            "snapshot_id": indexed["snapshot_id"],
            "expected_sha256": indexed["sha256"],
            "replacements": [
                {
                    "part": part,
                    "expected_sha256": digest(members[part]),
                    "content_utf8": json.dumps(changed),
                }
            ],
        },
        index=1,
    )
    after = package_members(store.lane("power_bi").read_object(edited["sha256"]))
    assert all(after[name] == value for name, value in members.items() if name != part)
    assert json.loads(after[part])["displayName"] == "Revised overview"
    original = call(
        powerbi_system,
        "powerbi_read",
        {
            "snapshot_id": edited["snapshot_id"],
            "representation": "original_source",
            "max_bytes": 131072,
        },
    )
    assert base64.b64decode(original.result["result"]["content_base64"]) == primary_raw
    original_part = call(
        powerbi_system,
        "powerbi_read",
        {
            "snapshot_id": edited["snapshot_id"],
            "representation": "original_member",
            "member": part,
            "max_bytes": 131072,
        },
    )
    assert base64.b64decode(original_part.result["result"]["content_base64"]) == members[part]
    assert (store.source_root / part).read_bytes() == members[part]
    execute(
        powerbi_system,
        "powerbi_export",
        {"snapshot_id": edited["snapshot_id"], "filename": "revised.zip"},
        index=2,
    )
    assert (store.source_root / "revised.zip").read_bytes() == store.lane("power_bi").read_object(
        edited["sha256"]
    )


def test_bim_and_pbir_generation_publish_real_derivatives(powerbi_system):
    plan(powerbi_system, ["powerbi_generate", "powerbi_generate"])
    model = execute(
        powerbi_system,
        "powerbi_generate",
        {"logical_name": "generated.bim", "database": model_document()},
    )
    report = execute(
        powerbi_system,
        "powerbi_generate",
        {"logical_name": "report.zip", "files": project_documents(), "entrypoint": "Example.pbip"},
        index=1,
    )
    assert model["fidelity"]["model_metadata_deserialized"]
    assert report["fidelity"]["report_schema_validated"]
    assert not report["fidelity"]["layout_verified"] and not report["fidelity"]["dax_evaluated"]
    assert "Example.Report/definition/report.json" in package_members(
        powerbi_system[1].lane("power_bi").read_object(report["sha256"])
    )


def test_schema_catalog_and_unknown_uri_never_initialize_the_lane(powerbi_system):
    store = powerbi_system[1]
    response = call(powerbi_system, "powerbi_schema")
    assert response.status == "ok" and len(response.result["result"]["resources"]) == 98
    assert response.result["result"]["evidence"]["network_used"] is False
    rejected = call(powerbi_system, "powerbi_schema", {"schema_uri": "http://127.0.0.1:1/private"})
    assert rejected.error.code == "POWERBI_SCHEMA_UNSUPPORTED"
    current = call(powerbi_system, "powerbi_current")
    assert current.status == "ok" and not current.result["result"]["initialized"]
    assert not (store.root / "power_bi/power_bi_sector_v001.sqlite").exists()


def test_companion_paths_are_guarded_before_worker_reads(powerbi_system):
    plan(powerbi_system, ["powerbi_index"], permitted_paths=["Example.pbip"])
    execute(
        powerbi_system,
        "powerbi_index",
        {"filename": "Example.pbip", "companion_files": ["Example.SemanticModel/model.bim"]},
        failure="DELTA_PATH_SCOPE",
    )
    current = call(powerbi_system, "powerbi_current")
    assert not current.result["result"]["initialized"]
