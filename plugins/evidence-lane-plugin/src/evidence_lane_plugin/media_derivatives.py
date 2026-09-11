"""OCR and extracted frame/audio files retain exact source-snapshot bindings."""

from __future__ import annotations

import base64
import io
import json
import re
import wave

from .compute_routes import ComputeContract
from .errors import LaneError
from .hashing import canonical_json_bytes
from .media_contracts import MediaExtract, MediaExtractionRead, MediaOcr, MediaOcrRead
from .media_parsers import digest, empty_frame_reviews
from .media_profile import MediaResult, _calls, _natural, read_snapshot, result
from .media_schema import MEDIA_MIGRATIONS
from .migrations import apply_migrations
from .registry import ActionSpec
from .storage import now, project_snapshot
from .tool_routes import ToolRoute


def manifest(store, identity, kind):
    lane = store.lane("images_ocr")
    table, key = (
        ("media_ocr_run", "ocr_id") if kind == "ocr" else ("media_extraction", "extraction_id")
    )
    with lane.connection(read_only=True) as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (identity,)).fetchone()
    if row is None:
        raise LaneError(
            "MEDIA_DERIVATIVE_MISSING", "Select a derivative from this image/media lane."
        )
    body = json.loads(lane.read_object(row["manifest_object"]))
    snapshot, _ = read_snapshot(store, row["snapshot_id"])
    if (
        digest(canonical_json_bytes(body)) != identity
        or body["project_id"] != store.project_id
        or body["lane_id"] != "images_ocr"
        or body["kind"] != kind
        or body["snapshot_id"] != row["snapshot_id"]
        or body["created_at"] != row["created_at"]
        or body["result"]["input_sha256"] != snapshot["raw_object"]
    ):
        raise LaneError(
            "MEDIA_DERIVATIVE_INTEGRITY", "The derivative differs from its immutable media binding."
        )
    return body


def _derive(context, request, kind, *, backend="rapidocr"):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    snapshot, _ = read_snapshot(store, request.snapshot_id)
    lane = store.lane("images_ocr")
    args = {
        "logical_name": snapshot["logical_name"],
        "expected_sha256": snapshot["raw_object"],
        "content_base64": base64.b64encode(lane.read_object(snapshot["raw_object"])).decode(),
        "request": request.model_dump(mode="json"),
        "backend": backend,
    }
    computing = context.computation if kind == 'ocr' and backend == 'rapidocr' else None
    if kind == 'ocr' and backend == 'rapidocr' and computing is None:
        raise LaneError('COMPUTE_ADMISSION_REQUIRED', 'OCR requires engine-owned compute admission.')
    response = (computing or execution).submit("media_ocr" if kind == "ocr" else "media_extract", args).result()
    if computing:
        computing.result(response)
    if response["status"] != "ok":
        raise LaneError(
            response.get("code", "MEDIA_DERIVATIVE_FAILED"),
            "The owned media derivative did not complete.",
        )
    generated = json.loads(canonical_json_bytes(response["result"]))
    if generated["input_sha256"] != snapshot["raw_object"]:
        raise LaneError(
            "MEDIA_DERIVATIVE_BINDING", "The media codec returned different source bytes."
        )
    raw = None
    if kind == "ocr":
        if [row["frame"] for row in generated["frames"]] != request.frames or generated[
            "options"
        ] != request.model_dump(mode="json"):
            raise LaneError(
                "MEDIA_OCR_BINDING", "OCR returned a different frame selection or request."
            )
        identities = set()
        for line in generated["lines"]:
            clean = dict(line)
            line_id = clean.pop("line_id")
            if (
                line_id in identities
                or digest(canonical_json_bytes(clean)) != line_id
                or line["frame"] not in request.frames
            ):
                raise LaneError(
                    "MEDIA_OCR_BINDING", "An OCR line has a different immutable frame binding."
                )
            identities.add(line_id)
        if generated["review_regions"] != [
            row["line_id"] for row in generated["lines"] if row["review_required"]
        ]:
            raise LaneError(
                "MEDIA_OCR_BINDING", "Review regions differ from OCR confidence evidence."
            )
        if generated.get("review_frames") != empty_frame_reviews(generated["frames"]):
            raise LaneError("MEDIA_OCR_BINDING", "Empty-frame review differs from OCR frame evidence.")
    else:
        raw = base64.b64decode(generated.pop("content_base64"), validate=True)
        if (
            digest(raw) != generated["sha256"]
            or len(raw) != generated["bytes"]
            or generated["filename"]
            != ("frame.png" if request.kind == "video_frame" else "audio.wav")
            or generated["kind"] != request.kind
            or generated["request"] != request.model_dump(mode="json")
        ):
            raise LaneError(
                "MEDIA_EXTRACTION_BINDING",
                "The extracted file differs from its byte or request binding.",
            )
    body = {
        "schema": "evidence-lane.media-derivative.v4",
        "project_id": store.project_id,
        "lane_id": "images_ocr",
        "kind": kind,
        "snapshot_id": request.snapshot_id,
        "request": request.model_dump(mode="json"),
        "result": generated,
        "created_at": now(),
    }
    identity = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(["images_ocr", "receipts"]):
        apply_migrations(lane, MEDIA_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            address = lane.put_object(canonical_json_bytes(body))
            if kind == "ocr":
                connection.execute(
                    "INSERT INTO media_ocr_run VALUES(?,?,?,?)",
                    (identity, request.snapshot_id, address, body["created_at"]),
                )
                for line in generated["lines"]:
                    connection.execute(
                        "INSERT INTO media_ocr_line VALUES(?,?,?,?,?)",
                        (
                            identity,
                            line["line_id"],
                            line["frame"],
                            line["ordinal"],
                            canonical_json_bytes(line).decode(),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO media_ocr_fts VALUES(?,?,?)",
                        (identity, line["line_id"], line["text"]),
                    )
                for line_id in generated["review_regions"]:
                    connection.execute(
                        "INSERT INTO media_review_region VALUES(?,?)", (identity, line_id)
                    )
                for frame in generated["review_frames"]:
                    connection.execute("INSERT INTO media_review_frame VALUES(?,?,?)",
                        (identity, frame['frame'], canonical_json_bytes(frame).decode()))
                text = "\n\n".join(
                    f"Frame {frame}\n"
                    + "\n".join(
                        line["text"] for line in generated["lines"] if line["frame"] == frame
                    )
                    for frame in request.frames
                )
                path = _natural(lane, identity, "ocr.txt", text.encode())
            else:
                lane.put_object(raw)
                path = _natural(lane, identity, generated["filename"], raw)
                connection.execute(
                    "INSERT INTO media_extraction VALUES(?,?,?,?)",
                    (identity, request.snapshot_id, address, body["created_at"]),
                )
            store.append_receipt(
                "media_" + kind,
                {
                    "derivative_id": identity,
                    "snapshot_id": request.snapshot_id,
                    "job_id": execution.claim.job_id,
                    "source_bytes_mutated": False,
                },
            )
    return result(
        store,
        "media_" + kind,
        {
            ("ocr_id" if kind == "ocr" else "extraction_id"): identity,
            "snapshot_id": request.snapshot_id,
            "frames": generated.get("frames", []),
            "ocr_lines": len(generated.get("lines", [])),
            "review_regions": len(generated.get("review_regions", [])) + len(generated.get("review_frames", [])),
            "review_frames": generated.get("review_frames", []),
            "sha256": generated.get("sha256"),
            "bytes": generated.get("bytes"),
            "details": generated.get("details"),
            "natural_path": path,
            "evidence": generated["evidence"],
            "source_bytes_mutated": False,
        },
    )


def ocr(context, request):
    return _derive(context, request, "ocr")


def ocr_tesseract(context, request):
    return _derive(context, request, "ocr", backend="tesseract")


def extract(context, request):
    return _derive(context, request, "extraction")


def ocr_rows(store, identity):
    body = manifest(store, identity, "ocr")
    expected = sorted(body["result"]["lines"], key=lambda row: (row["frame"], row["ordinal"]))
    with store.lane("images_ocr").connection(read_only=True) as connection:
        rows = connection.execute(
            "SELECT * FROM media_ocr_line WHERE ocr_id=? ORDER BY frame,ordinal", (identity,)
        ).fetchall()
        fts = connection.execute(
            "SELECT line_id,text_content FROM media_ocr_fts WHERE ocr_id=? ORDER BY line_id",
            (identity,),
        ).fetchall()
        regions = {
            row[0]
            for row in connection.execute(
                "SELECT line_id FROM media_review_region WHERE ocr_id=?", (identity,)
            )
        }
        frame_reviews = connection.execute(
            "SELECT frame,payload_json FROM media_review_frame WHERE ocr_id=? ORDER BY frame", (identity,)
        ).fetchall()
    if (
        [json.loads(row["payload_json"]) for row in rows] != expected
        or [(row["line_id"], row["frame"], row["ordinal"]) for row in rows]
        != [(row["line_id"], row["frame"], row["ordinal"]) for row in expected]
        or [tuple(row) for row in fts] != sorted((row["line_id"], row["text"]) for row in expected)
        or regions != set(body["result"]["review_regions"])
        or [(row['frame'], json.loads(row['payload_json'])) for row in frame_reviews]
        != [(row['frame'], row) for row in sorted(body['result'].get('review_frames', []), key=lambda row: row['frame'])]
    ):
        raise LaneError(
            "MEDIA_OCR_INTEGRITY",
            "OCR rows, FTS or review regions differ from their immutable result.",
        )
    return body, expected


def _bounded(rows, request):
    selected, used = [], 0
    for row in rows[request.offset : request.offset + request.limit]:
        size = len(canonical_json_bytes(row))
        if used + size > request.max_bytes:
            if not selected:
                raise LaneError(
                    "MEDIA_QUERY_ITEM_TOO_LARGE", "Increase max_bytes for this complete OCR item."
                )
            break
        selected.append(row)
        used += size
    end = request.offset + len(selected)
    return {
        "rows": selected,
        "next_offset": end if end < len(rows) else None,
        "truncated": end < len(rows),
    }


def read_ocr(store, request):
    body, rows = ocr_rows(store, request.ocr_id)
    return {
        **_bounded(
            [row for row in rows if request.frame is None or row["frame"] == request.frame], request
        ),
        "ocr_id": request.ocr_id,
        "snapshot_id": body["snapshot_id"],
        "frames": body["result"]["frames"],
        "review_frames": body["result"].get("review_frames", []),
        "evidence": body["result"]["evidence"],
    }


def query_ocr(store, request):
    with store.lane("images_ocr").connection(read_only=True) as connection:
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT ocr_id FROM media_ocr_run WHERE snapshot_id=? ORDER BY created_at,ocr_id LIMIT 129",
                (request.snapshot_id,),
            )
        ]
    if len(ids) > 128:
        raise LaneError("MEDIA_OCR_QUERY_RUN_BUDGET", "Select an exact result with media_ocr_read.")
    selected = None
    if request.query and request.collection != "ocr_run":
        tokens = re.findall(r"\w+", request.query, re.UNICODE)
        if not 1 <= len(tokens) <= 32:
            raise LaneError(
                "MEDIA_QUERY_TERMS_REQUIRED", "Use one to thirty-two literal OCR terms."
            )
        with store.lane("images_ocr").connection(read_only=True) as connection:
            matches = connection.execute(
                "SELECT f.ocr_id,f.line_id,bm25(media_ocr_fts) AS rank FROM media_ocr_fts f JOIN media_ocr_run r ON r.ocr_id=f.ocr_id WHERE r.snapshot_id=? AND media_ocr_fts MATCH ?",
                (request.snapshot_id, (" OR " if request.match_mode == 'any' else " AND ").join('"' + token + '"' for token in tokens)),
            ).fetchmany(20001)
        if len(matches) > 20000:
            raise LaneError("MEDIA_OCR_QUERY_RUN_BUDGET", "Select a smaller OCR query.")
        selected = {(row["ocr_id"], row["line_id"]): row["rank"] for row in matches}
    rows = []
    for identity in ids:
        body, lines = ocr_rows(store, identity)
        if request.collection == "ocr_run":
            rows.append(
                {
                    "ocr_id": identity,
                    "frames": body["result"]["frames"],
                    "review_frames": body["result"].get("review_frames", []),
                    "evidence": body["result"]["evidence"],
                }
            )
            continue
        if request.collection == 'review_region' and selected is None:
            rows.extend({'ocr_id': identity, 'kind': 'frame_review', **frame}
                for frame in body['result'].get('review_frames', [])
                if request.frame is None or request.frame == frame['frame'])
        for line in lines:
            if request.frame is not None and line["frame"] != request.frame:
                continue
            if request.collection == "review_region" and not line["review_required"]:
                continue
            if selected is not None and (identity, line["line_id"]) not in selected:
                continue
            rows.append(
                {
                    "ocr_id": identity,
                    **line,
                    **(
                        {"rank": selected[(identity, line["line_id"])]}
                        if selected is not None
                        else {}
                    ),
                }
            )
    if selected is not None:
        rows.sort(key=lambda row: (row["rank"], row["ocr_id"], row["frame"], row["ordinal"]))
    return {
        **_bounded(rows, request),
        "snapshot_id": request.snapshot_id,
        "source_bytes_mutated": False,
    }


def read_extraction(store, request):
    body = manifest(store, request.extraction_id, "extraction")
    file = body["result"]
    raw = store.lane("images_ocr").read_object(file["sha256"])
    if len(raw) != file["bytes"]:
        raise LaneError(
            "MEDIA_EXTRACTION_INTEGRITY", "The extracted file differs from its recorded size."
        )
    chunk = raw[request.offset : request.offset + request.max_bytes]
    end = request.offset + len(chunk)
    return {
        "extraction_id": request.extraction_id,
        "snapshot_id": body["snapshot_id"],
        "filename": file["filename"],
        "sha256": file["sha256"],
        "total_bytes": len(raw),
        "details": file["details"],
        "offset": request.offset,
        "content_base64": base64.b64encode(chunk).decode(),
        "next_offset": end if end < len(raw) else None,
    }


def verify_derivative(context, request, output):
    from PIL import Image

    kind = "ocr" if isinstance(request, MediaOcr) else "extraction"
    identity = output.result["ocr_id" if kind == "ocr" else "extraction_id"]
    body = manifest(context.store, identity, kind)
    valid = body["request"] == request.model_dump(mode="json")
    if kind == "ocr":
        _, lines = ocr_rows(context.store, identity)
        valid &= len(lines) == output.result["ocr_lines"]
    else:
        file = body["result"]
        raw = context.store.lane("images_ocr").read_object(file["sha256"])
        valid &= len(raw) == file["bytes"]
        if request.kind == "video_frame":
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                valid &= image.format == "PNG" and image.size == (
                    file["details"]["width"],
                    file["details"]["height"],
                )
        else:
            with wave.open(io.BytesIO(raw), "rb") as audio:
                valid &= (
                    audio.getnchannels() == request.channels
                    and audio.getframerate() == request.sample_rate
                    and audio.getnframes() == file["details"]["samples_per_channel"]
                )
    return [
        {
            "check_id": name,
            "passed": valid,
            "evidence": {
                "derivative_id": identity,
                "source_bytes_mutated": False,
                "visual_or_ocr_accuracy_verified": False,
            },
        }
        for name in context.requested_checks
    ]


def register_media_derivatives(engine):
    for name, model, handler, worker, routes in [
        (
            "media_ocr",
            MediaOcr,
            ocr,
            "media_ocr",
            (
                ToolRoute(
                    "media_ocr.rapidocr",
                    ocr,
                    ("Python", "Pillow", "OpenCV", "RapidOCR_ONNX_Runtime"),
                    argument_values=(("language", ("eng",)),),
                    compute=ComputeContract('media_ocr', 'OCR_MEDIA', ('CPU', 'DIRECTML'), 2048, 'rapidocr_lines'),
                ),
                ToolRoute(
                    "media_ocr.tesseract",
                    ocr_tesseract,
                    ("Python", "Pillow", "pytesseract_Tesseract"),
                    systems=("Windows",),
                ),
            ),
        ),
        (
            "media_extract",
            MediaExtract,
            extract,
            "media_extract",
            (
                ToolRoute(
                    "media_extract.ffmpeg_frame",
                    extract,
                    ("Python", "Pillow", "FFmpeg"),
                    systems=("Windows",),
                    argument_values=(("kind", ("video_frame",)),),
                ),
                ToolRoute(
                    "media_extract.ffmpeg_audio",
                    extract,
                    ("Python", "FFmpeg"),
                    systems=("Windows",),
                    argument_values=(("kind", ("audio_segment",)),),
                ),
            ),
        ),
    ]:
        engine.registry.register(
            ActionSpec(
                name,
                "Create a separate hash-bound OCR or frame/audio derivative from exact media.",
                model,
                MediaResult,
                handler,
                permission="write",
                mutates=True,
                requires_delta=True,
                profile="images_ocr",
                workflow="source-intake",
                worker_operations=(worker,),
                verification_checks=("media_derivative_integrity",),
                verifier=verify_derivative,
                tool_routes=routes,
            )
        )

    def reader(function, name):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, name, function(store, request))

        return handler

    for name, model, function in [
        ("media_ocr_read", MediaOcrRead, read_ocr),
        ("media_extraction_read", MediaExtractionRead, read_extraction),
    ]:
        engine.registry.register(
            ActionSpec(
                name,
                "Read a bounded immutable image/media derivative.",
                model,
                MediaResult,
                reader(function, name),
                profile="images_ocr",
                workflow="source-intake",
                queryable_in_delta=True,
                cross_project_read=True,
                studio_read=True,
                read_migrations=MEDIA_MIGRATIONS,
            )
        )
