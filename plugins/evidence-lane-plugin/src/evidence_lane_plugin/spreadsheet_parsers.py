"""Native OpenXML spreadsheet structure adapted from the admitted sheet parser.

Intake never opens external links, runs macros, or evaluates formula expressions.
Sparse cell coordinates are retained instead of expanding a worksheet dimension.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urldefrag

from .document_parsers import REL, R, package_members, xml_root
from .tabular_values import MAX_TABLES, Facts, number, typed

S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
OPENXML = {'.xlsx', '.xlsm', '.xltx', '.xltm'}
CELL = re.compile(r'^([A-Z]{1,3})([1-9][0-9]{0,6})$')


def coordinates(reference):
    match = CELL.fullmatch(reference)
    if not match:
        raise ValueError('SPREADSHEET_CELL_REFERENCE_INVALID')
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - 64
    row = int(match[2])
    if column > 16_384 or row > 1_048_576:
        raise ValueError('SPREADSHEET_CELL_REFERENCE_INVALID')
    return row, column


def member_target(base, target, members):
    target = unquote(urldefrag(target)[0], errors='strict')
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target) or '\\' in target or '\x00' in target or re.search(r'%[0-9A-Fa-f]{2}', target):
        raise ValueError('SPREADSHEET_PACKAGE_REFERENCE_INVALID')
    # OpenXML permits package-root-relative targets, never filesystem roots.
    selected = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base, target))
    if selected.startswith('../') or selected == '..' or selected not in members:
        raise ValueError('SPREADSHEET_PACKAGE_REFERENCE_MISSING')
    return selected


def relationships(members, part):
    path = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    result = {}
    if path not in members:
        return result
    for row in xml_root(members[path]).findall(REL + 'Relationship'):
        identity = row.get('Id', '')
        if not identity or identity in result:
            raise ValueError('SPREADSHEET_RELATIONSHIP_INVALID')
        external = row.get('TargetMode') == 'External'
        result[identity] = {'id': identity, 'type': row.get('Type', ''), 'target': row.get('Target', ''),
            'external': external, 'member': None if external else member_target(posixpath.dirname(part), row.get('Target', ''), members)}
    return result


def cell_value(cell, strings):
    value = cell.find(S + 'v')
    raw = value.text if value is not None and value.text is not None else None
    kind = cell.get('t', 'n')
    if kind == 'inlineStr':
        return typed(''.join(node.text or '' for node in cell.iter(S + 't')))
    if raw is None:
        return typed(None)
    if kind == 's':
        index = int(raw)
        if index < 0 or index >= len(strings):
            raise ValueError('SPREADSHEET_SHARED_STRING_INVALID')
        return typed(strings[index])
    if kind == 'b':
        if raw not in {'0', '1'}:
            raise ValueError('SPREADSHEET_BOOLEAN_INVALID')
        return typed(raw == '1')
    if kind == 'n':
        return number(raw)
    if kind in {'str', 'd', 'e'}:
        value = typed(raw)
        if kind in {'d', 'e'}:
            value['type'] = 'iso_date' if kind == 'd' else 'error'
        return value
    raise ValueError('SPREADSHEET_CELL_TYPE_UNSUPPORTED')


def dependencies(formula, sheet):
    # Literal strings are excluded. Structured/named/dynamic/external references
    # are not resolved, and ranges remain ranges rather than expanded cell sets.
    masked = re.sub(r'"(?:[^"]|"")*"', lambda m: ' ' * len(m[0]), formula)
    pattern = (r"(?<![A-Za-z0-9_\[.])(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<sheet>[A-Za-z_][A-Za-z0-9_.]*))!)?"
               r'(?P<start>\$?[A-Z]{1,3}\$?[1-9][0-9]*)(?::(?P<end>\$?[A-Z]{1,3}\$?[1-9][0-9]*))?'
               r'(?![A-Za-z0-9_(])')
    found = set()
    for match in re.finditer(pattern, masked):
        start = match['start'].replace('$', '')
        end = (match['end'] or '').replace('$', '')
        coordinates(start)
        if end:
            coordinates(end)
        target_sheet = (match['quoted'] or match['sheet'] or sheet).replace("''", "'")
        found.add((target_sheet, start, end))
    return [{'sheet': title, 'range': start + (':' + end if end else '')} for title, start, end in sorted(found)]


def parse_openxml(filename, content):
    members, facts = package_members(content), Facts('data_excel', PurePosixPath(filename).suffix.lower())
    workbook = xml_root(members['xl/workbook.xml'])
    xml_root(members['[Content_Types].xml'])
    links = relationships(members, 'xl/workbook.xml')
    strings = []
    if 'xl/sharedStrings.xml' in members:
        strings = [''.join(node.text or '' for node in item.iter(S + 't'))
            for item in xml_root(members['xl/sharedStrings.xml']).findall(S + 'si')]
    styles = []
    if 'xl/styles.xml' in members:
        style = xml_root(members['xl/styles.xml'])
        formats = {int(node.get('numFmtId')): node.get('formatCode', '') for node in style.findall(S + 'numFmts/' + S + 'numFmt')}
        styles = [{'number_format_id': int(node.get('numFmtId', '0')), 'number_format': formats.get(int(node.get('numFmtId', '0'))),
                   'style_attributes': dict(node.attrib)} for node in style.findall(S + 'cellXfs/' + S + 'xf')]
    sheets = workbook.findall(S + 'sheets/' + S + 'sheet')
    if not 1 <= len(sheets) <= MAX_TABLES or len({sheet.get('name', '').casefold() for sheet in sheets}) != len(sheets):
        raise ValueError('SPREADSHEET_SHEET_BUDGET_OR_DUPLICATE')
    features = {'macros': any(name.lower().endswith('vbaproject.bin') for name in members),
        'external_links': any(row['external'] for row in links.values()) or any(name.startswith('xl/externalLinks/') for name in members),
        'embedded_objects': any(name.startswith('xl/embeddings/') for name in members), 'formulas': False}
    facts.add('workbook', 'xl/workbook.xml', 0, PurePosixPath(filename).name,
        date_system='1904' if workbook.find(S + 'workbookPr') is not None and workbook.find(S + 'workbookPr').get('date1904') in {'1', 'true'} else '1900',
        sheet_count=len(sheets), calculation=dict(workbook.find(S + 'calcPr').attrib) if workbook.find(S + 'calcPr') is not None else {})
    for index, name in enumerate(workbook.findall(S + 'definedNames/' + S + 'definedName')):
        facts.add('defined_name', 'xl/workbook.xml', index, name.text or '', name=name.get('name', ''), attributes=dict(name.attrib))
    for sheet_index, sheet in enumerate(sheets):
        name = sheet.get('name', '')
        relation = links.get(sheet.get(R + 'id', ''))
        if not name or relation is None or relation['external'] or not relation['type'].endswith('/worksheet'):
            raise ValueError('SPREADSHEET_SHEET_RELATIONSHIP_INVALID')
        part = relation['member']
        root = xml_root(members[part])
        dimension = root.find(S + 'dimension')
        facts.add('sheet', part, sheet_index, name, name=name, state=sheet.get('state', 'visible'),
                  dimension=dimension.get('ref', '') if dimension is not None else '', complete=True)
        seen = set()
        for ordinal, cell in enumerate(root.findall(S + 'sheetData/' + S + 'row/' + S + 'c')):
            reference = cell.get('r', '')
            row_number, column_number = coordinates(reference)
            if reference in seen:
                raise ValueError('SPREADSHEET_CELL_DUPLICATE')
            seen.add(reference)
            value, formula = cell_value(cell, strings), cell.find(S + 'f')
            style_index = int(cell.get('s', '0'))
            if style_index < 0 or styles and style_index >= len(styles):
                raise ValueError('SPREADSHEET_CELL_STYLE_INVALID')
            payload = {'sheet': name, 'cell': reference, 'row': row_number, 'column': column_number,
                'value': value, 'style_index': style_index, 'style': styles[style_index] if styles else None,
                'formula': formula.text or '' if formula is not None else None,
                'formula_attributes': dict(formula.attrib) if formula is not None else {},
                'cached_value': value if formula is not None else None}
            text = '=' + payload['formula'] if formula is not None else str(value['value']) if value['value'] is not None else ''
            facts.add('cell', part, ordinal, text, **payload)
            if formula is not None:
                features['formulas'] = True
                references = dependencies(formula.text or '', name)
                facts.add('formula', part, ordinal, '=' + (formula.text or ''), **payload, dependencies=references,
                    dependency_fidelity='syntactic_A1_ranges_only_not_full_formula_resolution')
        for kind, path in (('merged_range', 'mergeCells/mergeCell'), ('validation', 'dataValidations/dataValidation'),
                           ('hyperlink', 'hyperlinks/hyperlink')):
            for ordinal, node in enumerate(root.findall('/'.join(S + value for value in path.split('/')))):
                facts.add(kind, part, ordinal, node.get('ref', node.get('sqref', '')), sheet=name,
                          attributes=dict(node.attrib), expressions=[child.text or '' for child in node])
        for ordinal, relation in enumerate(relationships(members, part).values()):
            facts.add('relationship', part, ordinal, relation['target'], sheet=name, **relation)
            features['external_links'] |= relation['external']
    for part in sorted(members):
        if re.fullmatch(r'xl/tables/[^/]+\.xml', part):
            root = xml_root(members[part])
            facts.add('table', part, 0, root.get('displayName', ''), attributes=dict(root.attrib),
                columns=[dict(node.attrib) for node in root.findall(S + 'tableColumns/' + S + 'tableColumn')])
        elif re.fullmatch(r'xl/charts/[^/]+\.xml', part):
            root = xml_root(members[part])
            facts.add('chart', part, 0, ' '.join(node.text or '' for node in root.iter() if node.tag.endswith('}t')),
                chart_types=sorted({node.tag.rsplit('}', 1)[-1] for node in root.iter() if node.tag.endswith('Chart')}),
                series_references=sorted({node.text or '' for node in root.iter() if node.tag.endswith('}f')}))
    return facts.finish(features=features, fidelity={'structure': 'native_OpenXML', 'cells': 'complete_within_rejection_budgets',
        'formulas': 'source_expression_and_cached_value_separate', 'cached_value_verification': 'not_performed_by_native_extraction', 'layout': 'not_rendered',
        'date_values': 'source_numeric_serial_plus_number_format_and_workbook_date_system'}, limitations=[
        'Formula dependency locators cover syntactic A1 cells/ranges; names, structured references and dynamic dependencies are unresolved.',
        'Cached formula values may be stale. Extraction does not calculate formulas or refresh links.',
        'Chart metadata and retained formatting are not a visual fidelity claim.',
        'Macros, external resources and embedded objects are inventoried or preserved without execution.'])
