"""Separate native TOM metadata and offline PBIX data-model readers."""

from __future__ import annotations

import base64
import hashlib

from .errors import LaneError
from .powerbi_process import invoke_shared


def parse_native(format, inputs, *, timeout_seconds=45):
    if format not in {"bim", "tmdl"} or not 1 <= len(inputs) <= 256:
        raise LaneError(
            "POWERBI_MODEL_INPUT_INVALID",
            "Select one BIM document or a bounded complete TMDL model.",
        )
    if sum(len(content) for _, content in inputs) > 16_777_216 or any(
        len(content) > 8_388_608 for _, content in inputs
    ):
        raise LaneError(
            "POWERBI_INPUT_BUDGET", "The selected model exceeds its native byte budget."
        )
    from .powerbi_parsers import decode_text

    body = {"schema": "evidence-lane.powerbi-tom-request.v1", "format": format}
    if format == "bim":
        if len(inputs) != 1:
            raise LaneError("POWERBI_MODEL_INPUT_INVALID", "Select one exact BIM model document.")
        body["database_json"] = decode_text(inputs[0][1])
    else:
        body["files"] = [
            {"name": name, "content_base64": base64.b64encode(content).decode("ascii")}
            for name, content in inputs
        ]
    value, evidence = invoke_shared(
        "powerbi_tom_runtime", "evidence-lane-powerbi.exe", body, timeout_seconds=timeout_seconds
    )
    if (
        value.get("schema") != "evidence-lane.powerbi-tom.v1"
        or value.get("package_version") != "19.114.12"
        or value.get("metadata_deserialized") is not True
        or not isinstance(value.get("database"), dict)
        or any(
            value.get(field) is not False
            for field in ("server_connected", "dax_executed", "data_refreshed")
        )
    ):
        raise LaneError(
            "POWERBI_RUNTIME_INVALID",
            "The TOM response does not match its pinned protocol and offline behavior.",
        )
    return value, {
        **evidence,
        "engine": "Microsoft.AnalysisServices.TOM",
        "version": "19.114.12",
        "filesystem_scope": "private_temporary_copy_of_admitted_TMDL_only",
    }


def parse_pbix(content, *, max_rows_per_table=20, timeout_seconds=45):
    expected = hashlib.sha256(content).hexdigest()
    value, evidence = invoke_shared(
        "powerbi_pbix_runtime",
        "python.exe",
        {
            "schema": "evidence-lane.powerbi-pbix-request.v1",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "sha256": expected,
            "max_rows_per_table": max_rows_per_table,
        },
        script_name="powerbi_pbix_child.py",
        timeout_seconds=timeout_seconds,
    )
    if (
        value.get("schema") != "evidence-lane.powerbi-pbix.v1"
        or value.get("package_version") != "0.15.5"
        or value.get("python_version") != "3.13.15"
        or value.get("sha256") != expected
        or value.get("metadata_read") is not True
        or value.get("bounded_row_samples") is not True
        or any(
            value.get(field) is not False
            for field in (
                "server_connected",
                "dax_executed",
                "data_refreshed",
                "network_used",
                "source_bytes_mutated",
            )
        )
    ):
        raise LaneError(
            "POWERBI_RUNTIME_INVALID",
            "The PBIX response does not match its pinned input and offline protocol.",
        )
    return value, {
        **evidence,
        "engine": "PBIXRay",
        "version": "0.15.5",
        "python_version": "3.13.15",
        "filesystem_scope": "admitted_bytes_in_memory_only",
        "bounded_row_samples": True,
        "rows_requested": max_rows_per_table > 0,
    }
