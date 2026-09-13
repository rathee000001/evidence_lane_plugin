"""Document fidelity, refusal, persistence and real stdio protocol qualification."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import stat
import time
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.document_parsers import W, package_members, parse_document, xml_root
from evidence_lane_plugin.document_rendering import safe_render_package
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.storage import ProjectStore

from .test_document_profile_v4 import call, create_plan, execute
from .test_document_profile_v4 import document_system as document_system  # noqa: PLC0414
from .test_native_workflow_bindings import native


def package(members, *, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


def odt_fixture(*, explicit_table=False):
    content = b'''<?xml version="1.0"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.2">
 <office:body><office:text><text:h text:outline-level="1">ODT structure</text:h>
 <text:p>Alpha<text:s text:c="2"/>Beta<text:tab/>Gamma<text:line-break/>Delta</text:p>
 <table:table table:name="Values"><table:table-row table:number-rows-repeated="2">
 <table:table-cell table:number-columns-repeated="2" office:value-type="string"><text:p>Repeated value</text:p></table:table-cell>
 </table:table-row></table:table></office:text></office:body></office:document-content>'''
    if explicit_table:
        cell = b'<table:table-cell office:value-type="string"><text:p>Repeated value</text:p></table:table-cell>'
        table = b'<table:table table:name="Values"><table:table-column table:number-columns-repeated="2"/>'
        table += (b'<table:table-row>' + cell * 2 + b'</table:table-row>') * 2 + b'</table:table>'
        start, end = content.index(b'<table:table '), content.index(b'</table:table>') + len(b'</table:table>')
        content = content[:start] + table + content[end:]
    return package({'mimetype': b'application/vnd.oasis.opendocument.text', 'content.xml': content,
        'META-INF/manifest.xml': b'''<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>'''})


def word_package(xml, extra=None):
    return package({'[Content_Types].xml': b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        'word/document.xml': ('<w:document xmlns:w="' + W[1:-1] + '" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<w:body>' + xml + '</w:body></w:document>').encode(), **(extra or {})})


def admit(system, action, arguments, *, index=0):
    task = PlanStore(system[1]).task('doc-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments}, expected_revision=1)


def wait_run(system, admitted, timeout=45):
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with system[1].lane('plan').connection(read_only=True) as db:
                row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.02)
            continue
        if row['state'] in {'verified', 'blocked'}:
            system[0].delta.owned_completion(admitted.job_id).result(
                timeout=max(1, deadline - time.monotonic())
            )
            return row
        time.sleep(.02)
    pytest.fail('Document operation did not reach a terminal observation within its deadline')


def test_odt_spaces_tabs_line_breaks_and_repeated_cells_are_not_lost():
    facts = parse_document('fixture.odt', odt_fixture())
    paragraphs = [row['text'] for row in facts['items'] if row['kind'] == 'paragraph']
    assert paragraphs[1] == 'Alpha  Beta\tGamma\nDelta'
    assert next(row for row in facts['items'] if row['kind'] == 'table')['rows'] == [['Repeated value'] * 2] * 2
    assert facts['fidelity']['page_count'] is None


@pytest.mark.parametrize(('extension', 'content', 'expected'), [
    ('.html', '<h1>Heading</h1><script>secret()</script><style>hidden</style><p>A &amp; B<br>Next</p>', ['Heading', 'A & B', 'Next']),
    ('.md', '# Heading\nA café sentence.', ['Heading', 'A café sentence.']),
    ('.xml', '<root><title>Heading</title><p>A sentence.</p></root>', ['Heading', 'A sentence.']),
    ('.rst', 'Heading\n=======\nA sentence.', ['Heading', '=======', 'A sentence.']),
    ('.txt', '\ufeffA café sentence.', ['A café sentence.'])])
def test_text_format_facts_are_utf8_bounded_and_do_not_execute_resources(extension, content, expected):
    facts = parse_document('fixture' + extension, content.encode())
    assert [row['text'] for row in facts['items'] if row['kind'] == 'paragraph'] == expected
    assert not facts['fidelity']['resources_fetched'] and facts['fidelity']['layout'] == 'not_rendered'


def test_word_notes_controls_fields_images_links_bookmarks_and_revisions_have_locators():
    parts = {f'word/{kind}.xml': (f'<w:{kind} xmlns:w="{W[1:-1]}"><w:{tag} w:id="2">'
        f'<w:p><w:r><w:t>{kind} text</w:t></w:r></w:p></w:{tag}></w:{kind}>').encode()
        for kind, tag in [('footnotes', 'footnote'), ('endnotes', 'endnote'), ('comments', 'comment')]}
    parts['word/_rels/document.xml.rels'] = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/never-request" TargetMode="External"/>
<Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image.png"/></Relationships>'''
    parts['word/media/image.png'] = b'opaque-image-fixture'
    content = word_package('''<w:sdt><w:sdtPr><w:tag w:val="customer"/></w:sdtPr><w:sdtContent>
<w:p><w:r><w:t>Controlled text</w:t></w:r></w:p></w:sdtContent></w:sdt>
<w:p><w:bookmarkStart w:id="3" w:name="anchor"/><w:hyperlink r:id="rIdLink"><w:r><w:t>Link text</w:t></w:r></w:hyperlink>
<w:r><w:drawing r:embed="rIdImage"/></w:r><w:fldSimple w:instr="PAGE"><w:r><w:t>7</w:t></w:r></w:fldSimple></w:p>
<w:p><w:del w:id="4"><w:r><w:delText>Old</w:delText></w:r></w:del><w:ins w:id="5"><w:r><w:t>New</w:t></w:r></w:ins></w:p>''', parts)
    facts = parse_document('rich.dotx', content)
    kinds = {row['kind'] for row in facts['items']}
    assert {'content_control', 'field', 'image', 'hyperlink', 'bookmark', 'insertion', 'deletion'} <= kinds
    assert [row['text'] for row in facts['items'] if row['kind'] == 'paragraph' and row['part'] == 'word/document.xml'][-1] == 'New'
    assert {row['part']: row['note_id'] for row in facts['items'] if row['kind'] == 'paragraph' and row.get('note_id')} == {
        'word/comments.xml': '2', 'word/endnotes.xml': '2', 'word/footnotes.xml': '2'}
    image = next(row for row in facts['items'] if row['kind'] == 'image')
    assert image['relationships'][0]['member_present'] and image['relationships'][0]['member'] == 'word/media/image.png'
    assert facts['features']['tracked_changes'] and facts['features']['fields'] and facts['features']['content_controls']
    assert not facts['features']['external_resources']


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'word\\escape', 'word/document.xml:stream'])
def test_zip_path_members_are_refused_without_extraction(name):
    # Windows ZipInfo normalizes backslashes on creation. Put the literal
    # foreign member name into both ZIP headers to test the actual parser.
    content = package({name.replace('\\', '/'): b'content'})
    if '\\' in name:
        content = content.replace(name.replace('\\', '/').encode(), name.encode())
    with pytest.raises(ValueError, match='DOCUMENT_PACKAGE_MEMBER_INVALID'):
        package_members(content)


def test_duplicate_symlink_encrypted_and_high_ratio_zip_members_are_refused():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('same', 'one')
        with pytest.warns(UserWarning, match='Duplicate name'):
            archive.writestr('same', 'two')
    with pytest.raises(ValueError, match='DOCUMENT_PACKAGE_MEMBER_INVALID'):
        package_members(output.getvalue())
    item = zipfile.ZipInfo('link')
    item.create_system = 3
    item.external_attr = (stat.S_IFLNK | 0o777) << 16
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(item, 'target')
    with pytest.raises(ValueError, match='DOCUMENT_PACKAGE_MEMBER_INVALID'):
        package_members(output.getvalue())
    content = bytearray(package({'word/document.xml': b'<p/>'}))
    for signature, offset in [(b'PK\x03\x04', 6), (b'PK\x01\x02', 8)]:
        content[content.index(signature) + offset] |= 1
    with pytest.raises(ValueError, match='DOCUMENT_PACKAGE_MEMBER_INVALID'):
        package_members(content)
    with pytest.raises(ValueError, match='DOCUMENT_PACKAGE_MEMBER_INVALID'):
        package_members(package({'bomb.xml': b'A' * 2_000_000}))


@pytest.mark.parametrize('content', [b'<!DOCTYPE p [<!ENTITY x SYSTEM "file:///no-read">]><p>&x;</p>',
    b'<p>\0</p>', b'<p>' * 130 + b'</p>' * 130])
def test_xml_entities_nul_and_depth_are_refused(content):
    with pytest.raises(ValueError, match='DOCUMENT_XML_'):
        xml_root(content)


@pytest.mark.parametrize('body', [
    '<w:p><w:fldSimple w:instr="INCLUDETEXT &quot;file:///no-read&quot;"/></w:p>',
    '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>INCLUDE</w:instrText></w:r><w:r><w:instrText>TEXT "file:///no-read"</w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>',
    '<w:p><w:object/></w:p>'])
def test_active_and_fragmented_field_instructions_are_refused_before_office_starts(body):
    with pytest.raises(LaneError, match='field|objects'):
        safe_render_package(word_package(body), '.docx')


@pytest.mark.parametrize('mode', ['external_image', 'macro', 'odt_parent_resource'])
def test_unsafe_render_packages_are_rejected_before_dependency_resolution(mode):
    if mode == 'macro':
        content, extension = word_package('', {'word/vbaProject.bin': b'macro'}), '.docx'
    elif mode == 'external_image':
        content, extension = word_package('', {'word/_rels/document.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Type="image" TargetMode="External" Target="https://example.invalid/image"/></Relationships>'}), '.docx'
    else:
        members = package_members(odt_fixture())
        members['content.xml'] = members['content.xml'].replace(b'<text:p>Alpha', b'<text:p xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="../outside">Alpha')
        content, extension = package(members), '.odt'
    with pytest.raises(LaneError, match='resource|macro'):
        safe_render_package(content, extension)


@pytest.mark.parametrize('control', [b'\\field1', b'\\object0', b'\\objdata', b'\\filetbl-1'])
def test_numeric_rtf_active_controls_are_refused(control):
    from evidence_lane_plugin.document_conversion import safe_legacy
    with pytest.raises(LaneError, match='embedded objects'):
        safe_legacy(b'{\\rtf1\\ansi ' + control + b' data}', '.rtf')


def test_odt_expanded_space_and_repeat_budgets_are_checked_before_allocation():
    members = package_members(odt_fixture())
    for before, after in [(b'text:c="2"', b'text:c="9999999999"'),
                          (b'number-rows-repeated="2"', b'number-rows-repeated="9999999999"')]:
        content = package({**members, 'content.xml': members['content.xml'].replace(before, after)})
        with pytest.raises(ValueError, match='BUDGET'):
            parse_document('fixture.odt', content)


def test_document_receipt_failure_does_not_publish_a_current_version(document_system, monkeypatch):
    original = ProjectStore.append_receipt
    def failing(self, kind, *args, **kwargs):
        if kind == 'document_snapshot':
            raise RuntimeError('Injected document receipt failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ProjectStore, 'append_receipt', failing)
    create_plan(document_system, ['document_index'])
    before = (document_system[1].source_root / 'fixture.docx').read_bytes()
    row = wait_run(document_system, admit(document_system, 'document_index', {'filename': 'fixture.docx'}))
    assert row['state'] == 'blocked'
    assert call(document_system, 'document_current').result['result']['documents'] == []
    assert (document_system[1].source_root / 'fixture.docx').read_bytes() == before


def test_source_change_after_worker_extraction_does_not_publish_a_current_version(document_system, monkeypatch):
    from evidence_lane_plugin import document_profile
    original = document_profile._worker
    source = document_system[1].source_root / 'fixture.docx'
    def changing(*args, **kwargs):
        value = original(*args, **kwargs)
        source.write_bytes(source.read_bytes() + b'externally added bytes')
        return value
    monkeypatch.setattr(document_profile, '_worker', changing)
    create_plan(document_system, ['document_index'])
    row = wait_run(document_system, admit(document_system, 'document_index', {'filename': source.name}))
    assert row['state'] == 'blocked' and row['error_code'] == 'DOCUMENT_SOURCE_CHANGED'
    assert call(document_system, 'document_current').result['result']['documents'] == []
    assert source.read_bytes().endswith(b'externally added bytes')


@pytest.mark.parametrize('mode', ['invalid_xml', 'too_many_members', 'too_many_items'])
def test_malformed_and_oversized_document_structures_fail_inside_real_worker(document_system, mode):
    source = document_system[1].source_root / ('invalid.txt' if mode == 'too_many_items' else 'invalid.docx')
    if mode == 'invalid_xml':
        content = word_package('<w:p>')
    elif mode == 'too_many_members':
        content = package({str(i): b'x' for i in range(4097)})
    else:
        content = b'line\n' * 8200
    source.write_bytes(content)
    create_plan(document_system, ['document_index'])
    row = wait_run(document_system, admit(document_system, 'document_index', {'filename': source.name}))
    assert row['state'] == 'blocked'
    assert call(document_system, 'document_current').result['result']['documents'] == []
    assert source.read_bytes() == content


@pytest.mark.parametrize('mode', ['bad_hash', 'bad_text', 'stale_version', 'complex_paragraph'])
def test_refused_edits_leave_current_version_and_source_unchanged(document_system, mode):
    source = document_system[1].source_root / 'fixture.docx'
    if mode == 'complex_paragraph':
        from docx import Document
        from docx.oxml import OxmlElement
        doc = Document(source)
        doc.paragraphs[2]._p.append(OxmlElement('w:hyperlink'))
        doc.save(source)
    before = source.read_bytes()
    actions = ['document_index', 'document_edit']
    if mode == 'stale_version':
        actions.insert(1, 'document_refresh')
    create_plan(document_system, actions)
    indexed = execute(document_system, 'document_index', {'filename': 'fixture.docx'})
    current = indexed
    if mode == 'stale_version':
        current = execute(document_system, 'document_refresh', {'filename': 'fixture.docx', 'expected_snapshot': indexed['snapshot_id']}, index=1)
    row = wait_run(document_system, admit(document_system, 'document_edit', {'snapshot_id': indexed['snapshot_id'],
        'expected_sha256': '0' * 64 if mode == 'bad_hash' else indexed['sha256'], 'replacements': [{
            'paragraph_index': 2, 'expected_text': 'wrong text' if mode == 'bad_text' else 'The selected operation preserves the source document.',
            'replacement_text': 'Replacement'}]}, index=len(actions) - 1))
    assert row['state'] == 'blocked', row
    assert source.read_bytes() == before
    assert call(document_system, 'document_current').result['result']['documents'][0]['snapshot_id'] == current['snapshot_id']


@pytest.mark.parametrize('target_exists', [False, True])
def test_export_destination_race_preserves_external_bytes(document_system, monkeypatch, target_exists):
    from evidence_lane_plugin import document_profile
    source = document_system[1].source_root / 'export.docx'
    if target_exists:
        source.write_bytes(b'previous bytes')
    create_plan(document_system, ['document_index', 'document_export'])
    indexed = execute(document_system, 'document_index', {'filename': 'fixture.docx'})
    original = document_profile._export_mkstemp
    def concurrent_edit(*args, **kwargs):
        value = original(*args, **kwargs)
        source.write_bytes(b'concurrent user edit')
        return value
    monkeypatch.setattr(document_profile, '_export_mkstemp', concurrent_edit)
    row = wait_run(document_system, admit(document_system, 'document_export', {'snapshot_id': indexed['snapshot_id'],
        'filename': source.name, 'expected_sha256': hashlib.sha256(b'previous bytes').hexdigest() if target_exists else None}, index=1))
    assert row['state'] == 'blocked' and row['error_code'] == 'DOCUMENT_EXPORT_DESTINATION_CHANGED'
    assert source.read_bytes() == b'concurrent user edit'
    assert list(source.parent.glob('.evidence-lane-document-*.tmp')) == []
    if target_exists:
        assert document_system[1].lane('docs').read_object(hashlib.sha256(b'previous bytes').hexdigest()) == b'previous bytes'


@pytest.mark.parametrize('table', ['doc_paragraph', 'doc_chunk_fts'])
def test_tampered_structure_and_search_rows_cannot_be_returned_as_document_facts(document_system, table):
    create_plan(document_system, ['document_index'])
    indexed = execute(document_system, 'document_index', {'filename': 'fixture.docx'})
    engine, store, _ = document_system
    with engine.project_work.mutation(store) as lease, lease.transaction('docs') as db:
        if table == 'doc_paragraph':
            db.execute("UPDATE doc_paragraph SET payload_json=json_set(payload_json,'$.text','forged result') WHERE ordinal=2")
        else:
            db.execute("UPDATE doc_chunk_fts SET text_content='forged result'")
    response = call(document_system, 'document_query', {'snapshot_id': indexed['snapshot_id'],
        'collection': 'paragraph' if table == 'doc_paragraph' else 'text', 'query': 'forged'})
    assert response.status == 'error' and response.error.code == 'DOCUMENT_QUERY_INTEGRITY'


def test_document_graph_formats_bind_structure_locators_and_detect_artifact_changes(document_system):
    create_plan(document_system, ['document_index'])
    indexed = execute(document_system, 'document_index', {'filename': 'fixture.docx'})
    args = {'view_id': 'docs.structure', 'scope': {'query': indexed['document_id']}}
    preview = call(document_system, 'lane_view_preview', args).result
    assert {'document', 'document_heading', 'document_table'} == {node['kind'] for node in preview['graph']['nodes']}
    published = call(document_system, 'lane_view_refresh', {**args, 'scope': preview['scope'],
        'expected_generation': preview['generation'], 'contract_digest': preview['contract_digest'],
        'source_digest': preview['source_digest'], 'formats': ['mmd', 'dot'], 'include_pointer': True})
    assert published.status == 'ok', published.error
    assert {Path(row['path']).name for row in published.result['files']} == {'docs.mmd', 'docs.dot', 'docs.pointer.json'}
    read_args = {'view_id': args['view_id'], 'snapshot_digest': published.result['snapshot_digest']}
    read = call(document_system, 'lane_view_read', {**read_args, 'include_content': True})
    assert read.status == 'ok', read.error
    pointer = json.loads(read.result['contents']['pointer'])
    assert not pointer['page_numbers_from_graph']
    assert {locator['snapshot_id'] for locator in pointer['locators'].values()} == {indexed['snapshot_id']}
    assert pointer['snapshot_binding']['source_digest'] == preview['source_digest']
    path = Path(published.result['files'][0]['path'])
    path.write_bytes(path.read_bytes() + b'\nchanged')
    assert call(document_system, 'lane_view_read', read_args).error.code == 'VIEW_ARTIFACT_CHANGED'


def test_stdio_document_intake_query_edit_export_refresh_use_the_real_engine(document_system):
    engine, store, _ = document_system
    create_plan(document_system, ['document_index', 'document_edit', 'document_export', 'document_refresh'])
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            async def query(action, arguments):
                response = await session.call_tool(action, {'project_id': store.project_id, 'arguments': arguments})
                body = response.structuredContent
                assert body['status'] == 'ok', body
                return body['result']['result']
            async def delta(index, action, arguments):
                task = PlanStore(store).task('doc-' + str(index), expected_revision=1)
                response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                                  'action': action, 'arguments': arguments}})
                body = response.structuredContent
                assert body['status'] == 'queued', body
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    try:
                        with store.lane('plan').connection(read_only=True) as db:
                            row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                    except LaneError as error:
                        if error.code != 'PROJECT_RECOVERY_REQUIRED':
                            raise
                        await asyncio.sleep(.03)
                        continue
                    if row['state'] in {'verified', 'blocked'}:
                        break
                    await asyncio.sleep(.03)
                assert row['state'] == 'verified', row
                return json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
            first = await delta(0, 'document_index', {'filename': 'fixture.docx'})
            assert (await query('document_query', {'snapshot_id': first['snapshot_id'], 'query': 'preserves'}))['rows']
            edited = await delta(1, 'document_edit', {'snapshot_id': first['snapshot_id'], 'expected_sha256': first['sha256'],
                'replacements': [{'paragraph_index': 2, 'expected_text': 'The selected operation preserves the source document.',
                    'replacement_text': 'Protocol verified replacement.'}]})
            exported = await delta(2, 'document_export', {'snapshot_id': edited['snapshot_id'], 'filename': 'fixture.docx', 'expected_sha256': first['sha256']})
            assert not exported['source_index_refresh_required']
            refreshed = await delta(3, 'document_refresh', {'filename': 'fixture.docx',
                'expected_snapshot': exported['index_refresh']['result']['snapshot_id']})
            assert (await query('document_query', {'snapshot_id': refreshed['snapshot_id'], 'query': 'Protocol verified'}))['rows'][0]['text'] == 'Protocol verified replacement.'
            historical = await query('document_read', {'snapshot_id': first['snapshot_id'], 'max_bytes': 131072})
            assert hashlib.sha256(base64.b64decode(historical['content_base64'])).hexdigest() == first['sha256']
    with LocalEndpoint(engine):
        asyncio.run(run())
