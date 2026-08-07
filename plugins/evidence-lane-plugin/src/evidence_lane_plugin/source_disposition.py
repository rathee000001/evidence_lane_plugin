"""Deterministic disposition receipt for every supplied all-source authority."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import (
    SQLITE_BRAIN_BUILDER_MASTER_TOPOLOGY_AUTHORITY_SHA256,
    SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256,
)

CORRECTION_BASE_SOURCE_COMMIT = "6020563094154ff780705cbed85f453425884914"
DISPOSITIONS = (
    "ADOPTED_CONTRACT_AND_TEST",
    "BOUNDED_PROVENANCE_ROLE",
    "EVIDENCE_BACKED_REJECTION",
)

_EXACT_COUNTERPART_ZIPS = {10, 16, 18, 20, 39, 44, 46}
_SQLITE_BRAIN_INTAKE = {1, 3, 5, 7, 8, 15, 17, 23, 34}
_IMAGE_OCR_INTAKE = {11, 12, 13, 14}
_CSV_DATA_INTAKE = {32, 41, 47}
_DELTA_AND_ROUTE_INTAKE = {29, 30, 31}
_SPECIFIC_ADOPTIONS: dict[int, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    9: (
        "GIT_BRANCH_DEPLOY_PREFLIGHT_V1",
        ("evidence/implementation_v41/DELTA076B_CI_ADAPTERS_RECEIPT.json",),
        ("tests/test_security_persistence.py",),
    ),
    19: (
        "CODEQL_NEGATIVE_SCAN_RECEIPT_V1",
        ("evidence/implementation_v41/DELTA076B_CI_ADAPTERS_RECEIPT.json",),
        ("tests/test_ci_workflow_adapters.py",),
    ),
    26: (
        "DETERMINISTIC_RENDER_FAILURE_EVIDENCE_V1",
        ("evidence/implementation_v41/DELTA074_DETERMINISTIC_RENDER_RECEIPT.json",),
        ("tests/test_topology_rendering.py",),
    ),
    37: (
        "GH_AW_POLICY_PINNING_AND_OUTPUT_V1",
        ("evidence/implementation_v41/DELTA076A_PINNED_CI_MCP_GH_AW_RECEIPT.json",),
        ("tests/test_github_automation_governance.py",),
    ),
    38: (
        "MCP_EXPOSURE_ANNOTATION_AND_SANITIZATION_V1",
        ("evidence/implementation_v41/DELTA076A_PINNED_CI_MCP_GH_AW_RECEIPT.json",),
        ("tests/test_mcp_plugin.py",),
    ),
    42: (
        "SOURCE_GRAPH_IDENTITY_DIFF_AND_IMPACT_V1",
        (
            "evidence/implementation_v41/DELTA071_POLYGLOT_GRAPH_RECEIPT.json",
            "evidence/implementation_v41/DELTA072_GIT_HISTORY_IMPACT_RECEIPT.json",
        ),
        ("tests/test_source_graph.py", "tests/test_source_git_history.py"),
    ),
    43: (
        "DISPOSABLE_FIXTURE_AND_SAFE_OUTPUT_V1",
        ("evidence/implementation_v41/DELTA076B_CI_ADAPTERS_RECEIPT.json",),
        ("tests/test_ci_workflow_adapters.py",),
    ),
    48: (
        "ENV_UOP_MODE_GOVERNANCE_V1",
        ("evidence/implementation_v41/DELTA070_MODE_GOVERNANCE_RECEIPT.json",),
        ("tests/test_operating_modes.py",),
    ),
}

_UPSTREAM_DISPOSITIONS = (
    {
        "repository": "https://github.com/github/gh-aw",
        "commit": "5107b7bd547ce124f69084dc623e45a539cd17f5",
        "disposition": "ADOPTED_CONTRACT_AND_TEST",
        "contract_id": "GH_AW_POLICY_PINNING_AND_OUTPUT_V1",
        "test_files": ["tests/test_github_automation_governance.py"],
    },
    {
        "repository": "https://github.com/github/gh-aw-mcpg",
        "commit": "0edcc73107c5a08b5bd8cfd489e68400b5ef95db",
        "disposition": "ADOPTED_CONTRACT_AND_TEST",
        "contract_id": "MCP_EXPOSURE_ANNOTATION_AND_SANITIZATION_V1",
        "test_files": ["tests/test_mcp_plugin.py"],
    },
    {
        "repository": "https://github.com/github/copilot-sdk",
        "commit": "4e95deee58f2712a9e75e66a1ffba4460cc9a1d5",
        "disposition": "BOUNDED_PROVENANCE_ROLE",
        "contract_id": "AGENT_SESSION_IDENTITY_RESEARCH_ONLY_V1",
        "test_files": ["tests/test_github_automation_governance.py"],
    },
    {
        "repository": "https://github.com/github/gh-aw-threat-detection",
        "commit": "09cc2eed706368655776af394eab7146629f905a",
        "disposition": "ADOPTED_CONTRACT_AND_TEST",
        "contract_id": "POST_OUTPUT_ADVISORY_INSPECTION_V1",
        "test_files": ["tests/test_github_automation_governance.py"],
    },
    {
        "repository": "https://github.com/github/gh-aw-harness",
        "commit": "75ed171c12321e0cf4249a732c9860386bdc46a0",
        "disposition": "ADOPTED_CONTRACT_AND_TEST",
        "contract_id": "REGISTERED_AGENT_EXECUTION_HARNESS_V1",
        "test_files": ["tests/test_github_automation_governance.py"],
        "sqlite_continuity_harness_used": False,
    },
    {
        "repository": "https://github.com/open-webui/open-webui",
        "commit": "01f4282f1ffe0d6212f58d3afbeae21fffd0c4be",
        "disposition": "BOUNDED_PROVENANCE_ROLE",
        "contract_id": "INSPECTABLE_UI_CONCEPTUAL_REFERENCE_ONLY_V1",
        "test_files": ["tests/test_full_app_ui_conformance.py"],
    },
)

_SUPPLIED_TOPOLOGY_AUTHORITIES = (
    {
        "name": "generate_lane_mmd.py",
        "sha256": SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256,
        "disposition": "ADOPTED_CONTRACT_AND_TEST",
        "contract_id": "SQLITE_DERIVED_CODE_LOGICAL_TOPOLOGY_V1",
        "adopted_role": (
            "Bind the seven-entity code projection to an exact supplied generator "
            "identity while independently implementing the graph emitter."
        ),
        "test_files": [
            "tests/test_universal_lanes.py",
            "tests/test_topology_reconciliation.py",
        ],
        "external_bytes_imported": False,
    },
    {
        "name": "project_master_topology.mmd",
        "sha256": SQLITE_BRAIN_BUILDER_MASTER_TOPOLOGY_AUTHORITY_SHA256,
        "disposition": "ADOPTED_CONTRACT_AND_TEST",
        "contract_id": "SQLITE_DERIVED_STABLE_TOPOLOGY_PROFILE_V1",
        "adopted_role": (
            "Require an end-to-end SQLite-derived topology with stable identities, "
            "explicit confidence, coverage, impact, and distinct GitHub/Local profiles."
        ),
        "test_files": [
            "tests/test_public_dummy_lane_packages.py",
            "tests/test_universal_lanes.py",
        ],
        "external_bytes_imported": False,
    },
)


def _adopted_contract(
    ordinal: int,
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    if ordinal in _SQLITE_BRAIN_INTAKE:
        return (
            "READ_ONLY_SQLITE_BRAIN_AND_ARCHIVE_INTAKE_V1",
            (
                "evidence/implementation_v41/DELTA067A_ARCHIVE_INTAKE_RECEIPT.json",
                "evidence/implementation_v41/DELTA067B_SQLITE_FORENSIC_INTAKE_RECEIPT.json",
            ),
            ("tests/test_source_authority_registry.py", "tests/test_source_sqlite.py"),
        )
    if ordinal in _IMAGE_OCR_INTAKE:
        return (
            "IMAGE_OCR_OBSERVED_VS_INFERRED_INTAKE_V1",
            ("evidence/implementation_v41/DELTA078_ALL_SOURCE_LANE_HISTORY_RECONCILIATION_RECEIPT.json",),
            ("tests/test_full_reconciliation.py",),
        )
    if ordinal in _CSV_DATA_INTAKE:
        return (
            "CSV_SCHEMA_ROW_AND_PROVENANCE_INTAKE_V1",
            ("evidence/implementation_v41/DELTA078_ALL_SOURCE_LANE_HISTORY_RECONCILIATION_RECEIPT.json",),
            ("tests/test_full_reconciliation.py",),
        )
    if ordinal in _DELTA_AND_ROUTE_INTAKE:
        return (
            "ROUTE_AND_DELTA_IDENTITY_RECONCILIATION_V1",
            ("evidence/implementation_v41/DELTA078_ALL_SOURCE_LANE_HISTORY_RECONCILIATION_RECEIPT.json",),
            ("tests/test_full_reconciliation.py", "tests/test_full_app_ui_conformance.py"),
        )
    return _SPECIFIC_ADOPTIONS.get(ordinal)


def build_all_source_disposition_receipt(repository_root: str | Path) -> dict[str, Any]:
    """Bind every v41 supplied source and every later upstream reference."""

    root = Path(repository_root).resolve()
    evidence_root = root / "evidence" / "implementation_v41"
    crosswalk_path = evidence_root / "ALL_SOURCE_AUTHORITY_CROSSWALK.json"
    archive_path = evidence_root / "DELTA067A_ARCHIVE_INTAKE_RECEIPT.json"
    sqlite_path = evidence_root / "DELTA067B_SQLITE_FORENSIC_INTAKE_RECEIPT.json"
    crosswalk = json.loads(crosswalk_path.read_text(encoding="utf-8"))
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    sqlite = json.loads(sqlite_path.read_text(encoding="utf-8"))
    sources = crosswalk.get("sources")
    require(
        crosswalk.get("schema") == "evidence-lane.all-source-authority-crosswalk.v1"
        and isinstance(sources, list)
        and len(sources) == crosswalk.get("expected_source_count") == 48,
        "SOURCE_DISPOSITION_CROSSWALK_INVALID",
        "The historical all-source crosswalk must contain the exact 48 rows.",
        status="BLOCKED",
    )
    archive_by_name = {item["name"]: item for item in archive["archives"]}
    sqlite_names = {
        item["name"] for item in sqlite["source_authorities"] if item["assets"] > 0
    }

    entries: list[dict[str, Any]] = []
    for expected_ordinal, row in enumerate(sources, start=1):
        ordinal = int(row.get("ordinal", 0))
        require(
            ordinal == expected_ordinal,
            "SOURCE_DISPOSITION_ORDER_INVALID",
            "Supplied source ordinals must remain exact and contiguous.",
            status="BLOCKED",
            expected_ordinal=expected_ordinal,
            observed_ordinal=ordinal,
        )
        name = str(row["name"])
        if ordinal in _EXACT_COUNTERPART_ZIPS:
            archive_entry = archive_by_name.get(name)
            require(
                archive_entry is not None
                and archive_entry.get("use_state")
                == "SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART",
                "SOURCE_DISPOSITION_REJECTION_UNPROVEN",
                "A rejected archive must have an exact extracted counterpart receipt.",
                status="BLOCKED",
                ordinal=ordinal,
                name=name,
            )
            disposition = "EVIDENCE_BACKED_REJECTION"
            contract_id = "EXACT_EXTRACTED_COUNTERPART_SKIP_V1"
            evidence_files = [
                "evidence/implementation_v41/DELTA067A_ARCHIVE_INTAKE_RECEIPT.json"
            ]
            test_files = ["tests/test_source_authority_registry.py"]
            disposition_detail = (
                "Reject independent payload use and double-counting; preserve the "
                "archive identity because exact extracted equivalence passed."
            )
        else:
            adopted = _adopted_contract(ordinal)
            if adopted is not None:
                disposition = "ADOPTED_CONTRACT_AND_TEST"
                contract_id, evidence_refs, test_refs = adopted
                evidence_files = list(evidence_refs)
                test_files = list(test_refs)
                disposition_detail = str(row["planned_use"])
            else:
                disposition = "BOUNDED_PROVENANCE_ROLE"
                contract_id = "READ_ONLY_PROVENANCE_AND_INDEPENDENT_REIMPLEMENTATION_V1"
                evidence_files = [
                    "evidence/implementation_v41/ALL_SOURCE_AUTHORITY_CROSSWALK.json",
                    "evidence/implementation_v41/DELTA069_SOURCE_IDENTITY_MATRIX_RECEIPT.json",
                ]
                test_files = ["tests/test_source_authority_registry.py"]
                disposition_detail = str(row["planned_use"])
        for reference in (*evidence_files, *test_files):
            require(
                (root / reference).is_file(),
                "SOURCE_DISPOSITION_EVIDENCE_MISSING",
                "A disposition cites a repository file that is missing.",
                status="BLOCKED",
                reference=reference,
            )
        entry = {
            "ordinal": ordinal,
            "name": name,
            "kind": row["kind"],
            "license_state": row["license_state"],
            "disposition": disposition,
            "contract_id": contract_id,
            "disposition_detail": disposition_detail,
            "forbidden_use": row["rejected_use"],
            "contains_verified_sqlite_assets": name in sqlite_names,
            "evidence_files": evidence_files,
            "test_files": test_files,
            "crosswalk_row_sha256": sha256_bytes(canonical_json_bytes(row)),
        }
        entry["entry_sha256"] = sha256_bytes(canonical_json_bytes(entry))
        entries.append(entry)

    counts = Counter(item["disposition"] for item in entries)
    require(
        set(counts) == set(DISPOSITIONS),
        "SOURCE_DISPOSITION_CLASS_MISSING",
        "The correction must exercise all three explicit disposition classes.",
        status="BLOCKED",
    )
    body: dict[str, Any] = {
        "schema": "evidence-lane.all-source-disposition-receipt.v1",
        "status": "PASS",
        "correction_base_source_commit": CORRECTION_BASE_SOURCE_COMMIT,
        "historical_crosswalk_preserved": True,
        "source_payloads_mutated": False,
        "source_crosswalk_sha256": sha256_file(crosswalk_path),
        "archive_intake_receipt_sha256": sha256_file(archive_path),
        "sqlite_intake_receipt_sha256": sha256_file(sqlite_path),
        "source_count": len(entries),
        "disposition_counts": dict(sorted(counts.items())),
        "sources": entries,
        "upstream_reference_count": len(_UPSTREAM_DISPOSITIONS),
        "upstream_references": list(_UPSTREAM_DISPOSITIONS),
        "supplied_topology_authority_count": len(_SUPPLIED_TOPOLOGY_AUTHORITIES),
        "supplied_topology_authorities": list(_SUPPLIED_TOPOLOGY_AUTHORITIES),
        "safety": {
            "unlicensed_bytes_imported": False,
            "exact_counterpart_archives_double_counted": False,
            "generator_identity_inferred_from_filename": False,
            "sqlite_continuity_harness_distinct_from_agent_execution_harness": True,
            "supplied_topology_reference_bytes_imported": False,
        },
    }
    body["source_disposition_set_sha256"] = sha256_bytes(
        canonical_json_bytes(entries)
    )
    body["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body
