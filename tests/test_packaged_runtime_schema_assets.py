from __future__ import annotations

from pathlib import Path

from evidence_lane_plugin import canon_task_graph, lanes

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_SCHEMAS = ROOT / "plugins" / "evidence-lane-plugin" / "schemas"
PACKAGE_SCHEMAS = Path(lanes.__file__).resolve().parent / "schemas"

RUNTIME_SCHEMA_ASSETS = (
    Path("lane-schema-registry.v001.json"),
    Path("lane-schema-evolution.v001.json"),
    Path("lane-artifact-contract.v001.json"),
    Path("canon/canon-ledger.v1.sql"),
    Path("canon/canon-consequence-graph.v1.sql"),
    Path("canon/canon-receipts.v1.schema.json"),
    Path("canon/canon-schema-manifest.v1.json"),
    Path("memory/project-memory.v1.sql"),
)


def test_runtime_schema_authorities_are_package_owned() -> None:
    assert lanes.LANE_SCHEMA_REGISTRY_PATH == (
        PACKAGE_SCHEMAS / "lane-schema-registry.v001.json"
    )
    assert lanes.LANE_SCHEMA_EVOLUTION_POLICY_PATH == (
        PACKAGE_SCHEMAS / "lane-schema-evolution.v001.json"
    )
    assert lanes.LANE_ARTIFACT_ROLE_REGISTRY_PATH == (
        PACKAGE_SCHEMAS / "lane-artifact-contract.v001.json"
    )
    assert canon_task_graph._CANON_SCHEMA_ROOT == PACKAGE_SCHEMAS / "canon"


def test_repository_schema_projection_matches_packaged_runtime_bytes() -> None:
    for relative_path in RUNTIME_SCHEMA_ASSETS:
        repository_asset = REPOSITORY_SCHEMAS / relative_path
        package_asset = PACKAGE_SCHEMAS / relative_path

        assert repository_asset.is_file(), relative_path.as_posix()
        assert package_asset.is_file(), relative_path.as_posix()
        assert package_asset.read_bytes() == repository_asset.read_bytes(), (
            relative_path.as_posix()
        )
