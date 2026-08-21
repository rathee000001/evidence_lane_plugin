"""Digest-keyed read-only ENV15/UOP15 runtime projection.

The locked authority databases remain immutable inputs.  This module builds a
derived, content-addressed SQLite projection under the installation data root;
it never writes inside the plugin bundle or a PV.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import sqlite3
from pathlib import Path
from typing import Any

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file

FLASH_PROJECTION_SCHEMA = "evidence-lane.env-uop-runtime-projection.v1"


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "bytes": len(value),
            "sha256": sha256_bytes(value),
            "value": base64.b64encode(value).decode("ascii"),
        }
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _host_abi() -> str:
    value = (
        f"python-{platform.python_version()}-sqlite-{sqlite3.sqlite_version}-"
        f"{platform.system().lower()}-{platform.machine().lower()}"
    )
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")


class FlashRuntimeProjection:
    def __init__(self, *, data_root: str | Path, asset_root: str | Path) -> None:
        self.data_root = Path(data_root).resolve()
        self.asset_root = Path(asset_root).resolve()

    @property
    def installed_content_addressed_bundle(self) -> bool:
        normalized = self.asset_root.as_posix().lower()
        return "/.codex/plugins/cache/" in normalized

    @staticmethod
    def _source_tables(path: Path) -> list[dict[str, Any]]:
        uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows = list(
                connection.execute(
                    """
                    SELECT name, type, sql
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                )
            )
            result: list[dict[str, Any]] = []
            for row in rows:
                name = str(row["name"])
                object_type = str(row["type"])
                record: dict[str, Any] = {
                    "name": name,
                    "object_type": object_type,
                    "sql": row["sql"],
                    "rows": [],
                }
                if object_type == "table" and not any(
                    name.endswith(suffix)
                    for suffix in ("_data", "_idx", "_docsize", "_config")
                ):
                    try:
                        source_rows = connection.execute(
                            f"SELECT * FROM {_quoted_identifier(name)} ORDER BY rowid"
                        ).fetchall()
                    except sqlite3.DatabaseError:
                        source_rows = connection.execute(
                            f"SELECT * FROM {_quoted_identifier(name)}"
                        ).fetchall()
                    record["rows"] = [
                        {
                            key: _json_value(value)
                            for key, value in dict(item).items()
                        }
                        for item in source_rows
                    ]
                result.append(record)
            return result
        finally:
            connection.close()

    @staticmethod
    def _validate(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
        uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            meta = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key,value FROM projection_meta")
            }
            row_count = int(
                connection.execute("SELECT COUNT(*) FROM authority_row").fetchone()[0]
            )
            fts_count = int(
                connection.execute("SELECT COUNT(*) FROM authority_fts").fetchone()[0]
            )
        finally:
            connection.close()
        require(
            integrity == ["ok"]
            and not foreign_keys
            and meta.get("schema") == FLASH_PROJECTION_SCHEMA
            and meta.get("authority_digest") == expected["authority_digest"]
            and meta.get("host_abi") == expected["host_abi"]
            and row_count == fts_count,
            "FLASH_RUNTIME_PROJECTION_INVALID",
            "The derived ENV/UOP runtime projection failed validation.",
            status="FAIL",
            integrity=integrity,
            foreign_key_errors=len(foreign_keys),
            row_count=row_count,
            fts_count=fts_count,
        )
        return {
            "integrity": integrity,
            "foreign_key_errors": 0,
            "row_count": row_count,
            "fts_count": fts_count,
        }

    def ensure(self, report: dict[str, Any]) -> dict[str, Any]:
        host_abi = _host_abi()
        key_payload = {
            "schema": FLASH_PROJECTION_SCHEMA,
            "authority_digest": report["authority_digest"],
            "manifest_sha256": report["manifest_sha256"],
            "host_abi": host_abi,
        }
        projection_key = sha256_bytes(canonical_json_bytes(key_payload))
        root = (
            self.data_root
            / "installation"
            / "flash_projection"
            / projection_key.lower()
        )
        path = root / "env_uop_projection.sqlite"
        manifest_path = root / "manifest.json"
        can_reuse = self.installed_content_addressed_bundle
        if can_reuse and path.is_file() and manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                require(
                    manifest.get("key") == key_payload
                    and manifest.get("sqlite_sha256") == sha256_file(path),
                    "FLASH_RUNTIME_PROJECTION_HASH_MISMATCH",
                    "The cached ENV/UOP projection does not match its manifest.",
                    status="MISMATCH",
                )
                validation = self._validate(path, key_payload)
                return {
                    "status": "PASS",
                    "action": "REUSED",
                    "path": str(path),
                    "projection_key": projection_key,
                    "sqlite_sha256": manifest["sqlite_sha256"],
                    "installed_content_addressed_bundle": True,
                    "source_verification": "FULL_BEFORE_REUSE",
                    **validation,
                }
            except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
                # Rebuild only the exact derived projection; locked inputs are untouched.
                pass

        root.mkdir(parents=True, exist_ok=True)
        temp_path = root / "env_uop_projection.sqlite.tmp"
        if temp_path.is_file():
            temp_path.unlink()
        connection = sqlite3.connect(temp_path, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE projection_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) STRICT;
                CREATE TABLE authority_object(
                    authority TEXT NOT NULL,
                    object_name TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    source_sql TEXT,
                    source_sha256 TEXT NOT NULL,
                    PRIMARY KEY(authority, object_name)
                ) STRICT;
                CREATE TABLE authority_row(
                    authority TEXT NOT NULL,
                    object_name TEXT NOT NULL,
                    row_ordinal INTEGER NOT NULL,
                    row_json TEXT NOT NULL,
                    row_sha256 TEXT NOT NULL,
                    PRIMARY KEY(authority, object_name, row_ordinal),
                    FOREIGN KEY(authority, object_name)
                        REFERENCES authority_object(authority, object_name)
                ) STRICT;
                CREATE VIRTUAL TABLE authority_fts USING fts5(
                    authority UNINDEXED,
                    object_name,
                    row_json,
                    tokenize='unicode61'
                );
                """
            )
            connection.executemany(
                "INSERT INTO projection_meta(key,value) VALUES(?,?)",
                (
                    ("schema", FLASH_PROJECTION_SCHEMA),
                    ("authority_digest", report["authority_digest"]),
                    ("manifest_sha256", report["manifest_sha256"]),
                    ("host_abi", host_abi),
                ),
            )
            for authority in ("env", "uop"):
                source_path = self.asset_root / authority / f"{authority}_sqlite.sqlite"
                for source_object in self._source_tables(source_path):
                    object_digest = sha256_bytes(canonical_json_bytes(source_object))
                    connection.execute(
                        "INSERT INTO authority_object VALUES(?,?,?,?,?)",
                        (
                            authority,
                            source_object["name"],
                            source_object["object_type"],
                            source_object["sql"],
                            object_digest,
                        ),
                    )
                    for ordinal, row in enumerate(source_object["rows"], start=1):
                        row_json = json.dumps(
                            row, sort_keys=True, separators=(",", ":")
                        )
                        row_digest = sha256_bytes(row_json.encode("utf-8"))
                        connection.execute(
                            "INSERT INTO authority_row VALUES(?,?,?,?,?)",
                            (
                                authority,
                                source_object["name"],
                                ordinal,
                                row_json,
                                row_digest,
                            ),
                        )
                        connection.execute(
                            "INSERT INTO authority_fts VALUES(?,?,?)",
                            (authority, source_object["name"], row_json),
                        )
            connection.commit()
        finally:
            connection.close()
        os.replace(temp_path, path)
        validation = self._validate(path, key_payload)
        manifest = {
            "schema": FLASH_PROJECTION_SCHEMA,
            "key": key_payload,
            "projection_key": projection_key,
            "sqlite_sha256": sha256_file(path),
            "immutable_inputs": True,
            "inside_pv": False,
            "hidden_reasoning_stored": False,
        }
        atomic_write_json(manifest_path, manifest)
        return {
            "status": "PASS",
            "action": "REBUILT",
            "path": str(path),
            "manifest_path": str(manifest_path),
            "projection_key": projection_key,
            "sqlite_sha256": manifest["sqlite_sha256"],
            "installed_content_addressed_bundle": can_reuse,
            "source_verification": "FULL_BEFORE_REUSE",
            **validation,
        }
