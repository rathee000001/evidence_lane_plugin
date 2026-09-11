"""Explicit Calc rendering and recalculation, distinct from native extraction."""
from __future__ import annotations

import base64
import io
import json
import re
import tempfile
from pathlib import Path

from pydantic import Field

from . import tabular_contracts as contracts
from .document_parsers import digest, package_members, xml_root
from .document_rendering import _convert
from .errors import LaneError
from .hashing import canonical_json_bytes
from .registry import ActionSpec
from .spreadsheet_parsers import OPENXML, member_target, parse_openxml
from .storage import now, project_snapshot
from .tabular_profile import (
    TabularResult,
    check_calls,
    current_snapshot,
    natural_file,
    publish,
    read_bytes,
    read_snapshot,
    relative_path,
    result,
    verify_snapshot,
    worker,
)
from .tabular_schema import tabular_migrations
from .tool_routes import ToolRoute

SAFE_FUNCTIONS = {'SUM', 'SUMIF', 'SUMIFS', 'COUNT', 'COUNTA', 'COUNTIF', 'COUNTIFS', 'AVERAGE', 'AVERAGEIF',
    'AVERAGEIFS', 'MIN', 'MAX', 'IF', 'IFS', 'IFERROR', 'AND', 'OR', 'NOT', 'ABS', 'ROUND', 'ROUNDUP', 'ROUNDDOWN',
    'PRODUCT', 'SUMPRODUCT', 'INT', 'MOD', 'POWER', 'SQRT', 'LEN', 'LEFT', 'RIGHT', 'MID', 'TRIM', 'UPPER', 'LOWER',
    'CONCAT', 'CONCATENATE', 'TEXTJOIN', 'VALUE', 'DATE', 'YEAR', 'MONTH', 'DAY', 'INDEX', 'MATCH', 'VLOOKUP', 'HLOOKUP',
    'XLOOKUP', 'TRUE', 'FALSE', 'ISBLANK', 'ISNUMBER', 'ISTEXT', 'ROWS', 'COLUMNS', 'SUBTOTAL'}


class SpreadsheetRender(contracts.Snapshot):
    max_pages: int = Field(default=20, ge=1, le=100)
    dpi: int = Field(default=110, ge=72, le=150)
    timeout_seconds: int = Field(default=45, ge=5, le=120)
    max_output_bytes: int = Field(default=8_388_608, ge=65_536, le=16_777_216)


class SpreadsheetRecalculate(contracts.Snapshot):
    expected_sha256: str = Field(pattern=contracts.DIGEST)
    timeout_seconds: int = Field(default=45, ge=5, le=120)


class DerivativeRead(contracts.Selection):
    derivative_id: str = Field(pattern=contracts.DIGEST)


def safe_calc(content, filename):
    if Path(filename).suffix.lower() not in OPENXML:
        raise LaneError('SPREADSHEET_CALC_FORMAT', 'Calc operations accept the inspected native OpenXML formats.')
    facts = parse_openxml(filename, content)
    if any(facts['features'][key] for key in ('macros', 'external_links', 'embedded_objects')):
        raise LaneError('SPREADSHEET_CALC_ACTIVE_CONTENT', 'Calc operations require a workbook without macros, external links or embedded objects.')
    members = package_members(content)
    for name, raw in members.items():
        folded = name.casefold()
        if any(value in folded for value in ('/activex/', '/embeddings/', '/externallinks/', '/querytables/', 'connections.xml')):
            raise LaneError('SPREADSHEET_CALC_ACTIVE_CONTENT', 'Calc operations do not refresh embedded or external data sources.')
        if not name.endswith(('.xml', '.rels')):
            continue
        root = xml_root(raw)
        for node in root.iter():
            local = node.tag.rsplit('}', 1)[-1]
            if local == 'Relationship':
                if node.get('TargetMode') == 'External':
                    raise LaneError('SPREADSHEET_CALC_ACTIVE_CONTENT', 'External package targets are outside the Calc contract.')
                base = name.rsplit('/_rels/', 1)[0] if '/_rels/' in name else ''
                member_target(base, node.get('Target', ''), members)
            if local in {'f', 'definedName', 'formula1', 'formula2', 'calculatedColumnFormula', 'totalsRowFormula'}:
                expression = node.text or ''
                masked = re.sub(r'"(?:[^"]|"")*"', '', expression)
                if (any(char in masked for char in ('[', ']', '|'))
                        or re.search(r'(?:https?|file|ftp|smb|ldap):', masked, re.IGNORECASE)):
                    raise LaneError('SPREADSHEET_CALC_EXTERNAL_FORMULA', 'External formula references are outside the Calc contract.')
                functions = re.findall(r'([A-Za-z_][A-Za-z0-9_.]*)\s*\(', masked)
                if any(name.upper() not in SAFE_FUNCTIONS for name in functions):
                    raise LaneError('SPREADSHEET_CALC_FUNCTION_UNSUPPORTED', 'A formula uses a function outside the declared Calc allowlist.')
    return facts


def render_worker(arguments):
    import pypdfium2 as pdfium
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise LaneError('SPREADSHEET_RENDER_SOURCE_CHANGED', 'Render requires the exact snapshot bytes.')
    safe_calc(content, arguments['logical_name'])
    with tempfile.TemporaryDirectory(prefix='evidence-lane-workbook-render-') as temporary:
        temporary = Path(temporary)
        source = temporary / ('workbook' + Path(arguments['logical_name']).suffix.lower())
        source.write_bytes(content)
        pdf_path, evidence = _convert(source, temporary, timeout_seconds=arguments['timeout_seconds'], family='calc')
        pdf = read_bytes(pdf_path, arguments['max_output_bytes'])
        files = [{'filename': 'workbook.pdf', 'role': 'pdf', 'content_base64': base64.b64encode(pdf).decode(),
                  'sha256': digest(pdf), 'bytes': len(pdf)}]
        used, pages = len(pdf), []
        document = pdfium.PdfDocument(pdf)
        try:
            if not 1 <= len(document) <= arguments['max_pages']:
                raise LaneError('SPREADSHEET_RENDER_PAGE_BUDGET', 'The workbook print layout exceeds the requested page budget.')
            for index in range(len(document)):
                page = document[index]
                bitmap, text_page = None, None
                try:
                    width, height = page.get_size()
                    if width * height * (arguments['dpi'] / 72) ** 2 > 20_000_000:
                        raise LaneError('SPREADSHEET_RENDER_PIXEL_BUDGET', 'A page exceeds the finite pixel budget.')
                    bitmap = page.render(scale=arguments['dpi'] / 72)
                    image = bitmap.to_pil()
                    output = io.BytesIO()
                    image.save(output, format='PNG')
                    image.close()
                    png = output.getvalue()
                    used += len(png)
                    if used > arguments['max_output_bytes']:
                        raise LaneError('SPREADSHEET_RENDER_OUTPUT_BUDGET', 'The workbook pages exceed the requested output byte budget.')
                    text_page = page.get_textpage()
                    text = text_page.get_text_range()
                    files.append({'filename': f'page-{index + 1}.png', 'role': 'page_png', 'page_number': index + 1,
                        'content_base64': base64.b64encode(png).decode(), 'sha256': digest(png), 'bytes': len(png)})
                    pages.append({'page_number': index + 1, 'width_points': width, 'height_points': height,
                        'text_sha256': digest(text.encode()), 'text_characters': len(text)})
                finally:
                    if text_page is not None:
                        text_page.close()
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
        finally:
            document.close()
        if source.read_bytes() != content:
            raise LaneError('SPREADSHEET_RENDER_SOURCE_CHANGED', 'The converter changed its private input copy.')
    return {'input_sha256': arguments['expected_sha256'], 'files': files, 'pages': pages, 'evidence': {**evidence,
        'family': 'Calc', 'raster_engine': 'pypdfium2', 'dpi': arguments['dpi'], 'source_bytes_mutated': False,
        'visual_review': 'required_not_performed_by_renderer', 'excel_layout_equivalence': False,
        'formula_values': 'Calc_evaluated_allowlisted_formulas_not_Excel_equivalence', 'font_substitution': 'possible'}}


def render(context, request):
    execution, store = context.execution, context.execution.store
    check_calls(execution)
    manifest, _ = read_snapshot(store, 'data_excel', request.snapshot_id)
    lane = store.lane('data_excel')
    content = lane.read_object(manifest['raw_object'])
    response = execution.submit('spreadsheet_render', request.model_dump(mode='json') | {
        'logical_name': manifest['logical_name'], 'expected_sha256': manifest['raw_object'],
        'content_base64': base64.b64encode(content).decode()}).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'SPREADSHEET_RENDER_FAILED'), 'The spreadsheet render worker did not complete.')
    generated, contents = json.loads(json.dumps(response['result'])), {}
    for row in generated['files']:
        raw = base64.b64decode(row.pop('content_base64'), validate=True)
        if digest(raw) != row['sha256'] or len(raw) != row['bytes']:
            raise LaneError('SPREADSHEET_RENDER_BINDING', 'A rendered artifact failed byte verification.')
        contents[row['filename']] = raw
    expected = {'workbook.pdf', *(f'page-{i + 1}.png' for i in range(len(generated['pages'])))}
    if (generated['input_sha256'] != manifest['raw_object'] or set(contents) != expected
            or len(contents) != len(generated['files']) or not 1 <= len(generated['pages']) <= request.max_pages
            or sum(map(len, contents.values())) > request.max_output_bytes):
        raise LaneError('SPREADSHEET_RENDER_BINDING', 'The worker returned a different source or bounded page set.')
    body = {'schema': 'evidence-lane.spreadsheet-render.v4', 'project_id': store.project_id, 'lane_id': 'data_excel',
        'snapshot_id': request.snapshot_id, **generated, 'created_at': now()}
    identity = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['data_excel', 'receipts']):  # noqa: SIM117
        with lane.transaction() as connection:
            for name, raw in contents.items():
                lane.put_object(raw)
                natural_file(lane, identity, name, raw)
            connection.execute('INSERT INTO sheet_derivative VALUES(?,?,?,?,?)',
                (identity, request.snapshot_id, 'render', lane.put_object(canonical_json_bytes(body)), body['created_at']))
            store.append_receipt('spreadsheet_render', {'derivative_id': identity, 'snapshot_id': request.snapshot_id,
                'page_count': len(generated['pages']), 'source_bytes_mutated': False})
    return result(store, 'data_excel', 'spreadsheet_render', {'derivative_id': identity, 'snapshot_id': request.snapshot_id,
        'page_count': len(generated['pages']), 'files': [{**row, 'path': str(lane.files / 'natural' / identity / row['filename'])}
            for row in generated['files']], 'evidence': generated['evidence'], 'source_bytes_mutated': False})


def read_render(store, identity):
    lane = store.lane('data_excel')
    with lane.connection(read_only=True) as connection:
        row = connection.execute("SELECT * FROM sheet_derivative WHERE derivative_id=? AND kind='render'", (identity,)).fetchone()
    if row is None:
        raise LaneError('SPREADSHEET_RENDER_MISSING', 'Select an exact workbook render from this lane.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(manifest)) != identity or manifest['project_id'] != store.project_id
            or manifest['lane_id'] != 'data_excel' or manifest['snapshot_id'] != row['snapshot_id']):
        raise LaneError('SPREADSHEET_RENDER_INTEGRITY', 'The render manifest differs from its indexed identity.')
    for item in manifest['files']:
        if '/' in relative_path(item['filename']):
            raise LaneError('SPREADSHEET_RENDER_INTEGRITY', 'A render artifact must have a simple filename.')
        raw = read_bytes(lane.files / 'natural' / identity / item['filename'], 16_777_216)
        if digest(raw) != item['sha256'] or lane.read_object(item['sha256']) != raw:
            raise LaneError('SPREADSHEET_RENDER_INTEGRITY', 'A rendered artifact differs from its immutable bytes.')
    return {'derivative_id': identity, **manifest}


def verify_render(context, request, output):
    manifest = read_render(context.store, output.result['derivative_id'])
    valid = manifest['snapshot_id'] == request.snapshot_id and len(manifest['pages']) == output.result['page_count']
    return [{'check_id': name, 'passed': valid, 'evidence': {'derivative_id': output.result['derivative_id'],
        'visual_review': 'not_inferred'}} for name in context.requested_checks]


def recalculate_worker(arguments):
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise LaneError('SPREADSHEET_RECALCULATE_BINDING', 'Recalculation requires the exact snapshot bytes.')
    if Path(arguments['logical_name']).suffix.lower() != '.xlsx':
        raise LaneError('SPREADSHEET_RECALCULATE_FORMAT', 'Recalculation publishes an XLSX derivative from XLSX input.')
    safe_calc(content, arguments['logical_name'])
    with tempfile.TemporaryDirectory(prefix='evidence-lane-workbook-calculate-') as temporary:
        temporary = Path(temporary)
        source = temporary / 'source.xlsx'
        source.write_bytes(content)
        path, evidence = _convert(source, temporary, timeout_seconds=arguments['timeout_seconds'], output_format='xlsx', family='calc')
        raw = read_bytes(path)
        safe_calc(raw, arguments['logical_name'])
        if source.read_bytes() != content:
            raise LaneError('SPREADSHEET_RECALCULATE_BINDING', 'The converter changed its private input copy.')
    from .tabular_workers import encoded
    return encoded('data_excel', arguments['logical_name'], raw, evidence={**evidence, 'family': 'Calc',
        'operation': 'recalculate', 'formulas_recalculated': True, 'excel_formula_equivalence': False,
        'package_roundtrip': 'LibreOffice_may_rewrite_formatting_and_objects', 'source_bytes_mutated': False})


def recalculate(context, request):
    execution, store = context.execution, context.execution.store
    check_calls(execution)
    manifest, _ = read_snapshot(store, 'data_excel', request.snapshot_id)
    if (manifest['raw_object'] != request.expected_sha256
            or current_snapshot(store, 'data_excel', manifest['source_id']) != request.snapshot_id):
        raise LaneError('SPREADSHEET_RECALCULATE_BINDING', 'Recalculate the exact current workbook snapshot.')
    content, parsed = worker(execution, 'spreadsheet_recalculate', request.model_dump(mode='json') | {
        'logical_name': manifest['logical_name'], 'content_base64': base64.b64encode(store.lane('data_excel').read_object(manifest['raw_object'])).decode()})
    return publish(context, lane_id='data_excel', source_id=manifest['source_id'], logical_name=manifest['logical_name'],
        source_path=manifest['source_path'], origin=manifest['origin'], previous=request.snapshot_id,
        content=content, parsed=parsed, operation='spreadsheet_recalculate')


def register_spreadsheet_rendering(engine):
    for action, model, handler, checks, verifier, tools in (
        ('spreadsheet_render', SpreadsheetRender, render, ('spreadsheet_render_bytes_verified',), verify_render, ('Python', 'LibreOffice', 'pypdfium2')),
        ('spreadsheet_recalculate', SpreadsheetRecalculate, recalculate, ('tabular_snapshot_integrity',), verify_snapshot, ('Python', 'LibreOffice'))):
        engine.registry.register(ActionSpec(action, 'Use the verified shared Calc runtime on exact inspected workbook bytes with explicit formula and fidelity limits.',
            contracts.model_for('data_excel', model), TabularResult, handler, permission='write', mutates=True, requires_delta=True,
            profile='spreadsheet', workflow='build', worker_operations=(action,), verification_checks=checks, verifier=verifier,
            tool_routes=(ToolRoute(action + '.calc', handler, tools, systems=('Windows',)),)))
    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return result(store, 'data_excel', 'spreadsheet_render_read', read_render(store, request.derivative_id))
    engine.registry.register(ActionSpec('spreadsheet_render_read', 'Read and verify an exact workbook rendering and its natural artifacts.',
        contracts.model_for('data_excel', DerivativeRead), TabularResult, read, profile='spreadsheet', workflow='source-intake',
        queryable_in_delta=True, cross_project_read=True, studio_read=True, read_migrations=tabular_migrations('data_excel')))
