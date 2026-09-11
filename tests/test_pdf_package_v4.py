"""The PDF package binds actual worker, schema and shared model contracts."""

import importlib.util
import json
from pathlib import Path

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.pdf_schema import PDF_MIGRATIONS
from evidence_lane_plugin.pdf_workers import pdf_worker_operations
from evidence_lane_plugin.sector_support import sector_package_contract

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"


def test_packaged_pdf_runtime_and_migrations_follow_executable_owner(tmp_path):
    engine = Engine(tmp_path / "runtime")
    contract = sector_package_contract("pdf_ocr", engine.registry)
    packaged = json.loads((PLUGIN / "authorities/project_sectors/pdf_ocr/manifest.v4.json").read_text())
    assert packaged["actions"] == contract["actions"]
    assert len(contract["actions"]) == 16
    from evidence_lane_plugin.artifact_contract import render_lane_view_worker

    workers = {row.name for row in pdf_worker_operations()} | {'render_lane_view'}
    assert render_lane_view_worker.__module__ == 'evidence_lane_plugin.artifact_contract'
    from evidence_lane_plugin.tool_routes import routes_for
    for action in contract["actions"]:
        spec = engine.registry.get(action["name"])
        for route in routes_for(spec):
            lane_values = dict(route.argument_values).get('lane_id')
            if lane_values is None or 'pdf_ocr' in lane_values:
                selected_workers = spec.worker_operations if route.worker_operations is None else route.worker_operations
                assert set(selected_workers) <= workers
    path = PLUGIN / "authorities/project_sectors/pdf_ocr/runtime.py"
    spec = importlib.util.spec_from_file_location("pdf_sector_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from evidence_lane_plugin.selector_schema import selector_migrations
    assert module.migrations() == (*PDF_MIGRATIONS, *selector_migrations("pdf_ocr")) and module.LANE_ID == "pdf_ocr"
    assert module.query_pdf.__module__ == "evidence_lane_plugin.pdf_profile"
    assert all(row.owner == "pdfocr" for row in PDF_MIGRATIONS)


def test_shared_bundle_has_pinned_pdf_models_and_authoring_dependency():
    shared = json.loads((PLUGIN / "toolchains/shared-toolchain.v4.json").read_text())
    for identity in ["rapidocr_models", "tesseract_languages", "docling_models"]:
        binding = shared["models"][identity]
        path = PLUGIN / binding["manifest"]
        assert sha256_file(path).lower() == binding["manifest_sha256"]
        manifest = json.loads(path.read_text())
        assert manifest["asset_id"] == identity and manifest["no_runtime_downloads"]
        assert all(len(row["sha256"]) == 64 and row["bytes"] > 0 for row in manifest["files"])
    tools = {row["tool_id"]: row for row in shared["entries"]}
    assert tools["ReportLab"]["packages"][0]["version"] == "5.0.1"
    assert tools["ReportLab"]["installation_required_for_windows_bundle"]
    assert set(tools["pytesseract_Tesseract"]["model_assets"]) == {
        "tesseract_languages",
        "tesseract_runtime",
    }
    assert tools["Poppler_pdftotext_pdfinfo"]["model_assets"] == ["poppler_runtime"]
    assert shared["full_bundle_ready"] is False
