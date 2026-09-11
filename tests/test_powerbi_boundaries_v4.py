"""Destructive-edit rejection, large native input and exact graph parentage."""

import base64
import json

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lane_contract import ViewScope
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.powerbi_authoring import edit_model
from evidence_lane_plugin.powerbi_parsers import bundle, digest, package_members, parse_powerbi
from evidence_lane_plugin.powerbi_views import powerbi_graph

from .powerbi_fixtures import model_document, project_documents
from .test_powerbi_parsers_v4 import powerbi_assets as powerbi_assets  # noqa: PLC0414
from .test_powerbi_profile_v4 import call, execute, plan
from .test_powerbi_profile_v4 import powerbi_system as powerbi_system  # noqa: PLC0414


def test_rejected_schema_edit_preserves_current_snapshot(powerbi_system):
    plan(powerbi_system, ["powerbi_generate", "powerbi_edit"])
    created = execute(
        powerbi_system,
        "powerbi_generate",
        {"logical_name": "report.zip", "files": project_documents(), "entrypoint": "Example.pbip"},
    )
    store = powerbi_system[1]
    before = call(powerbi_system, "powerbi_current").result
    raw = store.lane("power_bi").read_object(created["sha256"])
    part = "Example.Report/definition/pages/Main/page.json"
    old = package_members(raw)[part]
    invalid = json.loads(old)
    invalid["height"] = "invalid dimension"
    execute(
        powerbi_system,
        "powerbi_edit",
        {
            "snapshot_id": created["snapshot_id"],
            "expected_sha256": created["sha256"],
            "replacements": [
                {"part": part, "expected_sha256": digest(old), "content_utf8": json.dumps(invalid)}
            ],
        },
        index=1,
        failure="WORKER_OPERATION_FAILED",
    )
    assert call(powerbi_system, "powerbi_current").result == before
    assert store.lane("power_bi").read_object(created["sha256"]) == raw


@pytest.mark.parametrize("kind", ["stale_snapshot", "occupied_export"])
def test_stale_mutations_fail_without_replacing_current_bytes(powerbi_system, kind):
    action = "powerbi_edit" if kind == "stale_snapshot" else "powerbi_export"
    plan(powerbi_system, ["powerbi_index", action])
    indexed = execute(powerbi_system, "powerbi_index", {"filename": "model.bim"})
    store = powerbi_system[1]
    source = store.source_root / "model.bim"
    original = source.read_bytes()
    before = call(powerbi_system, "powerbi_current").result
    if kind == "stale_snapshot":
        args = {
            "snapshot_id": indexed["snapshot_id"],
            "expected_sha256": "0" * 64,
            "replacements": [
                {
                    "part": "model.bim",
                    "expected_sha256": digest(original),
                    "content_utf8": original.decode(),
                }
            ],
        }
        code = "POWERBI_EDIT_SNAPSHOT_CHANGED"
    else:
        args = {"snapshot_id": indexed["snapshot_id"], "filename": "model.bim"}
        code = "POWERBI_EXPORT_DESTINATION_CHANGED"
    execute(powerbi_system, action, args, index=1, failure=code)
    assert (
        source.read_bytes() == original and call(powerbi_system, "powerbi_current").result == before
    )


def test_add_delete_resource_preserves_all_other_parts(powerbi_assets):
    members = {name: raw.encode() for name, raw in project_documents().items()}
    raw = bundle(members)
    resource = "Example.Report/StaticResources/RegisteredResources/fixture.svg"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
    options = {"entrypoint": "Example.pbip", "inspect_models": True, "max_rows_per_table": 0}

    def edited(content, changes):
        return edit_model(
            {
                "logical_name": "report.zip",
                "content_base64": base64.b64encode(content).decode(),
                "expected_sha256": digest(content),
                "parse_options": options,
                "replacements": changes,
            }
        )

    added, evidence = edited(
        raw,
        [
            {
                "part": resource,
                "expected_sha256": None,
                "content_base64": base64.b64encode(svg).decode(),
            }
        ],
    )
    assert package_members(added) == {**members, resource: svg} and evidence["added_parts"] == [
        resource
    ]
    removed, evidence = edited(added, [{"part": resource, "expected_sha256": digest(svg)}])
    assert package_members(removed) == members and evidence["deleted_parts"] == [resource]
    with pytest.raises(LaneError) as rejected:
        edited(
            raw,
            [
                {
                    "part": "Example.SemanticModel/definition.pbism",
                    "expected_sha256": digest(members["Example.SemanticModel/definition.pbism"]),
                }
            ],
        )
    assert rejected.value.code == "POWERBI_PROJECT_REFERENCE_MISSING"


def test_large_bim_uses_explicit_worker_input_budget(powerbi_system):
    model = model_document()
    model["model"]["annotations"] = [
        {"name": f"provenance-{i}", "value": "x" * 4096} for i in range(300)
    ]
    assert len(json.dumps(model).encode()) > 1_048_576
    plan(powerbi_system, ["powerbi_generate"])
    engine, store, _ = powerbi_system
    connection = ConnectRequest(
        projects=[
            ProjectSelection(project_id=store.project_id, permissions=["read", "write", "tools"])
        ]
    )
    with LocalEndpoint(engine), LocalTransport(engine.root, connection=connection) as transport:
        result = execute(
            powerbi_system,
            "powerbi_generate",
            {"logical_name": "large.bim", "database": model},
            transport=transport,
        )
    assert result["fidelity"]["model_metadata_deserialized"]
    assert not result["fidelity"]["report_schema_validated"]


@pytest.mark.parametrize("source", ["pbix", "pbir"])
def test_structure_contains_actual_table_columns_and_page_visuals(powerbi_system, source):
    plan(powerbi_system, ["powerbi_index"])
    args = (
        {"filename": "abc.pbix", "max_rows_per_table": 0}
        if source == "pbix"
        else {
            "filename": "Example.pbip",
            "companion_files": [name for name in project_documents() if name != "Example.pbip"],
        }
    )
    indexed = execute(powerbi_system, "powerbi_index", args)
    graph = powerbi_graph(powerbi_system[1], ViewScope(node_limit=200, edge_limit=500))
    nodes = {node["id"]: node for node in graph["nodes"]}
    parent_kind, child_kind = (
        ("powerbi_table", "powerbi_column")
        if source == "pbix"
        else ("powerbi_page", "powerbi_visual")
    )
    children = [node for node in graph["nodes"] if node["kind"] == child_kind]
    assert children and not graph["truncated"]
    for child in children:
        edge = next(edge for edge in graph["edges"] if edge["target"] == child["id"])
        assert nodes[edge["source"]]["kind"] == parent_kind
        assert child["locator"]["snapshot_id"] == indexed["snapshot_id"]
    if source == "pbix":
        assert not indexed["fidelity"]["compressed_model_data_read"]


def test_descriptor_schema_alone_does_not_claim_report_validation():
    raw = project_documents()["Example.pbip"].encode()
    facts = parse_powerbi("Example.pbip", raw, inspect_models=False)
    assert facts["features"]["validated_json_documents"] == 1
    assert not facts["fidelity"]["report_schema_validated"]
