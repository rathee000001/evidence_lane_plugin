"""PDF operations through the SDK dispatcher, verified Delta and owned workers."""

import base64
import json
import time

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.pdf_parsers import digest, open_reader
from evidence_lane_plugin.pdf_profile import read_snapshot
from evidence_lane_plugin.pdf_workers import pdf_worker_operations
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from .pdf_fixtures import generation, pdf_bytes
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414


@pytest.mark.parametrize("backend", ["pdfplumber", "pypdf", "poppler"])
def test_backend_selection_runs_registered_adapter_and_preserves_source(pdf_system, backend):
    plan(pdf_system, ["pdf_index"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf", "text_backend": backend})
    _, facts = read_snapshot(pdf_system[1], indexed["snapshot_id"])
    assert facts["native_evidence"]["backend"] == backend
    assert any("Native page remains searchable" in item["text"] for item in facts["items"])


def test_page_form_widget_and_ocr_graphs_have_separate_locators(pdf_system):
    from pathlib import Path

    plan(pdf_system, ["pdf_index", "pdf_ocr"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    execute(
        pdf_system, "pdf_ocr", {"snapshot_id": indexed["snapshot_id"], "pages": [1, 2]}, index=1
    )
    arguments = {"view_id": "pdf_ocr.structure", "scope": {"query": indexed["pdf_id"]}}
    preview = call(pdf_system, "lane_view_preview", arguments)
    assert preview.status == "ok", preview.error
    kinds = {node["kind"] for node in preview.result["graph"]["nodes"]}
    assert {"pdf_page", "pdf_widget", "pdf_form_field", "pdf_ocr_line", "pdf_ocr_run"} <= kinds
    edges = preview.result["graph"]["edges"]
    assert sum(edge["kind"] == "DISPLAYED_BY" for edge in edges) == 3
    assert any(edge["kind"] == "OCR_EVIDENCE" for edge in edges)
    value = preview.result
    published = call(
        pdf_system,
        "lane_view_refresh",
        {
            **arguments,
            "scope": value["scope"],
            "expected_generation": value["generation"],
            "contract_digest": value["contract_digest"],
            "source_digest": value["source_digest"],
            "formats": ["mmd", "dot"],
            "include_pointer": True,
        },
    )
    assert published.status == "ok", published.error
    assert {Path(row["path"]).name for row in published.result["files"]} == {
        "pdf.mmd",
        "pdf.dot",
        "pdf.pointer.json",
    }
    assert all(
        "/sectors/pdf_ocr/" in row["path"].replace("\\", "/") for row in published.result["files"]
    )
    read = call(
        pdf_system,
        "lane_view_read",
        {
            "view_id": arguments["view_id"],
            "snapshot_digest": published.result["snapshot_digest"],
            "include_content": True,
        },
    )
    assert read.status == "ok", read.error
    pointer = json.loads(read.result["contents"]["pointer"])
    assert pointer["native_and_ocr_evidence_separate"] and pointer["ocr_accuracy_verified"] is False


def test_docling_native_conversion_is_a_separate_completed_projection(pdf_system):
    (pdf_system[1].source_root / "fixture.pdf").write_bytes(pdf_bytes())
    plan(pdf_system, ["pdf_index", "pdf_enrich"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    enriched = execute(
        pdf_system, "pdf_enrich", {"snapshot_id": indexed["snapshot_id"], "max_pages": 2}, index=1
    )
    assert enriched["converter_status"] == "success" and enriched["model_assets_required"] is True
    source = (pdf_system[1].source_root / "fixture.pdf").read_bytes()
    before = {p: p.read_bytes() for p in pdf_system[1].root.rglob("*.sqlite*") if p.is_file()}
    read = call(
        pdf_system,
        "pdf_enrichment_read",
        {
            "enrichment_id": enriched["enrichment_id"],
            "include_structure": True,
            "max_structure_bytes": 524288,
        },
    )
    assert read.status == "ok", read.error
    conversion = read.result["result"]
    assert (
        "Native page remains searchable" in conversion["markdown"]
        and "Samples" in conversion["markdown"]
    )
    assert conversion["document"]["tables"] and conversion["source_sha256"] == digest(source)
    assert (pdf_system[1].source_root / "fixture.pdf").read_bytes() == source
    assert before == {
        p: p.read_bytes() for p in pdf_system[1].root.rglob("*.sqlite*") if p.is_file()
    }


@pytest.fixture
def pdf_system(tmp_path, pdf_assets):
    source = tmp_path / "source"
    source.mkdir()
    (source / "fixture.pdf").write_bytes(pdf_bytes(mixed=True))
    operations = (
        *pdf_worker_operations(),
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
            task_id="pdf-" + str(index),
            title=action,
            requested_outcome="Verify the exact PDF operation",
            profile="pdf_ocr",
            allowed_actions=[action],
            permitted_tools=[
                "Python",
                "SQLite_FTS5_BM25",
                "pypdf",
                "PyMuPDF",
                "pdfplumber",
                "ReportLab",
                "Pillow",
                "pypdfium2",
                "RapidOCR_ONNX_Runtime",
                "OpenCV",
                "pytesseract_Tesseract",
                "Poppler_pdftotext_pdfinfo",
                "Docling",
                "LangGraph_Mermaid_engine",
                "Python_Graphviz_DOT_engine",
            ],
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
            PlanCreate(title="PDF fixture", tasks=tasks), lease, actor_id="fixture"
        )


def execute(system, action, arguments, *, index=0, failure=None, transport=None):
    store = system[1]
    task = PlanStore(store).task("pdf-" + str(index), expected_revision=1)
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
    return json.loads(store.lane("plan").read_object(row["result_object"]))["result"]["result"]


def test_index_query_forms_and_byte_reads_are_in_own_pdf_lane(pdf_system):
    plan(pdf_system, ["pdf_index"])
    store = pdf_system[1]
    raw = (store.source_root / "fixture.pdf").read_bytes()
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    assert "/sectors/pdf_ocr/files/natural/" in indexed["natural_path"].replace("\\", "/")
    before = {path: path.read_bytes() for path in store.root.rglob("*.sqlite*") if path.is_file()}
    for collection, count in [("page", 2), ("form_field", 3), ("widget", 3), ("table", 1)]:
        response = call(
            pdf_system,
            "pdf_query",
            {"snapshot_id": indexed["snapshot_id"], "collection": collection},
        )
        assert response.status == "ok", response.error
        assert len(response.result["result"]["rows"]) == count
    response = call(
        pdf_system,
        "pdf_query",
        {
            "snapshot_id": indexed["snapshot_id"],
            "collection": "text",
            "query": "searchable",
            "page": 1,
        },
    )
    assert response.status == "ok" and response.result["result"]["rows"], response.error
    response = call(
        pdf_system,
        "pdf_read",
        {"snapshot_id": indexed["snapshot_id"], "representation": "original_source"},
    )
    assert (
        response.status == "ok"
        and base64.b64decode(response.result["result"]["content_base64"]) == raw
    )
    assert before == {
        path: path.read_bytes() for path in store.root.rglob("*.sqlite*") if path.is_file()
    }
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE name GLOB 'pdf_*'"
            ).fetchone()[0]
            == 0
        )


def test_edit_export_and_refresh_bind_exact_source_versions(pdf_system):
    plan(pdf_system, ["pdf_index", "pdf_edit", "pdf_export", "pdf_refresh"])
    store = pdf_system[1]
    source = store.source_root / "fixture.pdf"
    original = source.read_bytes()
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    edited = execute(
        pdf_system,
        "pdf_edit",
        {
            "snapshot_id": indexed["snapshot_id"],
            "expected_sha256": indexed["sha256"],
            "fields": {"applicant": "Verified PDF derivative", "consent": True},
        },
        index=1,
    )
    assert source.read_bytes() == original
    manifest, _ = read_snapshot(store, edited["snapshot_id"])
    assert manifest["source_object"] == digest(original)
    changed = store.lane("pdf_ocr").read_object(edited["sha256"])
    assert open_reader(changed).get_fields()["applicant"]["/V"] == "Verified PDF derivative"
    exported = execute(
        pdf_system,
        "pdf_export",
        {"snapshot_id": edited["snapshot_id"], "filename": "copy.pdf"},
        index=2,
    )
    assert (
        exported["after_sha256"]
        == digest((store.source_root / "copy.pdf").read_bytes())
        == edited["sha256"]
    )
    source.write_bytes(changed)
    refreshed = execute(
        pdf_system,
        "pdf_refresh",
        {"filename": "fixture.pdf", "expected_snapshot": edited["snapshot_id"]},
        index=3,
    )
    current, _ = read_snapshot(store, refreshed["snapshot_id"])
    assert (
        current["source_object"] == digest(changed)
        and current["previous_snapshot"] == edited["snapshot_id"]
    )
    assert store.lane("pdf_ocr").read_object(indexed["sha256"]) == original


def test_render_and_ocr_store_separate_derivatives_and_bounded_reads(pdf_system):
    plan(pdf_system, ["pdf_index", "pdf_render", "pdf_ocr"])
    store = pdf_system[1]
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    raster = execute(
        pdf_system,
        "pdf_render",
        {"snapshot_id": indexed["snapshot_id"], "pages": [1, 2], "dpi": 110},
        index=1,
    )
    response = call(pdf_system, "pdf_render_read", {"render_id": raster["render_id"], "page": 2})
    assert response.status == "ok" and base64.b64decode(
        response.result["result"]["content_base64"]
    ).startswith(b"\x89PNG")
    converted = execute(
        pdf_system,
        "pdf_ocr",
        {"snapshot_id": indexed["snapshot_id"], "pages": [1, 2], "dpi": 160},
        index=2,
    )
    assert converted["ocr_lines"] >= 3 and converted["evidence"]["native_text_preserved"]
    before = {path: path.read_bytes() for path in store.root.rglob("*.sqlite*") if path.is_file()}
    response = call(pdf_system, "pdf_ocr_read", {"ocr_id": converted["ocr_id"], "page": 2})
    assert response.status == "ok", response.error
    assert "4827" in " ".join(row["text"] for row in response.result["result"]["rows"])
    query = call(
        pdf_system,
        "pdf_query",
        {"snapshot_id": indexed["snapshot_id"], "collection": "ocr_line", "query": "125"},
    )
    assert query.status == "ok" and query.result["result"]["rows"], query.error
    assert before == {
        path: path.read_bytes() for path in store.root.rglob("*.sqlite*") if path.is_file()
    }


def test_generation_reopens_canonical_values_and_preserves_interactivity(pdf_system):
    plan(pdf_system, ["pdf_generate"])
    generated = execute(pdf_system, "pdf_generate", generation())
    raw = pdf_system[1].lane("pdf_ocr").read_object(generated["sha256"])
    reader = open_reader(raw)
    assert reader.get_fields()["status"]["/V"] == "Review"
    assert len(reader.pages[0]["/Annots"]) == 3
