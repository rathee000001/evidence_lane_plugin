from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_rocm_metadata_wheel.py"
SPEC = importlib.util.spec_from_file_location("build_rocm_metadata_wheel", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
RELEASE_SCRIPT = (
    ROOT
    / "plugins/evidence-lane-plugin/scripts/build_first_detection_release.py"
)
RELEASE_SPEC = importlib.util.spec_from_file_location(
    "build_first_detection_release_for_rocm_test", RELEASE_SCRIPT
)
assert RELEASE_SPEC is not None and RELEASE_SPEC.loader is not None
RELEASE_BUILDER = importlib.util.module_from_spec(RELEASE_SPEC)
RELEASE_SPEC.loader.exec_module(RELEASE_BUILDER)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_current_rocm_wheel_and_receipt_are_exact_mirrors():
    contract_root = ROOT / "contracts/optional-runtimes"
    plugin_root = ROOT / "plugins/evidence-lane-plugin/toolchains/providers"
    receipt_paths = [
        contract_root / "rocm-source-build.json",
        plugin_root / "rocm-source-build.json",
    ]
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in receipt_paths]
    assert receipts[0] == receipts[1]
    receipt = receipts[0]
    body = dict(receipt)
    assert body.pop("receipt_sha256") == hashlib.sha256(
        BUILDER.canonical(body)
    ).hexdigest()
    assert receipt["status"] == "REPRODUCIBLE_LICENSE_RESTORED_WHEEL"
    assert receipt["upstream_commit"] == BUILDER.UPSTREAM_COMMIT
    assert receipt["source_sha256"] == BUILDER.SOURCE_HASH
    assert receipt["source_setup_sha256"] == BUILDER.SOURCE_SETUP_HASH
    assert (
        receipt["source_setup_normalized_sha256"]
        == BUILDER.SOURCE_SETUP_NORMALIZED_HASH
    )
    assert receipt["upstream_license_sha256"] == BUILDER.UPSTREAM_LICENSE_HASH
    assert receipt["reproducibility"]["byte_identical"] is True
    assert len(set(receipt["reproducibility"]["wheel_sha256s"])) == 1

    wheel_paths = [
        contract_root / receipt["wheel_file"],
        plugin_root / receipt["wheel_file"],
    ]
    assert {sha256(path) for path in wheel_paths} == {receipt["wheel_sha256"]}
    license_bytes = (
        ROOT / "contracts/optional-runtimes/licenses/rocm/LICENSE"
    ).read_bytes()
    observed = BUILDER.inspect_wheel(wheel_paths[0], license_bytes)
    assert observed == receipt["wheel_license"]

    manifest_paths = [contract_root / "rocm.json", plugin_root / "rocm.json"]
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in manifest_paths]
    assert manifests[0] == manifests[1]
    package = next(row for row in manifests[0]["packages"] if row["name"] == "rocm")
    assert package["sha256"] == receipt["wheel_sha256"]
    assert package["source_build_receipt"] == "rocm-source-build.json"
    assert package["source_build_receipt_sha256"] == sha256(receipt_paths[0])


def test_same_release_rocm_wheels_link_to_the_nested_vendor_license_corpus():
    devel_hash = "d" * 64
    projects = {
        "hipblas",
        "hipblas-common",
        "hipblaslt",
        "hipfft",
        "hiprand",
        "hipsolver",
        "hipsparse",
        "miopen-hip",
        "rocblas",
        "rocfft",
        "rocm-core",
        "rocrand",
        "rocsolver",
        "rocsparse",
    }

    def evidence(project: str) -> dict[str, object]:
        return {
            "path": (
                "rocm_sdk_devel/_devel.tar/_rocm_sdk_devel/share/doc/"
                + project
                + "/LICENSE.md"
            ),
            "bytes": 1,
            "sha256": hashlib.sha256(project.encode()).hexdigest(),
            "source_wheel": "rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
            "source_wheel_sha256": devel_hash,
            "source_container": "rocm_sdk_devel/_devel.tar",
        }

    llvm = {
        **evidence("llvm-placeholder"),
        "path": (
            "rocm_sdk_devel/_devel.tar/_rocm_sdk_devel/lib/llvm/"
            "include/llvm/Support/LICENSE.TXT"
        ),
    }
    records = [
        {
            "name": "rocm-sdk-core",
            "version": "7.2.1",
            "wheel": "rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
            "wheel_sha256": "c" * 64,
            "license_expression_or_classifiers": [],
            "license_files": [],
        },
        {
            "name": "rocm-sdk-devel",
            "version": "7.2.1",
            "wheel": "rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
            "wheel_sha256": devel_hash,
            "license_expression_or_classifiers": [],
            "license_files": [*(evidence(project) for project in projects), llvm],
        },
        {
            "name": "rocm-sdk-libraries-custom",
            "version": "7.2.1",
            "wheel": "rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl",
            "wheel_sha256": "e" * 64,
            "license_expression_or_classifiers": [],
            "license_files": [],
        },
    ]
    RELEASE_BUILDER._link_rocm_release_license_corpus(records)
    core, _, libraries = records
    assert core["license_evidence_link"]["covered_projects"] == ["rocm-core"]
    assert len(core["license_files"]) == 2
    assert libraries["license_evidence_link"]["source_wheel_sha256"] == devel_hash
    assert set(libraries["license_evidence_link"]["covered_projects"]) == (
        projects - {"rocm-core"}
    )
    assert len(libraries["license_files"]) == len(projects) - 1
