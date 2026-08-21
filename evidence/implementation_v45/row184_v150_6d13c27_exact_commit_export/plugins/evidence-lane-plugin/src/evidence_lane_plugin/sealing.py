"""Deterministic PV archives and optional AES-GCM Drive envelopes."""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .pv_package import validate_pv_package

_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def deterministic_archive(directory: str | Path) -> tuple[bytes, dict[str, Any]]:
    root = Path(directory).resolve()
    validation = validate_pv_package(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_STORED,
        strict_timestamps=True,
    ) as archive:
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, _ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            archive.writestr(info, path.read_bytes())
    payload = buffer.getvalue()
    return payload, {
        "schema": "evidence-lane.pv-archive.v1",
        "pv_ref": root.name,
        "package_sha256": validation["package_sha256"],
        "archive_sha256": sha256_bytes(payload),
        "archive_bytes": len(payload),
        "member_count": validation["members"],
    }


def _decode_key(value: str) -> bytes:
    raw = value.strip()
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise EvidenceLaneError(
            "DRIVE_ENCRYPTION_KEY_INVALID",
            "The Drive encryption key is not valid URL-safe base64.",
            status="BLOCKED",
        ) from exc
    require(
        len(key) == 32,
        "DRIVE_ENCRYPTION_KEY_LENGTH_INVALID",
        "The Drive encryption key must decode to exactly 32 bytes.",
        status="BLOCKED",
    )
    return key


def seal_archive(
    archive: bytes,
    metadata: dict[str, Any],
    *,
    key_value: str | None,
    require_encryption: bool,
) -> tuple[bytes, dict[str, Any], str]:
    if not key_value:
        require(
            not require_encryption,
            "DRIVE_ENCRYPTION_REQUIRED",
            "Private or restricted source requires an explicit Drive encryption key.",
            status="BLOCKED",
        )
        return archive, {**metadata, "encryption": "NONE"}, ".pv.zip"
    key = _decode_key(key_value)
    nonce = os.urandom(12)
    associated_data = canonical_json_bytes(metadata)
    ciphertext = AESGCM(key).encrypt(nonce, archive, associated_data)
    envelope = {
        "schema": "evidence-lane.pv-aesgcm-envelope.v1",
        "algorithm": "AES-256-GCM",
        "nonce_b64": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "associated_data_b64": base64.urlsafe_b64encode(associated_data).decode(
            "ascii"
        ),
        "ciphertext_b64": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
    }
    payload = canonical_json_bytes(envelope)
    return (
        payload,
        {
            **metadata,
            "encryption": "AES-256-GCM",
            "sealed_sha256": sha256_bytes(payload),
            "sealed_bytes": len(payload),
        },
        ".pv.enc.json",
    )


def unseal_archive(
    payload: bytes, *, key_value: str | None
) -> tuple[bytes, dict[str, Any]]:
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload, {"encryption": "NONE", "archive_sha256": sha256_bytes(payload)}
    if envelope.get("schema") != "evidence-lane.pv-aesgcm-envelope.v1":
        return payload, {"encryption": "NONE", "archive_sha256": sha256_bytes(payload)}
    require(
        bool(key_value),
        "DRIVE_DECRYPTION_KEY_REQUIRED",
        "This sealed PV requires the user-owned Drive encryption key.",
        status="BLOCKED",
    )
    key = _decode_key(key_value or "")
    nonce = base64.urlsafe_b64decode(envelope["nonce_b64"])
    associated_data = base64.urlsafe_b64decode(envelope["associated_data_b64"])
    ciphertext = base64.urlsafe_b64decode(envelope["ciphertext_b64"])
    try:
        archive = AESGCM(key).decrypt(nonce, ciphertext, associated_data)
    except Exception as exc:
        raise EvidenceLaneError(
            "DRIVE_DECRYPTION_FAILED",
            "The sealed PV could not be authenticated and decrypted.",
            status="MISMATCH",
        ) from exc
    return archive, json.loads(associated_data.decode("utf-8"))
