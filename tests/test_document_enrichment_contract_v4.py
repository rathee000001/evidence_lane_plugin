"""Deterministic converter boundary cases, separate from actual Docling execution."""
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.document_toolchain import DoclingRequest, extract_with_docling

from .test_document_verification_v4 import word_package


@pytest.mark.parametrize('status', ['pending', 'started', 'failure', 'partial_success', 'skipped'])
def test_incomplete_docling_statuses_cannot_be_reported_as_completed(tmp_path, monkeypatch, status):
    import docling.document_converter as vendor
    from docling.datamodel.base_models import ConversionStatus
    source = tmp_path / 'fixture.docx'
    original = word_package('<w:p><w:r><w:t>Original text</w:t></w:r></w:p>')
    source.write_bytes(original)
    converter = SimpleNamespace(convert=lambda *a, **k: SimpleNamespace(status=ConversionStatus(status)))
    monkeypatch.setattr(vendor, 'DocumentConverter', lambda **kwargs: converter)
    with pytest.raises(ValueError, match='DOCLING_CONVERSION_NOT_COMPLETE'):
        extract_with_docling(DoclingRequest(source_path=source, host_profile='STUDIO_WORKER'))
    assert source.read_bytes() == original


@pytest.mark.parametrize('failure', ['output_budget', 'source_changed'])
def test_success_status_still_requires_bounded_output_and_unchanged_source(tmp_path, monkeypatch, failure):
    import docling.document_converter as vendor
    from docling.datamodel.base_models import ConversionStatus
    source = tmp_path / 'fixture.docx'
    source.write_bytes(word_package('<w:p/>'))
    def convert(*args, **kwargs):
        if failure == 'source_changed':
            source.write_bytes(b'changed externally')
        return SimpleNamespace(status=ConversionStatus.SUCCESS, document=SimpleNamespace(
            export_to_markdown=lambda: 'A' * 5000 if failure == 'output_budget' else 'text',
            export_to_dict=lambda: {'texts': []}))
    monkeypatch.setattr(vendor, 'DocumentConverter', lambda **kwargs: SimpleNamespace(convert=convert))
    code = 'DOCLING_OUTPUT_BYTE_BUDGET' if failure == 'output_budget' else 'DOCLING_SOURCE_CHANGED'
    with pytest.raises(ValueError, match=code):
        extract_with_docling(DoclingRequest(source_path=source, host_profile='STUDIO_WORKER', max_output_bytes=4096))


def test_explicit_runtime_model_download_is_refused_before_converter_import(tmp_path):
    source = tmp_path / 'fixture.docx'
    source.write_bytes(word_package('<w:p/>'))
    with pytest.raises(ValueError, match='DOCLING_RUNTIME_MODEL_DOWNLOAD_FORBIDDEN'):
        extract_with_docling(DoclingRequest(source_path=source, host_profile='STUDIO_WORKER', allow_model_download=True))
