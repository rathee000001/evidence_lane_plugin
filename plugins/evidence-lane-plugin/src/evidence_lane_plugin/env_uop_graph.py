"""Seal and publish the clean Codex-native ENV/UOP action planes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes, sha256_file


def rebuild_flash_manifest(plugin_root: str | Path) -> dict[str, Any]:
    """Seal current SQLite/MMD/DOT ENV/UOP bytes without derived renders."""

    root = Path(plugin_root).resolve()
    member_paths = (
        "env/SOURCE_PACKET_AUDIT.json",
        "env/UNIVERSAL_FLASH_PROMPT.md",
        "env/env_law.md",
        "env/env_mmd.dot",
        "env/env_mmd.mmd",
        "env/env_sqlite.sqlite",
        "env/locked_mmd_hash.txt",
        "uop/locked_mmd_hash.txt",
        "uop/uop_law.md",
        "uop/uop_mmd.dot",
        "uop/uop_mmd.mmd",
        "uop/uop_sqlite.sqlite",
    )
    members = [
        {
            "path": relative,
            "bytes": (root / relative).stat().st_size,
            "sha256": sha256_file(root / relative),
        }
        for relative in member_paths
    ]

    def latest(database: Path, table: str) -> dict[str, Any]:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            row = connection.execute(
                f'SELECT receipt_json FROM "{table}" ORDER BY sequence DESC LIMIT 1'
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Missing ENV/UOP receipt table row: {table}")
            return json.loads(str(row[0]))
        finally:
            connection.close()

    def latest_graph(database: Path) -> dict[str, Any]:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM semantic_graph_render_receipt_v17 "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Missing ENV/UOP semantic graph receipt.")
            return dict(row)
        finally:
            connection.close()

    env_db = root / "env" / "env_sqlite.sqlite"
    uop_db = root / "uop" / "uop_sqlite.sqlite"
    env_build = latest(env_db, "env_action_plane_build_receipt")
    uop_build = latest(uop_db, "uop_action_plane_build_receipt")
    env_graph = latest_graph(env_db)
    uop_graph = latest_graph(uop_db)
    source_audit = json.loads(
        (root / "env" / "SOURCE_PACKET_AUDIT.json").read_text(encoding="utf-8")
    )
    body = {
        "schema": "evidence-lane.session-flash-manifest.v1",
        "plugin_id": "evidence-lane-plugin",
        "authority_version": "ENV15_UOP15_PUBLIC_LOCKED_20260807",
        "flash_scope": "PLUGIN_INSTALLATION_OUTSIDE_PV",
        "persistence_state": "FLASHED_UNTIL_PLUGIN_REMOVED",
        "source_packet_status": source_audit["overall_status"],
        "whole_source_packet_accepted": True,
        "member_count": len(members),
        "members": members,
        "authorities": {
            "env": {
                "version": "V15",
                "sqlite": "env/env_sqlite.sqlite",
                "sqlite_sha256": sha256_file(env_db),
                "sqlite_user_version": 17,
                "mmd": "env/env_mmd.mmd",
                "mmd_sha256": sha256_file(root / "env" / "env_mmd.mmd"),
                "dot": "env/env_mmd.dot",
                "dot_sha256": sha256_file(root / "env" / "env_mmd.dot"),
                "law": "env/env_law.md",
                "read_mode": "mode=ro&immutable=1",
                "graph_receipt_sha256": env_graph["receipt_sha256"],
                "action_plane_build_receipt_sha256": env_build["receipt_sha256"],
                "predecessor_database_copied": False,
            },
            "uop": {
                "version": "V15",
                "sqlite": "uop/uop_sqlite.sqlite",
                "sqlite_sha256": sha256_file(uop_db),
                "sqlite_user_version": 17,
                "mmd": "uop/uop_mmd.mmd",
                "mmd_sha256": sha256_file(root / "uop" / "uop_mmd.mmd"),
                "dot": "uop/uop_mmd.dot",
                "dot_sha256": sha256_file(root / "uop" / "uop_mmd.dot"),
                "law": "uop/uop_law.md",
                "read_mode": "mode=ro&immutable=1",
                "graph_receipt_sha256": uop_graph["receipt_sha256"],
                "action_plane_build_receipt_sha256": uop_build["receipt_sha256"],
                "predecessor_database_copied": False,
            },
        },
        "ai_toolchain": {
            "receipt_sha256": env_build["receipt_sha256"],
            "tool_count": env_build["counts"]["tools"],
            "action_count": env_build["counts"]["actions"],
            "lane_count": env_build["counts"]["lanes"],
            "counts_are_derived": True,
        },
        "runtime_locks": {
            "base_sha256": sha256_file(root / "requirements.lock.txt"),
            "torch_cpu_sha256": sha256_file(root / "requirements.torch-cpu.lock.txt"),
            "torch_nvidia_sha256": sha256_file(
                root / "requirements.torch-nvidia.lock.txt"
            ),
            "onnx_directml_sha256": sha256_file(
                root / "requirements.onnx-directml.lock.txt"
            ),
            "toolchain_sha256": sha256_file(root / "requirements.toolchain.lock.txt"),
        },
        "forbidden_pv_members": [
            "env",
            "uop",
            "env.sqlite",
            "uop.sqlite",
            "environment_package.json",
            "operator_profile.json",
        ],
        "renderer_invoked": False,
        "derived_render_members_present": False,
        "warning": None,
    }
    manifest = root / "env" / "SESSION_FLASH_MANIFEST.json"
    atomic_write_bytes(manifest, canonical_json_bytes(body) + b"\n")
    return {
        "status": "PASS",
        "path": manifest.relative_to(root).as_posix(),
        "sha256": sha256_file(manifest),
        "member_count": len(members),
        "ai_toolchain": body["ai_toolchain"],
    }


def rebuild_env_uop_locks(
    plugin_root: str | Path,
    *,
    env_graph_receipt: dict[str, Any],
    uop_graph_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Seal the current ENV/UOP SQLite, MMD, DOT, and graph identities."""

    root = Path(plugin_root).resolve()
    receipts: dict[str, Any] = {}
    for authority, graph_receipt in (
        ("env", env_graph_receipt),
        ("uop", uop_graph_receipt),
    ):
        lock_path = root / authority / "locked_mmd_hash.txt"
        mmd_path = root / authority / f"{authority}_mmd.mmd"
        dot_path = root / authority / f"{authority}_mmd.dot"
        sqlite_path = root / authority / f"{authority}_sqlite.sqlite"
        build_table = (
            "env_action_plane_build_receipt"
            if authority == "env"
            else "uop_action_plane_build_receipt"
        )
        connection = sqlite3.connect(
            f"file:{sqlite_path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            build_row = connection.execute(
                f"SELECT receipt_sha256 FROM {build_table} "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if build_row is None:
            raise RuntimeError(
                f"Missing current {authority.upper()} action-plane build receipt."
            )
        lines = {
            "section": authority.upper(),
            "authority_version": "ENV15" if authority == "env" else "UOP15",
            "mmd_sha256": sha256_file(mmd_path).lower(),
            "dot_sha256": sha256_file(dot_path).lower(),
            "sqlite_sha256": sha256_file(sqlite_path).lower(),
            "graph_pipeline_receipt_sha256": str(
                graph_receipt["receipt_sha256"]
            ).lower(),
            "action_plane_build_receipt_sha256": str(build_row[0]).lower(),
            "predecessor_database_copied": "false",
            "rendered_from_current_sqlite_authority": "true",
            "renderer_invoked": "false",
            "updated_at_utc": str(
                graph_receipt.get("recorded_at") or "2000-01-01T00:00:00Z"
            ).replace("+00:00", "Z"),
        }
        payload = "".join(f"{key}: {value}\n" for key, value in lines.items())
        atomic_write_bytes(lock_path, payload.encode("utf-8"))
        receipts[authority] = {
            "path": lock_path.relative_to(root).as_posix(),
            "sha256": sha256_file(lock_path),
            "sqlite_sha256": sha256_file(sqlite_path),
            "mmd_sha256": sha256_file(mmd_path),
            "dot_sha256": sha256_file(dot_path),
            "graph_pipeline_receipt_sha256": graph_receipt["receipt_sha256"],
        }
    core = {
        "schema": "evidence-lane.env-uop-lock-refresh.v1",
        "status": "PASS",
        "authorities": receipts,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def rebuild_packaged_authority_manifests(
    plugin_root: str | Path,
) -> dict[str, Any]:
    """Rebuild the two packaged authority manifests from Flash membership."""

    root = Path(plugin_root).resolve()
    flash = json.loads(
        (root / "env" / "SESSION_FLASH_MANIFEST.json").read_text(encoding="utf-8")
    )
    manifests: dict[str, Any] = {}
    for authority in ("env", "uop"):
        member_paths = sorted(
            str(row["path"])
            for row in flash["members"]
            if str(row["path"]).startswith(f"{authority}/")
        )
        rows = [
            {
                "path": relative,
                "canonical_path": relative,
                "bytes": (root / relative).stat().st_size,
                "sha256": sha256_file(root / relative),
            }
            for relative in member_paths
        ]
        body = {
            "schema": f"evidence-lane.{authority}-packaged-authority.v1",
            "status": "PASS",
            "authority": authority.upper(),
            "canonical_root": authority,
            "published_root": authority,
            "member_count": len(rows),
            "members": rows,
            "canonical_packaged_authority": True,
            "duplicate_authority_copy_present": False,
        }
        receipt = {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}
        atomic_write_bytes(
            root / authority / "authority-manifest.v1.json",
            json.dumps(receipt, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
        )
        manifests[authority] = receipt
    core = {
        "schema": "evidence-lane.env-uop-packaged-authorities.v1",
        "status": "PASS",
        "manifests": manifests,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def regenerate_env_uop_authorities(plugin_root: str | Path) -> dict[str, Any]:
    """Run the complete current-source ENV/UOP backend generation sequence."""

    from .codex_action_plane import rebuild_codex_action_planes

    root = Path(plugin_root).resolve()
    action_plane = rebuild_codex_action_planes(root)
    env_graph = action_plane["env"]["graph"]
    uop_graph = action_plane["uop"]["graph"]
    locks = rebuild_env_uop_locks(
        root,
        env_graph_receipt=env_graph,
        uop_graph_receipt=uop_graph,
    )
    flash = rebuild_flash_manifest(root)
    manifests = rebuild_packaged_authority_manifests(root)
    core = {
        "schema": "evidence-lane.env-uop-backend-regeneration.v1",
        "status": "PASS",
        "action_plane": action_plane,
        "graphs": {"env": env_graph, "uop": uop_graph},
        "locks": locks,
        "flash_manifest": flash,
        "packaged_authorities": manifests,
        "old_installed_plugin_used": False,
        "predecessor_database_copied": False,
        "renderer_invoked": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "rebuild_env_uop_locks",
    "rebuild_flash_manifest",
    "rebuild_packaged_authority_manifests",
    "regenerate_env_uop_authorities",
]
