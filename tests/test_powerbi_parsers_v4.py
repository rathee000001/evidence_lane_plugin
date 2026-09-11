"""Power BI schemas, closed package handling and actual native parser results."""

import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.powerbi_json_schema import validate_project_members
from evidence_lane_plugin.powerbi_parsers import bundle, package_members, parse_powerbi

from .powerbi_fixtures import model_document, project_documents, tmdl_documents

RUNTIME = Path(
    os.environ.get(
        "EVIDENCE_LANE_POWERBI_TEST_ROOT", ".work/qualification/powerbi-shared-fixture-r2"
    )
).resolve()
SAMPLES = Path(__file__).parent / "fixtures/powerbi"


@pytest.fixture
def powerbi_assets(monkeypatch):
    if os.name != "nt":
        pytest.skip("The native Power BI bundle is qualified on Windows.")
    assert (RUNTIME / "toolchains/asset-installation.v4.json").is_file(), (
        "Prepare the pinned Power BI candidate runtimes first."
    )
    monkeypatch.setenv("EVIDENCE_LANE_STUDIO_ROOT", str(RUNTIME))
    return RUNTIME


def test_source_fields_cannot_replace_governed_identifiers():
    model = model_document()
    model["model"]["tables"][0]["measures"][0].update(
        item_id="forged",
        kind="package_member",
        locator="../../outside",
        text="forged evidence",
        ordinal=-1,
    )
    facts = parse_powerbi("source.bim", json.dumps(model).encode(), inspect_models=False)
    measure = next(row for row in facts["items"] if row["kind"] == "measure")
    assert len(measure["item_id"]) == 64 and measure["item_id"] != "forged"
    assert measure["locator"] == "/model/tables/0/measures/0"
    assert measure["ordinal"] == 0 and measure["data"]["item_id"] == "forged"
    assert measure["text"] != "forged evidence"


@pytest.mark.parametrize(
    "path", ["../outside.json", "/root.json", "a/./b.json", "a//b.json", "CON.json", "a\\b.json"]
)
def test_package_path_variants_are_rejected(path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("fixture.json")
        info.filename = path
        archive.writestr(info, b"{}")
    with pytest.raises(LaneError):
        package_members(out.getvalue())


def test_arbitrary_zip_is_not_claimed_as_a_powerbi_package():
    with pytest.raises(LaneError, match="bounded format") as error:
        parse_powerbi("not-a-report.pbix", bundle({"ordinary.txt": b"ordinary"}))
    assert error.value.code == "POWERBI_PACKAGE_CONTENT_UNRECOGNIZED"


def test_duplicate_keys_and_nonfinite_numbers_are_rejected():
    for raw in (b'{"model":{},"model":{}}', b'{"model":{"value":1e999}}'):
        with pytest.raises(LaneError):
            parse_powerbi("source.bim", raw, inspect_models=False)


def test_offline_pbir_schemas_and_project_closure():
    members = {name: text.encode() for name, text in project_documents().items()}
    result = validate_project_members(members, "Example.pbip")
    assert result["project_closure_checked"] and result["report_json_schema_validated"]
    assert not result["external_references_opened"] and not result["rendering_verified"]
    page = "Example.Report/definition/pages/Main/page.json"
    data = json.loads(members[page])
    data["height"] = "not a number"
    with pytest.raises(LaneError) as error:
        validate_project_members({**members, page: json.dumps(data).encode()}, "Example.pbip")
    assert error.value.code == "POWERBI_SCHEMA_VALIDATION_FAILED"
    missing = dict(members)
    missing.pop("Example.SemanticModel/definition.pbism")
    with pytest.raises(LaneError) as error:
        validate_project_members(missing, "Example.pbip")
    assert error.value.code == "POWERBI_PROJECT_REFERENCE_MISSING"


def test_unadmitted_schema_url_is_not_fetched():
    members = {name: text.encode() for name, text in project_documents().items()}
    doc = json.loads(members["Example.pbip"])
    doc["$schema"] = "http://127.0.0.1:1/private"
    with pytest.raises(LaneError) as error:
        validate_project_members(
            {**members, "Example.pbip": json.dumps(doc).encode()}, "Example.pbip"
        )
    assert error.value.code == "POWERBI_SCHEMA_UNSUPPORTED"


def test_native_bim_and_tmdl_metadata_are_not_dax_execution(powerbi_assets):
    bim = parse_powerbi("source.bim", json.dumps(model_document()).encode())
    tmdl = parse_powerbi(
        "model.zip", bundle({name: text.encode() for name, text in tmdl_documents().items()})
    )
    for facts in (bim, tmdl):
        assert facts["counts"]["table"] == 1 and facts["counts"]["measure"] == 1
        assert facts["fidelity"]["model_metadata_deserialized"]
        assert not facts["fidelity"]["dax_evaluated"] and not facts["fidelity"]["layout_verified"]
        assert all(
            row["engine"] == "Microsoft.AnalysisServices.TOM" for row in facts["native_evidence"]
        )


def test_real_pbix_decodes_bounded_stored_values(powerbi_assets):
    raw = (SAMPLES / "abc.pbix").read_bytes()
    facts = parse_powerbi("abc.pbix", raw, max_rows_per_table=2)
    values = [
        row["data"]["values"]
        for row in facts["items"]
        if row["kind"] == "model_row" and row["table_name"] == "ABC"
    ]
    assert values == [[1, 5], [2, 6]]
    assert (
        facts["fidelity"]["compressed_model_data_read"]
        and facts["fidelity"]["bounded_stored_row_samples"]
    )
    assert not facts["fidelity"]["dax_evaluated"] and not facts["fidelity"]["live_data_read"]
    assert facts["native_evidence"][0]["process_memory_limit_bytes"] == 1_073_741_824


def test_thin_report_is_metadata_without_a_live_connection(powerbi_assets):
    facts = parse_powerbi("thin.pbix", (SAMPLES / "live-connection-pbiservice.pbix").read_bytes())
    assert facts["counts"]["report"] >= 1 and facts["counts"]["reference"] >= 1
    assert not facts["native_evidence"] and not facts["fidelity"]["live_data_read"]
    assert not facts["fidelity"]["compressed_model_data_read"]
