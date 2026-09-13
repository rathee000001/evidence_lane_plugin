"""Release-bound, same-repository first-detection installation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.installation_layout import StudioInstallation
from evidence_lane_plugin.installed_providers import load_installed_providers
from evidence_lane_plugin.shared_native_tools import resolve_native_tool
from evidence_lane_plugin.shared_tool_assets import resolve_shared_asset
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_script(
    "first_detection_release_builder",
    PLUGIN / "scripts/build_first_detection_release.py",
)
installer_module = load_script(
    "evidence_lane_first_detection",
    PLUGIN / "scripts/first_detection.py",
)


COMPONENTS = [
    ("core-engine-studio", "always"),
    ("native-tools", "always"),
    ("node-runtime", "always"),
    ("model-assets", "always"),
    ("powerbi-runtime", "always"),
    ("provider-cpu", "always"),
    ("provider-cuda", "nvidia_cuda_compatible"),
    ("provider-directml", "directml_compatible"),
    ("provider-rocm", "amd_rocm_compatible"),
    ("ghostscript-runtime", "explicit_ghostscript_license"),
]


def write(path: Path, content: bytes | str = b"fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)


def write_wheel(path: Path, name: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dist = name.replace("-", "_") + "-" + version + ".dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            dist + "/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nLicense-Expression: MIT\n",
        )
        archive.writestr(dist + "/licenses/LICENSE", "fixture MIT text\n")


def fixture_plugin(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    plugin = tmp_path / "marketplace/plugins/evidence-lane-plugin"
    write(
        plugin / ".codex-plugin/plugin.json",
        json.dumps({"name": "evidence-lane-plugin", "version": "4.0.1"}) + "\n",
    )
    for name in (
        "run_mcp.py",
        "run_engine.py",
        "verify_installed_runtime.py",
        "register_installed_runtime.py",
        "first_detection.py",
    ):
        write(plugin / "scripts" / name, f"# {name}\n")
    write(
        plugin
        / "scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.exe",
        b"fixture launcher",
    )
    write(plugin / "src/evidence_lane_plugin/__init__.py", "__version__='4.0.1'\n")
    cpu_lock = plugin / "toolchains/providers/cpu.lock.txt"
    write(cpu_lock, "fixture provider lock\n")
    cpu_lock_sha = hashlib.sha256(cpu_lock.read_bytes()).hexdigest()
    cpu_manifest = plugin / "toolchains/providers/cpu.json"
    write(
        cpu_manifest,
        json.dumps(
            {
                "runtime_id": "cpu",
                "lock_file": "cpu.lock.txt",
                "lock_sha256": cpu_lock_sha,
                "packages": [],
                "operations": [],
            }
        )
        + "\n",
    )
    cpu_manifest_sha = hashlib.sha256(cpu_manifest.read_bytes()).hexdigest()
    write(
        plugin / "toolchains/licenses/retained-install-license-index.v4.json",
        json.dumps(
            {
                "records": [
                    {
                        "tool_id": "fixture-native",
                        "evidence_sha256": "c" * 64,
                    }
                ]
            }
        )
        + "\n",
    )
    materialization = [
        {
            "operation": "create_venv",
            "component_id": "core-engine-studio",
            "interpreter": "toolchains/python/base/cp314/python.exe",
            "target": "engine/venv",
        },
        {
            "operation": "pip_install",
            "component_id": "core-engine-studio",
            "environment": "engine/venv",
            "wheelhouse": "install-inputs/engine/wheelhouse",
            "requirements": ["install-inputs/engine/core.offline.lock.txt"],
        },
    ]
    for provider, condition in (
        ("cpu", "always"),
        ("cuda", "nvidia_cuda_compatible"),
        ("directml", "directml_compatible"),
        ("rocm", "amd_rocm_compatible"),
    ):
        version = "cp314" if provider in {"cpu", "cuda"} else "cp312"
        environment = (
            f"toolchains/python/providers/cpu-{cpu_lock_sha[:16]}"
            if provider == "cpu"
            else f"toolchains/python/providers/{provider}/venv"
        )
        materialization.extend(
            [
                {
                    "operation": "create_venv",
                    "component_id": f"provider-{provider}",
                    "interpreter": f"toolchains/python/base/{version}/python.exe",
                    "target": environment,
                },
                {
                    "operation": "pip_install",
                    "component_id": f"provider-{provider}",
                    "environment": environment,
                    "wheelhouse": f"install-inputs/providers/{provider}/wheelhouse",
                    "requirements": [
                        f"install-inputs/providers/{provider}/{provider}.offline.lock.txt"
                    ],
                },
            ]
        )
    plan = builder.sealed(
        {
            "schema": "evidence-lane.first-detection-bundle-plan.v4",
            "status": "COMPLETE_PREINSTALL_SOURCE_PLAN",
            "plugin": {
                "id": "evidence-lane-plugin",
                "version": "4.0.1",
                "repository": builder.REPOSITORY,
                "sparse_root": builder.SPARSE_ROOT,
            },
            "components": [
                {
                    "component_id": component,
                    "condition": condition,
                    "release_asset_required": True,
                }
                for component, condition in COMPONENTS
            ],
            "materialization_steps": materialization,
            "native_inputs": [
                {
                    "tool_id": "ripgrep",
                    "version": "fixture",
                    "release_component": "native-tools",
                    "installed_executables": ["ripgrep/rg.exe"],
                    "catalog_tool_id": "fixture-native",
                }
            ],
            "asset_groups": [
                {
                    "asset_id": "embedding_snapshot",
                    "component_id": "model-assets",
                    "path": "toolchains/models/embedding_snapshot",
                    "source_manifest": None,
                }
            ],
            "provider_environments": [
                {
                    "runtime_id": "cpu",
                    "manifest": "toolchains/providers/cpu.json",
                    "manifest_sha256": cpu_manifest_sha,
                    "lock": "toolchains/providers/cpu.lock.txt",
                    "lock_sha256": cpu_lock_sha,
                }
            ],
            "entrypoints": {
                "runtime_python": "engine/venv/Scripts/python.exe",
                "runtime_pythonw": "engine/venv/Scripts/pythonw.exe",
                "plugin_root": "plugin",
                "studio_launcher_executable": "app/EvidenceLaneStudio.exe",
                "mcp_launcher": "plugin/scripts/run_mcp.py",
                "engine_launcher": "plugin/scripts/run_engine.py",
                "installation_self_test": (
                    "plugin/scripts/verify_installed_runtime.py"
                ),
                "installation_registration": (
                    "plugin/scripts/register_installed_runtime.py"
                ),
            },
            "external_service_configuration": [
                {
                    "tool_id": "fixture-service",
                    "credentials_in_release_assets": False,
                }
            ],
        }
    )
    write(
        plugin / "provisioning/full-bundle-plan.v4.json",
        json.dumps(plan, indent=2) + "\n",
    )
    template = builder.sealed(
        {
            "schema": "evidence-lane.first-detection-release-binding.v4",
            "status": "SOURCE_TEMPLATE_UNBOUND",
            "installation_enabled": False,
            "plugin_id": "evidence-lane-plugin",
            "plugin_version": "4.0.1",
            "repository": builder.REPOSITORY,
            "sparse_root": builder.SPARSE_ROOT,
            "release_ref": None,
            "bundle_plan": "provisioning/full-bundle-plan.v4.json",
            "bundle_plan_sha256": builder.sha256(
                plugin / "provisioning/full-bundle-plan.v4.json"
            ),
            "source_manifest": None,
            "source_manifest_sha256": None,
            "assets": [],
        }
    )
    write(
        plugin / "provisioning/release-binding.v4.json",
        json.dumps(template, indent=2) + "\n",
    )

    component_sources: dict[str, Path] = {}
    for component, _ in COMPONENTS:
        source = tmp_path / "component-sources" / component
        write(
            source / "install-inputs/component-markers" / f"{component}.txt"
        )
        component_sources[component] = source
    core = component_sources["core-engine-studio"]
    write(core / "toolchains/python/base/cp314/python.exe")
    write(core / "toolchains/python/base/cp312/python.exe")
    engine_wheel = core / "install-inputs/engine/wheelhouse/fixture.whl"
    write(engine_wheel)
    write(
        core / "install-inputs/engine/core.offline.lock.txt",
        "fixture==1 --hash=sha256:"
        + hashlib.sha256(engine_wheel.read_bytes()).hexdigest(),
    )
    native = component_sources["native-tools"]
    write(native / "toolchains/bin/ripgrep/rg.exe")
    write(native / "toolchains/bin/git/cmd/git.exe")
    node = component_sources["node-runtime"]
    write(node / "toolchains/node/node.exe")
    write(node / "toolchains/node/node_modules/.bin/mmdc.cmd")
    write(
        node
        / "toolchains/node/chrome-headless-shell/chrome-headless-shell.exe"
    )
    models = component_sources["model-assets"]
    write(models / "toolchains/models/embedding_snapshot/config.json", "{}\n")
    for provider in ("cpu", "cuda", "directml", "rocm"):
        source = component_sources[f"provider-{provider}"]
        wheel = source / f"install-inputs/providers/{provider}/wheelhouse/fixture.whl"
        write(wheel)
        write(
            source
            / f"install-inputs/providers/{provider}/{provider}.offline.lock.txt",
            "fixture==1 --hash=sha256:"
            + hashlib.sha256(wheel.read_bytes()).hexdigest(),
        )
    archives: dict[str, Path] = {}
    for component, _ in COMPONENTS:
        archive = tmp_path / "assets" / f"evidence-lane-{component}-4.0.1.zip"
        builder.build_component_archive(component, component_sources[component], archive)
        archives[component] = archive
    plan_value = json.loads(
        (plugin / "provisioning/full-bundle-plan.v4.json").read_text()
    )
    by_id = {row["component_id"]: row for row in plan_value["components"]}
    asset_rows = []
    for component in sorted(archives):
        archive = archives[component]
        manifest = builder.read_component_archive(archive)
        manifest_bytes = json.dumps(manifest, indent=2).encode() + b"\n"
        asset_rows.append(
            {
                "component_id": component,
                "asset_id": (
                    f"{component}.part-{manifest['part_index']:03d}-"
                    f"of-{manifest['part_count']:03d}"
                ),
                "part_index": manifest["part_index"],
                "part_count": manifest["part_count"],
                "condition": by_id[component]["condition"],
                "filename": archive.name,
                "bytes": archive.stat().st_size,
                "sha256": builder.sha256(archive),
                "component_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "file_count": manifest["file_count"],
                "payload_bytes": manifest["total_bytes"],
            }
        )
    assets_sha = hashlib.sha256(builder.canonical(asset_rows)).hexdigest()
    release_ref = f"refs/tags/evidence-lane-v4.0.1-bundle-{assets_sha[:16]}"
    builder.bind_release(
        plugin_root=plugin,
        release_ref=release_ref,
        asset_paths=archives,
    )
    return plugin, archives


def fake_runner(log: list[list[str]]):
    def run(command, cwd, environment):
        values = [str(value) for value in command]
        log.append(values)
        if values[1:5] == ["-I", "-m", "venv", "--copies"]:
            target = Path(values[5])
            write(target / "Scripts/python.exe")
            write(target / "Scripts/pythonw.exe")
            write(
                target / "pyvenv.cfg",
                f"home = {Path(values[0]).parent}\ncommand = {' '.join(values)}\n",
            )
        return 0

    return run


def release_ref_for_assets(
    plugin: Path, asset_pairs: list[tuple[str, Path]]
) -> str:
    plan = json.loads(
        (plugin / "provisioning/full-bundle-plan.v4.json").read_text()
    )
    by_id = {row["component_id"]: row for row in plan["components"]}
    rows = []
    for component, archive in sorted(
        asset_pairs, key=lambda item: (item[0], item[1].name)
    ):
        manifest = builder.read_component_archive(archive)
        manifest_bytes = json.dumps(manifest, indent=2).encode() + b"\n"
        rows.append(
            {
                "component_id": component,
                "asset_id": (
                    f"{component}.part-{manifest['part_index']:03d}-"
                    f"of-{manifest['part_count']:03d}"
                ),
                "part_index": manifest["part_index"],
                "part_count": manifest["part_count"],
                "condition": by_id[component]["condition"],
                "filename": archive.name,
                "bytes": archive.stat().st_size,
                "sha256": builder.sha256(archive),
                "component_manifest_sha256": hashlib.sha256(
                    manifest_bytes
                ).hexdigest(),
                "file_count": manifest["file_count"],
                "payload_bytes": manifest["total_bytes"],
            }
        )
    digest = hashlib.sha256(builder.canonical(rows)).hexdigest()
    return f"refs/tags/evidence-lane-v4.0.1-bundle-{digest[:16]}"


def test_production_plan_accounts_for_every_retained_tool_and_install_input() -> None:
    plan_path = PLUGIN / "provisioning/full-bundle-plan.v4.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    installer_module.verify_seal(plan, code="TEST")
    assert plan["retained_tool_count"] == len(plan["tool_assignments"]) == 103
    assert len({row["tool_id"] for row in plan["tool_assignments"]}) == 103
    assert len(plan["components"]) == 10
    assert len(plan["provider_environments"]) == 4
    assert {row["asset_id"] for row in plan["model_assets"]} == {
        "embedding_snapshot",
        "rapidocr_models",
        "tesseract_languages",
        "docling_models",
    }
    assert {row["asset_id"] for row in plan["asset_groups"]} == {
        "embedding_snapshot",
        "rapidocr_models",
        "tesseract_languages",
        "docling_models",
        "parser_grammars",
        "poppler_runtime",
        "tesseract_runtime",
        "ffmpeg_runtime",
        "libreoffice_runtime",
        "powerbi_tom_runtime",
        "powerbi_pbix_runtime",
    }
    assert len(plan["native_inputs"]) == 13
    git_runtime = next(
        row for row in plan["native_inputs"] if row["tool_id"] == "git"
    )
    assert git_runtime["version"] == "2.54.0.windows.1"
    assert git_runtime["release_component"] == "native-tools"
    assert plan["node_runtime"]["node_version"] == "24.15.0"
    assert plan["node_runtime"]["mermaid_cli_version"] == "11.16.0"
    assert plan["node_runtime"]["browser_version"] == "152.0.7977.75"
    assert plan["python_runtimes"]["versions"] == {
        "cp314": "3.14.2",
        "cp312": "3.12.10",
    }
    assert all(
        row["installed_executables"]
        and row["installed_executables"] != ["PYTHON_MODULE_RESOLVED"]
        for row in plan["native_inputs"]
    )
    assert len(plan["external_service_configuration"]) == 8
    assert len(plan["offline_wheel_sets"]) == 5
    engine_wheels = next(
        row for row in plan["offline_wheel_sets"] if row["environment_id"] == "engine"
    )
    assert len(engine_wheels["packages"]) == 272
    runtime_lock = PLUGIN / "requirements.runtime.lock.txt"
    assert engine_wheels["source_lock_sha256"] == hashlib.sha256(
        runtime_lock.read_bytes()
    ).hexdigest()
    runtime_lock_text = runtime_lock.read_text(encoding="utf-8")
    assert " @ " not in runtime_lock_text and "://" not in runtime_lock_text
    runtime_receipt_path = PLUGIN / "requirements.runtime-lock.v4.json"
    runtime_receipt = json.loads(runtime_receipt_path.read_text(encoding="utf-8"))
    installer_module.verify_seal(runtime_receipt, code="TEST")
    assert runtime_receipt["package_count"] == 272
    assert runtime_receipt["output_sha256"] == engine_wheels["source_lock_sha256"]
    assert engine_wheels["source_lock_receipt_sha256"] == hashlib.sha256(
        runtime_receipt_path.read_bytes()
    ).hexdigest()
    assert (PLUGIN / "requirements.lock.txt").read_bytes() == (
        ROOT / "requirements.lock.txt"
    ).read_bytes()
    assert plan["license_index"]["record_count"] == 103
    assert plan["release_asset_policy"]["repository_release_only"]
    assert plan["release_asset_policy"]["third_party_download_during_first_detection"] is False
    assert not {"OpenJDK", "Jackcess", "OneNote_Parser"}.intersection(
        row["tool_id"] for row in plan["tool_assignments"]
    )
    binding_path = PLUGIN / "provisioning/release-binding.v4.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    installer_module.verify_seal(binding, code="TEST")
    assert b"\r\n" not in binding_path.read_bytes()
    assert binding["status"] in {"SOURCE_TEMPLATE_UNBOUND", "RELEASE_BOUND"}
    if binding["status"] == "SOURCE_TEMPLATE_UNBOUND":
        assert binding["installation_enabled"] is False
        assert binding["release_ref"] is None
        assert binding["assets"] == []
    else:
        assert binding["installation_enabled"] is True
        assert binding["plugin_version"] == "4.0.4"
        assert binding["release_ref"].startswith("refs/tags/evidence-lane-v4.0.4-bundle-")
        assert len(binding["assets_sha256"]) == 64
        assert len(binding["assets"]) == 12
    assert binding["bundle_plan_sha256"] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    validated_binding, validated_plan = installer_module.validate_binding(PLUGIN)
    assert validated_binding == binding
    assert validated_plan == plan
    plan_schema = json.loads(
        (
            PLUGIN / "schemas/install/first-detection-bundle-plan.v4.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(plan_schema)
    Draft202012Validator(plan_schema).validate(plan)
    binding_schema = json.loads(
        (
            PLUGIN
            / "schemas/install/first-detection-release-binding.v4.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(binding_schema)
    Draft202012Validator(binding_schema).validate(binding)
    mcp = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["evidence-lane"]
    assert server["required"] is False
    assert server["startup_timeout_sec"] == 120
    assert server["command"] == "node"
    assert server["args"][0] == "./mcp/server.mjs"
    assert "EVIDENCE_LANE_GHOSTSCRIPT_LICENSE_RECEIPT" in server["env_vars"]


def test_component_parts_are_deterministic_complete_and_below_the_payload_cap(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    write(source / "install-inputs/a.bin", b"a" * 8)
    write(source / "install-inputs/b.bin", b"b" * 7)
    write(source / "install-inputs/c.bin", b"c" * 3)
    first = builder.build_component_parts(
        "provider-cuda", source, tmp_path / "first", maximum_payload_bytes=10
    )
    second = builder.build_component_parts(
        "provider-cuda", source, tmp_path / "second", maximum_payload_bytes=10
    )
    assert len(first) == len(second) == 2
    assert [row["sha256"] for row in first] == [row["sha256"] for row in second]
    manifests = [
        builder.read_component_archive(Path(row["path"])) for row in first
    ]
    assert [row["part_index"] for row in manifests] == [1, 2]
    assert {row["part_count"] for row in manifests} == {2}
    assert {file["path"] for row in manifests for file in row["files"]} == {
        "install-inputs/a.bin",
        "install-inputs/b.bin",
        "install-inputs/c.bin",
    }
    assert all(row["total_bytes"] <= 10 for row in manifests)


def test_offline_lock_rewrites_release_wheels_without_network_locations(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    first = wheelhouse / "alpha-1.0-py3-none-any.whl"
    second = wheelhouse / "beta-2.0-py3-none-any.whl"
    write_wheel(first, "alpha", "1.0")
    write_wheel(second, "beta", "2.0")
    packages = [
        {
            "name": "alpha",
            "version": "1.0",
            "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
        },
        {
            "distribution": "beta",
            "version": "2.0",
            "hashes": [hashlib.sha256(second.read_bytes()).hexdigest()],
        },
    ]
    lock = tmp_path / "runtime.offline.lock.txt"
    licenses = tmp_path / "runtime-wheel-licenses.json"
    result = builder.build_offline_lock(
        packages, wheelhouse, lock, license_output=licenses
    )
    assert result["network_locations"] == 0
    content = lock.read_text(encoding="utf-8")
    assert "://" not in content and " @ " not in content
    verified = installer_module.validate_offline_wheelhouse([lock], wheelhouse)
    assert verified["requirement_count"] == verified["wheel_count"] == 2
    assert installer_module.validate_runtime_license_index(
        licenses, wheelhouse
    )["wheel_count"] == 2
    lock.write_text(
        "alpha @ https://example.test/alpha.whl --hash=sha256:" + "a" * 64,
        encoding="utf-8",
    )
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.validate_offline_wheelhouse([lock], wheelhouse)
    assert error.value.code == "OFFLINE_LOCK_INVALID"


def test_release_binding_accepts_one_complete_multipart_provider_sequence(
    tmp_path: Path,
) -> None:
    plugin, archives = fixture_plugin(tmp_path)
    cuda_source = tmp_path / "component-sources/provider-cuda"
    largest = max(path.stat().st_size for path in cuda_source.rglob("*") if path.is_file())
    parts = builder.build_component_parts(
        "provider-cuda",
        cuda_source,
        tmp_path / "multipart",
        maximum_payload_bytes=largest + 1,
    )
    assert len(parts) >= 2
    asset_pairs = [
        (component, archive)
        for component, archive in archives.items()
        if component != "provider-cuda"
    ] + [("provider-cuda", Path(row["path"])) for row in parts]
    incomplete = [
        pair
        for pair in asset_pairs
        if not (
            pair[0] == "provider-cuda"
            and builder.read_component_archive(pair[1])["part_index"] == len(parts)
        )
    ]
    with pytest.raises(builder.ReleaseBuildError, match="complete sequence"):
        builder.bind_release(
            plugin_root=plugin,
            release_ref=release_ref_for_assets(plugin, incomplete),
            asset_paths=incomplete,
        )
    release_ref = release_ref_for_assets(plugin, asset_pairs)
    builder.bind_release(
        plugin_root=plugin,
        release_ref=release_ref,
        asset_paths=asset_pairs,
    )
    binding, _ = installer_module.validate_binding(plugin)
    cuda = [
        row for row in binding["assets"] if row["component_id"] == "provider-cuda"
    ]
    assert [row["part_index"] for row in cuda] == list(range(1, len(cuda) + 1))
    assert {row["part_count"] for row in cuda} == {len(cuda)}


def test_bound_non_windows_host_keeps_the_reduced_route_without_studio_install(
    tmp_path: Path,
) -> None:
    plugin, _ = fixture_plugin(tmp_path)
    target = tmp_path / "must-not-exist"
    result = installer_module.prepare_mcp(
        plugin,
        [],
        environment={"EVIDENCE_LANE_STUDIO_ROOT": str(target)},
        system="Darwin",
        machine="arm64",
    )
    assert result["mode"] == "REDUCED_NON_WINDOWS_HOST"
    assert result["studio_installed"] is False
    assert result["reexec"] is False
    assert not target.exists()


def test_ghostscript_grant_is_explicit_self_sealed_and_release_bound(
    tmp_path: Path,
) -> None:
    plugin, _ = fixture_plugin(tmp_path)
    binding_sha = installer_module.sha256(
        plugin / "provisioning/release-binding.v4.json"
    )
    grant = installer_module.sealed(
        {
            "schema": "evidence-lane.explicit-license-grant.v4",
            "status": "ACCEPTED",
            "tool_id": "ghostscript",
            "license_expression": (
                "AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial"
            ),
            "release_binding_sha256": binding_sha,
            "accepted_by": "fixture user",
            "accepted_at": "2026-09-10T00:00:00Z",
        }
    )
    path = tmp_path / "ghostscript-license.json"
    write(path, json.dumps(grant) + "\n")
    assert installer_module.validate_ghostscript_license_grant(
        path, release_binding_sha256=binding_sha
    ) == grant
    grant["accepted_by"] = "changed"
    write(path, json.dumps(grant) + "\n")
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.validate_ghostscript_license_grant(
            path, release_binding_sha256=binding_sha
        )
    assert error.value.code == "LICENSE_GRANT_INVALID"


def test_exact_release_assets_install_once_and_reexec_from_immutable_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, archives = fixture_plugin(tmp_path)
    install_root = tmp_path / "shared-installation"
    copied = []

    def fetch(asset, target):
        copied.append(asset["component_id"])
        source = archives[asset["component_id"]]
        shutil.copyfile(source, target)

    commands: list[list[str]] = []
    installer = installer_module.FirstDetectionInstaller(
        plugin,
        install_root,
        fetcher=fetch,
        runner=fake_runner(commands),
        gpu_probe=lambda: {
            "names": ["NVIDIA fixture"],
            "nvidia_cuda_compatible": True,
            "directml_compatible": True,
            "amd_rocm_compatible": False,
            "basis": "fixture",
        },
        system="Windows",
        machine="AMD64",
    )
    result = installer.ensure()
    assert result["installation_state"] == "INSTALLED_EXACT_RELEASE"
    expected = {
        "core-engine-studio",
        "native-tools",
        "node-runtime",
        "model-assets",
        "powerbi-runtime",
        "provider-cpu",
        "provider-cuda",
        "provider-directml",
    }
    assert set(copied) == expected
    release = result["release_root"]
    assert release == install_root.resolve()
    assert not (install_root / "releases").exists()
    assert result["plugin_root"] == release / "plugin"
    assert result["mcp_launcher"] == release / "plugin/scripts/run_mcp.py"
    configs = [
        release / "engine/venv/pyvenv.cfg",
        *(
            release / "toolchains/python/providers"
        ).glob("*/pyvenv.cfg"),
    ]
    assert configs and all(
        str(release) in config.read_text(encoding="utf-8")
        and ".staging" not in config.read_text(encoding="utf-8")
        for config in configs
    )
    assert (release / "toolchains/native-installation.v4.json").is_file()
    assert (release / "toolchains/asset-installation.v4.json").is_file()
    assert (release / "toolchains/provider-installation.v4.json").is_file()
    monkeypatch.setenv("EVIDENCE_LANE_STUDIO_ROOT", str(install_root))
    installation = StudioInstallation(install_root)
    assert installation.active_root == release
    assert resolve_native_tool("ripgrep", runtime_root=release).version == "fixture"
    assert resolve_shared_asset("embedding_snapshot")[0] == (
        release / "toolchains/models/embedding_snapshot"
    )
    providers, provider_state = load_installed_providers(
        installation=installation,
        contracts=release / "plugin/toolchains/providers",
    )
    assert [provider.runtime_id for provider in providers] == ["cpu"]
    assert provider_state["state"] == "observed"
    assert any(command[1:5] == ["-I", "-m", "venv", "--copies"] for command in commands)
    assert all("http" not in argument for command in commands for argument in command)
    first_command_count = len(commands)
    reused = installer.ensure()
    assert reused["installation_state"] == "REUSED_EXACT_RELEASE"
    assert len(commands) == first_command_count
    assert set(copied) == expected
    pointer = json.loads((install_root / "installation.json").read_text())
    installer_module.verify_seal(pointer, code="TEST")
    status = json.loads((install_root / "installation-status.json").read_text())
    installer_module.verify_seal(status, code="TEST")
    assert status["phase"] == "ACTIVE_EXACT_RELEASE"
    assert status["credentials_recorded"] is False
    pointer_schema = json.loads(
        (
            PLUGIN / "schemas/install/first-detection-installation.v4.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(pointer_schema).validate(pointer)
    receipt = json.loads((release / ".evidence-lane-release.json").read_text())
    assert receipt["project_database_count"] == 0
    assert receipt["project_files_copied"] is False
    assert all(
        row["status"] == "unconfigured" and row["credentials_stored"] is False
        for row in receipt["external_service_configuration"]
    )

    class ExistingInstaller:
        def __init__(self, *_args, **_kwargs):
            pass

        def ensure(self):
            return reused

    prepared = installer_module.prepare_mcp(
        plugin,
        ["--transport", "stdio"],
        environment={"EVIDENCE_LANE_STUDIO_ROOT": str(install_root)},
        system="Windows",
        machine="AMD64",
        installer_factory=ExistingInstaller,
    )
    assert prepared["reexec"] is True
    assert prepared["command"][:3] == [
        str(reused["runtime_python"]),
        "-I",
        "-B",
    ]
    assert prepared["command"][3] == str(reused["mcp_launcher"])
    assert "PYTHONPATH" not in prepared["environment"]
    scoped_path = prepared["environment"]["PATH"].split(os.pathsep)
    assert scoped_path[:3] == [
        str(release / "toolchains/node"),
        str(release / "toolchains/node/node_modules/.bin"),
        str(release / "toolchains/bin/git/cmd"),
    ]
    assert prepared["environment"]["EVIDENCE_LANE_MODEL_ROOT"] == str(
        release / "toolchains/models"
    )
    assert prepared["environment"]["PUPPETEER_EXECUTABLE_PATH"] == str(
        release / "toolchains/node/chrome-headless-shell/chrome-headless-shell.exe"
    )
    active = installer_module.prepare_mcp(
        reused["plugin_root"],
        [],
        environment={
            "EVIDENCE_LANE_STUDIO_ROOT": str(install_root),
            "EVIDENCE_LANE_INSTALLED_RUNTIME_ACTIVE": "1",
        },
        system="Windows",
        machine="AMD64",
    )
    assert active["mode"] == "ACTIVE_INSTALLED_RELEASE"
    assert active["reexec"] is False
    installed_files = json.loads(
        (release / ".evidence-lane-installed-files.json").read_text()
    )
    installer_module.verify_seal(installed_files, code="TEST")
    noncritical = release / "install-inputs/component-markers/node-runtime.txt"
    noncritical.write_bytes(b"changed")
    quick = installer_module.validate_active_installation_quick(install_root)
    assert quick["validation_scope"] == "sealed_pointer_and_critical_files"
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.validate_active_installation(install_root)
    assert error.value.code == "INSTALLED_RELEASE_CHANGED"


def test_mcp_first_detection_defers_installation_outside_initialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin, _archives = fixture_plugin(tmp_path)
    install_root = tmp_path / "stable-installation"
    calls = []
    monkeypatch.setattr(
        installer_module,
        "start_deferred_installation",
        lambda plugin_root, selected_root: calls.append((plugin_root, selected_root)) or True,
    )
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.prepare_mcp(
            plugin,
            ["--transport", "stdio"],
            environment={"EVIDENCE_LANE_STUDIO_ROOT": str(install_root)},
            system="Windows",
            machine="AMD64",
        )
    assert error.value.code == "INSTALLATION_PENDING"
    assert calls == [(plugin.resolve(), install_root.resolve())]
    assert not (install_root / "installation.json").exists()


def test_mcp_reports_recorded_deferred_install_failure_instead_of_in_progress(
    tmp_path: Path,
) -> None:
    plugin, _archives = fixture_plugin(tmp_path)
    install_root = tmp_path / "stable-installation"
    install_root.mkdir()
    write(install_root / ".installation-started.json", "{}\n")
    write(install_root / "legacy-layout/old.txt")
    status = installer_module.sealed(
        {
            "schema": "evidence-lane.first-detection-status.v4",
            "phase": "FAILED",
            "plugin_version": "4.0.1",
            "release_ref": "fixture",
            "selected_components": [],
            "error_code": "INSTALLATION_ROOT_OCCUPIED",
            "credentials_recorded": False,
            "project_state_changed": False,
            "installed_native_execution_claimed": False,
        }
    )
    write(install_root / "installation-status.json", json.dumps(status) + "\n")
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.prepare_mcp(
            plugin,
            ["--transport", "stdio"],
            environment={"EVIDENCE_LANE_STUDIO_ROOT": str(install_root)},
            system="Windows",
            machine="AMD64",
        )
    assert error.value.code == "INSTALLATION_ROOT_OCCUPIED"


def test_deferred_installation_marker_is_owned_control_state_not_foreign_content(
    tmp_path: Path,
) -> None:
    plugin, archives = fixture_plugin(tmp_path)
    install_root = tmp_path / "stable-installation"
    install_root.mkdir()
    write(install_root / ".installation-started.json", "{}\n")

    def fetch(asset, target):
        shutil.copyfile(archives[asset["component_id"]], target)

    installer = installer_module.FirstDetectionInstaller(
        plugin,
        install_root,
        fetcher=fetch,
        runner=fake_runner([]),
        gpu_probe=lambda: {
            "names": [],
            "nvidia_cuda_compatible": False,
            "directml_compatible": False,
            "amd_rocm_compatible": False,
        },
        system="Windows",
        machine="AMD64",
    )
    result = installer.ensure()
    assert result["installation_state"] == "INSTALLED_EXACT_RELEASE"
    assert result["release_root"] == install_root.resolve()


def test_successful_deferred_bootstrap_removes_its_exact_owned_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap = load_script(
        "evidence_lane_bootstrap_marker_test",
        PLUGIN / "scripts/bootstrap.py",
    )
    install_root = tmp_path / "stable-installation"
    install_root.mkdir()
    marker = install_root / ".installation-started.json"
    write(marker, "{}\n")

    class FakeInstaller:
        def __init__(self, *_args, **_kwargs):
            pass

        def ensure(self):
            return {
                "installation_state": "INSTALLED_EXACT_RELEASE",
                "release_root": install_root,
                "runtime_python": install_root / "engine/venv/Scripts/python.exe",
                "plugin_root": install_root / "plugin",
            }

    class FakeModule:
        FirstDetectionError = installer_module.FirstDetectionError
        FirstDetectionInstaller = FakeInstaller
        validate_binding = staticmethod(lambda _root: ({"status": "RELEASE_BOUND"}, {}))
        installation_root = staticmethod(
            lambda environment: Path(environment["EVIDENCE_LANE_STUDIO_ROOT"])
        )

    monkeypatch.setattr(bootstrap, "_module", lambda: FakeModule)
    assert bootstrap.main(
        [
            "--installation-root",
            str(install_root),
            "--deferred-marker",
            str(marker),
        ]
    ) == 0
    assert not marker.exists()


def test_failed_registration_quarantines_published_release_for_clean_retry(
    tmp_path: Path,
) -> None:
    plugin, archives = fixture_plugin(tmp_path)
    install_root = tmp_path / "stable-installation"
    commands: list[list[str]] = []
    base = fake_runner(commands)

    def fetch(asset, target):
        shutil.copyfile(archives[asset["component_id"]], target)

    def fail_registration(command, cwd, environment):
        values = [str(value) for value in command]
        if any(value.endswith("register_installed_runtime.py") for value in values):
            commands.append(values)
            return 1
        return base(command, cwd, environment)

    installer = installer_module.FirstDetectionInstaller(
        plugin,
        install_root,
        fetcher=fetch,
        runner=fail_registration,
        gpu_probe=lambda: {
            "names": [],
            "nvidia_cuda_compatible": False,
            "directml_compatible": False,
            "amd_rocm_compatible": False,
        },
        system="Windows",
        machine="AMD64",
    )
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer.ensure()
    assert error.value.code == "INSTALLATION_REGISTRATION_FAILED"
    assert not any(
        (install_root / name).exists()
        for name in ("app", "plugin", "engine", "toolchains", "installation.json")
    )
    status = json.loads((install_root / "installation-status.json").read_text())
    assert status["phase"] == "FAILED"
    failed = install_root.parent / ".EvidenceLaneStudio-failed"
    assert any((candidate / "plugin").is_dir() for candidate in failed.iterdir())


def test_first_detection_selects_only_compatible_gpu_providers(tmp_path: Path) -> None:
    plugin, _archives = fixture_plugin(tmp_path)
    binding, _plan = installer_module.validate_binding(plugin)

    def selected(gpu):
        assets, skipped = installer_module.selected_assets(binding, gpu=gpu)
        provider_ids = {
            row["component_id"]
            for row in assets
            if row["component_id"].startswith("provider-")
        }
        skipped_provider_ids = {
            row["component_id"]
            for row in skipped
            if row["component_id"].startswith("provider-")
        }
        return provider_ids, skipped_provider_ids

    providers, skipped = selected(
        {
            "nvidia_cuda_compatible": True,
            "amd_rocm_compatible": False,
            "directml_compatible": True,
        }
    )
    assert providers == {"provider-cpu", "provider-cuda", "provider-directml"}
    assert skipped == {"provider-rocm"}

    providers, skipped = selected(
        {
            "nvidia_cuda_compatible": False,
            "amd_rocm_compatible": True,
            "directml_compatible": True,
        }
    )
    assert providers == {"provider-cpu", "provider-directml", "provider-rocm"}
    assert skipped == {"provider-cuda"}

    providers, skipped = selected(
        {
            "nvidia_cuda_compatible": False,
            "amd_rocm_compatible": False,
            "directml_compatible": True,
        }
    )
    assert providers == {"provider-cpu", "provider-directml"}
    assert skipped == {"provider-cuda", "provider-rocm"}


def test_binding_rejects_changed_source_and_release_cache_bytes(tmp_path: Path) -> None:
    plugin, _archives = fixture_plugin(tmp_path)
    write(plugin / "scripts/run_engine.py", "changed\n")
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.validate_binding(plugin)
    assert error.value.code == "SOURCE_PACKAGE_CHANGED"
    plugin, _archives = fixture_plugin(tmp_path / "second")
    install_root = tmp_path / "shared"

    def changed_fetch(asset, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"changed")

    install = installer_module.FirstDetectionInstaller(
        plugin,
        install_root,
        fetcher=changed_fetch,
        runner=fake_runner([]),
        gpu_probe=lambda: {
            "names": [],
            "nvidia_cuda_compatible": False,
            "directml_compatible": False,
            "amd_rocm_compatible": False,
            "basis": "fixture",
        },
        system="Windows",
        machine="AMD64",
    )
    with pytest.raises(installer_module.FirstDetectionError) as error:
        install.ensure()
    assert error.value.code == "RELEASE_ASSET_IDENTITY_MISMATCH"
    assert not (install_root / "installation.json").exists()


def test_release_source_manifest_rejects_credential_shaped_files(tmp_path: Path) -> None:
    plugin, _ = fixture_plugin(tmp_path)
    write(plugin / ".env", "API_KEY=not-a-real-fixture-secret\n")
    with pytest.raises(builder.ReleaseBuildError, match="Credential-shaped"):
        builder.build_source_manifest(plugin)


def test_release_source_manifest_rejects_git_materialization_drift(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    plugin = repository / "plugins/evidence-lane-plugin"
    write(repository / ".gitattributes", "* text=auto eol=lf\n")
    write(plugin / ".codex-plugin/plugin.json", '{"name":"evidence-lane-plugin"}\n')
    write(plugin / "source.py", "first\nsecond\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Evidence Lane Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "evidence-lane@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    (plugin / "source.py").write_bytes(b"first\r\nsecond\r\n")

    with pytest.raises(builder.ReleaseBuildError, match="Git would transform"):
        builder.build_source_manifest(plugin)

    (plugin / "source.py").write_bytes(b"first\nchanged\n")
    with pytest.raises(builder.ReleaseBuildError, match="must be staged"):
        builder.build_source_manifest(plugin)


def test_invalid_existing_pointer_is_preserved_and_never_replaced(tmp_path: Path) -> None:
    plugin, archives = fixture_plugin(tmp_path)
    install_root = tmp_path / "shared"
    write(install_root / "installation.json", b"{different owner")
    before = (install_root / "installation.json").read_bytes()
    install = installer_module.FirstDetectionInstaller(
        plugin,
        install_root,
        fetcher=lambda asset, target: shutil.copyfile(
            archives[asset["component_id"]], target
        ),
        runner=fake_runner([]),
        gpu_probe=lambda: {
            "names": [],
            "nvidia_cuda_compatible": False,
            "directml_compatible": False,
            "amd_rocm_compatible": False,
            "basis": "fixture",
        },
        system="Windows",
        machine="AMD64",
    )
    with pytest.raises(installer_module.FirstDetectionError) as error:
        install.ensure()
    assert error.value.code == "INSTALLATION_MANIFEST_INVALID"
    assert (install_root / "installation.json").read_bytes() == before


def test_unbound_manifest_runs_only_from_a_real_development_checkout(tmp_path: Path) -> None:
    fake = tmp_path / "plugins/evidence-lane-plugin"
    shutil.copytree(PLUGIN / "provisioning", fake / "provisioning")
    shutil.copytree(PLUGIN / ".codex-plugin", fake / ".codex-plugin")
    plan_path = fake / "provisioning/full-bundle-plan.v4.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    template = builder.sealed(
        {
            "schema": "evidence-lane.first-detection-release-binding.v4",
            "status": "SOURCE_TEMPLATE_UNBOUND",
            "installation_enabled": False,
            "plugin_id": "evidence-lane-plugin",
            "plugin_version": plan["plugin"]["version"],
            "repository": plan["plugin"]["repository"],
            "sparse_root": plan["plugin"]["sparse_root"],
            "release_ref": None,
            "bundle_plan": "provisioning/full-bundle-plan.v4.json",
            "bundle_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "source_manifest": None,
            "source_manifest_sha256": None,
            "assets": [],
            "reason": (
                "Explicit unbound development fixture; production binding remains bound."
            ),
        }
    )
    write(
        fake / "provisioning/release-binding.v4.json",
        json.dumps(template, indent=2) + "\n",
    )
    with pytest.raises(installer_module.FirstDetectionError) as error:
        installer_module.prepare_mcp(fake, [], system="Windows", machine="AMD64")
    assert error.value.code == "RELEASE_BINDING_UNBOUND"
    write(tmp_path / ".git/fixture")
    prepared = installer_module.prepare_mcp(
        fake,
        [],
        environment={"EVIDENCE_LANE_DEVELOPMENT_RUNTIME_ACTIVE": "1"},
        system="Windows",
        machine="AMD64",
    )
    assert prepared["mode"] == "DEVELOPMENT_SOURCE"
    assert prepared["reexec"] is False
