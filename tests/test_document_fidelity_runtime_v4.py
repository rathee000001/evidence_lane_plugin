"""Rendered fidelity and output budgets using the actual isolated office runtime."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .qualification_outputs_v4 import qualification_output
from .test_document_profile_v4 import call, create_plan, execute
from .test_document_profile_v4 import document_system as document_system  # noqa: PLC0414
from .test_document_render_runtime_v4 import office_assets as office_assets  # noqa: PLC0414
from .test_document_verification_v4 import admit, odt_fixture, wait_run


def pdf_text(rendered):
    import pypdfium2 as pdfium
    pdf = next(row for row in rendered['files'] if row['role'] == 'pdf')
    document = pdfium.PdfDocument(pdf['path'])
    pages = []
    try:
        for i in range(len(document)):
            page = document[i]
            text = page.get_textpage()
            try:
                pages.append(text.get_text_range())
            finally:
                text.close()
                page.close()
    finally:
        document.close()
    return pages


def retain_review(name, snapshot, rendered, tmp_path):
    target = qualification_output('document-fidelity-review/' + name, tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    for row in rendered['files']:
        output = target / row['filename']
        shutil.copyfile(row['path'], output)
        row['review_copy'] = str(output.resolve())
    source = Path(snapshot['natural_path'])
    shutil.copyfile(source, target / source.name)
    qualification_output('document-fidelity-' + name + '.json', tmp_path).write_text(json.dumps({
        'snapshot': snapshot, 'rendered': rendered, 'visual_review_performed': False}, indent=2) + '\n')


def test_tracked_edit_line_breaks_and_table_render_from_exact_new_version(document_system, tmp_path):
    create_plan(document_system, ['document_generate', 'document_edit', 'document_render'])
    generated = execute(document_system, 'document_generate', {'logical_name': 'edited-document.docx',
        'title': 'Document revision review', 'blocks': [
            {'kind': 'heading', 'text': 'Edited paragraph'},
            {'kind': 'paragraph', 'text': 'The draft sentence will be replaced.'},
            {'kind': 'table', 'rows': [['Version', 'Meaning'], ['Original', 'Preserved'], ['Edited', 'Separate snapshot']]}]})
    edited = execute(document_system, 'document_edit', {'snapshot_id': generated['snapshot_id'],
        'expected_sha256': generated['sha256'], 'replacements': [{'paragraph_index': 2,
            'expected_text': 'The draft sentence will be replaced.',
            'replacement_text': 'The final sentence is visible.\nA second line\tkeeps its tab.', 'tracked': True}]}, index=1)
    rendered = execute(document_system, 'document_render', {'snapshot_id': edited['snapshot_id'], 'max_pages': 1}, index=2, timeout=185)
    text = pdf_text(rendered)[0]
    assert 'The final sentence is visible.' in text and 'A second line' in text and 'keeps its tab.' in text
    # Rendering the unchanged revision-bearing package uses the converter's
    # review display. Native current text excludes deletions; PDF markup may
    # retain them. Do not claim the renderer accepted or removed revisions.
    assert 'The draft sentence will be replaced.' in text
    assert rendered['evidence']['tracked_changes_display'] == 'converter_default_revision_markup_may_be_visible'
    assert all(value in text for value in ['Original', 'Preserved', 'Edited', 'Separate snapshot'])
    revisions = call(document_system, 'document_query', {'snapshot_id': edited['snapshot_id'], 'collection': 'deletion'})
    assert revisions.result['result']['rows'][0]['text'] == 'The draft sentence will be replaced.'
    retain_review('tracked-edit', edited, rendered, tmp_path)


def test_odt_native_text_and_explicit_table_survive_actual_pdf_rendering(document_system, tmp_path):
    source = document_system[1].source_root / 'structure.odt'
    original = odt_fixture(explicit_table=True)
    source.write_bytes(original)
    create_plan(document_system, ['document_index', 'document_render'])
    indexed = execute(document_system, 'document_index', {'filename': source.name})
    rendered = execute(document_system, 'document_render', {'snapshot_id': indexed['snapshot_id'], 'max_pages': 1}, index=1, timeout=185)
    text = pdf_text(rendered)[0]
    assert all(value in text for value in ['ODT structure', 'Alpha', 'Beta', 'Gamma', 'Delta'])
    assert text.count('Repeated value') == 4
    assert source.read_bytes() == original
    assert rendered['evidence']['odt_repeated_table_layout'] == 'converter_dependent_logical_repeats_not_guaranteed'
    retain_review('odt', indexed, rendered, tmp_path)


def test_real_office_page_budget_failure_leaves_no_render_publication(document_system):
    create_plan(document_system, ['document_generate', 'document_render'])
    generated = execute(document_system, 'document_generate', {'logical_name': 'two-pages.docx', 'title': 'Page budget fixture',
        'blocks': [{'kind': 'paragraph', 'text': 'First page.'}, {'kind': 'page_break'}, {'kind': 'paragraph', 'text': 'Second page.'}]})
    row = wait_run(document_system, admit(document_system, 'document_render', {'snapshot_id': generated['snapshot_id'], 'max_pages': 1}, index=1), timeout=185)
    assert row['state'] == 'blocked' and row['error_code'] == 'DOCUMENT_RENDER_PAGE_BUDGET'
    with document_system[1].lane('docs').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM doc_render').fetchone()[0] == 0
    assert call(document_system, 'document_current').result['result']['documents'][0]['snapshot_id'] == generated['snapshot_id']
