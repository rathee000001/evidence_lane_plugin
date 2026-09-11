"""Immutable images and media in the owning images_ocr lane, with CAS exports."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue

from .errors import LaneError
from .hashing import canonical_json_bytes
from .media_contracts import (
    MediaExport,
    MediaIndex,
    MediaQuery,
    MediaRead,
    MediaSelection,
    MediaTransform,
)
from .media_parsers import EXTENSIONS, MEDIA_EXTENSIONS, RASTER_EXTENSIONS, digest
from .media_schema import MEDIA_MIGRATIONS, MEDIA_TABLES
from .migrations import apply_migrations, read_compatibility
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql
from .storage import bounded_project_read, json_text, now, project_snapshot, reject_links
from .tool_routes import ToolRoute


def _parser_contract():
    names = (
        "media_parsers.py",
        "media_transform.py",
        "media_native.py",
        "media_workers.py",
        "media_child.py",
        "media_process.py",
        "media_contracts.py",
        "media_ocr.py",
    )
    return digest(
        canonical_json_bytes(
            {name: digest(Path(__file__).with_name(name).read_bytes()) for name in names}
        )
    )


def _chunks(facts):
    for item in facts["items"]:
        for ordinal, start in enumerate(range(0, len(item["text"]), 4096)):
            yield item["item_id"], ordinal, item["text"][start : start + 4096]


def format_contract():
    return {
        "raster": {
            "extensions": sorted(RASTER_EXTENSIONS),
            "operations": ["metadata", "frame_inspection", "selected_frame_transform", "ocr"],
            "transforms": [
                "exif_orientation",
                "crop",
                "resize",
                "right_angle_rotation",
                "flip",
                "color_mode",
            ],
            "outputs": ["PNG", "JPEG", "WEBP", "TIFF"],
            "animated_transform": "explicit_one_based_frame_required",
            "ocr_review": "low_confidence_lines_and_empty_selected_frames_require_review",
            "metadata": "originals_exact; derivatives_preserve_supported_EXIF_ICC_DPI_PNG_text_or_strip",
            "unsupported": [
                "arbitrary_edit_scripts",
                "generative_image_editing",
                "unqualified_color_profile_conversion",
            ],
        },
        "svg": {
            "extensions": [".svg"],
            "operation": "passive_structure_and_text",
            "renderer_invoked": False,
            "scripts_or_external_resources_executed": False,
        },
        "audio_video": {
            "extensions": sorted(MEDIA_EXTENSIONS),
            "operations": ["FFmpeg_metadata", "bounded_PNG_frame", "bounded_PCM_WAV_segment"],
            "speech_transcribed": False,
            "source_frame_pts_verified": False,
            "network_inputs_supported": False,
        },
        "limits": {
            "input_bytes": 16_777_216,
            "frames": 500,
            "pixels_per_frame": 25_000_000,
            "ocr_frames_per_operation": 25,
            "audio_seconds": 120,
        },
        "source_mapping": {
            "image_file": "media_file/media_version/media_current",
            "image_metadata": "media_structure/media_frame/media_exif",
            "image_ocr_run_and_lines": "media_ocr_run/media_ocr_line/media_review_region",
            "artifact_media_probe": "media_structure/media_stream/media_tag",
        },
    }


def index_media(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() not in EXTENSIONS:
        raise LaneError("MEDIA_FORMAT_UNSUPPORTED", "Select a supported image or media file.")
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, "read", path=path)
    media_id = digest(canonical_json_bytes([store.project_id, "source", relative]))
    if current_snapshot(store, media_id) != request.expected_snapshot:
        raise LaneError(
            "MEDIA_SNAPSHOT_CHANGED", "Refresh requires the exact current media snapshot."
        )
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior.get('source_route') is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this previously routed input.')
    content, parsed = _worker(
        execution,
        "media_parse_file",
        {
            "filename": str(path),
            "logical_name": relative,
            "max_file_bytes": request.max_file_bytes,
            "parse_options": {
                "max_frames": request.max_frames,
                "max_pixels_per_frame": request.max_pixels_per_frame,
            },
        },
    )
    if _bytes(path, request.max_file_bytes) != content:
        raise LaneError("MEDIA_SOURCE_CHANGED", "The admitted media changed during extraction.")
    return publish(
        context,
        media_id=media_id,
        logical_name=relative,
        source_path=relative,
        origin="source",
        previous=request.expected_snapshot,
        content=content,
        parsed=parsed,
        operation="media_index",
        source_content=content,
        max_file_bytes=request.max_file_bytes, intake=True,
    )


def transform_media(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    if (
        manifest["raw_object"] != request.expected_sha256
        or current_snapshot(store, manifest["media_id"]) != request.snapshot_id
    ):
        raise LaneError(
            "MEDIA_TRANSFORM_SNAPSHOT_CHANGED", "Transform the exact current media bytes."
        )
    name = _relative(request.logical_name)
    if "/" in name:
        raise LaneError("MEDIA_OUTPUT_NAME_INVALID", "Choose one derivative filename.")
    content, parsed = _worker(
        execution,
        "media_transform",
        {
            "logical_name": name,
            "content_base64": base64.b64encode(
                store.lane("images_ocr").read_object(manifest["raw_object"])
            ).decode(),
            "expected_sha256": request.expected_sha256,
            "request": request.model_dump(mode="json"),
        },
    )
    return publish(
        context,
        media_id=manifest["media_id"],
        logical_name=name,
        source_path=manifest["source_path"],
        origin=manifest["origin"],
        previous=request.snapshot_id,
        content=content,
        parsed=parsed,
        operation="media_transform",
    )


class MediaResult(Contract):
    project_id: str

    lane_id: Literal["images_ocr"] = "images_ocr"

    operation: str

    result: dict[str, JsonValue]


def result(store, operation, body):

    value = MediaResult(project_id=store.project_id, operation=operation, result=body)

    if len(canonical_json_bytes(value.model_dump(mode="json"))) > 2_097_152:
        raise LaneError("MEDIA_OUTPUT_BUDGET", "Select a smaller media result.")

    return value


def _relative(value):

    path = PurePosixPath(value.replace("\\", "/"))

    if (
        path.is_absolute()
        or ".." in path.parts
        or ":" in str(path)
        or "\x00" in str(path)
        or str(path) == "."
    ):
        raise LaneError("MEDIA_PATH_INVALID", "Select an exact project-relative media filename.")

    return path.as_posix()


def _bytes(path, limit=16_777_216):

    reject_links(path, Path(path.anchor))

    with path.open("rb") as stream:
        content = stream.read(limit + 1)

    if len(content) > limit:
        raise LaneError("MEDIA_FILE_BYTE_BUDGET", "The selected media exceeds its byte budget.")

    return content


def _lane(store):

    try:
        return store.lane("images_ocr")

    except LaneError as error:
        if error.code != "LANE_NOT_INITIALIZED":
            raise

        return None


def _calls(execution, remaining):

    execution.guard.check()

    if execution.guard.calls + remaining > execution.guard.task.budget.max_tool_calls:
        raise LaneError(
            "DELTA_TOOL_BUDGET",
            "The media operation must leave room for its required verification.",
        )


def current_snapshot(store, media_id):

    lane = _lane(store)

    if lane is None:
        return None

    read_compatibility(lane, MEDIA_MIGRATIONS)

    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='media_current'"
        ).fetchone():
            return None

        row = connection.execute(
            "SELECT snapshot_id FROM media_current WHERE media_id=?", (media_id,)
        ).fetchone()

    return row[0] if row else None


def read_snapshot(store, snapshot_id):

    lane = _lane(store)

    if lane is None:
        raise LaneError("MEDIA_SNAPSHOT_MISSING", "Index or generate the selected media first.")

    read_compatibility(lane, MEDIA_MIGRATIONS)

    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='media_version'"
        ).fetchone():
            raise LaneError("MEDIA_SNAPSHOT_MISSING", "Index or generate the selected media first.")

        row = connection.execute(
            "SELECT * FROM media_version WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()

    if row is None:
        raise LaneError(
            "MEDIA_SNAPSHOT_MISSING", "Select an exact media snapshot from this MEDIA lane."
        )

    manifest = json.loads(lane.read_object(row["manifest_object"]))

    if (
        digest(canonical_json_bytes(manifest)) != snapshot_id
        or manifest["project_id"] != store.project_id
        or manifest["lane_id"] != "images_ocr"
        or any(
            manifest[key] != row[key]
            for key in (
                "media_id",
                "generation",
                "previous_snapshot",
                "raw_object",
                "facts_object",
                "parser_contract",
            )
        )
    ):
        raise LaneError(
            "MEDIA_SNAPSHOT_INTEGRITY", "The media manifest differs from its indexed identity."
        )

    facts = json.loads(lane.read_object(manifest["facts_object"]))

    return manifest, facts


def _worker(execution, operation, arguments):

    response = execution.submit(operation, arguments).result()

    if response["status"] != "ok":
        raise LaneError(
            response.get("code", "MEDIA_WORKER_FAILED"),
            "The selected media worker did not complete.",
        )

    body = json.loads(json_text(response["result"]))

    content = base64.b64decode(body.pop("content_base64"), validate=True)

    if (
        digest(content) != body["sha256"]
        or len(content) != body["bytes"]
        or body["filename"] != arguments["logical_name"]
    ):
        raise LaneError(
            "MEDIA_WORKER_BINDING",
            "The media worker returned different bytes or a different media.",
        )

    return content, body


def _natural(lane, snapshot_id, name, content):

    name = _relative(name)

    if "/" in name:
        raise LaneError(
            "MEDIA_OUTPUT_NAME_INVALID", "A lane-owned natural artifact requires a simple filename."
        )

    target = lane.files / "natural" / snapshot_id / name

    reject_links(target, lane.folder)

    target.parent.mkdir(parents=True, exist_ok=True)

    reject_links(target, lane.folder)

    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    except FileExistsError:
        if _bytes(target, 16_777_216) != content:
            raise LaneError(
                "MEDIA_ARTIFACT_CHANGED",
                "An existing natural artifact differs from its immutable bytes.",
            ) from None

    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)

            stream.flush()

            os.fsync(stream.fileno())

    if _bytes(target, 16_777_216) != content:
        raise LaneError(
            "MEDIA_ARTIFACT_WRITE_FAILED", "The natural media artifact failed byte verification."
        )

    return str(target)


def publish(
    context,
    *,
    media_id,
    logical_name,
    source_path,
    origin,
    previous,
    content,
    parsed,
    operation,
    source_content=None,
    source_observation_route_id=None,
    max_file_bytes=None,
    intake=False,
    inputs=None,
):

    execution, store = context.execution, context.execution.store

    if current_snapshot(store, media_id) != previous:
        raise LaneError(
            "MEDIA_SNAPSHOT_CHANGED", "Bind this operation to the exact current media snapshot."
        )

    prior = read_snapshot(store, previous)[0] if previous else None

    selection = (execution.guard.source_route.selection.model_dump(mode='json') if execution.guard.source_route
                 else prior.get('source_route') if prior and not intake else None)
    observation = (source_observation_route_id or (selection['route_id'] if selection else None)
                   or (prior.get('source_observation_route_id') if prior and not intake else None))
    if source_observation_route_id and selection:
        selection = {**selection, 'route_id': source_observation_route_id}
    generation, stamp = (prior["generation"] + 1 if prior else 1), now()

    facts, contract = parsed["facts"], _parser_contract()

    manifest = {
        "schema": "evidence-lane.media-snapshot.v4",
        "project_id": store.project_id,
        "lane_id": "images_ocr",
        "media_id": media_id,
        "logical_name": logical_name,
        "source_path": source_path,
        "origin": origin,
        "generation": generation,
        "previous_snapshot": previous,
        "raw_object": digest(content),
        "facts_object": digest(canonical_json_bytes(facts)),
        "parser_contract": contract,
        "source_object": digest(source_content)
        if source_content is not None
        else prior.get("source_object")
        if prior
        else digest(content)
        if source_path is not None
        else None,
        "bytes": len(content),
        "created_at": stamp,
        "operation": operation,
        "tool_evidence": {**parsed["evidence"], "process": parsed.get("process_evidence")},
        "fidelity": facts["fidelity"],
        "source_bytes_mutated": False,
    }

    manifest.update(source_route=selection, source_observation_route_id=observation, inputs=inputs or [],
        capture_limits={'max_file_bytes': max_file_bytes or (prior.get('capture_limits', {}).get('max_file_bytes', 16_777_216) if prior else 16_777_216)})
    snapshot_id = digest(canonical_json_bytes(manifest))

    execution._before_more_work()

    with execution.lease.coordinated_transaction(["images_ocr", "receipts"]):
        lane = store.lane("images_ocr")

        apply_migrations(lane, MEDIA_MIGRATIONS, writer=execution.lease)

        with lane.transaction() as connection:
            live = connection.execute(
                "SELECT snapshot_id FROM media_current WHERE media_id=?", (media_id,)
            ).fetchone()

            if (live[0] if live else None) != previous:
                raise LaneError("MEDIA_SNAPSHOT_CHANGED", "The media changed before publication.")

            lane.put_object(content)

            if source_content is not None:
                lane.put_object(source_content)

            lane.put_object(canonical_json_bytes(facts))

            manifest_object = lane.put_object(canonical_json_bytes(manifest))

            connection.execute(
                "INSERT OR IGNORE INTO media_file VALUES(?,?,?,?,?)",
                (media_id, logical_name, source_path, origin, stamp),
            )

            connection.execute(
                "INSERT INTO media_version VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    media_id,
                    generation,
                    previous,
                    manifest_object,
                    manifest["raw_object"],
                    manifest["facts_object"],
                    contract,
                    stamp,
                ),
            )

            connection.execute(
                "INSERT INTO media_structure VALUES(?,?,?)",
                (snapshot_id, manifest["facts_object"], json_text(facts["fidelity"])),
            )

            for item in facts["items"]:
                connection.execute(
                    "INSERT INTO " + MEDIA_TABLES[item["kind"]] + " VALUES(?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        item["item_id"],
                        item["kind"],
                        item["ordinal"],
                        item["part"],
                        json_text(item),
                    ),
                )

            for item_id, ordinal, text in _chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))

                obj = lane.put_object(text.encode("utf-8"))

                connection.execute(
                    "INSERT INTO media_chunk VALUES(?,?,?,?,?)",
                    (snapshot_id, chunk_id, item_id, ordinal, obj),
                )

                connection.execute(
                    "INSERT INTO media_chunk_fts VALUES(?,?,?)", (snapshot_id, chunk_id, text)
                )

            path = _natural(lane, snapshot_id, PurePosixPath(logical_name).name, content)

            connection.execute(
                "INSERT INTO media_current VALUES(?,?) ON CONFLICT(media_id) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                (media_id, snapshot_id),
            )

            store.append_receipt(
                "media_snapshot",
                {
                    "snapshot_id": snapshot_id,
                    "media_id": media_id,
                    "job_id": execution.claim.job_id,
                    "operation": operation,
                    "source_bytes_mutated": False,
                },
            )

    return result(
        store,
        operation,
        {
            "snapshot_id": snapshot_id,
            "media_id": media_id,
            "generation": generation,
            "previous_snapshot": previous,
            "sha256": manifest["raw_object"],
            "bytes": len(content),
            "logical_name": logical_name,
            "natural_path": path,
            "source_bytes_mutated": False,
            "fidelity": facts["fidelity"],
            "limitations": facts["limitations"],
            "tool_evidence": {**parsed["evidence"], "process": parsed.get("process_evidence")},
        },
    )


def verify_media(context, request, output):

    from .media_process import invoke_media

    store, snapshot_id = context.store, output.result["snapshot_id"]

    manifest, facts = read_snapshot(store, snapshot_id)

    lane = store.lane("images_ocr")

    raw = lane.read_object(manifest["raw_object"])

    valid = len(raw) == manifest["bytes"] and manifest["raw_object"] == output.result["sha256"]

    reparsed = invoke_media(
        "parse",
        {
            "logical_name": manifest["logical_name"],
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "expected_sha256": manifest["raw_object"],
            "parse_options": facts["parse_options"],
        },
    )

    valid &= reparsed["facts"] == facts and _bytes(Path(output.result["natural_path"])) == raw

    with lane.connection(read_only=True) as connection:
        for table in sorted(set(MEDIA_TABLES.values())):
            rows = connection.execute(
                "SELECT * FROM " + table + " WHERE snapshot_id=? ORDER BY item_id", (snapshot_id,)
            ).fetchall()

            expected = sorted(
                [item for item in facts["items"] if MEDIA_TABLES[item["kind"]] == table],
                key=lambda item: item["item_id"],
            )

            valid &= [json.loads(row["payload_json"]) for row in rows] == expected

            valid &= all(
                (row["item_id"], row["kind"], row["ordinal"], row["part"])
                == (item["item_id"], item["kind"], item["ordinal"], item["part"])
                for row, item in zip(rows, expected)
            )

        expected = list(_chunks(facts))

        rows = connection.execute(
            "SELECT c.*,f.text_content,o.size_bytes AS registered_bytes FROM media_chunk c JOIN media_chunk_fts f "
            "ON c.chunk_id=f.chunk_id AND c.snapshot_id=f.snapshot_id LEFT JOIN objects o ON o.digest=c.text_object WHERE c.snapshot_id=?",
            (snapshot_id,),
        ).fetchall()

        valid &= len(rows) == len(expected)

        lookup = {(item_id, ordinal): text for item_id, ordinal, text in expected}

        for row in rows:
            valid &= _chunk_matches(
                lane, row, lookup.get((row["item_id"], row["ordinal"]))
            ) and row["chunk_id"] == digest(
                canonical_json_bytes([snapshot_id, row["item_id"], row["ordinal"]])
            )

    live = (
        not isinstance(request, MediaIndex)
        or _bytes(context.source_path(request.filename), request.max_file_bytes) == raw
    )

    checks = {"media_snapshot_integrity": valid, "media_source_hash_unchanged": live}

    return [
        {
            "check_id": name,
            "passed": checks[name],
            "evidence": {
                "snapshot_id": snapshot_id,
                "source_bytes_mutated": False,
                "layout_verified": False,
            },
        }
        for name in context.requested_checks
    ]


def current_media(store, request=None):

    lane = _lane(store)

    if lane is None:
        return {"media": [], "initialized": False}

    read_compatibility(lane, MEDIA_MIGRATIONS)

    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='media_current'"
        ).fetchone():
            return {"media": [], "initialized": False}

        rows = connection.execute(
            "SELECT v.*,f.logical_name,f.source_path,f.origin FROM media_current c "
            "JOIN media_version v USING(snapshot_id) JOIN media_file f ON f.media_id=c.media_id "
            f"WHERE {active} ORDER BY v.created_at DESC LIMIT 129"
        ).fetchall()

    return {
        "media": [dict(row) for row in rows[:128]],
        "truncated": len(rows) > 128,
        "initialized": True,
        "source_currentness": "not_checked_by_metadata_read",
    }


def read_media(store, request):

    manifest, _ = read_snapshot(store, request.snapshot_id)

    selected = (
        manifest["source_object"]
        if request.representation == "original_source"
        else manifest["raw_object"]
    )

    if selected is None:
        raise LaneError(
            "MEDIA_ORIGINAL_SOURCE_ABSENT",
            "This generated media has no separate original source file.",
        )

    raw = store.lane("images_ocr").read_object(selected)

    value = raw[request.offset : request.offset + request.max_bytes]

    end = request.offset + len(value)

    return {
        "snapshot_id": request.snapshot_id,
        "logical_name": manifest["source_path"]
        if request.representation == "original_source"
        else manifest["logical_name"],
        "sha256": selected,
        "representation": request.representation,
        "total_bytes": len(raw),
        "offset": request.offset,
        "content_base64": base64.b64encode(value).decode("ascii"),
        "next_offset": end if end < len(raw) else None,
        "source_bytes_mutated": False,
    }


def _export_parse_options(store, request):
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', _relative(request.filename)]))
    snapshot = current_snapshot(store, destination_id) or request.snapshot_id
    return read_snapshot(store, snapshot)[1]['parse_options']


def export_media(context, request):

    execution, store = context.execution, context.execution.store

    _calls(execution, 2)

    manifest, _ = read_snapshot(store, request.snapshot_id)

    relative = _relative(request.filename)

    if (
        PurePosixPath(relative).suffix.lower()
        != PurePosixPath(manifest["logical_name"]).suffix.lower()
    ):
        raise LaneError(
            "MEDIA_EXPORT_FORMAT_MISMATCH",
            "Export uses the existing media format; it is not conversion.",
        )

    path = execution.guard.path(relative)

    ProjectAccess(store).authorize(context.client_id, "write", path=path)

    reject_links(path, store.source_root)

    before_content = _bytes(path) if path.is_file() else None

    before = digest(before_content) if before_content is not None else None

    if path.exists() and not path.is_file() or before != request.expected_sha256:
        raise LaneError(
            "MEDIA_EXPORT_DESTINATION_CHANGED",
            "Bind export to the exact destination hash, or absence for a new file.",
        )

    if not path.parent.is_dir():
        raise LaneError(
            "MEDIA_EXPORT_PARENT_MISSING", "Select an existing granted destination directory."
        )

    lane, export_id = store.lane("images_ocr"), str(uuid4())

    content = lane.read_object(manifest["raw_object"])

    if manifest['parser_contract'] != _parser_contract():
        raise LaneError('MEDIA_PARSER_CONTRACT_CHANGED', 'Refresh this development snapshot with the current parser before exporting.')
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    previous = current_snapshot(store, destination_id)
    destination_manifest = read_snapshot(store, previous)[0] if previous else None
    if destination_manifest and 'source_observation_route_id' not in destination_manifest:
        raise LaneError('MEDIA_REFRESH_PROVENANCE_REQUIRED', 'Refresh the destination snapshot with current source provenance before export.')
    limit = (destination_manifest or manifest).get('capture_limits', {}).get('max_file_bytes', 16_777_216)
    if len(content) > limit:
        raise LaneError('MEDIA_FILE_BYTE_BUDGET', 'The proposed export exceeds the selected destination intake bound.')
    from .artifact_contract import LaneArtifacts
    artifacts = LaneArtifacts(execution.guard.engine, store)
    view_id = 'images_ocr.structure'
    view_selection = artifacts.current_selection(view_id)
    from .source_routing import SourceMutationRefresh
    source_refresh = SourceMutationRefresh(context, [relative], lane_id='images_ocr',
        action='media_export', max_file_bytes=16_777_216, allow_create=True,
        parent_route_id=destination_manifest.get('source_observation_route_id') if destination_manifest else None)
    source_refresh.expected_after(path, before, content)
    _calls(execution, 3 + int(bool(view_selection and view_selection['formats'])))
    parsed_content, parsed = _worker(execution, 'media_parse_bytes', {
        'filename': str(path), 'logical_name': relative, 'max_file_bytes': limit,
        'content_base64': base64.b64encode(content).decode('ascii'),
        'expected_sha256': manifest['raw_object'], 'parse_options': _export_parse_options(store, request)})
    execution.guard.observe(execution)
    if parsed_content != content or manifest['parser_contract'] != _parser_contract():
        raise LaneError('MEDIA_PARSER_CONTRACT_CHANGED', 'The proposed bytes or selected parser changed before export.')
    effect = execution.prepare_effect(
        "media:" + export_id, "Export exact versioned media bytes to one granted project filename."
    )

    with execution.lease.coordinated_transaction(["images_ocr", "receipts"]):
        with lane.transaction() as connection:
            if before_content is not None:
                lane.put_object(before_content)

            connection.execute(
                "INSERT INTO media_export VALUES(?,?,?,?,?,?,?)",
                (
                    export_id,
                    request.snapshot_id,
                    relative,
                    before,
                    manifest["raw_object"],
                    effect,
                    now(),
                ),
            )

        store.append_receipt(
            "media_export_prepared",
            {
                "export_id": export_id,
                "snapshot_id": request.snapshot_id,
                "destination": relative,
                "before_sha256": before,
                "after_sha256": manifest["raw_object"],
                "effect_id": effect,
            },
        )

    descriptor, temporary = tempfile.mkstemp(
        prefix=".evidence-lane-media-", suffix=".tmp", dir=path.parent
    )

    temporary = Path(temporary)

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)

            stream.flush()

            os.fsync(stream.fileno())

        execution._before_more_work()

        ProjectAccess(store).authorize(context.client_id, "write", path=path)

        reject_links(path, store.source_root)

        observed = digest(_bytes(path)) if path.is_file() else None

        if observed != before or path.exists() and not path.is_file():
            raise LaneError(
                "MEDIA_EXPORT_DESTINATION_CHANGED",
                "The destination changed immediately before export; reconcile this effect.",
            )

        if before is None:
            # Windows rename fails if a new destination appeared; unlike replace,

            # it cannot silently overwrite a concurrently created user file.

            if os.name == "nt":
                os.rename(temporary, path)

            else:
                os.link(temporary, path)

                temporary.unlink()

        else:
            os.chmod(temporary, path.stat().st_mode)

            os.replace(temporary, path)

        if _bytes(path) != content:
            raise LaneError(
                "MEDIA_EXPORT_VERIFY_FAILED", "The written media bytes were not confirmed."
            )

        with execution.lease.transaction("plan"):
            evidence = execution.plan_store.put_object(
                canonical_json_bytes(
                    {
                        "export_id": export_id,
                        "destination": relative,
                        "sha256": manifest["raw_object"],
                        "exact_bytes_verified": True,
                    }
                )
            )

            execution.confirm_effect(effect, evidence)

    finally:
        if temporary.exists():
            temporary.unlink()

    with execution.lease.coordinated_transaction(['sources', 'images_ocr', 'receipts']):
        source_result = source_refresh.publish(path=path, before_sha256=before, replacement=content,
            mutation_id=export_id, effect_id=effect)
        indexed = publish(context, media_id=destination_id, logical_name=relative,
            source_path=relative, origin='source', previous=previous, content=content, parsed=parsed,
            operation='media_export', max_file_bytes=limit,
            source_observation_route_id=source_result['route_id'],
            inputs=[{'lane_id': 'images_ocr', 'snapshot_id': request.snapshot_id, 'sha256': manifest['raw_object'], 'operation': 'export'}])
        view_result = artifacts.refresh_selected(view_id, view_selection, execution, actor_id=context.client_id)
        execution._before_more_work()
        final_capture = source_refresh.capture()
        from .source_authority import freeze_source_authority
        for spec in source_refresh.specs:
            freeze_source_authority(spec, capture=final_capture)
        if final_capture.identities != source_refresh.expected_after(path, before, content):
            raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'A source changed before the refreshed lanes could publish.')
    return result(
        store,
        "media_export",
        {
            "export_id": export_id,
            "snapshot_id": request.snapshot_id,
            "destination": relative,
            "before_sha256": before,
            "after_sha256": manifest["raw_object"],
            "effect_id": effect,
            "source_bytes_mutated": True,
            "source_index_refresh_required": False,
            "index_refresh": indexed.model_dump(mode="json"),
            "source_refresh": source_result, "view_refresh": view_result,
            "automatic_replay": False,
        },
    )


def verify_export(context, request, output, *, engine=None):
    manifest, _ = read_snapshot(context.store, request.snapshot_id)
    valid = digest(_bytes(context.source_path(request.filename))) == manifest['raw_object'] == output.result['after_sha256']
    lane = context.store.lane('images_ocr')
    with lane.connection(read_only=True) as connection:
        exported = connection.execute('SELECT * FROM media_export WHERE export_id=?', (output.result['export_id'],)).fetchone()
    with context.store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT * FROM jobs_effects WHERE effect_id=?', (output.result['effect_id'],)).fetchone()
    valid &= exported is not None and effect is not None and effect['job_id'] == context.job_id and effect['state'] == 'confirmed'
    if exported is not None and effect is not None:
        valid &= all(exported[key] == value for key, value in {'snapshot_id': request.snapshot_id,
            'destination': _relative(request.filename), 'before_sha256': request.expected_sha256,
            'after_sha256': manifest['raw_object'], 'effect_id': output.result['effect_id']}.items())
        valid &= json.loads(context.store.lane('plan').read_object(effect['evidence_object'])) == {
            'export_id': output.result['export_id'], 'destination': _relative(request.filename),
            'sha256': manifest['raw_object'], 'exact_bytes_verified': True}
    indexed = MediaResult.model_validate(output.result['index_refresh'])
    index_manifest, index_facts = read_snapshot(context.store, indexed.result['snapshot_id'])
    expected_id = digest(canonical_json_bytes([context.store.project_id, 'source', _relative(request.filename)]))
    valid &= (indexed.project_id == context.store.project_id and indexed.operation == 'media_export'
        and indexed.lane_id == 'images_ocr' and index_manifest['media_id'] == expected_id
        and current_snapshot(context.store, expected_id) == indexed.result['snapshot_id']
        and index_manifest['logical_name'] == index_manifest['source_path'] == _relative(request.filename)
        and index_manifest['raw_object'] == manifest['raw_object'] and not output.result['source_index_refresh_required']
        and index_manifest['inputs'] == [{'lane_id': 'images_ocr', 'snapshot_id': request.snapshot_id,
            'sha256': manifest['raw_object'], 'operation': 'export'}])
    valid &= all(row['passed'] for row in verify_media(replace(context,
        requested_checks=('media_snapshot_integrity',)), request, indexed))
    source = output.result['source_refresh']
    prior = read_snapshot(context.store, index_manifest['previous_snapshot'])[0] if index_manifest['previous_snapshot'] else None
    options_snapshot = index_manifest['previous_snapshot'] or request.snapshot_id
    valid &= index_facts['parse_options'] == read_snapshot(context.store, options_snapshot)[1]['parse_options']
    from .source_routing import verify_export_refresh
    valid &= (index_manifest['source_observation_route_id'] == source['route_id']
        and source['parent_route_id'] == (prior.get('source_observation_route_id') if prior else None)
        and verify_export_refresh(context, source, lane_id='images_ocr',
            action='media_export', mutation_id=output.result['export_id'],
            effect_id=output.result['effect_id'], max_file_bytes=16_777_216))
    if engine is None:
        valid = False
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed(
            'images_ocr.structure', output.result['view_refresh'])
        valid &= view_valid and len(context.worker_evidence) == 1 + graph_workers and all(
            row['status'] == 'ok' for row in context.worker_evidence)
    return [{'check_id': name, 'passed': valid, 'evidence': {'export_id': output.result['export_id'],
        'after_sha256': manifest['raw_object']}} for name in context.requested_checks]


def _chunk_matches(lane, row, expected):

    if expected is None:
        return False

    raw = expected.encode("utf-8")

    if (
        row["registered_bytes"] != len(raw)
        or row["text_object"] != digest(raw)
        or row["text_content"] != expected
    ):
        return False

    # The joined object registration belongs to the already verified, pinned

    # lane connection. Rehash the addressed file without reopening and hashing

    # the entire lane database for every individual text chunk.

    return lane.read_object(row["text_object"], require_registered=False) == raw


def query_media(store, request):
    with bounded_project_read(store.root, time.monotonic() + 5):
        manifest, facts = read_snapshot(store, request.snapshot_id)
        lane = store.lane("images_ocr")
        if request.collection in {"ocr_run", "ocr_line", "review_region"}:
            from .media_derivatives import query_ocr

            return query_ocr(store, request)
        items = {row["item_id"]: row for row in facts["items"]}
        if request.collection == "metadata":
            value = {
                "snapshot_id": request.snapshot_id,
                "media_id": manifest["media_id"],
                "logical_name": manifest["logical_name"],
                "source_object": manifest["source_object"],
                "raw_object": manifest["raw_object"],
                "metadata": facts["metadata"],
                "fidelity": facts["fidelity"],
                "limitations": facts["limitations"],
                "structure_counts": facts["counts"],
                "source_bytes_mutated": False,
            }
            if len(canonical_json_bytes(value)) > request.max_bytes:
                raise LaneError("MEDIA_QUERY_ITEM_TOO_LARGE", "Increase max_bytes for the complete media metadata item.")
            return value
        args = [request.snapshot_id]
        if request.collection == "text":
            tokens = re.findall(r"\w+", request.query or "", re.UNICODE)
            if not 1 <= len(tokens) <= 32:
                raise LaneError(
                    "MEDIA_QUERY_TERMS_REQUIRED", "Use one to thirty-two literal search terms."
                )
            sql = (
                "SELECT c.*,f.text_content,o.size_bytes AS registered_bytes,bm25(media_chunk_fts) AS rank "
                "FROM media_chunk_fts f JOIN media_chunk c ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id "
                "LEFT JOIN objects o ON o.digest=c.text_object WHERE c.snapshot_id=? AND media_chunk_fts MATCH ? "
            )
            args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
            if request.frame is not None:
                sql += "AND c.item_id IN (SELECT item_id FROM media_frame WHERE snapshot_id=? AND part=?) "
                args.extend([request.snapshot_id, f"frame:{request.frame}"])
            sql += "ORDER BY rank,c.item_id,c.ordinal"
        else:
            sql = (
                "SELECT * FROM "
                + MEDIA_TABLES[request.collection]
                + " WHERE snapshot_id=? AND kind=?"
            )
            args.append(request.collection)
            if request.frame is not None:
                sql += " AND json_extract(payload_json,'$.frame')=?"
                args.append(request.frame)
            if request.query:
                sql += " AND instr(lower(json_extract(payload_json,'$.text')),lower(?))>0"
                args.append(request.query)
            sql += " ORDER BY part,ordinal,item_id"
        with lane.connection(read_only=True) as connection:
            rows = connection.execute(
                sql + " LIMIT ? OFFSET ?", [*args, request.limit + 1, request.offset]
            ).fetchall()
        values, used, truncated = [], 0, False
        for row in rows:
            item = items.get(row["item_id"])
            if item is None:
                raise LaneError(
                    "MEDIA_QUERY_INTEGRITY", "The query row belongs to different immutable media."
                )
            if request.collection == "text":
                start = row["ordinal"] * 4096
                text = item["text"][start : start + 4096]
                if (
                    row["ordinal"] < 0
                    or not _chunk_matches(lane, row, text)
                    or row["chunk_id"]
                    != digest(
                        canonical_json_bytes([request.snapshot_id, row["item_id"], row["ordinal"]])
                    )
                ):
                    raise LaneError(
                        "MEDIA_QUERY_INTEGRITY",
                        "Indexed text differs from its immutable media facts.",
                    )
                value = {
                    "item_id": item["item_id"],
                    "part": item["part"],
                    "frame": item.get("frame"),
                    "text": text,
                    "rank": row["rank"],
                    "chunk_ordinal": row["ordinal"],
                }
            else:
                if json.loads(row["payload_json"]) != item or (
                    row["kind"],
                    row["ordinal"],
                    row["part"],
                ) != (item["kind"], item["ordinal"], item["part"]):
                    raise LaneError(
                        "MEDIA_QUERY_INTEGRITY", "The typed row differs from immutable media facts."
                    )
                value = item
            size = len(canonical_json_bytes(value))
            if len(values) >= request.limit or used + size > request.max_bytes:
                truncated = True
                break
            values.append(value)
            used += size
        if truncated and not values:
            raise LaneError(
                "MEDIA_QUERY_ITEM_TOO_LARGE", "Increase max_bytes for the next complete media item."
            )
        return {
            "snapshot_id": request.snapshot_id,
            "media_id": manifest["media_id"],
            "rows": values,
            "next_offset": request.offset + len(values) if truncated else None,
            "truncated": truncated,
            "fidelity": facts["fidelity"],
            "source_bytes_mutated": False,
        }


def register_media_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def export_route(render, backend=None):
        def applicable(context, request):
            from .artifact_contract import LaneArtifacts
            store = engine.directory.open(context.project_id)
            view = LaneArtifacts(engine, store).current_selection('images_ocr.structure')
            return (bool(view and view['formats']) is render
                and (backend is None or _export_parse_options(store, request)['native_backend'] == backend))
        return applicable

    def verify_exported(context, request, output):
        return verify_export(context, request, output, engine=engine)

    for name, workflow in [("media_index", "manage-project-sources"), ("media_refresh", "refresh-project-evidence")]:
        engine.registry.register(
            ActionSpec(
                name,
                "Snapshot exact image/media bytes and bounded raster, vector or container facts.",
                MediaIndex,
                MediaResult,
                index_media,
                permission="write",
                mutates=True,
                requires_delta=True,
                profile="images_ocr",
                workflow=workflow,
                path_fields=("filename",),
                source_lanes=('images_ocr',),
                materialization=SourceMaterialization() if name == 'media_index' else None,
                worker_operations=("media_parse_file",),
                verification_checks=("media_snapshot_integrity", "media_source_hash_unchanged"),
                verifier=verify_media,
                tool_routes=(
                    ToolRoute(
                        name + ".raster",
                        index_media,
                        ("Python", "Pillow"),
                        argument_suffixes=(("filename", tuple(sorted(RASTER_EXTENSIONS))),),
                    ),
                    ToolRoute(
                        name + ".vector",
                        index_media,
                        ("Python", "defusedxml"),
                        argument_suffixes=(("filename", (".svg",)),),
                    ),
                    ToolRoute(
                        name + ".container",
                        index_media,
                        ("Python", "FFmpeg"),
                        systems=("Windows",),
                        argument_suffixes=(("filename", tuple(sorted(MEDIA_EXTENSIONS))),),
                    ),
                ),
            )
        )
    engine.registry.register(
        ActionSpec(
            "media_transform",
            "Publish one immutable raster derivative with explicit frame, alpha and metadata handling.",
            MediaTransform,
            MediaResult,
            transform_media,
            permission="write",
            mutates=True,
            requires_delta=True,
            profile="images_ocr",
            workflow="execute-project-plan",
            worker_operations=("media_transform",),
            verification_checks=("media_snapshot_integrity",),
            verifier=verify_media,
            tool_routes=(
                ToolRoute("media_transform.pillow", transform_media, ("Python", "Pillow")),
            ),
        )
    )

    def reader(function, action):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, action, function(store, request))

        return handler

    for name, model, function, description in [
        (
            "media_current",
            MediaSelection,
            current_media,
            "Read current media versions without rescanning sources.",
        ),
        (
            "media_query",
            MediaQuery,
            query_media,
            "Read bounded media facts or literal FTS5/BM25 matches.",
        ),
        ("media_read", MediaRead, read_media, "Read bounded exact media or original-source bytes."),
    ]:
        engine.registry.register(
            ActionSpec(
                name,
                description,
                model,
                MediaResult,
                reader(function, name),
                profile="images_ocr",
                workflow="manage-project-sources",
                queryable_in_delta=True,
                cross_project_read=True,
                studio_read=True,
                read_migrations=MEDIA_MIGRATIONS,
                search=SearchRoute(('images_ocr',), 'rows', 'media_current', 'media', 'text', 'any', rerank_text='text') if name == 'media_query' else None,
                fetch=FetchRoute(('images_ocr',), 'media') if name == 'media_read' else None,
            )
        )
    engine.registry.register(
        ActionSpec(
            "media_export",
            "Parse exact exported bytes and renew Sources, destination facts and existing lane views with the selected native parser.",
            MediaExport,
            MediaResult,
            export_media,
            permission="write",
            mutates=True,
            requires_delta=True,
            profile="images_ocr",
            workflow="execute-project-plan",
            path_fields=("filename",),
            verification_checks=("media_export_hash_verified",),
            verifier=verify_exported, worker_operations=("media_parse_bytes", "render_lane_view"),
            tool_routes=tuple(ToolRoute('media_export.' + kind + ('_view' if render else ''), export_media,
                (*tools, *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
                argument_suffixes=(('filename', suffixes),), systems=('Windows',) if kind == 'container' else ('Windows', 'Darwin', 'Linux'),
                view_refresh=SelectedViewRefresh(engine, 'images_ocr.structure'), applicable=export_route(render), worker_operations=('media_parse_bytes', *(('render_lane_view',) if render else ())))
                for render in (False, True) for kind, tools, suffixes in (
                    ('raster', ('Python', 'Pillow'), tuple(sorted(RASTER_EXTENSIONS))),
                    ('vector', ('Python', 'defusedxml'), ('.svg',)),
                    ('container', ('Python', 'FFmpeg'), tuple(sorted(MEDIA_EXTENSIONS))))),
        )
    )
    from .media_derivatives import register_media_derivatives
    from .media_views import register_media_views

    register_media_derivatives(engine)
    register_media_views(engine)
