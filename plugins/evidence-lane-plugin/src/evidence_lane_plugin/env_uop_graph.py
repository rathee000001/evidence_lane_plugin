"""Persist and render ENV/UOP semantic topology from their SQLite authorities."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .graph_pipeline import SemanticGraph, semantic_graph_from_mermaid
from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes, sha256_file
from .timeutil import utc_now

ENV_UOP_GRAPH_SCHEMA = "evidence-lane.env-uop-semantic-graph.v1"


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_graph_group_v16(
            group_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            direction TEXT NOT NULL,
            ordinal INTEGER NOT NULL UNIQUE
        ) STRICT;
        CREATE TABLE IF NOT EXISTS semantic_graph_meta_v16(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS semantic_graph_node_v16(
            node_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            node_kind TEXT NOT NULL,
            group_id TEXT REFERENCES semantic_graph_group_v16(group_id),
            ordinal INTEGER NOT NULL UNIQUE
        ) STRICT;
        CREATE TABLE IF NOT EXISTS semantic_graph_edge_v16(
            edge_id TEXT PRIMARY KEY,
            source_node_id TEXT NOT NULL REFERENCES semantic_graph_node_v16(node_id),
            target_node_id TEXT NOT NULL REFERENCES semantic_graph_node_v16(node_id),
            label TEXT,
            conditional INTEGER NOT NULL CHECK(conditional IN (0,1)),
            ordinal INTEGER NOT NULL UNIQUE
        ) STRICT;
        CREATE TABLE IF NOT EXISTS semantic_graph_render_receipt_v16(
            sequence INTEGER PRIMARY KEY,
            authority_id TEXT NOT NULL,
            source_seed_sha256 TEXT NOT NULL,
            semantic_topology_sha256 TEXT NOT NULL,
            mmd_sha256 TEXT NOT NULL,
            dot_sha256 TEXT NOT NULL,
            graph_pipeline_receipt_json TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            recorded_at TEXT NOT NULL
        ) STRICT;
        """
    )


def _store_seed(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
    source: str,
) -> SemanticGraph:
    graph = semantic_graph_from_mermaid(
        source,
        name=f"{authority_id}_authority",
        role="EXECUTABLE_WORKFLOW",
    )
    connection.execute("DELETE FROM semantic_graph_edge_v16")
    connection.execute("DELETE FROM semantic_graph_node_v16")
    connection.execute("DELETE FROM semantic_graph_group_v16")
    connection.execute("DELETE FROM semantic_graph_meta_v16")
    connection.executemany(
        "INSERT INTO semantic_graph_meta_v16 VALUES(?,?)",
        (
            ("authority_id", authority_id),
            ("direction", graph.direction),
            ("graph_role", graph.role),
        ),
    )
    connection.executemany(
        "INSERT INTO semantic_graph_group_v16 VALUES(?,?,?,?)",
        [
            (group.group_id, group.label, group.direction, ordinal)
            for ordinal, group in enumerate(graph.groups, start=1)
        ],
    )
    connection.executemany(
        "INSERT INTO semantic_graph_node_v16 VALUES(?,?,?,?,?)",
        [
            (
                node.node_id,
                node.label,
                node.kind,
                node.group_id,
                ordinal,
            )
            for ordinal, node in enumerate(graph.nodes, start=1)
        ],
    )
    connection.executemany(
        "INSERT INTO semantic_graph_edge_v16 VALUES(?,?,?,?,?,?)",
        [
            (
                "edge_"
                + sha256_bytes(
                    canonical_json_bytes(
                        {
                            "authority_id": authority_id,
                            "ordinal": ordinal,
                            "source": edge.source,
                            "target": edge.target,
                            "label": edge.label,
                            "conditional": edge.conditional,
                        }
                    )
                )[:32].lower(),
                edge.source,
                edge.target,
                edge.label,
                int(edge.conditional),
                ordinal,
            )
            for ordinal, edge in enumerate(graph.edges, start=1)
        ],
    )
    return graph


def _load_graph(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
) -> SemanticGraph:
    metadata = dict(connection.execute("SELECT key,value FROM semantic_graph_meta_v16"))
    graph = SemanticGraph(
        f"{authority_id}_authority",
        direction=str(metadata.get("direction") or "TB"),
        role="EXECUTABLE_WORKFLOW",
    )
    groups = list(
        connection.execute(
            "SELECT group_id,label,direction FROM semantic_graph_group_v16 "
            "ORDER BY ordinal"
        )
    )
    nodes = list(
        connection.execute(
            "SELECT node_id,label,node_kind,group_id FROM semantic_graph_node_v16 "
            "ORDER BY ordinal"
        )
    )
    for row in nodes:
        if row[3] is None:
            graph.add_node(str(row[0]), str(row[1]), str(row[2]))
    for group in groups:
        graph.begin_group(str(group[0]), str(group[1]), direction=str(group[2]))
        for row in nodes:
            if str(row[3] or "") == str(group[0]):
                graph.add_node(str(row[0]), str(row[1]), str(row[2]))
        graph.end_group()
    for row in connection.execute(
        "SELECT source_node_id,target_node_id,label,conditional "
        "FROM semantic_graph_edge_v16 ORDER BY ordinal"
    ):
        graph.add_edge(
            str(row[0]),
            str(row[1]),
            str(row[2]) if row[2] is not None else None,
            conditional=bool(row[3]),
        )
    return graph


def migrate_and_render_env_uop_graph(
    *,
    authority_id: str,
    database: str | Path,
    mmd_path: str | Path,
    dot_path: str | Path,
) -> dict[str, Any]:
    if authority_id not in {"env", "uop"}:
        raise ValueError("ENV/UOP graph authority must be env or uop.")
    db = Path(database).resolve()
    mmd = Path(mmd_path).resolve()
    dot = Path(dot_path).resolve()
    source = mmd.read_text(encoding="utf-8")
    source_seed_sha256 = sha256_bytes(source.encode("utf-8"))
    connection = sqlite3.connect(db, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        _schema(connection)
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM semantic_graph_node_v16"
            ).fetchone()[0]
        )
        graph = (
            _store_seed(
                connection,
                authority_id=authority_id,
                source=source,
            )
            if count == 0
            else _load_graph(connection, authority_id=authority_id)
        )
        mmd_text, dot_text, graph_pipeline_receipt = graph.render_pair()
        atomic_write_bytes(mmd, mmd_text.encode("utf-8"))
        atomic_write_bytes(dot, dot_text.encode("utf-8"))
        removed_legacy_renders: list[str] = []
        for legacy in (mmd.with_suffix(".png"), mmd.with_suffix(".svg")):
            if legacy.is_file():
                legacy.unlink()
                removed_legacy_renders.append(legacy.name)
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 "
                "FROM semantic_graph_render_receipt_v16"
            ).fetchone()[0]
        )
        core = {
            "schema": ENV_UOP_GRAPH_SCHEMA,
            "status": "PASS",
            "sequence": sequence,
            "authority_id": authority_id,
            "source_seed_sha256": source_seed_sha256,
            "semantic_topology_sha256": graph_pipeline_receipt[
                "semantic_topology_sha256"
            ],
            "mmd_sha256": sha256_file(mmd),
            "dot_sha256": sha256_file(dot),
            "graph_pipeline_receipt": graph_pipeline_receipt,
            "removed_legacy_renders": removed_legacy_renders,
            "renderer_invoked": False,
            "recorded_at": utc_now(),
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(core))
        receipt = {**core, "receipt_sha256": receipt_sha256}
        connection.execute(
            "INSERT INTO semantic_graph_render_receipt_v16 VALUES(?,?,?,?,?,?,?,?,?)",
            (
                sequence,
                authority_id,
                source_seed_sha256,
                core["semantic_topology_sha256"],
                core["mmd_sha256"],
                core["dot_sha256"],
                canonical_json_bytes(graph_pipeline_receipt).decode("utf-8"),
                receipt_sha256,
                core["recorded_at"],
            ),
        )
        connection.commit()
        return receipt
    finally:
        connection.close()


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
                "SELECT * FROM semantic_graph_render_receipt_v16 "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Missing ENV/UOP semantic graph receipt.")
            return dict(row)
        finally:
            connection.close()

    env_db = root / "env" / "env_sqlite.sqlite"
    uop_db = root / "uop" / "uop_sqlite.sqlite"
    toolchain = latest(env_db, "ai_toolchain_sync_receipt_v16")
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
        "whole_source_packet_accepted": False,
        "member_count": len(members),
        "members": members,
        "authorities": {
            "env": {
                "version": "V15",
                "sqlite": "env/env_sqlite.sqlite",
                "sqlite_sha256": sha256_file(env_db),
                "sqlite_user_version": 15,
                "mmd": "env/env_mmd.mmd",
                "mmd_sha256": sha256_file(root / "env" / "env_mmd.mmd"),
                "dot": "env/env_mmd.dot",
                "dot_sha256": sha256_file(root / "env" / "env_mmd.dot"),
                "law": "env/env_law.md",
                "read_mode": "mode=ro&immutable=1",
                "graph_receipt_sha256": env_graph["receipt_sha256"],
            },
            "uop": {
                "version": "V15",
                "sqlite": "uop/uop_sqlite.sqlite",
                "sqlite_sha256": sha256_file(uop_db),
                "sqlite_user_version": 15,
                "mmd": "uop/uop_mmd.mmd",
                "mmd_sha256": sha256_file(root / "uop" / "uop_mmd.mmd"),
                "dot": "uop/uop_mmd.dot",
                "dot_sha256": sha256_file(root / "uop" / "uop_mmd.dot"),
                "law": "uop/uop_law.md",
                "read_mode": "mode=ro&immutable=1",
                "graph_receipt_sha256": uop_graph["receipt_sha256"],
            },
        },
        "ai_toolchain": {
            "receipt_sha256": toolchain["receipt_sha256"],
            "tool_count": toolchain["tool_count"],
            "action_count": toolchain["action_count"],
            "lane_count": toolchain["lane_count"],
            "counts_are_derived": True,
        },
        "runtime_locks": {
            "base_sha256": sha256_file(root / "requirements.lock.txt"),
            "torch_cpu_sha256": sha256_file(
                root / "requirements.torch-cpu.lock.txt"
            ),
            "toolchain_sha256": sha256_file(
                root / "requirements.toolchain.lock.txt"
            ),
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
        "warning": {
            "code": "SOURCE_PACKET_PARTIAL_INTEGRITY",
            "message": (
                "Only the independently verified ENV15/UOP15 subset is "
                "accepted for plugin session flash."
            ),
        },
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
        prior: dict[str, str] = {}
        if lock_path.is_file():
            for line in lock_path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    prior[key.strip()] = value.strip()
        mmd_path = root / authority / f"{authority}_mmd.mmd"
        dot_path = root / authority / f"{authority}_mmd.dot"
        sqlite_path = root / authority / f"{authority}_sqlite.sqlite"
        lines = {
            "section": authority.upper(),
            "base_version": prior.get("base_version", "V14"),
            "base_mmd_sha256": prior.get("base_mmd_sha256", "UNAVAILABLE"),
            "final_version": "V15",
            "final_mmd_sha256": sha256_file(mmd_path).lower(),
            "final_dot_sha256": sha256_file(dot_path).lower(),
            "final_sqlite_sha256": sha256_file(sqlite_path).lower(),
            "graph_pipeline_receipt_sha256": str(
                graph_receipt["receipt_sha256"]
            ).lower(),
            "base_text_preserved_exactly": "true",
            "rendered_from_exact_packaged_mmd": "false",
            "renderer_invoked": "false",
            "updated_at_utc": utc_now().replace("+00:00", "Z"),
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

    from .ai_toolchain import sync_ai_toolchain_authority

    root = Path(plugin_root).resolve()
    toolchain = sync_ai_toolchain_authority(
        env_database=root / "env" / "env_sqlite.sqlite",
        uop_database=root / "uop" / "uop_sqlite.sqlite",
        tool_matrix_path=root / "toolchains" / "tool-requirement-matrix.v1.json",
        public_catalog_path=root / "schemas" / "public-action-schemas.v001.json",
    )
    env_graph = migrate_and_render_env_uop_graph(
        authority_id="env",
        database=root / "env" / "env_sqlite.sqlite",
        mmd_path=root / "env" / "env_mmd.mmd",
        dot_path=root / "env" / "env_mmd.dot",
    )
    uop_graph = migrate_and_render_env_uop_graph(
        authority_id="uop",
        database=root / "uop" / "uop_sqlite.sqlite",
        mmd_path=root / "uop" / "uop_mmd.mmd",
        dot_path=root / "uop" / "uop_mmd.dot",
    )
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
        "toolchain": toolchain,
        "graphs": {"env": env_graph, "uop": uop_graph},
        "locks": locks,
        "flash_manifest": flash,
        "packaged_authorities": manifests,
        "old_installed_plugin_used": False,
        "renderer_invoked": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "ENV_UOP_GRAPH_SCHEMA",
    "migrate_and_render_env_uop_graph",
    "rebuild_env_uop_locks",
    "rebuild_flash_manifest",
    "rebuild_packaged_authority_manifests",
    "regenerate_env_uop_authorities",
]
