"""Bounded Docling enrichment with completion checks and shared model identity.

Native structures stay authoritative; this converter publishes separate views.
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .document_parsers import digest
from .hashing import canonical_json_bytes
from .storage import reject_links


class DoclingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    source_path: Path
    artifacts_path: Path | None = None
    host_profile: str
    allow_model_download: bool = False
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)
    max_output_bytes: int = Field(default=1_048_576, ge=4096, le=2_097_152)
    max_pages: int = Field(default=100, ge=1, le=500)


def docling_available():
    return importlib.util.find_spec('docling') is not None


def packaged_docling_artifacts_root():
    from .shared_tool_assets import resolve_shared_asset
    return resolve_shared_asset('docling_models')[0]


def extract_with_docling(request):
    source = request.source_path.absolute()
    reject_links(source, Path(source.anchor))
    if request.host_profile not in {'CODEX_DESKTOP', 'CODEX_CLI', 'CODEX_VM', 'STUDIO_WORKER'}:
        raise ValueError('DOCLING_CODEX_HOST_PROFILE_REQUIRED')
    if request.allow_model_download:
        raise ValueError('DOCLING_RUNTIME_MODEL_DOWNLOAD_FORBIDDEN')
    with source.open('rb') as stream:
        content = stream.read(request.max_file_bytes + 1)
    if len(content) > request.max_file_bytes:
        raise ValueError('DOCLING_INPUT_BYTE_BUDGET')
    from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption
    extension = source.suffix.lower()
    asset = None
    if extension == '.pdf':
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        from .shared_tool_assets import resolve_shared_asset
        artifacts, asset = resolve_shared_asset('docling_models')
        if request.artifacts_path is not None and request.artifacts_path.resolve() != artifacts:
            raise ValueError('DOCLING_SHARED_ASSET_REQUIRED')
        options = PdfPipelineOptions(artifacts_path=artifacts, do_ocr=False, do_table_structure=True,
            enable_remote_services=False, allow_external_plugins=False,
            accelerator_options=AcceleratorOptions(device='cpu', num_threads=2), document_timeout=180)
        converter = DocumentConverter(allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    elif extension in {'.docx', '.dotx'}:
        from .document_rendering import safe_render_package
        safe_render_package(content, extension)
        converter = DocumentConverter(allowed_formats=[InputFormat.DOCX])
    elif extension in {'.pptx', '.potx', '.ppsx'}:
        from .presentation_rendering import safe_render_package
        safe_render_package(content, extension)
        converter = DocumentConverter(allowed_formats=[InputFormat.PPTX])
    else:
        raise ValueError('DOCLING_FORMAT_NOT_BOUND')
    logical_name = 'document.docx' if extension == '.dotx' else 'presentation.pptx' if extension in {'.potx', '.ppsx'} else source.name
    converted = converter.convert(DocumentStream(name=logical_name,
        stream=io.BytesIO(content)), raises_on_error=False, max_file_size=request.max_file_bytes,
        max_num_pages=request.max_pages)
    if converted.status != ConversionStatus.SUCCESS:
        raise ValueError('DOCLING_CONVERSION_NOT_COMPLETE')
    markdown = converted.document.export_to_markdown()
    projection = converted.document.export_to_dict()
    if len(markdown.encode()) + len(canonical_json_bytes(projection)) > request.max_output_bytes:
        raise ValueError('DOCLING_OUTPUT_BYTE_BUDGET')
    with source.open('rb') as stream:
        after = stream.read(request.max_file_bytes + 1)
    if after != content:
        raise ValueError('DOCLING_SOURCE_CHANGED')
    if asset and resolve_shared_asset('docling_models')[1]['files_sha256'] != asset['files_sha256']:
        raise ValueError('DOCLING_MODELS_CHANGED')
    core = {'schema': 'evidence-lane.docling-extraction.v4', 'status': 'complete', 'engine': 'Docling',
        'converter_status': converted.status.value, 'source_sha256': digest(content),
        'markdown': markdown, 'markdown_sha256': digest(markdown.encode()), 'document': projection,
        'model_assets_required': extension == '.pdf', 'asset_identity': asset['files_sha256'] if asset else None,
        'allow_model_download': False, 'source_mutated': False, 'native_layout_fidelity': 'not_claimed',
        'host_profile': request.host_profile, 'host_identity': 'execution_configuration_not_native_client_attestation'}
    if asset:
        import importlib.metadata

        core['runtime'] = {'versions': {name: importlib.metadata.version(name) for name in
            ('docling', 'docling-core', 'docling-ibm-models', 'docling-parse', 'torch', 'transformers', 'safetensors')},
            'provider': 'CPU', 'threads': 2, 'ocr_enabled': False}
    return {**core, 'receipt_sha256': digest(canonical_json_bytes(core))}


__all__ = ['DoclingRequest', 'docling_available', 'extract_with_docling', 'packaged_docling_artifacts_root']
