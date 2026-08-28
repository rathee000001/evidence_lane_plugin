"""Docling enrichment behind exact native/OpenXML extraction."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


class DoclingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    artifacts_path: Path
    host_profile: str
    allow_model_download: bool = False


def docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


def packaged_docling_artifacts_root() -> Path:
    return Path(__file__).resolve().parents[2] / "toolchains" / "models" / "docling"


def extract_with_docling(request: DoclingRequest) -> dict[str, Any]:
    source = request.source_path.resolve(strict=True)
    artifacts = request.artifacts_path.resolve(strict=True)
    host = request.host_profile.strip().upper()
    if host not in {"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"}:
        raise ValueError("DOCLING_CODEX_HOST_PROFILE_REQUIRED")
    if request.allow_model_download:
        raise ValueError("DOCLING_RUNTIME_MODEL_DOWNLOAD_FORBIDDEN")
    if not docling_available():
        raise RuntimeError("DOCLING_DEPENDENCY_UNAVAILABLE")
    from docling.datamodel.base_models import InputFormat  # type: ignore[import-not-found]
    from docling.datamodel.pipeline_options import (  # type: ignore[import-not-found]
        PdfPipelineOptions,
    )
    from docling.document_converter import (  # type: ignore[import-not-found]
        DocumentConverter,
        PdfFormatOption,
    )

    options = PdfPipelineOptions(
        artifacts_path=artifacts,
        do_ocr=False,
        do_table_structure=True,
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    result = converter.convert(source)
    markdown = str(result.document.export_to_markdown())
    document_dict = result.document.export_to_dict()
    document_bytes = canonical_json_bytes(document_dict)
    document_projection: dict[str, Any] = (
        document_dict
        if len(document_bytes) <= 2_000_000
        else {
            "bounded": True,
            "canonical_bytes": len(document_bytes),
            "canonical_sha256": sha256_bytes(document_bytes),
        }
    )
    core: dict[str, Any] = {
        "schema": "evidence-lane.docling-extraction.v1",
        "status": "PASS",
        "engine": "Docling",
        "host_profile": host,
        "source_sha256": sha256_file(source),
        "artifacts_path_sha256": sha256_bytes(str(artifacts).encode("utf-8")),
        "artifacts_path_disclosed": False,
        "allow_model_download": False,
        "markdown": markdown,
        "markdown_sha256": sha256_bytes(markdown.encode("utf-8")),
        "document": document_projection,
        "source_mutated": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "DoclingRequest",
    "docling_available",
    "extract_with_docling",
    "packaged_docling_artifacts_root",
]
