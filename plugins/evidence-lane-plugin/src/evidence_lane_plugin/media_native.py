"""Fixed FFmpeg probes and bounded frame/audio derivatives from admitted bytes."""

from __future__ import annotations

import base64
import io
import re
import tempfile
import wave
from pathlib import Path

from .installation_layout import studio_installation
from .media_parsers import MAX_BYTES, MEDIA_EXTENSIONS, digest, fail
from .shared_native_tools import NativeInvocationRequest, run_native_tool
from .shared_tool_assets import resolve_shared_asset

DEMUXERS = "avi,flac,mov,mp3,ogg,wav,matroska,webm"


def _input(path):
    arguments = [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-xerror",
        "-threads",
        "2",
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        DEMUXERS,
        "-max_streams",
        "32",
        "-probesize",
        "8388608",
        "-analyzeduration",
        "2000000",
    ]
    if path.suffix.lower() in {".mp4", ".mov", ".m4a"}:
        arguments += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return arguments + ["-i", str(path)]


def _run(arguments):
    _, asset = resolve_shared_asset("ffmpeg_runtime")
    response = run_native_tool(
        NativeInvocationRequest(
            tool_id="ffmpeg",
            arguments=arguments,
            timeout_seconds=75,
            max_output_bytes=2_097_152,
            host_profile="CODEX_DESKTOP",
        ),
        runtime_root=studio_installation().active_root,
    )
    if response["status"] != "PASS":
        fail("NATIVE_OPERATION_FAILED")
    if resolve_shared_asset("ffmpeg_runtime")[1]["files_sha256"] != asset["files_sha256"]:
        fail("NATIVE_RUNTIME_CHANGED")
    return response, {
        "engine": "FFmpeg",
        "version": response["version"],
        "executable_sha256": response["executable_sha256"],
        "runtime_sha256": asset["files_sha256"],
        "protocols": ["file", "pipe"],
        "demuxers": DEMUXERS.split(","),
        "external_data_references_enabled": False,
        "network_protocols_enabled": False,
        "client_commands_accepted": False,
        "acceleration_claimed": False,
    }


def _normalize(text, path):
    text = text.replace(str(path), "<admitted-media>").replace(path.as_posix(), "<admitted-media>")
    return re.sub(r" @ (?:0x)?[0-9a-fA-F]{8,16}", " @ <address>", text)


def probe_media(extension, content):
    if extension not in MEDIA_EXTENSIONS:
        fail("FORMAT_UNSUPPORTED")
    with tempfile.TemporaryDirectory(prefix="evidence-lane-media-") as folder:
        path = Path(folder) / ("input" + extension)
        path.write_bytes(content)
        response, native = _run(
            _input(path) + ["-map_metadata", "0", "-t", "0", "-f", "ffmetadata", "pipe:1"]
        )
        if path.read_bytes() != content:
            fail("SOURCE_CHANGED")
        metadata_text = _normalize(response["stdout"], path)
        diagnostic = _normalize(response["stderr"], path)
    if not metadata_text.startswith(";FFMETADATA1"):
        fail("METADATA_FORMAT_INVALID")
    input_text = diagnostic.split("Output #", 1)[0]
    format_match = re.search(r"Input #0, ([^\r\n]+), from ", input_text)
    if not format_match:
        fail("METADATA_FORMAT_INVALID")
    demuxers = format_match[1].split(",")
    expected = (
        "mov"
        if extension in {".mp4", ".m4a", ".mov"}
        else "matroska"
        if extension in {".mkv", ".webm"}
        else extension[1:]
    )
    if expected not in demuxers:
        fail("FORMAT_EXTENSION_MISMATCH")
    streams = []
    for line in input_text.splitlines():
        match = re.match(
            r"\s*Stream #0:(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?: (Video|Audio|Subtitle|Data|Attachment): (.*)",
            line,
        )
        if not match:
            continue
        index, kind, description = match.groups()
        stream = {
            "kind": "stream",
            "part": "stream:" + index,
            "stream_index": int(index),
            "stream_type": kind.lower(),
            "codec": description.split(",", 1)[0],
            "text": description,
            "locator_basis": "ffmpeg_reported_stream_index",
        }
        if kind == "Video":
            dimensions = re.search(r"(?:^|,\s+)(\d{1,6})x(\d{1,6})(?:\s|,|$)", description)
            if dimensions:
                stream.update(width=int(dimensions[1]), height=int(dimensions[2]))
            fps = re.search(r"([0-9.]+) fps", description)
            if fps:
                stream["reported_fps"] = float(fps[1])
        if kind == "Audio":
            rate = re.search(r"(\d+) Hz", description)
            if rate:
                stream["sample_rate"] = int(rate[1])
        streams.append(stream)
    if not streams or len(streams) > 32:
        fail("STREAM_METADATA_INVALID")
    duration = re.search(r"Duration: (\d+):(\d+):([0-9.]+)", input_text)
    seconds = (
        int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else None
    )
    rows, scope = list(streams), "global"
    for line in metadata_text.splitlines()[1:]:
        if line.startswith("[") and line.endswith("]"):
            scope = line[1:-1]
        elif "=" in line and not line.startswith((";", "#")):
            key, value = line.split("=", 1)
            rows.append(
                {
                    "kind": "tag",
                    "part": "tag:" + str(len(rows)),
                    "scope": scope,
                    "name": key,
                    "escaped_value": value,
                    "text": key + " " + value,
                    "value_encoding": "ffmetadata_escaped",
                }
            )
    metadata = {
        "kind": "media",
        "format": demuxers[0],
        "extension": extension,
        "streams": len(streams),
        "reported_duration_seconds": seconds,
        "duration_basis": "ffmpeg_container_estimate",
        "ffmetadata": metadata_text,
        "probe_text": input_text,
        "tag_provenance": "ffmpeg_emitted_metadata",
        "full_decode_verified": False,
        "speech_transcribed": False,
    }
    native.update(
        metadata_sha256=digest(metadata_text.encode()),
        diagnostic_sha256=digest(input_text.encode()),
    )
    return metadata, rows, native


def extract_media(filename, content, request):
    from PIL import Image

    extension = Path(filename).suffix.lower()
    if extension not in MEDIA_EXTENSIONS:
        fail("EXTRACTION_FORMAT_UNSUPPORTED")
    if (
        request.kind == "audio_segment"
        and request.duration_seconds * request.sample_rate * request.channels * 2 + 4096 > MAX_BYTES
    ):
        fail("EXTRACTION_OUTPUT_BUDGET")
    with tempfile.TemporaryDirectory(prefix="evidence-lane-media-extract-") as folder:
        source = Path(folder) / ("input" + extension)
        source.write_bytes(content)
        destination = Path(folder) / ("frame.png" if request.kind == "video_frame" else "audio.wav")
        arguments = _input(source) + ["-ss", str(request.start_seconds)]
        if request.kind == "video_frame":
            # Numeric, server-built scale only; no client filter expression.
            arguments += [
                "-map",
                f"0:v:{request.stream}",
                "-an",
                "-sn",
                "-dn",
                "-frames:v",
                "1",
                "-vf",
                f"scale=w='min(iw,{request.max_width})':h='min(ih,{request.max_height})':force_original_aspect_ratio=decrease",
                "-c:v",
                "png",
                "-threads",
                "2",
                "-f",
                "image2",
                "-update",
                "1",
            ]
        else:
            arguments += [
                "-map",
                f"0:a:{request.stream}",
                "-vn",
                "-sn",
                "-dn",
                "-t",
                str(request.duration_seconds),
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(request.sample_rate),
                "-ac",
                str(request.channels),
                "-f",
                "wav",
            ]
        arguments += ["-map_metadata", "-1", "-fs", str(MAX_BYTES), "-y", str(destination)]
        response, native = _run(arguments)
        if source.read_bytes() != content:
            fail("SOURCE_CHANGED")
        if not destination.is_file() or not 1 <= destination.stat().st_size <= MAX_BYTES:
            fail("EXTRACTION_OUTPUT_BUDGET")
        raw = destination.read_bytes()
    if request.kind == "video_frame":
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.width > request.max_width
                or image.height > request.max_height
            ):
                fail("EXTRACTION_VERIFICATION")
            detail = {
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "timestamp_basis": "requested_seek_time_exact_source_pts_not_claimed",
            }
    else:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            if (
                audio.getsampwidth() != 2
                or audio.getnchannels() != request.channels
                or audio.getframerate() != request.sample_rate
                or audio.getnframes() == 0
            ):
                fail("EXTRACTION_VERIFICATION")
            actual = audio.getnframes() / audio.getframerate()
            if actual > request.duration_seconds + 0.1:
                fail("EXTRACTION_DURATION_BUDGET")
            detail = {
                "sample_rate": audio.getframerate(),
                "channels": audio.getnchannels(),
                "samples_per_channel": audio.getnframes(),
                "sample_width_bytes": audio.getsampwidth(),
                "actual_duration_seconds": actual,
                "shorter_than_requested": actual + 0.1 < request.duration_seconds,
            }
    return {
        "input_sha256": digest(content),
        "kind": request.kind,
        "filename": destination.name,
        "sha256": digest(raw),
        "bytes": len(raw),
        "content_base64": base64.b64encode(raw).decode(),
        "request": request.model_dump(mode="json"),
        "details": detail,
        "evidence": {
            **native,
            "source_bytes_mutated": False,
            "metadata_stripped": True,
            "speech_transcribed": False,
            "native_receipt_sha256": response["receipt_sha256"],
        },
    }
